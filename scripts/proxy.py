"""Streaming HTTP/WebSocket reverse proxy for local, explicitly listed services."""
import asyncio
from aiohttp import web, WSMsgType, ClientError

HOP = {'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization', 'te', 'trailer',
       'transfer-encoding', 'upgrade', 'content-length', 'content-encoding'}


def cache_policy(path, method, status):
    # Only Next's build-addressed assets are immutable. Never cache HTML/API/auth.
    if method in {'GET','HEAD'} and status in {200,304} and path.startswith('/_next/static/') and path.endswith(('.js','.css','.woff','.woff2','.png','.svg')):
        return 'private, max-age=31536000, immutable'
    if method in {'GET','HEAD'} and status in {200,304} and path in {'/api/config/custom.js','/api/config/custom.css'}:
        return 'private, no-cache'
    return 'private, no-store'


async def forward(request, target, cookie_prefix=None, location_prefix=None,
                  response_transform=None, transform_types=(), drop_cookies=False):
    headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP}
    # Both proxy hops decode responses before streaming. Do not advertise browser
    # codecs that are optional in either Python environment (brotli/zstd).
    headers['Accept-Encoding'] = 'gzip, deflate'
    for key in list(headers):
        if key.lower() == 'x-remote-user':
            del headers[key]
    headers['X-Forwarded-Proto'] = 'https'
    headers['X-Forwarded-Host'] = request.host
    headers['X-Forwarded-For'] = request.headers.get('X-Forwarded-For', request.remote or '127.0.0.1')
    client = request.app['client']
    try:
        if request.headers.get('Upgrade', '').lower() == 'websocket':
            for key in list(headers):
                if key.lower().startswith('sec-websocket-'):
                    del headers[key]
            protocols = [x.strip() for x in request.headers.get('Sec-WebSocket-Protocol', '').split(',') if x.strip()]
            async with client.ws_connect(target, headers=headers, protocols=protocols, heartbeat=30, max_msg_size=0) as upstream:
                ws = web.WebSocketResponse(protocols=[upstream.protocol] if upstream.protocol else [], heartbeat=30, max_msg_size=0)
                await ws.prepare(request)

                async def relay(source, destination):
                    async for message in source:
                        if message.type == WSMsgType.BINARY:
                            await destination.send_bytes(message.data)
                        elif message.type == WSMsgType.TEXT:
                            await destination.send_str(message.data)
                        else:
                            break

                tasks = [asyncio.create_task(relay(ws, upstream)), asyncio.create_task(relay(upstream, ws))]
                try:
                    await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                finally:
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                    await ws.close()
                return ws
        async with client.request(request.method, target, headers=headers, data=request.content if request.can_read_body else None,
                                  allow_redirects=False) as upstream:
            response = web.StreamResponse(status=upstream.status, reason=upstream.reason)
            for key, value in upstream.headers.items():
                low = key.lower()
                if low in HOP:
                    continue
                if low == 'location' and location_prefix and value.startswith('/') and not value.startswith(location_prefix):
                    value = location_prefix.rstrip('/') + value
                if low == 'set-cookie' and drop_cookies:
                    continue
                if low == 'set-cookie' and cookie_prefix:
                    value = value.replace('Path=/', 'Path=' + cookie_prefix.rstrip('/') + '/')
                response.headers.add(key, value)
            # File uploads are data, not trusted same-origin web applications.
            if '/workbench/files/' in request.path and any(kind in upstream.headers.get('Content-Type', '') for kind in ('text/html', 'image/svg+xml')) and not request.path.endswith('/') and not any(request.path.startswith('/workbench/files/'+prefix) for prefix in ('static/','files/','settings/')):
                response.headers['Content-Disposition'] = 'attachment'
                response.headers['Content-Security-Policy'] = "sandbox; default-src 'none'"
            response.headers['Cache-Control'] = cache_policy(request.path, request.method, upstream.status)
            response.headers['X-Content-Type-Options'] = 'nosniff'
            ct=upstream.headers.get('Content-Type','')
            if response_transform and any(kind in ct for kind in transform_types):
                chunks=[]; size=0
                async for chunk in upstream.content.iter_chunked(256*1024):
                    size+=len(chunk)
                    if size>16*1024*1024:
                        raise web.HTTPBadGateway(text='Application asset exceeds Home adapter limit')
                    chunks.append(chunk)
                body=b''.join(chunks)
                body=response_transform(body,ct)
                response.headers.pop('ETag',None)
                response.headers['Content-Length']=str(len(body))
                await response.prepare(request)
                await response.write(body)
                await response.write_eof()
                return response
            await response.prepare(request)
            async for chunk in upstream.content.iter_chunked(256 * 1024):
                await response.write(chunk)
            await response.write_eof()
            return response
    except (ClientError, OSError, asyncio.TimeoutError) as exc:
        raise web.HTTPBadGateway(text='服务暂不可用：' + type(exc).__name__) from exc

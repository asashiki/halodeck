"""Bounded image library behind the existing Homepage Access and same-origin checks."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import struct
from urllib.parse import unquote, urlsplit
import uuid
import zlib

from aiohttp import web
from common import ROOT, now
from panel_editor import host_check
from panel_lock import atomic, config_lock

LIBRARY = ROOT / 'config/wallpapers'
MAX_BYTES = 12 * 1024 * 1024
MAX_TOTAL = 128 * 1024 * 1024
MAX_COUNT = 32
MAX_PIXELS = 24_000_000
IDENT = re.compile(r'^[a-f0-9]{32}$')


def image_info(data):
    """Check bounded PNG/JPEG headers; serve only as images, never active content."""
    if not data or len(data) > MAX_BYTES: raise ValueError('图片最大12 MB')
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        if len(data) < 45 or data[8:16] != b'\0\0\0\rIHDR': raise ValueError('PNG文件头损坏')
        width, height = struct.unpack('>II', data[16:24])
        if zlib.crc32(data[12:29]) != struct.unpack('>I', data[29:33])[0]: raise ValueError('PNG文件头损坏')
        if not data.endswith(b'\0\0\0\0IEND\xaeB`\x82'): raise ValueError('PNG文件不完整')
        extension, mime = 'png', 'image/png'
    elif data.startswith(b'\xff\xd8\xff') and data.endswith(b'\xff\xd9'):
        offset = 2; width = height = 0
        while offset < len(data) - 2:
            if data[offset] != 255: raise ValueError('JPEG文件头损坏')
            while offset < len(data) and data[offset] == 255: offset += 1
            if offset >= len(data): break
            marker = data[offset]; offset += 1
            if marker in (0xda, 0xd9): break
            if marker == 0x01 or 0xd0 <= marker <= 0xd7: continue
            if offset + 2 > len(data): break
            size = int.from_bytes(data[offset:offset+2], 'big')
            if size < 2 or offset + size > len(data): raise ValueError('JPEG文件不完整')
            if marker in (0xc0, 0xc1, 0xc2):
                if size < 8: raise ValueError('JPEG尺寸无效')
                height, width = struct.unpack('>HH', data[offset+3:offset+7]); break
            offset += size
        extension, mime = 'jpg', 'image/jpeg'
    else: raise ValueError('请选择有效的JPG或PNG图片，不支持SVG等格式')
    if not width or not height or width * height > MAX_PIXELS or max(width, height) > 12000:
        raise ValueError('图片尺寸过大或无效，最多2400万像素、单边12000像素')
    return extension, mime, width, height


def manifest():
    path = LIBRARY / 'index.json'
    if not path.exists():
        if LIBRARY.exists() and any(LIBRARY.iterdir()):
            raise ValueError('壁纸目录正在恢复，请稍后重试；未初始化空列表')
        return []
    data = json.loads(path.read_text())
    if not isinstance(data, list): raise ValueError('壁纸目录尚未就绪')
    return data


def public(rows):
    return [dict(id=r['id'], name=r['name'], url='/lab-api/wallpapers/image/'+r['id'],
                 bytes=r['bytes'], width=r['width'], height=r['height']) for r in rows]


def add(data, name):
    extension, mime, width, height = image_info(data)
    ident = hashlib.sha256(data).hexdigest()[:32]
    with config_lock():
        rows = manifest()
        if any(r['id'] == ident for r in rows): return public(rows)
        if len(rows) >= MAX_COUNT or sum(r['bytes'] for r in rows) + len(data) > MAX_TOTAL:
            raise ValueError('壁纸库已满（最多32张、合计128 MB），请先移除不用的壁纸')
        LIBRARY.mkdir(parents=True, exist_ok=True, mode=0o700)
        target = LIBRARY / (ident+'.'+extension)
        temp = LIBRARY / ('.upload-'+uuid.uuid4().hex)
        try:
            with temp.open('xb') as stream:
                os.chmod(temp, 0o600); stream.write(data); stream.flush(); os.fsync(stream.fileno())
            os.replace(temp, target)
            label = ''.join(c for c in name if c.isprintable()).strip()[:60] or '我的壁纸'
            rows.append(dict(id=ident, name=label, extension=extension, mime=mime,
                             width=width, height=height, bytes=len(data), added_at=now()))
            atomic(LIBRARY / 'index.json', rows)
        finally: temp.unlink(missing_ok=True)
        return public(rows)


def remove(ident):
    if not IDENT.fullmatch(ident): raise web.HTTPNotFound()
    with config_lock():
        rows = manifest(); item = next((r for r in rows if r['id'] == ident), None)
        if item is None: raise web.HTTPNotFound()
        atomic(LIBRARY / 'index.json', [r for r in rows if r['id'] != ident])
        (LIBRARY / (ident+'.'+item['extension'])).unlink(missing_ok=True)
        return public([r for r in rows if r['id'] != ident])


def check(request, write=False):
    host_check(request)
    if write:
        origin = urlsplit(request.headers.get('Origin', ''))
        if (origin.scheme not in {'http', 'https'} or origin.netloc != request.host or
            request.headers.get('Sec-Fetch-Site') in {'cross-site', 'same-site'} or
            request.headers.get('X-Lab-Wallpaper') != '1'):
            raise web.HTTPForbidden(text='请从首页的壁纸选择中操作')


async def listing(request):
    check(request)
    try: rows = await asyncio.to_thread(manifest)
    except (OSError, ValueError): raise web.HTTPServiceUnavailable(text='壁纸库暂时不可用，请稍后重试')
    return web.json_response({'items': public(rows)})


async def upload(request):
    check(request, True)
    if request.content_type != 'application/octet-stream': raise web.HTTPUnsupportedMediaType()
    if request.content_length and request.content_length > MAX_BYTES:
        raise web.HTTPRequestEntityTooLarge(max_size=MAX_BYTES, actual_size=request.content_length)
    data = bytearray()
    async for chunk in request.content.iter_chunked(65536):
        data.extend(chunk)
        if len(data) > MAX_BYTES: raise web.HTTPRequestEntityTooLarge(max_size=MAX_BYTES, actual_size=len(data))
    name = unquote(request.headers.get('X-Lab-Wallpaper-Name', '我的壁纸')[:720])
    try: items = await asyncio.to_thread(add, bytes(data), name)
    except ValueError as exc: raise web.HTTPBadRequest(text=str(exc))
    except OSError: raise web.HTTPServiceUnavailable(text='未能保存，请稍后重试')
    return web.json_response({'items': items})


async def delete(request):
    check(request, True)
    try: items = await asyncio.to_thread(remove, request.match_info['ident'])
    except ValueError: raise web.HTTPServiceUnavailable(text='壁纸目录尚未就绪')
    return web.json_response({'items': items})


async def image(request):
    check(request); ident = request.match_info['ident']
    if not IDENT.fullmatch(ident): raise web.HTTPNotFound()
    try: rows = await asyncio.to_thread(manifest)
    except (OSError, ValueError): raise web.HTTPServiceUnavailable()
    item = next((r for r in rows if r['id'] == ident), None)
    if item is None: raise web.HTTPNotFound()
    path = LIBRARY / (ident+'.'+item['extension'])
    if not path.is_file(): raise web.HTTPNotFound()
    response = web.FileResponse(path, headers={'Cache-Control':'private, max-age=86400, immutable'})
    response.content_type = item['mime']
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


def register(app):
    app.router.add_get('/lab-api/wallpapers', listing)
    app.router.add_post('/lab-api/wallpapers', upload)
    app.router.add_delete('/lab-api/wallpapers/{ident}', delete)
    app.router.add_get('/lab-api/wallpapers/image/{ident}', image)

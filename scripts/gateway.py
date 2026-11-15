#!/usr/bin/env python3
"""Status adapter, bounded display editors and proxy to fixed loopback destinations."""
import asyncio
import importlib.util
import json
import os
import re
import shutil
import time
import datetime as dt
from pathlib import Path
from urllib.parse import urlsplit
from aiohttp import web, ClientSession, ClientTimeout
from common import ROOT, STATE, now, read_json, write_json, CONFIG, HOSTS, PORT, WORKSPACE
from guard import inspect

import proxy
import dashboard
import liveness
import panel_editor
import wallpapers
import mounts
import terminal_images
import native_metrics
import cloud_backup

GATEWAY_PORT=PORT
HOMEPAGE_PORT=PORT+1
PREVIEW=os.environ.get('LAB_PREVIEW')=='1'

SERVICES = json.loads((ROOT/'config/services.json').read_text())
HOMEPAGE_APIS = {'/api/healthcheck', '/api/hash', '/api/services', '/api/bookmarks',
    '/api/widgets', '/api/siteMonitor', '/api/config/custom.css', '/api/config/custom.js',
    '/api/services/proxy', '/api/widgets/customapi'}
HOMEPAGE_APIS.update({'/api/auth/session','/api/validate','/api/revalidate'})

def network_sample():
    rx=tx=0
    for line in Path('/proc/net/dev').read_text().splitlines()[2:]:
        name,fields=line.split(':',1)
        if name.strip()=='lo':continue
        values=fields.split();rx+=int(values[0]);tx+=int(values[8])
    return rx,tx

def cpu_sample():
    fields = [int(x) for x in Path('/proc/stat').read_text().splitlines()[0].split()[1:9]]
    return sum(fields), fields[3]+fields[4]

def capacity_reduced(data,baseline):
    old_cpu=baseline.get('cpu',{}).get('visible_limit_upper_bound_cores')
    current_cpu=min(data['cpu_visible'],data.get('cpu_quota') or float('inf'))
    old_memory=baseline.get('kernel_memory_view',{}).get('MemTotal_bytes')
    old_memory_limit=baseline.get('cgroup',{}).get('visible_memory_limit_bytes')
    if old_memory and old_memory_limit:old_memory=min(old_memory,old_memory_limit)
    current_memory=min(data['memory_total'],data.get('memory_limit') or float('inf'))
    old_disk=next((x.get('total_bytes') for x in baseline.get('storage',[]) if x.get('path')=='/workspace'),None)
    return bool((old_cpu and current_cpu<old_cpu) or (old_memory and current_memory<old_memory*.9) or
                (old_disk and data['disk_total']<old_disk*.9))

def resources(previous, started):
    current = cpu_sample()
    total, idle = current[0]-previous[0], current[1]-previous[1]
    mem = {}
    for line in Path('/proc/meminfo').read_text().splitlines():
        fields = line.split()
        if fields[0] in ('MemTotal:', 'MemAvailable:'): mem[fields[0][:-1]] = int(fields[1])*1024
    cpu_limit = None
    memory_limit = None
    cgroup = Path('/sys/fs/cgroup/agent')
    if not cgroup.exists(): cgroup = Path('/sys/fs/cgroup')
    try:
        quota, period = (cgroup/'cpu.max').read_text().split()
        if quota != 'max': cpu_limit = int(quota)/int(period)
        value = (cgroup/'memory.max').read_text().strip()
        if value != 'max': memory_limit = int(value)
    except (OSError, ValueError): pass
    disk = shutil.disk_usage(WORKSPACE)
    fs = os.statvfs(WORKSPACE)
    data = {'observed_at': now(), 'cpu_percent': round((1-idle/total)*100, 1) if total else None,
            'cpu_visible': len(os.sched_getaffinity(0)), 'cpu_quota': cpu_limit,
            'memory_total': mem['MemTotal'], 'memory_available': mem['MemAvailable'],
            'memory_limit': memory_limit, 'disk_total': disk.total, 'disk_free': disk.free,
            'inodes_free': fs.f_favail, 'inodes_total': fs.f_files,
            'scope': '当前 Linux 可见系统；cgroup 未见额外数值上限，隐藏的宿主配额未知'}
    baseline = read_json(ROOT/'reports/preflight-before.json', {})
    data['capacity_reduced'] = capacity_reduced(data,baseline)
    if cpu_limit is not None or memory_limit is not None:
        data['scope']='当前 Linux 系统读数；可见 cgroup 上限：CPU '+str(cpu_limit or '未限制')+'，内存 '+str(memory_limit or '未限制')+' 字节；隐藏祖先限制未知'
    return current, data

async def probe(client, service):
    started = time.monotonic()
    try:
        async with client.get(service['health_url'], headers=service.get('health_headers'), timeout=ClientTimeout(total=4), allow_redirects=False) as r:
            body = await r.content.read(512)
            good = r.status in service.get('health_codes', [200])
            if service.get('health_marker'): good = good and service['health_marker'].encode() in body
            return {'id': service['id'], 'name': service['name'], 'ok': good, 'http_status': r.status,
                    'observed_at': now(), 'ms': round((time.monotonic()-started)*1000)}
    except Exception as exc:
        return {'id': service['id'], 'name': service['name'], 'ok': False,
                'observed_at': now(), 'reason': type(exc).__name__}

async def sample_loop(app):
    previous = cpu_sample()
    previous_network=network_sample();previous_time=time.monotonic()
    first=True
    while True:
        try:
            await asyncio.sleep(2 if first else 15)
            first=False
            previous, data = resources(previous, 0)
            network=network_sample();sample_time=time.monotonic();elapsed=sample_time-previous_time
            data['network_rx']=max(0,(network[0]-previous_network[0])/elapsed)
            data['network_tx']=max(0,(network[1]-previous_network[1])/elapsed)
            previous_network=network;previous_time=sample_time
            if not PREVIEW: await asyncio.to_thread(native_metrics.record, data)
            checks = await asyncio.gather(*(probe(app['client'], s) for s in SERVICES))
            backup = read_json(STATE/'backup-status.json', {'state': 'NOT_CONFIGURED', 'offsite': False,
                'reason': '异地存储尚未授权；本机副本不算异地备份'})
            recovery = inspect()
            try:
                live_24h = await asyncio.to_thread(liveness.query_timeline, liveness.DB_PATH, '24h')
                liveness_summary = {
                    'current_state': live_24h['current_state'],
                    'current_state_label': live_24h['current_state_label'],
                    'first_observed_at': live_24h['first_observed_iso'],
                    'last_tick_at': live_24h['last_tick_iso'],
                    'observed_alive_ratio_24h': live_24h['observed_alive_ratio'],
                    'gap_count_24h': live_24h['gap_count'],
                    'restart_count_24h': live_24h['restart_count'],
                }
            except Exception:
                liveness_summary = {'current_state': 'unknown'}
            snapshot = {'collected_at': now(), 'expires_after_seconds': 60,
                'resources': data, 'services': checks, 'recovery': recovery, 'backup': backup,
                'liveness': liveness_summary,
                'heartbeat': read_json(STATE/'heartbeat-status.json', {'state':'NOT_CONFIGURED',
                    'reason':'尚未连接机器外部的告警服务'}),
                'restore': read_json(STATE/'restore-status.json', {'state': 'NOT_TESTED', 'offsite':False})}
            app['data']['snapshot']=snapshot
            app['data']['dashboard']=await dashboard.collect(app['client'],snapshot)
            if not PREVIEW:
                write_json(STATE/'status.json',snapshot)
                write_json(STATE/'dashboard-status.json',app['data']['dashboard'])
        except asyncio.CancelledError: raise
        except Exception as exc:
            print('collection failed: '+type(exc).__name__, flush=True)
            await asyncio.sleep(15)

async def context(app):
    await asyncio.to_thread(panel_editor.recover_pending)
    app['data']={}
    cached=read_json(STATE/'dashboard-status.json')
    if cached:app['data']['dashboard']=cached
    cached=read_json(STATE/'status.json')
    if cached:app['data']['snapshot']=cached
    app['client'] = ClientSession(auto_decompress=True, timeout=ClientTimeout(total=None, sock_connect=5))
    task = asyncio.create_task(sample_loop(app))
    yield
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await app['client'].close()

async def status(request):
    return web.json_response(request.app['data'].get('snapshot', {'collected_at': None, 'state': 'UNKNOWN'}))

async def liveness_timeline(request):
    window = request.query.get('window', '24h')
    if window not in liveness.WINDOWS:
        raise web.HTTPBadRequest(text='window must be 24h or 7d')
    payload = await asyncio.to_thread(liveness.query_timeline, liveness.DB_PATH, window)
    return web.json_response(payload)

def dashboard_payload(app):
    data=app['data'].get('dashboard',{'collected_at':None,'widgets':{},'history':[]})
    try:age=(dt.datetime.now(dt.timezone.utc)-dt.datetime.fromisoformat(data['collected_at'])).total_seconds()
    except (ValueError,TypeError,KeyError):age=float('inf')
    if age<=60:return dict(data,stale=False)
    widgets={}
    for name,values in data.get('widgets',{}).items():
        widgets[name]={k:([{'name':'数据已过期','label':'等待重新采集'}] if k=='items' else '未知') for k in values}
    return dict(data,stale=True,widgets=widgets,history=[])

async def dashboard_status(request):
    return web.json_response(dashboard_payload(request.app))

async def dashboard_widget(request):
    name=request.match_info['name']
    data=dashboard_payload(request.app)
    if name not in data['widgets']:raise web.HTTPNotFound()
    return web.json_response(dict(data['widgets'][name],collected_at=data['collected_at'],stale=data['stale']))

async def service_health(request):
    data=dashboard_payload(request.app)
    checks=request.app['data'].get('snapshot',{}).get('services',[])
    check=next((s for s in checks if s['id']==request.match_info['name']),None)
    if check is None:raise web.HTTPNotFound()
    return web.json_response({'ok':check['ok'] and not data['stale'],'observed_at':check['observed_at']},
                             status=200 if check['ok'] and not data['stale'] else 503)

async def top(request):
    return web.json_response(await native_metrics.top(request.app['data'].get('snapshot',{})))

def pwa_html(body, content_type):
    # Homepage ships its own manifest first; browsers must see only HaloDeck's.
    body = re.sub(rb"<link\b[^>]*\brel=[\"']manifest[\"'][^>]*>", b'', body, flags=re.I)
    tags = b'<link rel="manifest" href="/lab-assets/manifest.webmanifest" crossorigin="use-credentials"><meta name="theme-color" content="#0b1120"><meta name="apple-mobile-web-app-capable" content="yes"><meta name="apple-mobile-web-app-status-bar-style" content="black-translucent"><meta name="apple-mobile-web-app-title" content="HaloDeck"><link rel="apple-touch-icon" href="/lab-assets/halodeck-apple-touch.png">'
    return body.replace(b'</head>', tags+b'</head>', 1)

async def catalog(request):
    return web.json_response(json.loads((ROOT/'config/catalog.json').read_text()))

async def assets(request):
    name = request.match_info['name']
    if name == 'panel-editor.html':
        raise web.HTTPNotFound()
    if name not in {p.name for p in (ROOT/'config/assets').iterdir() if p.is_file()}:
        raise web.HTTPNotFound()
    cache='private, max-age=86400' if Path(name).suffix.lower() in {'.jpg','.jpeg','.png','.svg','.woff','.woff2'} else 'private, no-cache'
    response=web.FileResponse(ROOT/'config/assets'/name,headers={'Cache-Control':cache})
    if name.endswith('.webmanifest'): response.content_type='application/manifest+json'
    return response

async def dispatch(request):
    if request.path == '/term' or request.path.startswith('/term/'):
        return await proxy.forward(request,'http://127.0.0.1:'+str(PORT+2)+request.rel_url.path_qs,
            response_transform=terminal_images.enhance,transform_types=('text/html',))
    for prefix,port in [('/workbench/files',PORT+4),('/workbench/code',PORT+3)]:
        if request.path==prefix:raise web.HTTPPermanentRedirect(location=prefix+'/')
        if request.path.startswith(prefix+'/'):
            path=request.rel_url.path_qs if port==PORT+4 else request.rel_url.path_qs[len(prefix):]
            forwarded=request.clone(headers={k:v for k,v in request.headers.items() if k.lower()!='authorization'})
            return await proxy.forward(forwarded,'http://127.0.0.1:'+str(port)+path,cookie_prefix=prefix,location_prefix=prefix)
    if request.method not in {'GET','HEAD'}:raise web.HTTPMethodNotAllowed(request.method,['GET','HEAD'])
    if request.path.startswith('/api/') and request.path.rstrip('/') not in HOMEPAGE_APIS:raise web.HTTPForbidden()
    forwarded=request.clone(headers=dict(request.headers,Host='localhost'))
    return await proxy.forward(forwarded,'http://127.0.0.1:'+str(HOMEPAGE_PORT)+request.rel_url.path_qs,response_transform=pwa_html,transform_types=('text/html',))

@web.middleware
async def headers(request, handler):
    if request.host.split(':')[0] not in HOSTS:raise web.HTTPMisdirectedRequest()
    # Authentication also covers WebSockets and uploads; never trust client identity headers.
    import base64,hashlib,hmac
    auth=read_json(ROOT/'private/auth.json',{})
    try:
        scheme,value=request.headers.get('Authorization','').split(' ',1)
        if scheme.lower()!='basic':raise ValueError()
        user,password=base64.b64decode(value,validate=True).decode().split(':',1)
        valid=hmac.compare_digest(user,auth['username']) and hmac.compare_digest(hashlib.sha256(password.encode()).hexdigest(),auth['password_sha256'])
    except (ValueError,KeyError,UnicodeError):valid=False
    if not valid:raise web.HTTPUnauthorized(headers={'WWW-Authenticate':'Basic realm="HaloDeck", charset="UTF-8"'})
    if request.method not in {'GET','HEAD','OPTIONS'}:
        origin=request.headers.get('Origin')
        if origin and urlsplit(origin).netloc != request.host:
            raise web.HTTPForbidden(text='Origin does not match this application')
    response = await handler(request)
    if not response.prepared:
        response.headers.setdefault('Cache-Control', 'private, no-store')
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'same-origin'
    return response

app = web.Application(middlewares=[headers], client_max_size=10*1024**3)
app.cleanup_ctx.append(context)
panel_editor.register(app)
wallpapers.register(app)
mounts.register(app)
terminal_images.register(app)
cloud_backup.register(app)
app.router.add_get('/lab-api/top', top)
app.router.add_get('/lab-api/status', status)
app.router.add_get('/lab-api/liveness', liveness_timeline)
app.router.add_get('/lab-api/dashboard', dashboard_status)
app.router.add_get('/lab-api/dashboard/widget/{name}', dashboard_widget)
app.router.add_get('/lab-api/health/{name}',service_health)
app.router.add_get('/lab-api/catalog', catalog)
app.router.add_get('/lab-assets/{name}', assets)
app.router.add_route('*', '/{tail:.*}', dispatch)
if __name__ == '__main__': web.run_app(app, host='127.0.0.1', port=GATEWAY_PORT, access_log=None)

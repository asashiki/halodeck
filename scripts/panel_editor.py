"""Small, bounded editor for Homepage display configuration. No command or proxy editor."""
import asyncio
import copy
import hashlib
import json
import os
import re
import uuid
from pathlib import Path
from urllib.parse import urlsplit, parse_qsl

from aiohttp import web, ClientTimeout
from common import ROOT, STATE, now
from panel_lock import atomic, config_lock

CONFIG = ROOT / 'config/homepage-v2'
HISTORY = STATE / 'panel-editor'
FILES = ('services.yaml', 'settings.yaml', 'widgets.yaml')
from common import HOSTS
ALLOWED_HOSTS = HOSTS


def locked():
    return config_lock(CONFIG, HISTORY)


def bundle():
    return {name: json.loads((CONFIG / name).read_text()) for name in FILES}


def revision(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def catalogue():
    return json.loads((ROOT / 'config/panel-catalog.json').read_text())


def display(data):
    settings = data['settings.yaml']
    result = {'title': next((w['greeting']['text'] for w in data['widgets.yaml'] if 'greeting' in w), 'HaloDeck'),
              'background': settings.get('background', {}).get('image', ''),
              'opacity': settings.get('background', {}).get('opacity', 38),
              'show_metrics': False, 'show_recent': False, 'groups': []}
    for group in data['services.yaml']:
        name, cards = next(iter(group.items()))
        items = []
        for card in cards:
            label, value = next(iter(card.items()))
            cid = value.get('id', '')
            if cid.startswith('metric-'):
                result['show_metrics'] = True
            elif cid == 'activity':
                result['show_recent'] = True
            elif not cid.startswith('future-'):
                items.append(dict(id=cid or 'custom-' + uuid.uuid4().hex[:12], name=label,
                                  **{k: value.get(k, '') for k in ('href', 'description', 'icon')}))
        if items: result['groups'].append({'name': name, 'items': items})
    return result


def text(value, label, limit, empty=False):
    if not isinstance(value, str) or len(value) > limit or (not value.strip() and not empty):
        raise ValueError(f'{label}不能为空且最多 {limit} 个字符' if not empty else f'{label}最多 {limit} 个字符')
    if any(ord(c) < 32 for c in value): raise ValueError(f'{label}不能包含控制字符')
    return value.strip()


def safe_url(value):
    value = text(value, '网址', 2000)
    if '\\' in value or any(c.isspace() for c in value): raise ValueError('网址不能包含空格或反斜杠')
    parts = urlsplit(value)
    if not ((value.startswith('/') and not value.startswith('//')) or
            (parts.scheme in {'https', 'http'} and parts.hostname)):
        raise ValueError('请输入 https:// 或 http:// 网址，或本站 / 开头的路径')
    if parts.username or parts.password: raise ValueError('网址不能保存账号或密码')
    if any(re.search(r'token|password|secret|api.?key|authorization', k, re.I) for k, _ in parse_qsl(parts.query)):
        raise ValueError('不要把令牌或密码放进首页网址')
    return value


def allowed_icons(cat, data):
    icons = list(cat.get('icons', []))
    seen = set(icons)
    candidates = [v.get('icon', '') for item in cat.get('services', []) for v in item.values()]
    candidates += [v.get('icon', '') for grp in data.get('services.yaml', []) for cards in grp.values() for card in cards for v in card.values()]
    for icon in candidates:
        if isinstance(icon, str) and icon not in seen and re.fullmatch(r'/lab-assets/[A-Za-z0-9._-]+', icon):
            seen.add(icon)
            icons.append(icon)
    return icons


def compile_config(model, old):
    if not isinstance(model, dict): raise ValueError('首页格式不正确')
    cat = catalogue()
    title = text(model.get('title'), '首页名称', 60)
    background = model.get('background')
    if background not in cat['backgrounds']: raise ValueError('请选择已有背景')
    opacity = model.get('opacity')
    if type(opacity) is not int or not 10 <= opacity <= 80: raise ValueError('背景强度应为 10–80')
    for field in ('show_metrics', 'show_recent'):
        if type(model.get(field)) is not bool: raise ValueError('显示开关格式不正确')
    groups = model.get('groups')
    if not isinstance(groups, list) or not 1 <= len(groups) <= 12: raise ValueError('请保留 1–12 个分组')
    templates = {v['id']: v for item in cat['services'] for v in item.values()}
    icons = set(allowed_icons(cat, old))
    result = []; layout = {}; seen = set(); group_names = set(); total = 0
    if model['show_metrics']:
        result.append(copy.deepcopy(cat['metrics']))
        layout['本机概览'] = {'style': 'row', 'columns': 4, 'header': False}
    for group in groups:
        if not isinstance(group, dict): raise ValueError('分组格式不正确')
        name = text(group.get('name'), '分组名称', 30)
        if name in group_names or name in {'本机概览', '最近任务', '__proto__', 'constructor', 'prototype'}:
            raise ValueError('分组名称重复或被占用')
        group_names.add(name)
        entries = group.get('items')
        if not isinstance(entries, list) or not 1 <= len(entries) <= 24: raise ValueError(f'“{name}”需要 1–24 个入口')
        cards = []; names = set()
        for item in entries:
            if not isinstance(item, dict): raise ValueError('入口格式不正确')
            cid = item.get('id')
            if not isinstance(cid, str) or not re.fullmatch(r'(app|custom)-[a-z0-9-]{1,60}', cid) or cid in seen:
                raise ValueError('入口标识无效或重复')
            seen.add(cid)
            label = text(item.get('name'), '入口名称', 40)
            if label in names or label in {'__proto__', 'constructor', 'prototype'}: raise ValueError('同组入口名称不能重复')
            names.add(label)
            href = safe_url(item.get('href'))
            icon = item.get('icon')
            if icon not in icons: raise ValueError('请选择内置图标')
            base = copy.deepcopy(templates.get(cid, {}))
            # A user-changed URL no longer represents the registered application.
            if base.get('href') != href:
                base = {}
            base.update(id=cid, href=href, icon=icon, description=text(item.get('description', ''), '说明', 100, True))
            cards.append({label: base}); total += 1
        result.append({name: cards})
        columns = old['settings.yaml'].get('layout', {}).get(name, {}).get('columns', 3)
        layout[name] = {'style': 'row', 'columns': columns if columns in (2, 3, 4) else 3, 'header': old['settings.yaml'].get('layout', {}).get(name, {}).get('header', True)}
    if total > 80: raise ValueError('最多保存 80 个入口')
    if model['show_recent'] and cat.get('recent'):
        result.append(copy.deepcopy(cat['recent']))
        layout['最近任务'] = {'style': 'row', 'columns': 1}
    settings = copy.deepcopy(old['settings.yaml'])
    settings.update(title=title + ' · 工作台', layout=layout)
    settings['background'] = {'image': background, 'opacity': opacity} if background else {}
    widgets = copy.deepcopy(old['widgets.yaml'])
    for widget in widgets:
        if 'greeting' in widget: widget['greeting']['text'] = title
    return {'services.yaml': result, 'settings.yaml': settings, 'widgets.yaml': widgets}


def state():
    with locked():
        data = bundle(); cat = catalogue()
        return {'revision': revision(data), 'model': display(data), 'catalog': cat['choices'],
                'icons': allowed_icons(cat, data), 'backgrounds': cat['backgrounds'],
                'can_undo': (HISTORY / 'last.json').is_file()}


def save(payload, undo=False):
    with locked():
        old = bundle()
        if payload.get('revision') != revision(old):
            raise web.HTTPConflict(text='首页已经在别处修改。请先导出当前草稿，再重新载入，避免覆盖。')
        if undo:
            last = HISTORY / 'last.json'
            if not last.is_file(): raise ValueError('还没有可撤销的保存')
            previous = json.loads(last.read_text())
            new = json.loads((HISTORY / previous['file']).read_text())['config']
        else:
            new = compile_config(payload.get('model'), old)
        ident = uuid.uuid4().hex + '.json'
        atomic(HISTORY / ident, {'at': now(), 'config': old})
        atomic(HISTORY / 'pending.json', old)
        try:
            for name in FILES: atomic(CONFIG / name, new[name])
            atomic(HISTORY / 'last.json', {'file': ident, 'at': now()})
            (HISTORY / 'pending.json').unlink()
        except BaseException:
            for name in FILES: atomic(CONFIG / name, old[name])
            (HISTORY / 'pending.json').unlink(missing_ok=True)
            raise
        return {'revision': revision(new), 'model': display(new), 'saved_at': now(), 'can_undo': True}


def host_check(request, write=False):
    if urlsplit('//' + request.host).hostname not in ALLOWED_HOSTS: raise web.HTTPMisdirectedRequest()
    if write:
        origin = request.headers.get('Origin', '')
        parsed = urlsplit(origin)
        if parsed.scheme not in {'http', 'https'} or parsed.netloc != request.host:
            raise web.HTTPForbidden(text='请从首页打开编辑器再保存')
        if request.headers.get('Sec-Fetch-Site') in {'cross-site', 'same-site'}:
            raise web.HTTPForbidden(text='只接受同一页面的保存请求')
        if request.headers.get('X-Lab-Editor') != '1' or request.content_type != 'application/json':
            raise web.HTTPForbidden(text='保存请求格式不正确')
        if request.content_length and request.content_length > 100000:
            raise web.HTTPRequestEntityTooLarge(max_size=100000, actual_size=request.content_length)


async def get_state(request):
    host_check(request)
    try: return web.json_response(await asyncio.to_thread(state))
    except (OSError, ValueError, KeyError):
        raise web.HTTPServiceUnavailable(text='首页配置尚未就绪；未创建或覆盖任何配置')


async def mutate(request):
    host_check(request, True)
    raw = bytearray()
    async for chunk in request.content.iter_chunked(16384):
        raw.extend(chunk)
        if len(raw) > 100000: raise web.HTTPRequestEntityTooLarge(max_size=100000, actual_size=len(raw))
    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict): raise ValueError('请求格式不正确')
        result = await asyncio.to_thread(save, payload, request.path.endswith('/undo'))
    except (ValueError, TypeError, KeyError) as exc:
        raise web.HTTPBadRequest(text=str(exc))
    except OSError:
        raise web.HTTPServiceUnavailable(text='未能保存；已有配置和副本已保留，请稍后重试')
    try:
        port = os.environ.get('LAB_HOMEPAGE_PORT', '8104')
        async with request.app['client'].get(f'http://127.0.0.1:{port}/api/revalidate', timeout=ClientTimeout(total=5)) as response:
            result['reloaded'] = response.status == 200
    except Exception:
        result['reloaded'] = False
    return web.json_response(result)


async def page(request):
    host_check(request)
    response = web.FileResponse(ROOT / 'config/assets/panel-editor.html')
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; frame-ancestors 'self'; base-uri 'none'; form-action 'self'"
    return response


def register(app):
    app.router.add_get('/lab-edit/', page)
    app.router.add_get('/lab-api/editor', get_state)
    app.router.add_post('/lab-api/editor', mutate)
    app.router.add_post('/lab-api/editor/undo', mutate)


def recover_pending():
    with locked():
        pass

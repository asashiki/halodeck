"""Bounded clipboard-image uploads for the existing authenticated Home terminal."""
import asyncio
import fcntl
import hashlib
import os
from pathlib import Path
import tempfile
from urllib.parse import urlsplit
from aiohttp import web
from wallpapers import image_info, MAX_BYTES

from common import WORKSPACE, HOSTS
UPLOADS=WORKSPACE/'inbox/terminal-images'
MAX_COUNT=128
MAX_TOTAL=256*1024*1024



def boundary(request):
    if urlsplit('//'+request.host).hostname not in HOSTS:
        raise web.HTTPMisdirectedRequest()
    origin=urlsplit(request.headers.get('Origin',''))
    if origin.scheme not in {'http','https'} or origin.netloc!=request.host:
        raise web.HTTPForbidden(text='请从终端页面上传图片')
    if request.headers.get('Sec-Fetch-Site') in {'cross-site','same-site'}:
        raise web.HTTPForbidden(text='只接受同源图片上传')
    if request.headers.get('X-Terminal-Image')!='1':
        raise web.HTTPForbidden(text='图片上传请求格式不正确')


def store(data):
    ext,mime,width,height=image_info(data)
    digest=hashlib.sha256(data).hexdigest()
    # The parent is already the owner's file inbox; never accept a client path.
    UPLOADS.mkdir(parents=True,exist_ok=True,mode=0o700)
    if UPLOADS.is_symlink():raise ValueError('图片目录不可用')
    with (UPLOADS/'.upload.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        target=UPLOADS/(digest+'.'+ext)
        files=[p for p in UPLOADS.iterdir() if p.is_file() and p.suffix in {'.png','.jpg'}]
        if not target.exists():
            if len(files)>=MAX_COUNT or sum(p.stat().st_size for p in files)+len(data)>MAX_TOTAL:
                raise ValueError('图片收件箱已满，请到文件页面整理 terminal-images 文件夹后再上传')
            fd,name=tempfile.mkstemp(prefix='.upload-',dir=UPLOADS)
            try:
                with os.fdopen(fd,'wb') as out:
                    out.write(data);out.flush();os.fsync(out.fileno())
                os.replace(name,target)
            finally:
                Path(name).unlink(missing_ok=True)
        elif target.is_symlink() or target.read_bytes()!=data:
            raise ValueError('同名图片文件不可用')
    return {'path':str(target),'bytes':len(data),'width':width,'height':height,'mime':mime}


async def upload(request):
    boundary(request)
    if request.content_length and request.content_length>MAX_BYTES:
        raise web.HTTPRequestEntityTooLarge(max_size=MAX_BYTES,actual_size=request.content_length)
    data=bytearray()
    async for part in request.content.iter_chunked(128*1024):
        data.extend(part)
        if len(data)>MAX_BYTES:
            raise web.HTTPRequestEntityTooLarge(max_size=MAX_BYTES,actual_size=len(data))
    try:result=await asyncio.to_thread(store,bytes(data))
    except ValueError as e:raise web.HTTPBadRequest(text=str(e))
    except OSError:raise web.HTTPServiceUnavailable(text='图片暂时无法保存，请稍后重试')
    return web.json_response(result,status=201)


def enhance(body,content_type):
    if 'text/html' not in content_type:return body
    return body.replace(b'</head>',b'<script defer src="/lab-assets/terminal-images.js"></script></head>',1)


def register(app):
    app.router.add_post('/lab-api/terminal-images',upload)

"""Fixed Magpie mount; no personal bot integrations."""
import json,re
from aiohttp import web
import proxy
from common import CONFIG,ROOT,PORT
HOME_HOST=CONFIG.get('hostname','localhost')
# Same-origin adaptation is limited to this owner's local Magpie process.
BASE_SCRIPT='''<script>(()=>{const p='/magpie';const map=x=>{try{let u=new URL(x,location.href);if(u.origin===location.origin&&!u.pathname.startsWith(p+'/'))u.pathname=p+u.pathname;return u.href}catch{return x}};const f=window.fetch;window.fetch=(x,o)=>f(x instanceof Request?new Request(map(x.url),x):map(x),o);const open=XMLHttpRequest.prototype.open;XMLHttpRequest.prototype.open=function(m,u,...a){return open.call(this,m,map(u),...a)};for(const name of ['WebSocket','EventSource']){let Old=window[name];if(Old)window[name]=new Proxy(Old,{construct(T,a){let u=new URL(a[0],location.href);if(u.host===location.host&&!u.pathname.startsWith(p+'/'))u.pathname=p+u.pathname;a[0]=u.href;return Reflect.construct(T,a)}})}})();</script>'''
def rewrite_body(body,content_type,prefix,path):
 text=body.decode('utf-8')
 if 'text/html' in content_type:
  text=re.sub(r'''((?:href|src|action)=["'])/(?!/)([^"']*)''',lambda m:m[1]+prefix+'/'+m[2],text)
  text=text.replace('<head>','<head>'+BASE_SCRIPT,1)
 if 'text/css' in content_type:text=re.sub(r'''url\((["']?)/(?!/)''',lambda m:'url('+m[1]+prefix+'/',text)
 return text.encode()
async def route(request):
 if request.path=='/magpie':raise web.HTTPPermanentRedirect(location='/magpie/')
 if not CONFIG.get('magpie_enabled',True):raise web.HTTPServiceUnavailable(text='Magpie is disabled in settings.json')
 headers={k:v for k,v in request.headers.items() if k.lower() not in {'authorization','cookie'}}
 headers['Cookie']='magpie_web_'+str(PORT+5)+'='+(ROOT/'private/magpie-web-key').read_text().strip()
 headers['X-Forwarded-Prefix']='/magpie'
 return await proxy.forward(request.clone(headers=headers),'http://127.0.0.1:'+str(PORT+5)+request.rel_url.path_qs[len('/magpie'):],cookie_prefix='/magpie',location_prefix='/magpie',response_transform=lambda b,c:rewrite_body(b,c,'/magpie',request.path),transform_types=('text/html','text/css'),drop_cookies=True)
def register(app):
 app.router.add_route('*','/magpie',route);app.router.add_route('*','/magpie/{tail:.*}',route)

import asyncio,sys,unittest,hashlib
from unittest.mock import patch
from pathlib import Path
from aiohttp import web,ClientSession
from aiohttp.test_utils import TestClient,TestServer
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import proxy,gateway

class HomeTransport(unittest.IsolatedAsyncioTestCase):
 async def test_transform_reads_all_chunks_and_scopes_cookie(self):
  async def content(r):
   resp=web.StreamResponse(headers={'Content-Type':'text/html','Set-Cookie':'session=fixture; Path=/; HttpOnly'})
   await resp.prepare(r)
   await resp.write(b'<head>first')
   await asyncio.sleep(.02)
   await resp.write(b'last</head>');await resp.write_eof();return resp
  up=TestServer(web.Application());up.app.router.add_get('/',content);await up.start_server()
  client=ClientSession();app=web.Application();app['client']=client
  async def route(r):
   return await proxy.forward(r,str(up.make_url('/')),cookie_prefix='/backup',response_transform=lambda b,ct:b.replace(b'first',b'FIRST'),transform_types=('text/html',))
  app.router.add_get('/',route)
  try:
   async with TestClient(TestServer(app)) as test:
    r=await test.get('/')
    self.assertEqual(await r.read(),b'<head>FIRSTlast</head>')
    self.assertIn('Path=/backup/',r.headers['Set-Cookie'])
  finally:await client.close();await up.close()
 async def test_home_mounts_reject_foreign_origin_and_unknown_host(self):
  app=web.Application(middlewares=[gateway.headers]);app.router.add_route('*','/{path:.*}',lambda r:web.Response(text='not called'))
  async with TestClient(TestServer(app)) as c:
   with patch.object(gateway,'read_json',return_value={'username':'tester','password_sha256':hashlib.sha256(b'fixture').hexdigest()}):
    r=await c.post('/magpie/api/settings',headers={'Host':'localhost','Origin':'https://foreign.example','Authorization':'Basic dGVzdGVyOmZpeHR1cmU='})
   self.assertEqual(r.status,403)
   r=await c.get('/magpie/',headers={'Host':'foreign.example'})
   self.assertEqual(r.status,421)
if __name__=='__main__':unittest.main()

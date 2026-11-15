import hashlib,json,struct,sys,tempfile,unittest,zlib
from pathlib import Path
from unittest.mock import patch
from aiohttp import web
from aiohttp.test_utils import TestClient,TestServer
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import terminal_images as images

def png(width=2,height=2):
 def chunk(kind,data):return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data))
 return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',width,height,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(b'\0\1\2\3'))+chunk(b'IEND',b'')

class UploadTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)/'images'
  self.patch=patch.object(images,'UPLOADS',self.root);self.patch.start()
  app=web.Application();images.register(app);self.client=TestClient(TestServer(app));await self.client.start_server()
  self.headers={'Host':'localhost','Origin':'https://localhost','X-Terminal-Image':'1'}
 async def asyncTearDown(self):
  await self.client.close();self.patch.stop();self.tmp.cleanup()
 async def test_roundtrip_hash_dedup_and_private_file(self):
  data=png();r=await self.client.post('/lab-api/terminal-images',data=data,headers=self.headers);self.assertEqual(r.status,201)
  d=await r.json();p=Path(d['path']);self.assertEqual(p.read_bytes(),data);self.assertEqual(p.stat().st_mode&0o777,0o600)
  self.assertEqual(p.name,hashlib.sha256(data).hexdigest()+'.png')
  r=await self.client.post('/lab-api/terminal-images',data=data,headers=self.headers);self.assertEqual(r.status,201)
  self.assertEqual(len(list(self.root.glob('*.png'))),1)
 async def test_foreign_origin_missing_header_and_untrusted_host(self):
  for diff in [{'Origin':'https://foreign.example'},{'Origin':'null'},{'X-Terminal-Image':'0'},{'Host':'foreign.example','Origin':'https://foreign.example'}]:
   r=await self.client.post('/lab-api/terminal-images',data=png(),headers=dict(self.headers,**diff));self.assertIn(r.status,[403,421])
  self.assertFalse(self.root.exists())
 async def test_svg_and_html_rejected(self):
  for data in [b'<svg xmlns="http://www.w3.org/2000/svg"/>',b'<script>alert(1)</script>']:
   r=await self.client.post('/lab-api/terminal-images',data=data,headers=self.headers);self.assertEqual(r.status,400)
  self.assertFalse(self.root.exists())
 async def test_quota_rejects_without_deleting_existing_image(self):
  with patch.object(images,'MAX_COUNT',1):
   r=await self.client.post('/lab-api/terminal-images',data=png(),headers=self.headers);self.assertEqual(r.status,201)
   r=await self.client.post('/lab-api/terminal-images',data=png(3,3),headers=self.headers);self.assertEqual(r.status,400)
   self.assertEqual(len(list(self.root.glob('*.png'))),1)
 async def test_chunked_oversize_is_bounded(self):
  async def chunks():
   yield b'a'*16;yield b'b'*16
  with patch.object(images,'MAX_BYTES',24):
   r=await self.client.post('/lab-api/terminal-images',data=chunks(),headers=self.headers);self.assertEqual(r.status,413)
  self.assertFalse(self.root.exists())
 async def test_path_and_filename_ignored(self):
  r=await self.client.post('/lab-api/terminal-images?path=/tmp/escape&filename=../../escape',data=png(),headers=self.headers)
  self.assertEqual(r.status,201);self.assertEqual(Path((await r.json())['path']).parent,self.root)
 async def test_no_public_file_get_or_listing(self):
  self.assertEqual((await self.client.get('/lab-api/terminal-images')).status,405)
 async def test_dimensions_and_header_crc(self):
  r=await self.client.post('/lab-api/terminal-images',data=png(30000,1),headers=self.headers);self.assertEqual(r.status,400)
  data=bytearray(png());data[20]^=1
  r=await self.client.post('/lab-api/terminal-images',data=data,headers=self.headers);self.assertEqual(r.status,400)

if __name__=='__main__':unittest.main()

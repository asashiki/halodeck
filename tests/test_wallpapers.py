"""Image-library contracts exercise temporary directories and isolated HTTP only."""
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
import zlib

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import wallpapers as library
from panel_lock import config_lock


def png(color=0):
    def chunk(kind, data):
        return struct.pack('>I', len(data))+kind+data+struct.pack('>I', zlib.crc32(kind+data))
    return (b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0))+
            chunk(b'IDAT', zlib.compress(bytes([0, color, 0, 0])))+chunk(b'IEND', b''))


class Fixture:
    def setUp(self):
        folder = tempfile.TemporaryDirectory(); self.addCleanup(folder.cleanup)
        root = Path(folder.name); self.library = root/'config/wallpapers'
        for target, value in [('LIBRARY', self.library),
                              ('config_lock', lambda: config_lock(root/'config/homepage-v2', root/'history'))]:
            replacement = patch.object(library, target, value); replacement.start(); self.addCleanup(replacement.stop)


class StorageTests(Fixture, unittest.TestCase):
    def test_persistent_deduplicated_library_and_exact_removal(self):
        items = library.add(png(), '../../我的壁纸.png'); ident = items[0]['id']
        self.assertEqual(len(library.add(png(), 'duplicate')), 1)
        self.assertEqual(library.public(library.manifest()), items)
        self.assertTrue((self.library/(ident+'.png')).is_file())
        self.assertEqual(library.remove(ident), [])
        self.assertFalse((self.library/(ident+'.png')).exists())
        self.assertTrue((self.library/'index.json').exists())

    def test_invalid_active_content_quota_and_missing_manifest_fail_closed(self):
        for data in [b'<svg xmlns="http://www.w3.org/2000/svg"/>', b'<html>fake.jpg</html>', png()[:30]]:
            with self.assertRaises(ValueError): library.add(data, 'bad')
        library.add(png(), 'one')
        with patch.object(library, 'MAX_COUNT', 1):
            with self.assertRaises(ValueError): library.add(png(1), 'two')
        with self.assertRaises(web.HTTPNotFound): library.remove('../index.json')
        (self.library/'index.json').unlink()
        with self.assertRaises(ValueError): library.add(png(2), 'do not replace lost index')
        self.assertEqual(len(list(self.library.glob('*.png'))), 1)

    def test_jpeg_dimensions_and_large_dimensions_rejected(self):
        real = (Path(__file__).resolve().parents[1]/'tests/fixtures/sample.jpg').read_bytes()
        ext, mime, width, height = library.image_info(real)
        self.assertEqual((ext, mime), ('jpg', 'image/jpeg'))
        self.assertGreater(width*height, 1)
        with patch.object(library, 'MAX_PIXELS', 0):
            with self.assertRaises(ValueError): library.image_info(png())


class HttpTests(Fixture, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        app = web.Application(); library.register(app)
        self.client = TestClient(TestServer(app)); await self.client.start_server(); self.addAsyncCleanup(self.client.close)
        self.headers = {'Origin': str(self.client.make_url('/')).rstrip('/'),
                        'X-Lab-Wallpaper': '1', 'Content-Type': 'application/octet-stream',
                        'X-Lab-Wallpaper-Name': 'sample.png'}

    async def test_same_origin_upload_read_and_delete(self):
        denied = await self.client.post('/lab-api/wallpapers', data=png())
        self.assertEqual(denied.status, 403)
        for headers in [dict(self.headers, Origin='https://other.example'),
                        dict(self.headers, **{'Sec-Fetch-Site':'cross-site'}),
                        dict(self.headers, **{'X-Lab-Wallpaper':'wrong'})]:
            denied = await self.client.post('/lab-api/wallpapers', data=png(), headers=headers)
            self.assertEqual(denied.status, 403)
        response = await self.client.post('/lab-api/wallpapers', data=png(), headers=self.headers)
        self.assertEqual(response.status, 200, await response.text())
        item = (await response.json())['items'][0]
        response = await self.client.get(item['url'])
        self.assertEqual(response.content_type, 'image/png')
        self.assertEqual(response.headers['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(await response.read(), png())
        response = await self.client.delete('/lab-api/wallpapers/'+item['id'], headers=self.headers)
        self.assertEqual((await response.json())['items'], [])
        response = await self.client.get(item['url']); self.assertEqual(response.status, 404)

    async def test_invalid_image_and_stream_size_are_rejected(self):
        response = await self.client.post('/lab-api/wallpapers', data=b'<svg/>', headers=self.headers)
        self.assertEqual(response.status, 400)
        with patch.object(library, 'MAX_BYTES', 32):
            async def stream():
                yield b'x'*20
                yield b'y'*20
            response = await self.client.post('/lab-api/wallpapers', data=stream(), headers=self.headers)
            self.assertEqual(response.status, 413)


if __name__ == '__main__': unittest.main()

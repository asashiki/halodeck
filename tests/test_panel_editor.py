"""Editor contract checks use temporary files and isolated HTTP servers only."""
import asyncio
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import panel_editor as editor


class EditorFixture:
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='lab-editor-contract-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = self.root / 'config/homepage-v2'
        self.history = self.root / 'state/panel-editor'
        self.config.mkdir(parents=True)
        assets = self.root / 'config/assets'
        assets.mkdir()
        (assets / 'panel-editor.html').write_text('<!doctype html><title>Fixture editor</title>')
        self.template = {
            'id': 'app-terminal', 'href': 'https://lab.example/term/',
            'icon': '/lab-assets/terminal.svg', 'description': '继续会话',
            'siteMonitor': 'http://127.0.0.1:8100/lab-api/health/terminal',
            'widget': {'type': 'customapi', 'url': 'http://127.0.0.1:8100/fixed',
                       'key': 'TEST_ONLY_SERVER_FIELD'},
        }
        self.original = {
            'services.yaml': [{'工作': [{'终端': copy.deepcopy(self.template)}]}],
            'settings.yaml': {'title': '原首页', 'background': {'image': '/lab-assets/bg.jpg', 'opacity': 38},
                              'layout': {'工作': {'style': 'row', 'columns': 3}}, 'target': '_self'},
            'widgets.yaml': [{'greeting': {'text': '原首页'}}, {'customapi': {'url': 'http://127.0.0.1:8100/fixed'}}],
        }
        choice = {'id': 'app-terminal', 'name': '终端', **{
            key: self.template[key] for key in ('href', 'description', 'icon')}}
        catalog = {
            'services': [{'终端': self.template}], 'choices': [choice],
            'icons': ['/lab-assets/terminal.svg', '/lab-assets/web.svg'],
            'backgrounds': ['', '/lab-assets/bg.jpg'],
            'metrics': {'本机概览': [{'CPU': {'id': 'metric-cpu', 'widget': {'url': 'http://127.0.0.1:8100/cpu'}}}]},
            'recent': {'最近任务': [{'动态': {'id': 'activity', 'widget': {'url': 'http://127.0.0.1:8100/recent'}}}]},
        }
        (self.root / 'config/panel-catalog.json').write_text(json.dumps(catalog))
        for name, value in self.original.items():
            (self.config / name).write_text(json.dumps(value))
        for name, value in [('ROOT', self.root), ('CONFIG', self.config), ('HISTORY', self.history)]:
            replacement = patch.object(editor, name, value)
            replacement.start()
            self.addCleanup(replacement.stop)

    def payload(self, title='新首页'):
        current = editor.state()
        model = copy.deepcopy(current['model'])
        model['title'] = title
        return {'revision': current['revision'], 'model': model}

    def disk(self):
        return {name: json.loads((self.config / name).read_text()) for name in editor.FILES}


class PersistenceTests(EditorFixture, unittest.TestCase):
    def test_save_is_persistent_and_public_state_omits_technical_fields(self):
        payload = self.payload()
        payload['model']['groups'][0]['items'][0]['name'] = '继续终端'
        saved = editor.save(payload)
        self.assertEqual(self.disk()['widgets.yaml'][0]['greeting']['text'], '新首页')
        self.assertEqual(editor.state()['model'], saved['model'])
        self.assertEqual(editor.state()['revision'], saved['revision'])
        public = json.dumps(editor.state())
        self.assertNotIn('TEST_ONLY_SERVER_FIELD', public)
        self.assertNotIn('siteMonitor', public)
        self.assertFalse((self.history / 'pending.json').exists())
        self.assertEqual((self.config / 'services.yaml').stat().st_mode & 0o777, 0o600)

    def test_stale_revision_conflict_keeps_current_config(self):
        stale = self.payload('旧标签页')
        editor.save(self.payload('最新标签页'))
        current = self.disk()
        with self.assertRaises(web.HTTPConflict):
            editor.save(stale)
        self.assertEqual(self.disk(), current)

    def test_two_concurrent_editors_cannot_silently_overwrite(self):
        first, second = self.payload('并发 A'), self.payload('并发 B')
        barrier = threading.Barrier(2)
        def worker(payload):
            barrier.wait(timeout=5)
            try:
                editor.save(payload)
                return 'saved'
            except web.HTTPConflict:
                return 'conflict'
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(worker, value) for value in (first, second)]
            results = [future.result(timeout=10) for future in futures]
        self.assertCountEqual(results, ['saved', 'conflict'])
        self.assertIn(editor.state()['model']['title'], {'并发 A', '并发 B'})

    def test_undo_restores_all_previous_files(self):
        saved = editor.save(self.payload())
        editor.save({'revision': saved['revision']}, undo=True)
        self.assertEqual(self.disk(), self.original)

    def test_startup_recovery_repairs_partial_transaction_without_opening_editor(self):
        self.history.mkdir(parents=True)
        (self.history / 'pending.json').write_text(json.dumps(self.original))
        (self.config / 'services.yaml').write_text('[{"半次保存": []}]')
        editor.recover_pending()
        self.assertEqual(self.disk(), self.original)
        self.assertFalse((self.history / 'pending.json').exists())

    def test_write_failure_rolls_back_entire_bundle(self):
        payload = self.payload()
        real_atomic = editor.atomic
        failed = False
        def fail_once(path, value):
            nonlocal failed
            if path == self.config / 'settings.yaml' and not failed:
                failed = True
                raise OSError('injected write failure')
            return real_atomic(path, value)
        with patch.object(editor, 'atomic', side_effect=fail_once):
            with self.assertRaises(OSError):
                editor.save(payload)
        self.assertTrue(failed)
        self.assertEqual(self.disk(), self.original)
        self.assertFalse((self.history / 'pending.json').exists())

    def test_missing_expected_config_is_not_initialized(self):
        missing = self.config / 'services.yaml'
        missing.unlink()
        with self.assertRaises(FileNotFoundError):
            editor.state()
        self.assertFalse(missing.exists())
        self.assertEqual(json.loads((self.config / 'settings.yaml').read_text()), self.original['settings.yaml'])

    def test_normal_rebuild_preserves_owner_edits_and_preview_uses_same_config(self):
        generator_path = Path(__file__).resolve().parents[1] / 'scripts/make-homepage-config.py'
        spec = importlib.util.spec_from_file_location('panel_test_generator', generator_path)
        generator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(generator)
        saved = editor.save(self.payload('主人保存的标题'))
        before = self.disk()
        preview = self.root / 'preview'
        with patch.object(generator, 'ROOT', self.root):
            generator.build(self.config, 8100)
            self.assertEqual(self.disk(), before)
            generator.build(preview, 18100)
        preview_bundle = {name: json.loads((preview / name).read_text()) for name in editor.FILES}
        self.assertEqual(editor.display(preview_bundle), saved['model'])
        card = next(iter(preview_bundle['services.yaml'][0]['工作'][0].values()))
        self.assertEqual(card['siteMonitor'], 'http://127.0.0.1:18100/lab-api/health/terminal')
        self.assertEqual(self.disk(), before)

    def test_rebuild_with_missing_canonical_config_refuses_empty_initialization(self):
        generator_path = Path(__file__).resolve().parents[1] / 'scripts/make-homepage-config.py'
        spec = importlib.util.spec_from_file_location('panel_test_generator_missing', generator_path)
        generator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(generator)
        destination = self.root / 'new-preview'
        with patch.object(generator, 'ROOT', self.root / 'missing-root'):
            with self.assertRaises(RuntimeError):
                generator.build(destination, 18100)
        self.assertFalse(destination.exists())


class ConfigurationBoundaryTests(EditorFixture, unittest.TestCase):
    def test_untrusted_technical_fields_cannot_change_probes_or_widgets(self):
        model = self.payload()['model']
        model.update(settings={'auth': 'none'}, command='do-not-run', config_path='/outside')
        item = model['groups'][0]['items'][0]
        item.update(siteMonitor='http://attacker.invalid/', widget={'url': 'http://attacker.invalid/'},
                    command='do-not-run', secret_ref='/outside')
        result = editor.compile_config(model, self.original)
        card = next(iter(result['services.yaml'][0]['工作'][0].values()))
        self.assertEqual(card['siteMonitor'], self.template['siteMonitor'])
        self.assertEqual(card['widget'], self.template['widget'])
        self.assertNotIn('command', card)
        self.assertNotIn('secret_ref', card)
        self.assertNotIn('attacker.invalid', json.dumps(result))
        self.assertNotIn('auth', result['settings.yaml'])

    def test_changed_destination_does_not_inherit_registered_health(self):
        model = self.payload()['model']
        model['groups'][0]['items'][0]['href'] = 'https://example.org/other'
        result = editor.compile_config(model, self.original)
        card = next(iter(result['services.yaml'][0]['工作'][0].values()))
        self.assertEqual(card['href'], 'https://example.org/other')
        self.assertNotIn('siteMonitor', card)
        self.assertNotIn('widget', card)

    def test_dangerous_urls_and_credential_urls_are_rejected(self):
        for value in ['javascript:alert(1)', 'data:text/html,test', 'file:///etc/passwd',
                      '//example.org/', '/\\example.org/', 'https://example.org/a b',
                      'https://user:password@example.org/', 'https://example.org/?api_key=test',
                      'https://example.org/?access_token=test', 'https://example.org/\nnext']:
            with self.subTest(url=value):
                with self.assertRaises(ValueError):
                    editor.safe_url(value)
        self.assertEqual(editor.safe_url('/lab-edit/'), '/lab-edit/')
        self.assertEqual(editor.safe_url('https://example.org/path?q=search'), 'https://example.org/path?q=search')

    def test_external_assets_and_duplicate_ids_are_rejected(self):
        for field, value in [('icon', 'https://attacker.invalid/image.svg'), ('id', '../../outside')]:
            model = self.payload()['model']
            model['groups'][0]['items'][0][field] = value
            with self.subTest(field=field):
                with self.assertRaises(ValueError):
                    editor.compile_config(model, self.original)
        model = self.payload()['model']
        duplicate = copy.deepcopy(model['groups'][0]['items'][0])
        duplicate['name'] = '另一个名字'
        model['groups'][0]['items'].append(duplicate)
        with self.assertRaises(ValueError):
            editor.compile_config(model, self.original)


class HTTPBoundaryTests(EditorFixture, unittest.IsolatedAsyncioTestCase):
    headers = {'Host': 'localhost', 'Origin': 'http://localhost', 'X-Lab-Editor': '1',
               'Content-Type': 'application/json', 'Sec-Fetch-Site': 'same-origin'}

    async def asyncSetUp(self):
        app = web.Application()
        editor.register(app)
        self.client = TestClient(TestServer(app))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)

    async def test_fragmented_valid_json_saves_complete_request(self):
        content = json.dumps(self.payload('分段请求')).encode()
        async def fragments():
            yield content[:12]
            await asyncio.sleep(0.02)
            yield content[12:]
        response = await self.client.post('/lab-api/editor', headers=self.headers, data=fragments())
        self.assertEqual(response.status, 200, await response.text())
        self.assertEqual(editor.state()['model']['title'], '分段请求')
        self.assertFalse((await response.json())['reloaded'])  # No production HTTP client is installed.

    async def test_cross_site_and_missing_csrf_headers_cannot_write(self):
        cases = [({'Origin': 'https://attacker.invalid'}, 403),
                 ({'Origin': None}, 403), ({'X-Lab-Editor': None}, 403),
                 ({'Sec-Fetch-Site': 'cross-site'}, 403),
                 ({'Sec-Fetch-Site': 'same-site'}, 403),
                 ({'Content-Type': 'text/plain'}, 403),
                 ({'Host': 'attacker.invalid'}, 421)]
        before = self.disk()
        for updates, expected in cases:
            headers = {**self.headers, **updates}
            headers = {key: value for key, value in headers.items() if value is not None}
            with self.subTest(updates=updates):
                response = await self.client.post('/lab-api/editor', headers=headers, data=json.dumps(self.payload()))
                self.assertEqual(response.status, expected)
                self.assertEqual(self.disk(), before)

    async def test_chunked_oversize_request_is_rejected_without_writing(self):
        async def large_body():
            for _ in range(7):
                yield b' ' * 16000
        response = await self.client.post('/lab-api/editor', headers=self.headers, data=large_body())
        self.assertEqual(response.status, 413)
        self.assertEqual(self.disk(), self.original)

    async def test_stale_http_save_returns_conflict_and_preserves_state(self):
        stale = self.payload('旧请求')
        editor.save(self.payload('服务器最新'))
        response = await self.client.post('/lab-api/editor', headers=self.headers, json=stale)
        self.assertEqual(response.status, 409)
        self.assertEqual(editor.state()['model']['title'], '服务器最新')

    async def test_editor_html_has_csp_and_static_alias_is_unavailable(self):
        response = await self.client.get('/lab-edit/', headers={'Host': 'localhost'})
        self.assertEqual(response.status, 200)
        self.assertIn("frame-ancestors 'self'", response.headers['Content-Security-Policy'])
        import gateway
        app = web.Application()
        app.router.add_get('/lab-assets/{name}', gateway.assets)
        with patch.object(gateway, 'ROOT', self.root):
            async with TestClient(TestServer(app)) as client:
                alias = await client.get('/lab-assets/panel-editor.html')
                self.assertEqual(alias.status, 404)


if __name__ == '__main__':
    unittest.main()

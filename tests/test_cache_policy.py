"""Personal assets may be cached locally; protected live data may never be shared."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import proxy

class CachePolicyTests(unittest.TestCase):
    def test_only_build_assets_are_long_lived_and_private(self):
        for p in ['/_next/static/chunks/a-f15.js','/_next/static/css/a.css']:
            self.assertEqual(proxy.cache_policy(p,'GET',200),'private, max-age=31536000, immutable')
    def test_api_and_uploaded_active_content_never_cached(self):
        for p in ['/','/lab-api/status','/lab-api/backup','/api/auth/session','/workbench/files/api/raw/test.html','/_next/data/build/index.json']:
            self.assertEqual(proxy.cache_policy(p,'GET',200),'private, no-store')
    def test_errors_redirects_writes_do_not_gain_static_cache_policy(self):
        for method,status in [('POST',200),('GET',302),('GET',403),('GET',500)]:
            self.assertEqual(proxy.cache_policy('/_next/static/a.js',method,status),'private, no-store')
    def test_editable_theme_must_revalidate(self):
        for p in ['/api/config/custom.css','/api/config/custom.js']:
            self.assertEqual(proxy.cache_policy(p,'GET',200),'private, no-cache')

class ManifestTests(unittest.TestCase):
    def test_homepage_manifest_is_replaced_not_duplicated(self):
        import gateway
        body=b'<html><head><link rel="shortcut icon" href="/homepage.ico"><link rel="icon" type="image/png" sizes="32x32" href="/favicon-32x32.png?v=4"><link rel="manifest" href="/site.webmanifest?v=4"></head></html>'
        result=gateway.pwa_html(body,'text/html')
        self.assertEqual(result.count(b'rel="manifest"'),1)
        self.assertNotIn(b'/site.webmanifest',result)
        self.assertNotIn(b'/homepage.ico',result)
        self.assertNotIn(b'/favicon-32x32.png',result)
        self.assertIn(b'/lab-assets/halodeck-favicon.svg',result)
        self.assertIn(b'crossorigin="use-credentials"',result)

#!/usr/bin/env python3
"""Unit and integration tests for local system liveness recording and aggregation."""
from contextlib import closing
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import gateway
import liveness


def make_probe(
    wall_ts: float,
    mono_ts: float,
    *,
    boot_ts: float | None = None,
    boot_id: str = 'boot-111111111111',
    sandbox_boot_id: str = 'sb-aaaa',
    btime: int = 1794400000,
    pid: int = 1000,
    session_id: str = 'sess-1',
) -> liveness.SystemProbe:
    return liveness.SystemProbe(
        wall_ts=wall_ts,
        mono_ts=mono_ts,
        boot_ts=boot_ts if boot_ts is not None else mono_ts,
        boot_id=boot_id,
        sandbox_boot_id=sandbox_boot_id,
        btime=btime,
        pid=pid,
        session_id=session_id,
    )


class LivenessRecorderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.db_path = self.base / 'liveness.db'
        self.latest_path = self.base / 'latest.json'

    def test_continuous_heartbeats_coalesce_into_single_alive_span(self):
        rec = liveness.Recorder(self.db_path, self.latest_path, session_id='s1')
        t0 = __import__('time').time() - 60
        for i in range(10):
            res = rec.step(make_probe(t0 + i * 5.0, 100.0 + i * 5.0, session_id='s1'))
            self.assertEqual(res['transition'], 'initial' if i == 0 else 'alive')

        with closing(sqlite3.connect(self.db_path)) as conn:
            spans = conn.execute('SELECT kind, start_ts, end_ts, duration_sec, sample_count FROM liveness_spans').fetchall()
        self.assertEqual(len(spans), 1)
        self.assertEqual(spans[0][0], 'alive')
        self.assertAlmostEqual(spans[0][1], t0)
        self.assertAlmostEqual(spans[0][2], t0 + 45.0)
        self.assertAlmostEqual(spans[0][3], 45.0)
        self.assertEqual(spans[0][4], 10)

        tl = liveness.query_timeline(
            self.db_path,
            window='24h',
            now_ts=t0 + 47.0,
            current_probe=make_probe(t0 + 47.0, 147.0),
        )
        self.assertEqual(tl['current_state'], 'alive')
        self.assertAlmostEqual(tl['alive_seconds'], 47.0, places=2)
        self.assertAlmostEqual(tl['monitored_seconds'], 47.0, places=2)
        self.assertAlmostEqual(tl['observed_alive_ratio'], 100.0, places=2)

    def test_unobserved_gap_is_honest_and_does_not_claim_confirmed_freeze(self):
        rec = liveness.Recorder(self.db_path, self.latest_path, session_id='s1')
        t0 = __import__('time').time() - 60
        rec.step(make_probe(t0, 10.0, session_id='s1'))
        rec.step(make_probe(t0 + 5.0, 15.0, session_id='s1'))
        # 90s gap in both wall and monotonic clocks with identical boot_id & session_id
        res = rec.step(make_probe(t0 + 95.0, 105.0, session_id='s1'))
        self.assertEqual(res['transition'], 'unobserved_gap')
        rec.step(make_probe(t0 + 100.0, 110.0, session_id='s1'))

        tl = liveness.query_timeline(
            self.db_path,
            window='24h',
            now_ts=t0 + 102.0,
            current_probe=make_probe(t0 + 102.0, 112.0),
        )
        self.assertEqual(tl['gap_count'], 1)
        self.assertEqual(tl['restart_count'], 0)
        gap = next(s for s in tl['segments'] if s['kind'] == 'unobserved_gap')
        self.assertEqual(gap['label'], '未观测空档 / 疑似冻结')
        self.assertIn('不单独断定为确定冻结', gap['reason'])
        self.assertAlmostEqual(gap['duration_sec'], 90.0, places=2)
        self.assertLess(tl['observed_alive_ratio'], 20.0)

    def test_clock_jump_forward_and_backward_detected_without_false_freeze(self):
        rec = liveness.Recorder(self.db_path, self.latest_path, session_id='s1')
        t0 = __import__('time').time() - 60
        rec.step(make_probe(t0, 10.0, session_id='s1'))
        # Wall jumps +180s while monotonic advances only 5s
        res = rec.step(make_probe(t0 + 185.0, 15.0, session_id='s1'))
        self.assertEqual(res['transition'], 'clock_jump')
        rec.step(make_probe(t0 + 190.0, 20.0, session_id='s1'))
        # Backward clock jump (-60s) while monotonic advances 5s
        res2 = rec.step(make_probe(t0 + 130.0, 25.0, session_id='s1'))
        self.assertEqual(res2['transition'], 'clock_jump')

        with closing(sqlite3.connect(self.db_path)) as conn:
            kinds = [r[0] for r in conn.execute('SELECT kind FROM liveness_spans ORDER BY id').fetchall()]
        self.assertEqual(kinds.count('clock_jump'), 2)
        self.assertEqual(kinds.count('unobserved_gap'), 0)

    def test_system_restart_detected_on_boot_id_change(self):
        rec1 = liveness.Recorder(self.db_path, self.latest_path, session_id='before-reboot')
        t0 = __import__('time').time() - 60
        rec1.step(make_probe(t0, 500.0, boot_id='boot-aaa111', sandbox_boot_id='sb-1', btime=1794499500, pid=101, session_id='before-reboot'))
        rec1.step(make_probe(t0 + 5.0, 505.0, boot_id='boot-aaa111', sandbox_boot_id='sb-1', btime=1794499500, pid=101, session_id='before-reboot'))

        # Simulate host reboot + 360s copy-in + bashrc trigger delay
        rec2 = liveness.Recorder(self.db_path, self.latest_path, session_id='after-reboot')
        res = rec2.step(make_probe(t0 + 365.0, 345.0, boot_ts=345.0, boot_id='boot-bbb222', sandbox_boot_id='sb-2', btime=int(t0 + 20), pid=202, session_id='after-reboot'))
        self.assertEqual(res['transition'], 'restart')

        tl = liveness.query_timeline(
            self.db_path,
            window='24h',
            now_ts=t0 + 370.0,
            current_probe=make_probe(t0 + 370.0, 350.0, boot_id='boot-bbb222', sandbox_boot_id='sb-2'),
        )
        self.assertEqual(tl['restart_count'], 1)
        rst = next(s for s in tl['segments'] if s['kind'] == 'restart')
        self.assertEqual(rst['label'], '明确系统重启')
        self.assertEqual(rst['evidence']['prev_boot_id'], 'boot-aaa111')
        self.assertEqual(rst['evidence']['new_boot_id'], 'boot-bbb222')
        self.assertGreater(rst['evidence']['boot_to_logger_ready_sec'], 300.0)

    def test_logger_anomaly_on_unclean_restart_and_internal_error_recovery(self):
        rec1 = liveness.Recorder(self.db_path, self.latest_path, session_id='sess-old')
        t0 = __import__('time').time() - 60
        rec1.step(make_probe(t0, 10.0, boot_id='boot-same', pid=111, session_id='sess-old'))
        # Simulate logger crash and restart 3s later on the SAME boot_id with advancing CLOCK_MONOTONIC
        rec2 = liveness.Recorder(self.db_path, self.latest_path, session_id='sess-new')
        res = rec2.step(make_probe(t0 + 3.0, 13.0, boot_id='boot-same', pid=222, session_id='sess-new'))
        self.assertEqual(res['transition'], 'logger_anomaly')

        # Simulate internal sampling exception, then recover on next tick
        err_res = rec2.step(
            make_probe(t0 + 8.0, 18.0, boot_id='boot-same', pid=222, session_id='sess-new'),
            simulated_error='sqlite3.OperationalError: simulated transient lock',
        )
        self.assertEqual(err_res['transition'], 'logger_anomaly')
        ok_res = rec2.step(make_probe(t0 + 13.0, 23.0, boot_id='boot-same', pid=222, session_id='sess-new'))
        self.assertEqual(ok_res['transition'], 'alive')

        tl = liveness.query_timeline(
            self.db_path,
            window='24h',
            now_ts=t0 + 15.0,
            current_probe=make_probe(t0 + 15.0, 25.0, boot_id='boot-same'),
        )
        self.assertEqual(tl['logger_anomaly_count'], 2)
        self.assertEqual(tl['restart_count'], 0)
        self.assertEqual(tl['current_state'], 'alive')

    def test_pre_activation_history_stays_unmonitored_in_24h_and_7d(self):
        rec = liveness.Recorder(self.db_path, self.latest_path, session_id='s1')
        t0 = __import__('time').time() - 60
        rec.step(make_probe(t0, 10.0, session_id='s1'))
        rec.step(make_probe(t0 + 3600.0 - 5.0, 3605.0, session_id='s1'))

        # Overwrite to emulate continuous 5s sampling up to +1h via direct clean ticks:
        tl_no_db = liveness.query_timeline(self.base / 'missing.db', window='24h', now_ts=t0, current_probe=make_probe(t0, 10.0))
        self.assertEqual(tl_no_db['current_state'], 'unmonitored')
        self.assertIsNone(tl_no_db['observed_alive_ratio'])
        self.assertEqual([s['kind'] for s in tl_no_db['segments']], ['unmonitored'])

        for win, expected_sec in (('24h', 86400.0), ('7d', 604800.0)):
            tl = liveness.query_timeline(
                self.db_path,
                window=win,
                now_ts=t0 + 10.0,
                current_probe=make_probe(t0 + 10.0, 20.0),
            )
            self.assertEqual(tl['segments'][0]['kind'], 'unmonitored')
            self.assertAlmostEqual(tl['unmonitored_seconds'], expected_sec - 10.0, places=2)
            self.assertAlmostEqual(tl['monitored_seconds'], 10.0, places=2)
            self.assertGreater(tl['unmonitored_seconds'], expected_sec - 20.0)
            self.assertIsNotNone(tl['observed_alive_ratio'])

    def test_stale_logger_trailing_state_distinguishes_logger_anomaly_vs_restart(self):
        rec = liveness.Recorder(self.db_path, self.latest_path, session_id='s1')
        t0 = __import__('time').time() - 60
        rec.step(make_probe(t0, 10.0, boot_id='boot-1', session_id='s1'))

        # 60s later with SAME boot_id -> kernel alive, logger stopped -> logger_anomaly
        tl_logger = liveness.query_timeline(
            self.db_path,
            window='24h',
            now_ts=t0 + 60.0,
            current_probe=make_probe(t0 + 60.0, 70.0, boot_id='boot-1'),
        )
        self.assertEqual(tl_logger['current_state'], 'logger_anomaly')
        self.assertEqual(tl_logger['segments'][-1]['kind'], 'logger_anomaly')

        # 60s later with NEW boot_id -> system restarted -> restart
        tl_restart = liveness.query_timeline(
            self.db_path,
            window='24h',
            now_ts=t0 + 60.0,
            current_probe=make_probe(t0 + 60.0, 20.0, boot_id='boot-2'),
        )
        self.assertEqual(tl_restart['current_state'], 'restart')
        self.assertEqual(tl_restart['segments'][-1]['kind'], 'restart')


class LivenessHttpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = Path(self.tmp.name) / 'liveness.db'
        rec = liveness.Recorder(self.db_path, Path(self.tmp.name) / 'latest.json', session_id='http-test')
        t0 = __import__('time').time() - 60
        rec.step(make_probe(t0, 10.0, session_id='http-test'))
        rec.step(make_probe(t0 + 5.0, 15.0, session_id='http-test'))

        auth_patch=patch.object(gateway,'read_json',return_value={'username':'tester','password_sha256':__import__('hashlib').sha256(b'fixture').hexdigest()})
        auth_patch.start();self.addCleanup(auth_patch.stop)
        app = web.Application(middlewares=[gateway.headers])
        app.router.add_get('/lab-api/liveness', gateway.liveness_timeline)
        self.client = TestClient(TestServer(app),headers={'Authorization':'Basic dGVzdGVyOmZpeHR1cmU='})
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)

    async def test_gateway_liveness_endpoint_windows_and_validation(self):
        with patch.object(liveness, 'DB_PATH', self.db_path):
            r24 = await self.client.get('/lab-api/liveness?window=24h')
            self.assertEqual(r24.status, 200)
            p24 = await r24.json()
            self.assertEqual(p24['window'], '24h')
            self.assertEqual(p24['window_sec'], 86400)
            self.assertGreaterEqual(len(p24['segments']), 2)
            self.assertIn('no-store', r24.headers['Cache-Control'])

            r7d = await self.client.get('/lab-api/liveness?window=7d')
            self.assertEqual(r7d.status, 200)
            p7d = await r7d.json()
            self.assertEqual(p7d['window'], '7d')
            self.assertEqual(p7d['window_sec'], 604800)

            rbad = await self.client.get('/lab-api/liveness?window=99d')
            self.assertEqual(rbad.status, 400)


if __name__ == '__main__':
    unittest.main()

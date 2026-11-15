#!/usr/bin/env python3
"""Local real system liveness sampler and interval aggregation engine.

Samples this Ren box's kernel clocks, boot_id, and sandbox boot marker every 5s
into SQLite (WAL) independently of HTTP requests, agent loops, or remote VPS
probes. Classifies continuity and discontinuities strictly from observed facts.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from dataclasses import dataclass, asdict
import datetime as dt
import json
import os
from pathlib import Path
import signal
import sqlite3
import sys
import time
import uuid

from common import STATE

LIVENESS_DIR = STATE / 'liveness'
DB_PATH = LIVENESS_DIR / 'liveness.db'
LATEST_PATH = LIVENESS_DIR / 'latest.json'

SAMPLE_INTERVAL = 5.0
GAP_THRESHOLD = 15.0
CLOCK_JUMP_THRESHOLD = 10.0
TICK_RETENTION_SEC = 48 * 3600
SPAN_RETENTION_SEC = 90 * 24 * 3600
PRUNE_EVERY_TICKS = 120

WINDOWS = {
    '24h': 24 * 3600,
    '7d': 7 * 24 * 3600,
}

TZ_SHANGHAI = dt.timezone(dt.timedelta(hours=8))

KIND_LABELS = {
    'alive': '已观察存活',
    'unobserved_gap': '未观测空档 / 疑似冻结',
    'restart': '明确系统重启',
    'logger_anomaly': '记录进程异常',
    'clock_jump': '系统时钟跳变',
    'unmonitored': '启用前无数据',
}

HONESTY_NOTES = {
    'gap_policy': '心跳缺失 >15s 记为「未观测空档 / 疑似冻结」，因休眠或卡死期间本机无法执行代码自证，不单独断言为确定系统冻结',
    'restart_policy': '仅当 /proc/sys/kernel/random/boot_id、/tmp/sand-box-boot-id 或内核启动时间改变时标为「明确系统重启」',
    'logger_policy': '仅当同一 boot_id 下 CLOCK_MONOTONIC 持续递增而采样进程 PID/会话中断或内部报错恢复时标为「记录进程异常」',
    'history_policy': '首次启用前标为「启用前无数据」，绝不填绿、不伪造历史，也不计入已观测存活率分母',
    'autostart_limitation': '平台重建容器时会覆盖 /usr/local/bin/start-sand-box 并执行约 5-6 分钟 copy-in；现有回生 .bashrc 钩子需等待首个交互/宿主 bash 会话触发，不宣称零延迟自启',
}


@dataclass(frozen=True)
class SystemProbe:
    wall_ts: float
    mono_ts: float
    boot_ts: float
    boot_id: str
    sandbox_boot_id: str
    btime: int
    pid: int
    session_id: str = ''


def iso_shanghai(ts: float | None) -> str | None:
    if ts is None:
        return None
    return dt.datetime.fromtimestamp(float(ts), tz=TZ_SHANGHAI).isoformat(timespec='seconds')


def _read_text(path: str | Path) -> str:
    try:
        return Path(path).read_text(encoding='utf-8').strip()
    except OSError:
        return ''


def _read_kernel_btime(fallback_wall: float, boot_ts: float) -> int:
    try:
        for line in Path('/proc/stat').read_text(encoding='utf-8').splitlines():
            if line.startswith('btime '):
                return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        pass
    return int(round(fallback_wall - boot_ts))


def read_system_probe(session_id: str = '') -> SystemProbe:
    wall_ts = time.time()
    mono_ts = time.clock_gettime(time.CLOCK_MONOTONIC)
    boot_ts = time.clock_gettime(time.CLOCK_BOOTTIME)
    boot_id = _read_text('/proc/sys/kernel/random/boot_id') or 'unknown'
    sandbox_boot_id = _read_text('/tmp/sand-box-boot-id')
    btime = _read_kernel_btime(wall_ts, boot_ts)
    return SystemProbe(
        wall_ts=wall_ts,
        mono_ts=mono_ts,
        boot_ts=boot_ts,
        boot_id=boot_id,
        sandbox_boot_id=sandbox_boot_id,
        btime=btime,
        pid=os.getpid(),
        session_id=session_id,
    )


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA synchronous=NORMAL')
    conn.execute('PRAGMA busy_timeout=5000')
    conn.executescript(
        '''
        CREATE TABLE IF NOT EXISTS liveness_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS liveness_sessions (
            session_id TEXT PRIMARY KEY,
            pid INTEGER NOT NULL,
            boot_id TEXT NOT NULL,
            sandbox_boot_id TEXT NOT NULL,
            btime INTEGER NOT NULL,
            started_wall_ts REAL NOT NULL,
            started_mono_ts REAL NOT NULL,
            last_wall_ts REAL NOT NULL,
            last_mono_ts REAL NOT NULL,
            clean_exit INTEGER NOT NULL DEFAULT 0,
            error_count INTEGER NOT NULL DEFAULT 0,
            last_error TEXT
        );

        CREATE TABLE IF NOT EXISTS liveness_spans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,
            start_ts REAL NOT NULL,
            end_ts REAL NOT NULL,
            duration_sec REAL NOT NULL,
            boot_id TEXT NOT NULL,
            sandbox_boot_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            start_mono REAL NOT NULL,
            end_mono REAL NOT NULL,
            sample_count INTEGER NOT NULL DEFAULT 1,
            reason TEXT NOT NULL,
            evidence_json TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_spans_end_start
            ON liveness_spans(end_ts, start_ts);

        CREATE TABLE IF NOT EXISTS heartbeat_ticks (
            ts REAL PRIMARY KEY,
            mono_ts REAL NOT NULL,
            boot_ts REAL NOT NULL,
            boot_id TEXT NOT NULL,
            session_id TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_ticks_ts
            ON heartbeat_ticks(ts);
        '''
    )
    conn.execute(
        "INSERT OR IGNORE INTO liveness_meta(key, value) VALUES ('schema_version', '1')"
    )
    conn.commit()


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    tmp.chmod(0o600)
    os.replace(tmp, path)


def _get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute('SELECT value FROM liveness_meta WHERE key = ?', (key,)).fetchone()
    return row[0] if row else None


def _set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        'INSERT INTO liveness_meta(key, value) VALUES (?, ?) '
        'ON CONFLICT(key) DO UPDATE SET value = excluded.value',
        (key, value),
    )


def _insert_span(
    conn: sqlite3.Connection,
    *,
    kind: str,
    start_ts: float,
    end_ts: float,
    probe: SystemProbe,
    start_mono: float,
    end_mono: float,
    sample_count: int,
    reason: str,
    evidence: dict,
) -> int:
    duration = max(0.0, float(end_ts) - float(start_ts))
    cur = conn.execute(
        '''
        INSERT INTO liveness_spans (
            kind, start_ts, end_ts, duration_sec, boot_id, sandbox_boot_id,
            session_id, start_mono, end_mono, sample_count, reason, evidence_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''',
        (
            kind,
            float(start_ts),
            float(end_ts),
            duration,
            probe.boot_id,
            probe.sandbox_boot_id,
            probe.session_id,
            float(start_mono),
            float(end_mono),
            int(sample_count),
            reason,
            json.dumps(evidence, ensure_ascii=False),
        ),
    )
    return int(cur.lastrowid)


class Recorder:
    """Stateful local liveness recorder backed by SQLite."""

    def __init__(
        self,
        db_path: Path = DB_PATH,
        latest_path: Path | None = LATEST_PATH,
        sample_interval: float = SAMPLE_INTERVAL,
        gap_threshold: float = GAP_THRESHOLD,
        clock_jump_threshold: float = CLOCK_JUMP_THRESHOLD,
        session_id: str | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.latest_path = Path(latest_path) if latest_path is not None else None
        self.sample_interval = float(sample_interval)
        self.gap_threshold = float(gap_threshold)
        self.clock_jump_threshold = float(clock_jump_threshold)
        self.session_id = session_id or uuid.uuid4().hex[:12]
        self._tick_counter = 0

        self.db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with closing(sqlite3.connect(self.db_path, timeout=10.0)) as conn:
            init_db(conn)

    def _ensure_session(self, conn: sqlite3.Connection, probe: SystemProbe) -> None:
        conn.execute(
            '''
            INSERT INTO liveness_sessions (
                session_id, pid, boot_id, sandbox_boot_id, btime,
                started_wall_ts, started_mono_ts, last_wall_ts, last_mono_ts,
                clean_exit, error_count, last_error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, NULL)
            ON CONFLICT(session_id) DO UPDATE SET
                pid = excluded.pid,
                last_wall_ts = excluded.last_wall_ts,
                last_mono_ts = excluded.last_mono_ts
            ''',
            (
                probe.session_id,
                probe.pid,
                probe.boot_id,
                probe.sandbox_boot_id,
                probe.btime,
                probe.wall_ts,
                probe.mono_ts,
                probe.wall_ts,
                probe.mono_ts,
            ),
        )

    def _is_restart(self, prev: dict, probe: SystemProbe) -> bool:
        if probe.boot_id != prev.get('boot_id'):
            return True
        prev_sb = prev.get('sandbox_boot_id') or ''
        if probe.sandbox_boot_id and prev_sb and probe.sandbox_boot_id != prev_sb:
            return True
        prev_btime = int(prev.get('btime') or probe.btime)
        if abs(probe.btime - prev_btime) > 5 and probe.mono_ts < float(prev.get('mono_ts', probe.mono_ts)):
            return True
        return False

    def _open_alive_span(self, conn: sqlite3.Connection, probe: SystemProbe) -> None:
        _insert_span(
            conn,
            kind='alive',
            start_ts=probe.wall_ts,
            end_ts=probe.wall_ts,
            probe=probe,
            start_mono=probe.mono_ts,
            end_mono=probe.mono_ts,
            sample_count=1,
            reason='本机内核时钟与 boot_id 连续打点验证存活',
            evidence={
                'boot_id': probe.boot_id[:12],
                'sandbox_boot_id': probe.sandbox_boot_id,
                'pid': probe.pid,
                'session_id': probe.session_id,
            },
        )

    def _extend_or_open_alive(self, conn: sqlite3.Connection, probe: SystemProbe) -> None:
        latest = conn.execute(
            'SELECT id, kind, start_ts, boot_id, session_id, sample_count '
            'FROM liveness_spans ORDER BY id DESC LIMIT 1'
        ).fetchone()
        if (
            latest
            and latest[1] == 'alive'
            and latest[3] == probe.boot_id
            and latest[4] == probe.session_id
            and probe.wall_ts >= latest[2]
        ):
            duration = max(0.0, probe.wall_ts - float(latest[2]))
            conn.execute(
                '''
                UPDATE liveness_spans
                SET end_ts = ?, duration_sec = ?, end_mono = ?, sample_count = ?
                WHERE id = ?
                ''',
                (probe.wall_ts, duration, probe.mono_ts, int(latest[5]) + 1, latest[0]),
            )
        else:
            self._open_alive_span(conn, probe)

    def _maybe_prune(self, conn: sqlite3.Connection, now_wall: float) -> None:
        self._tick_counter += 1
        if self._tick_counter % PRUNE_EVERY_TICKS != 0:
            return
        conn.execute('DELETE FROM heartbeat_ticks WHERE ts < ?', (now_wall - TICK_RETENTION_SEC,))
        conn.execute('DELETE FROM liveness_spans WHERE end_ts < ?', (now_wall - SPAN_RETENTION_SEC,))
        conn.execute('DELETE FROM liveness_sessions WHERE last_wall_ts < ?', (now_wall - SPAN_RETENTION_SEC,))

    def _write_latest_file(self, probe: SystemProbe, transition: str, first_observed_at: float) -> None:
        if self.latest_path is None:
            return
        _atomic_write_json(
            self.latest_path,
            {
                'wall_ts': probe.wall_ts,
                'observed_at': iso_shanghai(probe.wall_ts),
                'mono_ts': probe.mono_ts,
                'boot_ts': probe.boot_ts,
                'boot_id': probe.boot_id,
                'sandbox_boot_id': probe.sandbox_boot_id,
                'btime': probe.btime,
                'pid': probe.pid,
                'session_id': probe.session_id,
                'transition': transition,
                'first_observed_at': first_observed_at,
                'sample_interval_sec': self.sample_interval,
                'gap_threshold_sec': self.gap_threshold,
            },
        )

    def step(self, probe: SystemProbe | None = None, simulated_error: str | None = None) -> dict:
        if probe is None:
            probe = read_system_probe(session_id=self.session_id)
        elif not probe.session_id:
            probe = SystemProbe(**{**asdict(probe), 'session_id': self.session_id})

        if simulated_error is not None:
            return self.record_internal_error(simulated_error, probe=probe)

        transition = 'alive'
        with closing(sqlite3.connect(self.db_path, timeout=10.0)) as conn:
            init_db(conn)
            first_raw = _get_meta(conn, 'first_observed_at')
            if first_raw is None:
                first_observed_at = probe.wall_ts
                _set_meta(conn, 'first_observed_at', repr(first_observed_at))
            else:
                first_observed_at = float(first_raw)

            prev_raw = _get_meta(conn, 'last_sample')
            prev = json.loads(prev_raw) if prev_raw else None

            self._ensure_session(conn, probe)

            if prev is None:
                transition = 'initial'
                self._open_alive_span(conn, probe)
            else:
                prev_wall = float(prev['wall_ts'])
                prev_mono = float(prev['mono_ts'])
                delta_wall = probe.wall_ts - prev_wall
                delta_mono = probe.mono_ts - prev_mono

                if self._is_restart(prev, probe):
                    transition = 'restart'
                    _insert_span(
                        conn,
                        kind='restart',
                        start_ts=prev_wall,
                        end_ts=max(prev_wall, probe.wall_ts),
                        probe=probe,
                        start_mono=prev_mono,
                        end_mono=probe.mono_ts,
                        sample_count=1,
                        reason='检测到内核 boot_id / 沙箱启动标识变更，确认为系统或容器重启（含平台启动与回生自启耗时）',
                        evidence={
                            'prev_boot_id': str(prev.get('boot_id', ''))[:12],
                            'new_boot_id': probe.boot_id[:12],
                            'prev_sandbox_boot_id': prev.get('sandbox_boot_id', ''),
                            'new_sandbox_boot_id': probe.sandbox_boot_id,
                            'kernel_btime': probe.btime,
                            'kernel_boot_iso': iso_shanghai(probe.btime),
                            'post_boot_uptime_sec': round(probe.boot_ts, 2),
                            'unobserved_before_boot_sec': round(max(0.0, probe.btime - prev_wall), 2),
                            'boot_to_logger_ready_sec': round(max(0.0, probe.wall_ts - probe.btime), 2),
                        },
                    )
                    self._open_alive_span(conn, probe)

                elif 0.0 <= delta_mono <= self.gap_threshold and (
                    abs(delta_wall - delta_mono) > self.clock_jump_threshold or delta_wall < -1.0
                ):
                    transition = 'clock_jump'
                    span_start = prev_wall
                    span_end = probe.wall_ts if probe.wall_ts >= prev_wall else prev_wall
                    _insert_span(
                        conn,
                        kind='clock_jump',
                        start_ts=span_start,
                        end_ts=span_end,
                        probe=probe,
                        start_mono=prev_mono,
                        end_mono=probe.mono_ts,
                        sample_count=1,
                        reason='单调时钟连续推进但系统墙钟发生跳变（NTP 校时或系统时钟调整），本机并未冻结',
                        evidence={
                            'prev_wall_ts': prev_wall,
                            'new_wall_ts': probe.wall_ts,
                            'delta_wall_sec': round(delta_wall, 3),
                            'delta_mono_sec': round(delta_mono, 3),
                            'clock_skew_sec': round(delta_wall - delta_mono, 3),
                            'boot_id': probe.boot_id[:12],
                        },
                    )
                    self._open_alive_span(conn, probe)

                elif probe.session_id != prev.get('session_id') and delta_mono >= 0.0:
                    prev_clean = bool(prev.get('clean_exit'))
                    if delta_wall > self.gap_threshold or not prev_clean:
                        transition = 'logger_anomaly'
                        reason = (
                            '记录进程收到停止信号并重启恢复，同一 boot_id 下单调时钟连续递增'
                            if prev_clean
                            else '同一 boot_id 下内核单调时钟持续递增，记录进程异常中断后自恢复（非整机重启）'
                        )
                        _insert_span(
                            conn,
                            kind='logger_anomaly',
                            start_ts=prev_wall,
                            end_ts=max(prev_wall, probe.wall_ts),
                            probe=probe,
                            start_mono=prev_mono,
                            end_mono=probe.mono_ts,
                            sample_count=1,
                            reason=reason,
                            evidence={
                                'prev_pid': prev.get('pid'),
                                'new_pid': probe.pid,
                                'prev_session': prev.get('session_id'),
                                'new_session': probe.session_id,
                                'clean_exit': prev_clean,
                                'delta_wall_sec': round(delta_wall, 2),
                                'delta_mono_sec': round(delta_mono, 2),
                                'boot_id': probe.boot_id[:12],
                            },
                        )
                        self._open_alive_span(conn, probe)
                    else:
                        transition = 'alive'
                        # Quick graceful session rollover within threshold: keep continuous alive span
                        conn.execute(
                            'UPDATE liveness_spans SET session_id = ?, end_ts = ?, end_mono = ?, '
                            'duration_sec = MAX(0.0, ? - start_ts), sample_count = sample_count + 1 '
                            'WHERE id = (SELECT id FROM liveness_spans ORDER BY id DESC LIMIT 1) '
                            'AND kind = "alive" AND boot_id = ?',
                            (probe.session_id, probe.wall_ts, probe.mono_ts, probe.wall_ts, probe.boot_id),
                        )
                        if conn.total_changes == 0:
                            self._open_alive_span(conn, probe)

                elif delta_wall > self.gap_threshold or delta_mono > self.gap_threshold:
                    transition = 'unobserved_gap'
                    _insert_span(
                        conn,
                        kind='unobserved_gap',
                        start_ts=prev_wall,
                        end_ts=max(prev_wall, probe.wall_ts),
                        probe=probe,
                        start_mono=prev_mono,
                        end_mono=probe.mono_ts,
                        sample_count=1,
                        reason='连续两次打点间隔超过 15 秒阈值，存在未观测空档；可能为宿主机挂起/疑似冻结或高负载阻塞，仅凭心跳缺失不单独断定为确定冻结',
                        evidence={
                            'delta_wall_sec': round(delta_wall, 2),
                            'delta_mono_sec': round(delta_mono, 2),
                            'gap_threshold_sec': self.gap_threshold,
                            'boot_id': probe.boot_id[:12],
                            'pid': probe.pid,
                        },
                    )
                    self._open_alive_span(conn, probe)

                else:
                    transition = 'alive'
                    self._extend_or_open_alive(conn, probe)

            conn.execute(
                'INSERT OR REPLACE INTO heartbeat_ticks(ts, mono_ts, boot_ts, boot_id, session_id) '
                'VALUES (?, ?, ?, ?, ?)',
                (probe.wall_ts, probe.mono_ts, probe.boot_ts, probe.boot_id, probe.session_id),
            )
            last_payload = {
                'wall_ts': probe.wall_ts,
                'mono_ts': probe.mono_ts,
                'boot_ts': probe.boot_ts,
                'boot_id': probe.boot_id,
                'sandbox_boot_id': probe.sandbox_boot_id,
                'btime': probe.btime,
                'pid': probe.pid,
                'session_id': probe.session_id,
                'clean_exit': False,
            }
            _set_meta(conn, 'last_sample', json.dumps(last_payload, ensure_ascii=False))
            self._maybe_prune(conn, probe.wall_ts)
            conn.commit()

        self._write_latest_file(probe, transition, first_observed_at)
        return {'transition': transition, 'first_observed_at': first_observed_at, 'probe': asdict(probe)}

    def record_internal_error(self, error_msg: str, probe: SystemProbe | None = None) -> dict:
        if probe is None:
            probe = read_system_probe(session_id=self.session_id)
        with closing(sqlite3.connect(self.db_path, timeout=10.0)) as conn:
            init_db(conn)
            first_raw = _get_meta(conn, 'first_observed_at')
            first_observed_at = float(first_raw) if first_raw is not None else probe.wall_ts
            if first_raw is None:
                _set_meta(conn, 'first_observed_at', repr(first_observed_at))
            prev_raw = _get_meta(conn, 'last_sample')
            prev = json.loads(prev_raw) if prev_raw else None
            start_ts = float(prev['wall_ts']) if prev else probe.wall_ts
            start_mono = float(prev['mono_ts']) if prev else probe.mono_ts
            self._ensure_session(conn, probe)
            conn.execute(
                'UPDATE liveness_sessions SET error_count = error_count + 1, last_error = ? '
                'WHERE session_id = ?',
                (str(error_msg)[:500], probe.session_id),
            )
            _insert_span(
                conn,
                kind='logger_anomaly',
                start_ts=start_ts,
                end_ts=max(start_ts, probe.wall_ts),
                probe=probe,
                start_mono=start_mono,
                end_mono=probe.mono_ts,
                sample_count=1,
                reason='记录进程内部采样异常并已自动恢复，同一 boot_id 下内核运行未中断',
                evidence={
                    'error': str(error_msg)[:300],
                    'boot_id': probe.boot_id[:12],
                    'pid': probe.pid,
                    'session_id': probe.session_id,
                    'delta_mono_sec': round(probe.mono_ts - start_mono, 2),
                },
            )
            self._open_alive_span(conn, probe)
            last_payload = {
                'wall_ts': probe.wall_ts,
                'mono_ts': probe.mono_ts,
                'boot_ts': probe.boot_ts,
                'boot_id': probe.boot_id,
                'sandbox_boot_id': probe.sandbox_boot_id,
                'btime': probe.btime,
                'pid': probe.pid,
                'session_id': probe.session_id,
                'clean_exit': False,
            }
            _set_meta(conn, 'last_sample', json.dumps(last_payload, ensure_ascii=False))
            conn.commit()
        self._write_latest_file(probe, 'logger_anomaly', first_observed_at)
        return {'transition': 'logger_anomaly', 'error': str(error_msg), 'probe': asdict(probe)}

    def mark_clean_exit(self) -> None:
        if not self.db_path.is_file():
            return
        try:
            with closing(sqlite3.connect(self.db_path, timeout=5.0)) as conn:
                conn.execute(
                    'UPDATE liveness_sessions SET clean_exit = 1 WHERE session_id = ?',
                    (self.session_id,),
                )
                raw = _get_meta(conn, 'last_sample')
                if raw:
                    last = json.loads(raw)
                    if last.get('session_id') == self.session_id:
                        last['clean_exit'] = True
                        _set_meta(conn, 'last_sample', json.dumps(last, ensure_ascii=False))
                conn.commit()
        except sqlite3.Error:
            pass


def _build_segment(
    *,
    kind: str,
    start_ts: float,
    end_ts: float,
    window_start: float,
    window_sec: float,
    boot_id: str,
    sample_count: int,
    reason: str,
    evidence: dict,
) -> dict:
    dur = max(0.0, end_ts - start_ts)
    left_pct = round(max(0.0, (start_ts - window_start) / window_sec * 100.0), 4)
    width_pct = round(max(0.0, dur / window_sec * 100.0), 4)
    return {
        'kind': kind,
        'label': KIND_LABELS.get(kind, kind),
        'start_ts': round(start_ts, 3),
        'end_ts': round(end_ts, 3),
        'duration_sec': round(dur, 2),
        'start_iso': iso_shanghai(start_ts),
        'end_iso': iso_shanghai(end_ts),
        'left_pct': left_pct,
        'width_pct': width_pct,
        'boot_id': str(boot_id)[:12],
        'sample_count': int(sample_count),
        'reason': reason,
        'evidence': evidence,
    }


def _merge_adjacent_segments(segments: list[dict]) -> list[dict]:
    if not segments:
        return []
    merged: list[dict] = []
    for seg in segments:
        if (
            merged
            and seg['kind'] == 'alive'
            and merged[-1]['kind'] == 'alive'
            and abs(seg['start_ts'] - merged[-1]['end_ts']) <= GAP_THRESHOLD
            and seg['boot_id'] == merged[-1]['boot_id']
        ):
            prev = merged[-1]
            prev['end_ts'] = max(prev['end_ts'], seg['end_ts'])
            prev['end_iso'] = iso_shanghai(prev['end_ts'])
            prev['duration_sec'] = round(max(0.0, prev['end_ts'] - prev['start_ts']), 2)
            prev['sample_count'] += seg['sample_count']
        else:
            merged.append(seg)
    return merged


def query_timeline(
    db_path: Path = DB_PATH,
    window: str = '24h',
    now_ts: float | None = None,
    current_probe: SystemProbe | None = None,
) -> dict:
    if window not in WINDOWS:
        raise ValueError(f'Unsupported window: {window}')
    window_sec = float(WINDOWS[window])
    now_wall = float(now_ts if now_ts is not None else time.time())
    window_start = now_wall - window_sec
    probe = current_probe or read_system_probe()

    db_file = Path(db_path)
    first_observed_at: float | None = None
    last_sample: dict | None = None
    raw_spans: list[tuple] = []

    if db_file.is_file() and db_file.stat().st_size > 0:
        uri = db_file.as_uri() + '?mode=ro'
        with closing(sqlite3.connect(uri, uri=True, timeout=5.0)) as conn:
            conn.execute('PRAGMA busy_timeout=5000')
            first_raw = _get_meta(conn, 'first_observed_at')
            if first_raw is not None:
                first_observed_at = float(first_raw)
            last_raw = _get_meta(conn, 'last_sample')
            if last_raw is not None:
                last_sample = json.loads(last_raw)
            if first_observed_at is not None:
                raw_spans = conn.execute(
                    '''
                    SELECT id, kind, start_ts, end_ts, boot_id, sandbox_boot_id,
                           session_id, sample_count, reason, evidence_json
                    FROM liveness_spans
                    WHERE end_ts >= ? AND start_ts <= ?
                    ORDER BY start_ts ASC, id ASC
                    ''',
                    (max(window_start, first_observed_at), now_wall),
                ).fetchall()

    if first_observed_at is None or last_sample is None:
        seg = _build_segment(
            kind='unmonitored',
            start_ts=window_start,
            end_ts=now_wall,
            window_start=window_start,
            window_sec=window_sec,
            boot_id=probe.boot_id,
            sample_count=0,
            reason='存活打点服务尚未首次启用，不填绿也不伪造历史记录',
            evidence={'boot_id': probe.boot_id[:12]},
        )
        return {
            'window': window,
            'window_sec': int(window_sec),
            'window_start': round(window_start, 3),
            'window_start_iso': iso_shanghai(window_start),
            'now_ts': round(now_wall, 3),
            'now_iso': iso_shanghai(now_wall),
            'current_state': 'unmonitored',
            'current_state_label': KIND_LABELS['unmonitored'],
            'first_observed_at': None,
            'first_observed_iso': None,
            'last_tick_at': None,
            'last_tick_iso': None,
            'last_tick_age_sec': None,
            'monitored_seconds': 0.0,
            'unmonitored_seconds': round(window_sec, 2),
            'alive_seconds': 0.0,
            'unobserved_gap_seconds': 0.0,
            'restart_seconds': 0.0,
            'logger_anomaly_seconds': 0.0,
            'clock_jump_seconds': 0.0,
            'observed_alive_ratio': None,
            'gap_count': 0,
            'restart_count': 0,
            'logger_anomaly_count': 0,
            'clock_jump_count': 0,
            'sample_interval_sec': SAMPLE_INTERVAL,
            'gap_threshold_sec': GAP_THRESHOLD,
            'clock_jump_threshold_sec': CLOCK_JUMP_THRESHOLD,
            'current_boot_id': probe.boot_id[:12],
            'sandbox_boot_id': probe.sandbox_boot_id,
            'kernel_btime': probe.btime,
            'kernel_boot_iso': iso_shanghai(probe.btime),
            'uptime_sec': round(probe.boot_ts, 1),
            'honesty_notes': HONESTY_NOTES,
            'segments': [seg],
        }

    segments: list[dict] = []
    cursor = window_start

    # Pre-activation window: strictly marked unmonitored, never filled green.
    if window_start < first_observed_at:
        unmonitored_end = min(now_wall, first_observed_at)
        segments.append(
            _build_segment(
                kind='unmonitored',
                start_ts=window_start,
                end_ts=unmonitored_end,
                window_start=window_start,
                window_sec=window_sec,
                boot_id='',
                sample_count=0,
                reason='本机存活打点服务于首次启用后才开始记录；启用前时间段保持空白，不填绿也不伪造历史',
                evidence={'first_observed_iso': iso_shanghai(first_observed_at)},
            )
        )
        cursor = unmonitored_end

    # Recorded spans within [cursor, now_wall].
    for row in raw_spans:
        _, kind, s_ts, e_ts, s_boot, _, _, count, reason, ev_json = row
        ev = json.loads(ev_json) if ev_json else {}
        s_clip = max(cursor, float(s_ts))
        e_clip = min(now_wall, max(s_clip, float(e_ts)))
        if float(e_ts) < cursor:
            continue

        if s_clip - cursor > GAP_THRESHOLD:
            segments.append(
                _build_segment(
                    kind='unobserved_gap',
                    start_ts=cursor,
                    end_ts=s_clip,
                    window_start=window_start,
                    window_sec=window_sec,
                    boot_id=s_boot,
                    sample_count=0,
                    reason='记录区间之间存在超过 15 秒的未观测空档（不单独断言为确定系统冻结）',
                    evidence={'gap_sec': round(s_clip - cursor, 2), 'boot_id': str(s_boot)[:12]},
                )
            )
        elif s_clip > cursor and segments and segments[-1]['kind'] == 'alive':
            # Continuous <= GAP_THRESHOLD bridge belongs to observed alive.
            prev = segments[-1]
            prev['end_ts'] = round(s_clip, 3)
            prev['end_iso'] = iso_shanghai(s_clip)
            prev['duration_sec'] = round(max(0.0, s_clip - prev['start_ts']), 2)
        elif s_clip > cursor and kind == 'alive':
            s_clip = cursor

        segments.append(
            _build_segment(
                kind=kind,
                start_ts=s_clip,
                end_ts=e_clip,
                window_start=window_start,
                window_sec=window_sec,
                boot_id=s_boot,
                sample_count=count,
                reason=reason,
                evidence=ev,
            )
        )
        cursor = max(cursor, e_clip)

    # Trailing region up to now_wall.
    last_wall = float(last_sample['wall_ts'])
    last_age = max(0.0, now_wall - last_wall)
    last_boot = str(last_sample.get('boot_id', ''))

    if last_age <= GAP_THRESHOLD and now_wall > cursor:
        if segments and segments[-1]['kind'] == 'alive' and segments[-1]['boot_id'] == last_boot[:12]:
            prev = segments[-1]
            prev['end_ts'] = round(now_wall, 3)
            prev['end_iso'] = iso_shanghai(now_wall)
            prev['duration_sec'] = round(max(0.0, now_wall - prev['start_ts']), 2)
        else:
            segments.append(
                _build_segment(
                    kind='alive',
                    start_ts=cursor,
                    end_ts=now_wall,
                    window_start=window_start,
                    window_sec=window_sec,
                    boot_id=last_boot,
                    sample_count=1,
                    reason='最近 15 秒内存在有效心跳，当前持续存活',
                    evidence={'boot_id': last_boot[:12], 'last_tick_age_sec': round(last_age, 2)},
                )
            )
        current_state = 'alive'
    elif last_age > GAP_THRESHOLD and now_wall > cursor:
        if probe.boot_id != last_boot:
            current_state = 'restart'
            segments.append(
                _build_segment(
                    kind='restart',
                    start_ts=cursor,
                    end_ts=now_wall,
                    window_start=window_start,
                    window_sec=window_sec,
                    boot_id=probe.boot_id,
                    sample_count=0,
                    reason='检测到系统 boot_id 已变更，采样进程尚未完成重启后首次打点',
                    evidence={
                        'prev_boot_id': last_boot[:12],
                        'new_boot_id': probe.boot_id[:12],
                        'kernel_btime': probe.btime,
                        'uptime_sec': round(probe.boot_ts, 2),
                    },
                )
            )
        else:
            current_state = 'logger_anomaly'
            segments.append(
                _build_segment(
                    kind='logger_anomaly',
                    start_ts=cursor,
                    end_ts=now_wall,
                    window_start=window_start,
                    window_sec=window_sec,
                    boot_id=probe.boot_id,
                    sample_count=0,
                    reason='当前系统在线且同一 boot_id 单调时钟正常推进，但采样进程超过 15 秒未写入新心跳（记录进程异常或暂停）',
                    evidence={
                        'boot_id': probe.boot_id[:12],
                        'last_tick_age_sec': round(last_age, 2),
                        'last_logger_pid': last_sample.get('pid'),
                    },
                )
            )
    else:
        current_state = segments[-1]['kind'] if segments else 'alive'

    segments = _merge_adjacent_segments(segments)

    # Recompute exact left_pct / width_pct after merging.
    totals = {k: 0.0 for k in KIND_LABELS}
    counts = {k: 0 for k in KIND_LABELS}
    for seg in segments:
        dur = max(0.0, float(seg['end_ts']) - float(seg['start_ts']))
        seg['duration_sec'] = round(dur, 2)
        seg['left_pct'] = round(max(0.0, (seg['start_ts'] - window_start) / window_sec * 100.0), 4)
        seg['width_pct'] = round(max(0.0, dur / window_sec * 100.0), 4)
        totals[seg['kind']] = totals.get(seg['kind'], 0.0) + dur
        counts[seg['kind']] = counts.get(seg['kind'], 0) + 1

    monitored_seconds = max(0.0, now_wall - max(window_start, first_observed_at))
    observed_alive_ratio = (
        round(min(100.0, max(0.0, totals['alive'] / monitored_seconds * 100.0)), 2)
        if monitored_seconds > 0.0
        else None
    )

    return {
        'window': window,
        'window_sec': int(window_sec),
        'window_start': round(window_start, 3),
        'window_start_iso': iso_shanghai(window_start),
        'now_ts': round(now_wall, 3),
        'now_iso': iso_shanghai(now_wall),
        'current_state': current_state,
        'current_state_label': KIND_LABELS.get(current_state, current_state),
        'first_observed_at': round(first_observed_at, 3),
        'first_observed_iso': iso_shanghai(first_observed_at),
        'last_tick_at': round(last_wall, 3),
        'last_tick_iso': iso_shanghai(last_wall),
        'last_tick_age_sec': round(last_age, 2),
        'monitored_seconds': round(monitored_seconds, 2),
        'unmonitored_seconds': round(totals['unmonitored'], 2),
        'alive_seconds': round(totals['alive'], 2),
        'unobserved_gap_seconds': round(totals['unobserved_gap'], 2),
        'restart_seconds': round(totals['restart'], 2),
        'logger_anomaly_seconds': round(totals['logger_anomaly'], 2),
        'clock_jump_seconds': round(totals['clock_jump'], 2),
        'observed_alive_ratio': observed_alive_ratio,
        'gap_count': counts['unobserved_gap'],
        'restart_count': counts['restart'],
        'logger_anomaly_count': counts['logger_anomaly'],
        'clock_jump_count': counts['clock_jump'],
        'sample_interval_sec': SAMPLE_INTERVAL,
        'gap_threshold_sec': GAP_THRESHOLD,
        'clock_jump_threshold_sec': CLOCK_JUMP_THRESHOLD,
        'current_boot_id': probe.boot_id[:12],
        'sandbox_boot_id': probe.sandbox_boot_id,
        'kernel_btime': probe.btime,
        'kernel_boot_iso': iso_shanghai(probe.btime),
        'uptime_sec': round(probe.boot_ts, 1),
        'honesty_notes': HONESTY_NOTES,
        'segments': segments,
    }


def run_daemon(sample_interval: float = SAMPLE_INTERVAL) -> int:
    recorder = Recorder(sample_interval=sample_interval)
    stop_requested = False

    def handle_signal(signum, frame):
        nonlocal stop_requested
        stop_requested = True

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    while not stop_requested:
        started = time.monotonic()
        try:
            recorder.step()
        except Exception as exc:
            print(f'liveness step error: {type(exc).__name__}: {exc}', file=sys.stderr, flush=True)
            try:
                recorder.record_internal_error(f'{type(exc).__name__}: {exc}')
            except Exception as inner:
                print(f'liveness error recording failed: {inner}', file=sys.stderr, flush=True)

        deadline = started + sample_interval
        while not stop_requested and time.monotonic() < deadline:
            time.sleep(min(0.25, max(0.0, deadline - time.monotonic())))

    recorder.mark_clean_exit()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description='Local real system liveness sampler and timeline query.')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--daemon', action='store_true', help='Run continuous 5s heartbeat daemon')
    group.add_argument('--once', action='store_true', help='Record a single sample tick and exit')
    group.add_argument('--query', choices=['24h', '7d'], help='Query aggregated timeline JSON')
    args = parser.parse_args()

    if args.daemon:
        return run_daemon()
    if args.once:
        result = Recorder().step()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    payload = query_timeline(DB_PATH, window=args.query)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())

"""Fail closed before starting stateful applications or taking a backup."""
import json
import sqlite3
import subprocess
import sys
from pathlib import Path
from common import ROOT, now, read_json

def inspect(manifest=None, base=None):
    manifest = manifest or read_json(ROOT/'config/expected-state.json')
    if not manifest:
        return {'state': 'WAITING_FOR_WORKSPACE', 'checked_at': now(), 'problems': ['预期数据清单尚未恢复']}
    problems = []
    for item in manifest['required']:
        path = (Path(base)/item['path'].lstrip('/')) if base else Path(item['path'])
        try:
            if not path.is_file() or path.stat().st_size < item.get('min_bytes', 1):
                problems.append(item['label'] + '缺失或为空')
                continue
            kind = item.get('kind')
            if kind == 'sqlite':
                with path.open('rb') as f:
                    if f.read(16) != b'SQLite format 3\x00':
                        problems.append(item['label'] + '数据库格式不符')
                # No database opening here: an inspection must never create a database.
            elif kind == 'bolt':
                with path.open('rb') as f:
                    f.seek(16)
                    if f.read(8) != bytes.fromhex('edda0ced02000000'): problems.append(item['label'] + '数据库格式不符')
            elif kind == 'json':
                data = json.loads(path.read_text())
                if not data:
                    problems.append(item['label'] + '内容为空')
            elif kind == 'marker' and path.read_text().strip() != item['value']:
                problems.append(item['label'] + '标记不匹配')
        except (OSError, ValueError):
            problems.append(item['label'] + '无法核验')
    return {'state': 'WAITING_FOR_WORKSPACE' if problems else 'READY', 'checked_at': now(),
            'problems': problems, 'checked_files': len(manifest['required']),
            'platform_cold_recovery': 'NOT_TESTED', 'dual_active': 'UNKNOWN'}

if __name__ == '__main__':
    result = inspect()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result['state'] == 'READY' else 75)

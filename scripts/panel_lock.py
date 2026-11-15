"""Stdlib-only Homepage transaction lock, shared by editor and backup export."""
import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from common import ROOT, STATE

FILES = ('services.yaml', 'settings.yaml', 'widgets.yaml')


def atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix='.panel-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name): os.unlink(name)


@contextmanager
def config_lock(config=None, history=None):
    config = config if config is not None else ROOT / 'config/homepage-v2'
    history = history if history is not None else STATE / 'panel-editor'
    history.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (history / 'lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        pending = history / 'pending.json'
        if pending.exists():
            old = json.loads(pending.read_text())
            for name in FILES: atomic(config / name, old[name])
            pending.unlink()
        yield

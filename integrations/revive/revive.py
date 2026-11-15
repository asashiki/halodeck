#!/usr/bin/env python3
"""回生 (Revive): Declarative service registry & serial idempotent boot supervisor."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

HOME_DIR = Path(os.environ.get("REVIVE_HOME", str(Path.home())))
STATE = Path(os.environ.get("REVIVE_STATE", str(HOME_DIR / ".local/share/revive")))
RUNTIME = STATE / "runtime"
ENV = dict(
    os.environ,
    HOME=str(HOME_DIR),
    PATH=f"{HOME_DIR / '.local/bin'}:/usr/local/bin:/usr/bin:/bin",
)


def read_registry() -> dict:
    path = STATE / "registry.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def validate(registry: dict) -> None:
    """Validate service specs, argv arrays, and check dependency DAG for cycles."""
    for name, spec in registry.items():
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", name):
            raise ValueError("Invalid service name")
        for key in ("start", "check", "prepare"):
            value = spec.get(key)
            if key in ("start", "check") and not value:
                raise ValueError("start/check required: " + name)
            if value is not None and (
                not isinstance(value, list)
                or not value
                or not all(isinstance(x, str) for x in value)
            ):
                raise ValueError("Commands must be argv arrays: " + name)
        if not isinstance(spec.get("depends", []), list):
            raise ValueError("depends must be an array")

    def visit(name: str, active: set[str]) -> None:
        if name in active:
            raise ValueError("Dependency cycle")
        if name not in registry:
            raise ValueError("Unknown dependency: " + name)
        for dep in registry[name].get("depends", []):
            visit(dep, active | {name})

    for name in registry:
        visit(name, set())


def run(command: list[str], timeout: int = 60, output=None) -> int:
    return subprocess.run(
        command,
        env=ENV,
        stdin=subprocess.DEVNULL,
        stdout=output,
        stderr=subprocess.STDOUT if output else subprocess.DEVNULL,
        timeout=timeout,
        check=False,
    ).returncode


def healthy(spec: dict) -> bool:
    try:
        return run(spec["check"], timeout=8, output=subprocess.DEVNULL) == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def start_services(registry: dict, names: list[str]) -> bool:
    """Execute prepare -> depends -> check -> idempotent start -> wait loop."""
    done: set[str] = set()
    failed: set[str] = set()

    def start(name: str) -> bool:
        if name in done:
            return name not in failed
        done.add(name)
        spec = registry[name]
        try:
            with (STATE / "logs" / (name + ".log")).open("a", encoding="utf-8") as log:
                # Always run asset prepare first: a live process does not guarantee disk assets remain intact.
                if spec.get("prepare") and run(spec["prepare"], output=log) != 0:
                    raise RuntimeError("prepare failed")
                for dep in spec.get("depends", []):
                    if not start(dep):
                        raise RuntimeError("dependency failed: " + dep)
                if not healthy(spec):
                    if spec.get("background"):
                        subprocess.Popen(
                            spec["start"],
                            env=ENV,
                            cwd=spec.get("cwd", str(HOME_DIR)),
                            stdin=subprocess.DEVNULL,
                            stdout=log,
                            stderr=subprocess.STDOUT,
                            start_new_session=True,
                        )
                    elif run(spec["start"], timeout=spec.get("timeout", 60), output=log) != 0:
                        raise RuntimeError("start failed")
                    deadline = time.monotonic() + spec.get("wait", 30)
                    while not healthy(spec):
                        if time.monotonic() >= deadline:
                            raise RuntimeError("health timeout")
                        time.sleep(0.5)
                print(name + ": OK", flush=True)
                return True
        except Exception as exc:
            print(name + ": FAILED (" + str(exc) + ")", flush=True)
            failed.add(name)
            return False

    for name in names:
        start(name)
    return bool(failed)


def main() -> int:
    parser = argparse.ArgumentParser(description="回生 (Revive) — 统一启动登记与自愈监督系统")
    sub = parser.add_subparsers(dest="action", required=True)
    for action in ("start", "status", "ensure"):
        s = sub.add_parser(action)
        s.add_argument("service", nargs="?")
    reg = sub.add_parser("register")
    reg.add_argument("name")
    reg.add_argument("--file", required=True)
    args = parser.parse_args()

    STATE.mkdir(parents=True, exist_ok=True)
    (STATE / "logs").mkdir(exist_ok=True)
    with (STATE / "registry.lock").open("a", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        registry = read_registry()
        if args.action == "register":
            registry[args.name] = json.loads(Path(args.file).read_text(encoding="utf-8"))
            validate(registry)
            temp = STATE / "registry.json.tmp"
            temp.write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            temp.replace(STATE / "registry.json")
            print(args.name + ": registered")
            return 0

        validate(registry)
        names = [args.service] if args.service else list(registry)
        if any(n not in registry for n in names):
            parser.error("Unknown service")
        if args.action in ("start", "ensure"):
            return int(start_services(registry, names))

        failed = False
        for name in names:
            ok = healthy(registry[name])
            failed |= not ok
            print(f'{name:20} {"OK" if ok else "DOWN/DEGRADED"}  {registry[name].get("description", "")}')
        return int(failed)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError) as exc:
        print("revive: " + str(exc), file=sys.stderr)
        sys.exit(1)

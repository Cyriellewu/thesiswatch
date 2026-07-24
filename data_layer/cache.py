from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

try:
    import fcntl
except ImportError:  # pragma: no cover - fcntl is unavailable on Windows.
    fcntl = None


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


class JsonTTLCache:
    """Tiny file-backed TTL cache suitable for cron-style jobs."""

    def __init__(self, namespace: str, ttl_seconds: int) -> None:
        self.namespace = namespace
        self.ttl = ttl_seconds
        self.path = repo_root() / "data" / "cache" / f"{namespace}.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_path = self.path.with_suffix(".lock")

    def key(self, *parts: str) -> str:
        h = hashlib.sha256("|".join(parts).encode()).hexdigest()[:24]
        return h

    def get(self, k: str) -> Any | None:
        if not self.path.exists():
            return None
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            entry = raw.get(k)
            if not entry:
                return None
            if time.time() - entry["ts"] > self.ttl:
                return None
            return entry["value"]
        except (OSError, json.JSONDecodeError, KeyError, TypeError):
            return None

    @contextmanager
    def _exclusive_write_lock(self):
        if fcntl is None:
            yield
            return
        with self.lock_path.open("a", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def set(self, k: str, value: Any) -> None:
        with self._exclusive_write_lock():
            data: dict[str, dict[str, Any]] = {}
            if self.path.exists():
                try:
                    data = json.loads(self.path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    data = {}
            data[k] = {"ts": time.time(), "value": value}

            tmp_path: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    "w",
                    encoding="utf-8",
                    dir=self.path.parent,
                    prefix=f".{self.path.stem}.{os.getpid()}.",
                    suffix=".tmp",
                    delete=False,
                ) as tmp:
                    tmp_path = Path(tmp.name)
                    json.dump(data, tmp)
                    tmp.flush()
                    os.fsync(tmp.fileno())
                tmp_path.chmod(0o644)
                tmp_path.replace(self.path)
            finally:
                if tmp_path is not None and tmp_path.exists():
                    tmp_path.unlink()

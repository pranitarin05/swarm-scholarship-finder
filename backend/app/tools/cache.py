"""Disk cache for web responses (keyed by URL hash)."""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Optional

from ..config import settings


class DiskCache:
    """Tiny JSON file cache with TTL. Used by search/fetch/GitHub tools."""

    def __init__(self, root: Optional[Path] = None, ttl_seconds: int = 6 * 3600) -> None:
        self.root = Path(root) if root else settings.cache_dir
        self.root.mkdir(parents=True, exist_ok=True)
        self.ttl = ttl_seconds

    def _path(self, key: str) -> Path:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return self.root / f"{digest}.json"

    def get(self, key: str) -> Optional[dict]:
        path = self._path(key)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        if time.time() - payload.get("stored_at", 0) > self.ttl:
            return None
        return payload.get("value")

    def set(self, key: str, value: dict) -> None:
        path = self._path(key)
        payload = {"stored_at": time.time(), "value": value}
        try:
            path.write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )
        except OSError:
            pass

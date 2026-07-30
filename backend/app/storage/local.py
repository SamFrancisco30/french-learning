"""Local-disk object store — the offline development backend.

Keys map straight onto paths under `data_dir`, and URLs go through the `/media` static
mount the API already serves. Keeping this backend working means the whole pipeline can be
developed and tested with no cloud credentials at all.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from ..config import settings

log = logging.getLogger(__name__)


class LocalStore:
    name = "local"

    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or settings.data_dir).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        # Reject traversal: a key must stay inside the root.
        target = (self.root / key).resolve()
        if not str(target).startswith(str(self.root)):
            raise ValueError(f"key escapes storage root: {key!r}")
        return target

    def put_file(self, key: str, path: Path, *, overwrite: bool = False) -> str:
        dst = self._path(key)
        if dst.exists() and not overwrite:
            return key
        dst.parent.mkdir(parents=True, exist_ok=True)
        if path.resolve() != dst:
            shutil.copy2(path, dst)
        return key

    def put_bytes(self, key: str, data: bytes, *, overwrite: bool = False) -> str:
        dst = self._path(key)
        if dst.exists() and not overwrite:
            return key
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(data)
        return key

    def exists(self, key: str) -> bool:
        return self._path(key).exists()

    def url_for(self, key: str) -> str:
        return f"/media/{key}"

    def size(self, key: str) -> int | None:
        p = self._path(key)
        return p.stat().st_size if p.exists() else None

    def delete(self, key: str) -> None:
        p = self._path(key)
        if p.exists():
            p.unlink()

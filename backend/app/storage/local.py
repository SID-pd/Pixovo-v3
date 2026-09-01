"""Local filesystem StorageBackend."""

import shutil
from pathlib import Path
from typing import BinaryIO, Iterable, Optional

from app.storage.base import StorageBackend

_COPY_CHUNK = 1024 * 1024  # 1 MB — constant memory regardless of file size


class LocalDiskBackend(StorageBackend):
    def __init__(self, root: Path, url_prefix: str = "/uploads"):
        self.root = Path(root)
        self.url_prefix = url_prefix.rstrip("/")

    # ------------------------------------------------------------------ paths

    def _p(self, key: str) -> Path:
        """
        Resolves a key to a path inside the storage root.

        The traversal guard is load-bearing: keys embed client-supplied photo
        ids, so without it a photo_id of "../../etc/x" would write outside the
        uploads root. Checked both syntactically and by resolved prefix, because
        a symlink inside the tree could otherwise escape it.
        """
        candidate = Path(key)
        if not key or candidate.is_absolute() or ".." in candidate.parts:
            raise ValueError(f"Unsafe storage key: {key!r}")

        full = (self.root / candidate).resolve()
        root = self.root.resolve()
        if root != full and root not in full.parents:
            raise ValueError(f"Storage key escapes root: {key!r}")
        return full

    # ------------------------------------------------------------------ write

    def put_stream(self, key: str, stream: BinaryIO, content_type: str = "image/jpeg") -> str:
        path = self._p(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(path, "wb") as dest:
                shutil.copyfileobj(stream, dest, length=_COPY_CHUNK)
        except Exception:
            path.unlink(missing_ok=True)  # never leave a truncated file behind
            raise
        return self.url_for(key)

    def put_stream_iter(self, key: str, chunks: Iterable[bytes], content_type: str = "image/jpeg") -> str:
        path = self._p(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(path, "wb") as dest:
                for chunk in chunks:
                    dest.write(chunk)
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return self.url_for(key)

    # ------------------------------------------------------------------- read

    def get_path(self, key: str) -> Optional[str]:
        try:
            path = self._p(key)
        except ValueError:
            return None
        return str(path) if path.is_file() else None

    def url_for(self, key: str) -> str:
        return f"{self.url_prefix}/{key}"

    def key_for_url(self, url: str) -> Optional[str]:
        if not url:
            return None
        clean = url.split("?")[0]
        prefix = self.url_prefix + "/"
        if not clean.startswith(prefix):
            return None
        return clean[len(prefix):]

    def exists(self, key: str) -> bool:
        try:
            return self._p(key).is_file()
        except ValueError:
            return False

    # ----------------------------------------------------------------- delete

    def delete(self, key: str) -> None:
        try:
            self._p(key).unlink(missing_ok=True)
        except ValueError:
            pass

    def delete_prefix(self, prefix: str) -> int:
        try:
            directory = self._p(prefix)
        except ValueError:
            return 0
        if not directory.is_dir():
            return 0
        count = sum(1 for f in directory.rglob("*") if f.is_file())
        shutil.rmtree(directory, ignore_errors=True)
        return count

    # ------------------------------------------------------------ accounting

    def total_bytes(self, prefix: str = "") -> int:
        directory = self._p(prefix) if prefix else self.root
        if not directory.is_dir():
            return 0
        return sum(f.stat().st_size for f in directory.rglob("*") if f.is_file())

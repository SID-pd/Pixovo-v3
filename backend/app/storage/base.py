"""Abstract storage backend. LocalDisk today; S3/GCS later without caller changes."""

from abc import ABC, abstractmethod
from typing import BinaryIO, Iterable, Optional


class StorageBackend(ABC):
    """
    Abstracts where photo bytes live.

    Keys are always relative, forward-slash separated, and of the shape
    "{kind}/{session_id}/{name}" — see app.storage.storage_key(). Implementations
    MUST reject absolute keys and any key containing '..', because keys are built
    from client-supplied photo ids and filenames.
    """

    @abstractmethod
    def put_stream(self, key: str, stream: BinaryIO, content_type: str = "image/jpeg") -> str:
        """Write a file-like object to `key` in fixed chunks. Returns the public URL."""

    @abstractmethod
    def put_stream_iter(self, key: str, chunks: Iterable[bytes], content_type: str = "image/jpeg") -> str:
        """
        Write an iterator of byte chunks to `key`. Lets the caller enforce a size
        cap mid-copy so an oversized upload is rejected before it fully lands.
        A partial write MUST be cleaned up if the iterator raises.
        """

    @abstractmethod
    def get_path(self, key: str) -> Optional[str]:
        """
        Local filesystem path for reading, or None if absent.
        A remote backend downloads to a temp file and returns that path.
        """

    @abstractmethod
    def url_for(self, key: str) -> str:
        """Browser-facing URL for `key`."""

    @abstractmethod
    def exists(self, key: str) -> bool:
        ...

    @abstractmethod
    def delete(self, key: str) -> None:
        ...

    @abstractmethod
    def delete_prefix(self, prefix: str) -> int:
        """Delete everything under a prefix. Returns the count. Used by retention."""

    @abstractmethod
    def total_bytes(self, prefix: str = "") -> int:
        """
        Sum of stored bytes under a prefix.

        Walks storage, so it is too slow for a per-request check — use the
        running total on sessions.total_bytes for that. This is for sweeps,
        diagnostics and tests.
        """

    @abstractmethod
    def key_for_url(self, url: str) -> Optional[str]:
        """Inverse of url_for(). Returns None if the URL is not ours."""

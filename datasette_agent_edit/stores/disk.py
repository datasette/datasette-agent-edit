"""A filesystem-backed :class:`~datasette_agent_edit.store.EditStore`.

``ref`` is a path relative to a root directory. This backend is unversioned:
``version`` is the content file's ``mtime_ns``, used purely as a display/
freshness token. Atomicity within a process comes from a per-store
``asyncio.Lock`` plus a write-to-temp-then-``os.replace`` swap; blocking file
I/O is pushed to a worker thread so the event loop is never blocked. For
cross-process safety you would add an ``fcntl.flock`` around the read/replace,
which is the disk analogue of the SQLite write transaction.
"""

import asyncio
import json
import os
import stat

from ulid import ULID

from ..store import Editable, NotFound, Transform

META_SUFFIX = ".meta.json"


class DiskStore:
    """Store each piece of content as a file under ``root_dir``.

    Metadata, if any, is written to a ``<path>.meta.json`` sidecar, so refs
    ending in ``.meta.json`` are reserved and rejected. Symlinks are resolved
    before the containment check, so a link inside the root cannot be used to
    reach files outside it. Line endings are preserved byte-for-byte and an
    edited file keeps its permission bits.
    """

    def __init__(self, root_dir):
        self.root_dir = os.path.realpath(root_dir)
        self._lock = asyncio.Lock()

    def _resolve(self, ref):
        """Resolve ``ref`` to an absolute path, refusing escapes from root."""
        if not isinstance(ref, str) or not ref or "\x00" in ref:
            raise ValueError(f"invalid ref: {ref!r}")
        if ref.endswith(META_SUFFIX):
            raise ValueError(f"ref uses the reserved {META_SUFFIX} suffix: {ref!r}")
        path = os.path.realpath(os.path.join(self.root_dir, ref))
        if not path.startswith(self.root_dir + os.sep):
            raise ValueError(f"ref escapes storage root: {ref!r}")
        return path

    def _meta_path(self, path):
        return path + META_SUFFIX

    def _read_sync(self, ref):
        path = self._resolve(ref)
        try:
            # newline="" keeps CRLF (and lone CR) exactly as stored.
            with open(path, "r", encoding="utf-8", newline="") as f:
                content = f.read()
        except (FileNotFoundError, IsADirectoryError, NotADirectoryError):
            raise NotFound(ref) from None
        except UnicodeDecodeError:
            raise ValueError(f"not a UTF-8 text file: {ref!r}") from None
        metadata = {}
        meta_path = self._meta_path(path)
        if os.path.exists(meta_path):
            with open(meta_path, "r", encoding="utf-8") as f:
                metadata = json.load(f)
        version = os.stat(path).st_mtime_ns
        return Editable(ref=ref, content=content, metadata=metadata, version=version)

    def _write_sync(self, path, content):
        """Atomically replace ``path`` with ``content`` (temp file + rename).

        An existing file keeps its permission bits, and the temp file is
        removed if anything goes wrong before the swap.
        """
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.{ULID()}.tmp"
        try:
            with open(tmp, "w", encoding="utf-8", newline="") as f:
                f.write(content)
            try:
                os.chmod(tmp, stat.S_IMODE(os.stat(path).st_mode))
            except FileNotFoundError:
                pass
            os.replace(tmp, path)
        except BaseException:
            try:
                os.remove(tmp)
            except FileNotFoundError:
                pass
            raise

    async def create(self, content, *, ref=None, metadata=None):
        ref = ref or str(ULID())
        metadata = metadata or {}
        path = self._resolve(ref)

        def _do():
            if os.path.lexists(path):
                raise FileExistsError(ref)
            self._write_sync(path, content)
            if metadata:
                self._write_sync(self._meta_path(path), json.dumps(metadata))
            return os.stat(path).st_mtime_ns

        async with self._lock:
            version = await asyncio.to_thread(_do)
        return Editable(ref=ref, content=content, metadata=metadata, version=version)

    async def read(self, ref):
        return await asyncio.to_thread(self._read_sync, ref)

    async def edit(self, ref, transform: Transform):
        path = self._resolve(ref)

        def _do():
            current = self._read_sync(ref)  # raises NotFound if missing
            # Pure, synchronous surgery between read and atomic replace.
            new_content = transform(current.content)
            self._write_sync(path, new_content)
            return Editable(
                ref=ref,
                content=new_content,
                metadata=current.metadata,
                version=os.stat(path).st_mtime_ns,
            )

        async with self._lock:
            return await asyncio.to_thread(_do)

    async def delete(self, ref):
        path = self._resolve(ref)

        def _do():
            try:
                os.remove(path)
            except (FileNotFoundError, IsADirectoryError, PermissionError):
                # macOS reports unlinking a directory as PermissionError.
                if os.path.isdir(path) or not os.path.lexists(path):
                    raise NotFound(ref) from None
                raise
            meta_path = self._meta_path(path)
            if os.path.exists(meta_path):
                os.remove(meta_path)

        async with self._lock:
            await asyncio.to_thread(_do)

"""The storage seam: :class:`EditStore` and its supporting types.

A backend is anything that can create, read and atomically transform a piece
of text content addressed by a ``ref``. The defining method is
:meth:`EditStore.edit`, which takes a *synchronous, pure* ``transform``
callable and is responsible for running it inside whatever critical section
the backend needs (a SQLite write transaction, a file lock, an S3/GitHub
compare-and-set retry loop). Because the transform contains no I/O and no
awaits, the same editing logic is safe inside any of those contexts.
"""

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

try:
    from typing import Protocol, runtime_checkable
except ImportError:  # pragma: no cover - py<3.8 fallback
    from typing_extensions import Protocol, runtime_checkable


#: A pure, synchronous content transform: old content in, new content out.
#: It must not perform I/O or await; raise :class:`~.operations.EditError`
#: (a ``ValueError``) to abort the edit before anything is persisted.
Transform = Callable[[str], str]

#: An async transform, for the optional :class:`AsyncTransformStore` capability.
AsyncTransform = Callable[[str], Awaitable[str]]


class NotFound(Exception):
    """Raised by a store when ``ref`` does not identify existing content."""


class Conflict(Exception):
    """Raised when an atomic edit could not be committed due to contention.

    Backends that implement optimistic concurrency (S3 ``If-Match``, the
    GitHub contents API ``sha``) raise this after exhausting their retries.
    """


@dataclass
class Editable:
    """A snapshot of stored content plus its addressing and bookkeeping.

    ``version`` is an opaque token whose meaning is backend-defined: an
    incrementing integer for a versioned SQLite store, an mtime for a disk
    file, an ETag or git blob SHA for object stores. The toolset only echoes
    it back to the model and (optionally) uses it for display; it never
    interprets it.
    """

    ref: str
    content: str
    metadata: dict = field(default_factory=dict)
    version: Any = None


@runtime_checkable
class EditStore(Protocol):
    """The interface every storage backend implements."""

    async def create(
        self, content: str, *, ref: Optional[str] = None, metadata: Optional[dict] = None
    ) -> Editable:
        """Create new content and return its :class:`Editable`.

        ``ref`` may be supplied for path-addressed backends (disk, S3,
        GitHub); id-generating backends (SQLite) generate one when it is
        ``None``. Raises :class:`FileExistsError` if ``ref`` is already
        taken.
        """
        ...

    async def read(self, ref: str) -> Editable:
        """Return the current :class:`Editable` for ``ref``.

        Raises :class:`NotFound` if it does not exist.
        """
        ...

    async def edit(self, ref: str, transform: Transform) -> Editable:
        """Atomically apply ``transform`` to the content at ``ref``.

        The backend reads the current content, applies ``transform`` inside
        its critical section, persists the result and returns the new
        :class:`Editable`. If ``transform`` raises, nothing is persisted.
        Raises :class:`NotFound` if ``ref`` does not exist and
        :class:`Conflict` if the write could not be committed.
        """
        ...

    async def delete(self, ref: str) -> None:
        """Delete the content at ``ref`` (raising :class:`NotFound` if absent)."""
        ...


@runtime_checkable
class AsyncTransformStore(Protocol):
    """Opt-in capability for the rare backend that can await mid-edit.

    Most backends cannot (a SQLite write transform runs on a non-async write
    thread), so this is deliberately separate from :class:`EditStore`. The
    :class:`~.toolset.EditToolset` never calls it; it exists for specialised
    callers whose edit decision must consult something asynchronously and who
    cannot pre-resolve that work before calling :meth:`EditStore.edit`.
    """

    async def aedit(self, ref: str, transform: AsyncTransform) -> Editable:
        ...


class IdCodec:
    """Translates between the internal ``ref`` and the id the model sees.

    The default is an identity mapping. Subclass (or use :class:`PrefixCodec`)
    to expose, for example, ``artifact-01J...`` externally while storing the
    bare ULID internally.
    """

    def to_external(self, ref: str) -> str:
        return ref

    def to_internal(self, external_id: str) -> str:
        return external_id


class PrefixCodec(IdCodec):
    """An :class:`IdCodec` that adds/strips a fixed prefix."""

    def __init__(self, prefix: str):
        self.prefix = prefix

    def to_external(self, ref: str) -> str:
        return self.prefix + ref

    def to_internal(self, external_id: str) -> str:
        if external_id.startswith(self.prefix):
            return external_id[len(self.prefix) :]
        return external_id

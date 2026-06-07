"""Storage-agnostic file-editing tools for Datasette Agent plugins.

Three layers:

1. :mod:`datasette_agent_edit.operations` -- pure string-editing primitives.
2. :mod:`datasette_agent_edit.store` -- the :class:`EditStore` backend seam,
   plus ready-made :class:`SqliteVersionedStore` / :class:`DiskStore` backends.
3. :class:`EditToolset` -- builds Datasette Agent tools over any backend with
   a single consistent JSON envelope and optional presentation hook.
"""

from .operations import (
    EditApplyError,
    EditError,
    apply_edit,
    apply_edits,
    insert,
    str_replace,
    view_lines,
)
from .store import (
    AsyncTransformStore,
    Conflict,
    Editable,
    EditStore,
    IdCodec,
    NotFound,
    PrefixCodec,
    Transform,
)
from .stores import DiskStore, SqliteVersionedStore
from .toolset import EditToolset

__all__ = [
    # operations
    "view_lines",
    "str_replace",
    "insert",
    "apply_edit",
    "apply_edits",
    "EditError",
    "EditApplyError",
    # store
    "Editable",
    "EditStore",
    "AsyncTransformStore",
    "Transform",
    "IdCodec",
    "PrefixCodec",
    "NotFound",
    "Conflict",
    # backends
    "SqliteVersionedStore",
    "DiskStore",
    # toolset
    "EditToolset",
]

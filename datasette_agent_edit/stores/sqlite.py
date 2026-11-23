"""A versioned :class:`~datasette_agent_edit.store.EditStore` backed by SQLite.

Every edit appends a new row to a versions table and bumps a ``current_version``
pointer, giving full history. The atomic ``edit()`` runs the pure transform
*inside* Datasette's write thread via ``db.execute_write_fn``, so the
read-modify-write is a single serialised transaction with no chance of another
edit interleaving — and no ``await`` is possible (or needed) in that context.
"""

import json
import sqlite3
from datetime import datetime, timezone

from ulid import ULID

from ..store import Editable, NotFound, Transform


def _now():
    return datetime.now(timezone.utc).isoformat()


class SqliteVersionedStore:
    """Store content in two tables: objects + an append-only versions log.

    Parameters
    ----------
    db:
        A Datasette ``Database`` (e.g. ``datasette.get_internal_database()``).
    table:
        Name of the objects table (one row per piece of content).
    versions_table:
        Name of the append-only versions table.
    validate_metadata:
        Optional callable invoked as ``validate_metadata(metadata)`` on
        ``create``; raise ``ValueError`` to reject invalid metadata.
    """

    def __init__(
        self,
        db,
        *,
        table="datasette_agent_edit_objects",
        versions_table="datasette_agent_edit_versions",
        validate_metadata=None,
    ):
        self.db = db
        self.table = table
        self.versions_table = versions_table
        self.validate_metadata = validate_metadata
        self._ensured = False

    @property
    def schema_sql(self):
        return f"""
CREATE TABLE IF NOT EXISTS {self.table} (
    id TEXT PRIMARY KEY,
    metadata TEXT NOT NULL DEFAULT '{{}}',
    current_version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS {self.versions_table} (
    object_id TEXT NOT NULL REFERENCES {self.table}(id),
    version INTEGER NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (object_id, version)
);
"""

    async def ensure_tables(self):
        if not self._ensured:
            await self.db.execute_write_script(self.schema_sql)
            self._ensured = True

    async def create(self, content, *, ref=None, metadata=None):
        metadata = metadata or {}
        if self.validate_metadata is not None:
            self.validate_metadata(metadata)
        await self.ensure_tables()

        object_id = ref or str(ULID())
        now = _now()
        metadata_json = json.dumps(metadata)

        def _write(conn):
            # Both rows in one transaction, so a failure can never leave an
            # object without its first version.
            try:
                conn.execute(
                    f"INSERT INTO {self.table} "
                    "(id, metadata, current_version, created_at, updated_at) "
                    "VALUES (?, ?, 1, ?, ?)",
                    [object_id, metadata_json, now, now],
                )
            except sqlite3.IntegrityError:
                raise FileExistsError(object_id) from None
            conn.execute(
                f"INSERT INTO {self.versions_table} "
                "(object_id, version, content, created_at) VALUES (?, 1, ?, ?)",
                [object_id, content, now],
            )

        await self.db.execute_write_fn(_write)
        return Editable(ref=object_id, content=content, metadata=metadata, version=1)

    async def read(self, ref):
        await self.ensure_tables()
        row = (
            await self.db.execute(
                f"SELECT o.metadata, o.current_version, v.content "
                f"FROM {self.versions_table} v "
                f"JOIN {self.table} o ON o.id = v.object_id "
                f"WHERE v.object_id = ? AND v.version = o.current_version",
                [ref],
            )
        ).first()
        if not row:
            raise NotFound(ref)
        return Editable(
            ref=ref,
            content=row["content"],
            metadata=json.loads(row["metadata"]),
            version=row["current_version"],
        )

    async def edit(self, ref, transform: Transform):
        await self.ensure_tables()
        now = _now()

        def _write(conn):
            row = conn.execute(
                f"SELECT o.metadata, o.current_version, v.content "
                f"FROM {self.versions_table} v "
                f"JOIN {self.table} o ON o.id = v.object_id "
                f"WHERE v.object_id = ? AND v.version = o.current_version",
                [ref],
            ).fetchone()
            if not row:
                raise NotFound(ref)
            metadata_json, current_version, content = row
            # Pure, synchronous surgery inside the write transaction. If this
            # raises (e.g. a failed str_replace) the transaction is rolled
            # back and nothing is persisted.
            new_content = transform(content)
            new_version = current_version + 1
            conn.execute(
                f"INSERT INTO {self.versions_table} "
                "(object_id, version, content, created_at) VALUES (?, ?, ?, ?)",
                [ref, new_version, new_content, now],
            )
            conn.execute(
                f"UPDATE {self.table} SET current_version = ?, updated_at = ? "
                "WHERE id = ?",
                [new_version, now, ref],
            )
            return new_version, new_content, json.loads(metadata_json)

        new_version, new_content, metadata = await self.db.execute_write_fn(_write)
        return Editable(
            ref=ref, content=new_content, metadata=metadata, version=new_version
        )

    async def delete(self, ref):
        await self.ensure_tables()

        def _write(conn):
            # Children first, so this also works with foreign_keys enabled.
            conn.execute(
                f"DELETE FROM {self.versions_table} WHERE object_id = ?", [ref]
            )
            cur = conn.execute(f"DELETE FROM {self.table} WHERE id = ?", [ref])
            if cur.rowcount == 0:
                raise NotFound(ref)

        await self.db.execute_write_fn(_write)

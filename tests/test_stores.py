import pytest
from datasette.app import Datasette

from datasette_agent_edit import (
    DiskStore,
    EditError,
    NotFound,
    SqliteVersionedStore,
    str_replace,
)


@pytest.fixture
def sqlite_store():
    return SqliteVersionedStore(Datasette(memory=True).get_internal_database())


@pytest.fixture
def disk_store(tmp_path):
    return DiskStore(tmp_path)


@pytest.fixture(params=["sqlite", "disk"])
def store(request, tmp_path):
    if request.param == "sqlite":
        return SqliteVersionedStore(Datasette(memory=True).get_internal_database())
    return DiskStore(tmp_path)


# --- Shared EditStore contract ---


@pytest.mark.asyncio
async def test_create_read_roundtrip(store):
    created = await store.create("hello\nworld\n", metadata={"type": "html"})
    fetched = await store.read(created.ref)
    assert fetched.content == "hello\nworld\n"
    assert fetched.metadata == {"type": "html"}
    assert fetched.ref == created.ref


@pytest.mark.asyncio
async def test_read_missing_raises(store):
    with pytest.raises(NotFound):
        await store.read("does-not-exist")


@pytest.mark.asyncio
async def test_edit_applies_transform(store):
    created = await store.create("fill='red'")
    edited = await store.edit(
        created.ref, lambda c: str_replace(c, "red", "blue")
    )
    assert edited.content == "fill='blue'"
    assert (await store.read(created.ref)).content == "fill='blue'"


@pytest.mark.asyncio
async def test_edit_missing_raises(store):
    with pytest.raises(NotFound):
        await store.edit("nope", lambda c: c)


@pytest.mark.asyncio
async def test_failed_transform_persists_nothing(store):
    """A raising transform must leave stored content untouched (all-or-nothing)."""
    created = await store.create("original")

    def boom(content):
        raise EditError("no")

    with pytest.raises(EditError):
        await store.edit(created.ref, boom)
    assert (await store.read(created.ref)).content == "original"


@pytest.mark.asyncio
async def test_delete(store):
    created = await store.create("bye")
    await store.delete(created.ref)
    with pytest.raises(NotFound):
        await store.read(created.ref)


# --- SQLite-specific: version history ---


@pytest.mark.asyncio
async def test_sqlite_versions_increment(sqlite_store):
    created = await sqlite_store.create("v1 content")
    assert created.version == 1
    e2 = await sqlite_store.edit(created.ref, lambda c: c.replace("v1", "v2"))
    e3 = await sqlite_store.edit(created.ref, lambda c: c.replace("v2", "v3"))
    assert e2.version == 2
    assert e3.version == 3

    rows = (
        await sqlite_store.db.execute(
            f"SELECT version, content FROM {sqlite_store.versions_table} "
            "WHERE object_id = ? ORDER BY version",
            [created.ref],
        )
    ).rows
    assert [r["content"] for r in rows] == ["v1 content", "v2 content", "v3 content"]


@pytest.mark.asyncio
async def test_sqlite_validate_metadata():
    def only_html(metadata):
        if metadata.get("type") != "html":
            raise ValueError("type must be html")

    store = SqliteVersionedStore(
        Datasette(memory=True).get_internal_database(),
        validate_metadata=only_html,
    )
    with pytest.raises(ValueError, match="must be html"):
        await store.create("x", metadata={"type": "svg"})


# --- Disk-specific ---


@pytest.mark.asyncio
async def test_disk_explicit_ref_path(disk_store):
    created = await disk_store.create("contents", ref="sub/dir/file.txt")
    assert created.ref == "sub/dir/file.txt"
    assert (await disk_store.read("sub/dir/file.txt")).content == "contents"


@pytest.mark.asyncio
async def test_disk_rejects_escape(disk_store):
    with pytest.raises(ValueError, match="escapes"):
        await disk_store.create("x", ref="../outside.txt")


@pytest.mark.asyncio
async def test_disk_version_changes_on_edit(disk_store):
    created = await disk_store.create("a")
    edited = await disk_store.edit(created.ref, lambda c: c + "b")
    assert edited.version != created.version
    assert edited.content == "ab"


# --- Fixes: shared contract ---


@pytest.mark.asyncio
async def test_create_duplicate_ref_raises(store):
    await store.create("one", ref="dup.txt")
    with pytest.raises(FileExistsError):
        await store.create("two", ref="dup.txt")
    assert (await store.read("dup.txt")).content == "one"


@pytest.mark.asyncio
async def test_unchanged_edit_keeps_version(store):
    created = await store.create("same\n")
    edited = await store.edit(created.ref, lambda c: c)
    assert edited.version == created.version
    assert (await store.read(created.ref)).version == created.version


# --- Fixes: SQLite ---


@pytest.mark.asyncio
async def test_sqlite_create_is_atomic():
    db = Datasette(memory=True).get_internal_database()
    store = SqliteVersionedStore(db)
    await store.ensure_tables()
    # Force the second insert to fail: the first must roll back with it.
    await db.execute_write(
        "CREATE TRIGGER boom BEFORE INSERT ON datasette_agent_edit_versions "
        "BEGIN SELECT RAISE(ABORT, 'boom'); END"
    )
    with pytest.raises(Exception, match="boom"):
        await store.create("x", ref="r1")
    count = (
        await db.execute("select count(*) from datasette_agent_edit_objects")
    ).first()[0]
    assert count == 0


@pytest.mark.asyncio
async def test_sqlite_delete_with_foreign_keys_enforced(sqlite_store):
    created = await sqlite_store.create("x")
    await sqlite_store.edit(created.ref, lambda c: c + "y")

    def _delete_with_fks(conn):
        conn.execute("PRAGMA foreign_keys = ON")

    await sqlite_store.db.execute_write_fn(_delete_with_fks, transaction=False)
    try:
        await sqlite_store.delete(created.ref)
    finally:
        await sqlite_store.db.execute_write_fn(
            lambda conn: conn.execute("PRAGMA foreign_keys = OFF"), transaction=False
        )
    with pytest.raises(NotFound):
        await sqlite_store.read(created.ref)
    with pytest.raises(NotFound):
        await sqlite_store.delete(created.ref)


# --- Fixes: disk ---


@pytest.mark.asyncio
async def test_disk_edit_preserves_line_endings_and_mode(disk_store, tmp_path):
    path = tmp_path / "s.sh"
    path.write_bytes(b"#!/bin/sh\r\necho hi\r\n")
    path.chmod(0o755)
    await disk_store.edit("s.sh", lambda c: c.replace("hi", "yo"))
    assert path.read_bytes() == b"#!/bin/sh\r\necho yo\r\n"
    assert path.stat().st_mode & 0o777 == 0o755
    assert (await disk_store.read("s.sh")).content == "#!/bin/sh\r\necho yo\r\n"


@pytest.mark.asyncio
async def test_disk_metadata_sidecar_is_not_addressable(disk_store):
    await disk_store.create("x", ref="a.txt", metadata={"secret": 1})
    with pytest.raises(ValueError, match="reserved"):
        await disk_store.read("a.txt.meta.json")
    with pytest.raises(ValueError, match="reserved"):
        await disk_store.edit("a.txt.meta.json", lambda c: "{}")


@pytest.mark.asyncio
async def test_disk_rejects_symlink_escape(disk_store, tmp_path_factory, tmp_path):
    outside = tmp_path_factory.mktemp("outside")
    (outside / "o.txt").write_text("outside")
    (tmp_path / "link").symlink_to(outside)
    with pytest.raises(ValueError, match="escapes"):
        await disk_store.read("link/o.txt")
    with pytest.raises(ValueError, match="escapes"):
        await disk_store.edit("link/o.txt", lambda c: "pwned")
    assert (outside / "o.txt").read_text() == "outside"


@pytest.mark.asyncio
@pytest.mark.parametrize("ref", ["", ".", "sub"])
async def test_disk_directory_refs_are_not_content(disk_store, tmp_path, ref):
    (tmp_path / "sub").mkdir()
    with pytest.raises((ValueError, NotFound)):
        await disk_store.read(ref)
    with pytest.raises((ValueError, NotFound)):
        await disk_store.delete(ref)
    assert (tmp_path / "sub").is_dir()


@pytest.mark.asyncio
async def test_disk_non_utf8_is_value_error(disk_store, tmp_path):
    (tmp_path / "b.bin").write_bytes(b"\xff\xfe\x00")
    with pytest.raises(ValueError, match="UTF-8"):
        await disk_store.read("b.bin")


@pytest.mark.asyncio
async def test_disk_failed_write_leaves_no_temp_file(disk_store, tmp_path):
    await disk_store.create("ok", ref="a.txt")
    with pytest.raises(UnicodeEncodeError):
        await disk_store.edit("a.txt", lambda c: "\ud800")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.txt"]
    assert (tmp_path / "a.txt").read_text() == "ok"

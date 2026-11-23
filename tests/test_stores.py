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
async def test_disk_failed_write_leaves_no_temp_file(disk_store, tmp_path):
    await disk_store.create("ok", ref="a.txt")
    with pytest.raises(UnicodeEncodeError):
        await disk_store.edit("a.txt", lambda c: "\ud800")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.txt"]
    assert (tmp_path / "a.txt").read_text() == "ok"

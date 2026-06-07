import json

import pytest
from datasette.app import Datasette

from datasette_agent_edit import (
    DiskStore,
    EditToolset,
    PrefixCodec,
    SqliteVersionedStore,
)


@pytest.fixture(params=["sqlite", "disk"])
def store(request, tmp_path):
    if request.param == "sqlite":
        return SqliteVersionedStore(Datasette(memory=True).get_internal_database())
    return DiskStore(tmp_path)


@pytest.fixture
def toolset(store):
    # Exercises the id codec and a render hook end to end.
    return EditToolset(
        store,
        name_prefix="artifact",
        id_field="artifact_id",
        id_codec=PrefixCodec("artifact-"),
        render=lambda editable: {"_html": f"<iframe>{editable.content}</iframe>"},
    )


async def _make(store, toolset, content, **meta):
    created = await store.create(content, metadata=meta)
    return toolset.codec.to_external(created.ref)


@pytest.mark.asyncio
async def test_view_uses_external_id_and_line_numbers(store, toolset):
    ext = await _make(store, toolset, "<h1>Hello</h1>\n<p>World</p>\n", type="html")
    result = json.loads(await toolset.view(ext))
    assert result["artifact_id"] == ext
    assert ext.startswith("artifact-")
    assert "1:\t<h1>Hello</h1>" in result["content"]
    assert "2:\t<p>World</p>" in result["content"]
    assert result["metadata"] == {"type": "html"}


@pytest.mark.asyncio
async def test_view_range(store, toolset):
    ext = await _make(store, toolset, "l1\nl2\nl3\nl4\nl5\n")
    result = json.loads(await toolset.view(ext, view_range="2,4"))
    assert "2:\tl2" in result["content"]
    assert "1:\tl1" not in result["content"]


@pytest.mark.asyncio
async def test_view_not_found(toolset):
    result = json.loads(await toolset.view("artifact-nope"))
    assert "error" in result


@pytest.mark.asyncio
async def test_str_replace_does_not_render(store, toolset):
    ext = await _make(store, toolset, "<h1>Hello</h1>")
    result = json.loads(await toolset.str_replace(ext, "Hello", "Goodbye"))
    assert "_html" not in result
    assert result["artifact_id"] == ext
    view = json.loads(await toolset.view(ext))
    assert "Goodbye" in view["content"]


@pytest.mark.asyncio
async def test_str_replace_not_found(store, toolset):
    ext = await _make(store, toolset, "<h1>Hello</h1>")
    result = json.loads(await toolset.str_replace(ext, "xyz", "abc"))
    assert "not found" in result["error"]


@pytest.mark.asyncio
async def test_str_replace_ambiguous(store, toolset):
    ext = await _make(store, toolset, "<p>hi</p><p>hi</p>")
    result = json.loads(await toolset.str_replace(ext, "hi", "bye"))
    assert "appears 2 times" in result["error"]


@pytest.mark.asyncio
async def test_insert(store, toolset):
    ext = await _make(store, toolset, "line1\nline2\n")
    result = json.loads(await toolset.insert(ext, 1, "new line\n"))
    assert "_html" not in result
    view = json.loads(await toolset.view(ext))
    assert "1:\tline1" in view["content"]
    assert "2:\tnew line" in view["content"]
    assert "3:\tline2" in view["content"]


@pytest.mark.asyncio
async def test_edit_batch_renders(store, toolset):
    ext = await _make(store, toolset, "<h1>Hello</h1>\n<p>World</p>\n")
    result = json.loads(
        await toolset.edit(
            ext,
            [
                {"operation": "str_replace", "old_str": "Hello", "new_str": "Goodbye"},
                {"operation": "str_replace", "old_str": "World", "new_str": "Universe"},
            ],
        )
    )
    assert result["edits_applied"] == 2
    assert "_html" in result
    view = json.loads(await toolset.view(ext))
    assert "Goodbye" in view["content"]
    assert "Universe" in view["content"]


@pytest.mark.asyncio
async def test_edit_partial_failure_saves_nothing(store, toolset):
    ext = await _make(store, toolset, "<p>hello</p>")
    result = json.loads(
        await toolset.edit(
            ext,
            [
                {"operation": "str_replace", "old_str": "hello", "new_str": "bye"},
                {"operation": "str_replace", "old_str": "nope", "new_str": "x"},
            ],
        )
    )
    assert "Edit #2 failed" in result["error"]
    assert result["applied"] == ["str_replace #1: OK"]
    # Nothing persisted.
    view = json.loads(await toolset.view(ext))
    assert "hello" in view["content"]


@pytest.mark.asyncio
async def test_edit_unknown_operation(store, toolset):
    ext = await _make(store, toolset, "test")
    result = json.loads(await toolset.edit(ext, [{"operation": "delete"}]))
    assert "unknown operation" in result["error"]


@pytest.mark.asyncio
async def test_render(store, toolset):
    ext = await _make(store, toolset, "<h1>Hi</h1>")
    result = json.loads(await toolset.render(ext))
    assert result["_html"] == "<iframe><h1>Hi</h1></iframe>"


@pytest.mark.asyncio
async def test_no_render_hook_omits_render(store):
    """Without a render hook, render() errors and edits report plain status."""
    toolset = EditToolset(store, name_prefix="file", id_field="path")
    created = await store.create("data")
    ext = created.ref
    edit_result = json.loads(
        await toolset.edit(
            ext, [{"operation": "str_replace", "old_str": "data", "new_str": "info"}]
        )
    )
    assert "_html" not in edit_result
    render_result = json.loads(await toolset.render(ext))
    assert "error" in render_result

import json

from datasette.app import Datasette
import pytest

from datasette_agent_artifacts import (
    _create_artifact,
    _artifact_view,
    _artifact_str_replace,
    _artifact_insert,
    _artifact_edit,
    _artifact_render,
    _view_lines,
    _str_replace_content,
    _insert_content,
    ensure_tables,
)


# --- Pure function tests ---


def test_view_lines_basic():
    content = "line one\nline two\nline three\n"
    result = _view_lines(content)
    assert result == "1:\tline one\n2:\tline two\n3:\tline three"


def test_view_lines_range():
    content = "a\nb\nc\nd\ne\n"
    result = _view_lines(content, "2,4")
    assert result == "2:\tb\n3:\tc\n4:\td"


def test_view_lines_range_to_end():
    content = "a\nb\nc\nd\ne\n"
    result = _view_lines(content, "3,-1")
    assert result == "3:\tc\n4:\td\n5:\te"


def test_str_replace_content_basic():
    result = _str_replace_content("hello world", "hello", "goodbye")
    assert result == "goodbye world"


def test_str_replace_content_not_found():
    with pytest.raises(ValueError, match="not found"):
        _str_replace_content("hello world", "xyz", "abc")


def test_str_replace_content_ambiguous():
    with pytest.raises(ValueError, match="appears 2 times"):
        _str_replace_content("hello hello", "hello", "bye")


def test_insert_content_basic():
    result = _insert_content("line1\nline2\n", 1, "new\n")
    assert result == "line1\nnew\nline2\n"


def test_insert_content_beginning():
    result = _insert_content("line1\nline2\n", 0, "new\n")
    assert result == "new\nline1\nline2\n"


def test_insert_content_end():
    result = _insert_content("line1\nline2\n", 2, "new\n")
    assert result == "line1\nline2\nnew\n"


# --- Database-backed tool tests ---


@pytest.fixture
def datasette():
    return Datasette(memory=True)


@pytest.mark.asyncio
async def test_artifact_view(datasette):
    result = json.loads(
        await _create_artifact(datasette, None, content="<h1>Hello</h1>\n<p>World</p>\n", type="html")
    )
    artifact_id = result["artifact_id"]

    view_result = json.loads(
        await _artifact_view(datasette, None, artifact_id=artifact_id)
    )
    assert view_result["artifact_id"] == artifact_id
    assert view_result["type"] == "html"
    assert "1:\t<h1>Hello</h1>" in view_result["content"]
    assert "2:\t<p>World</p>" in view_result["content"]


@pytest.mark.asyncio
async def test_artifact_view_range(datasette):
    content = "line1\nline2\nline3\nline4\nline5\n"
    result = json.loads(
        await _create_artifact(datasette, None, content=content, type="html")
    )
    artifact_id = result["artifact_id"]

    view_result = json.loads(
        await _artifact_view(datasette, None, artifact_id=artifact_id, view_range="2,4")
    )
    assert "2:\tline2" in view_result["content"]
    assert "4:\tline4" in view_result["content"]
    assert "1:\tline1" not in view_result["content"]


@pytest.mark.asyncio
async def test_artifact_view_not_found(datasette):
    await ensure_tables(datasette)
    view_result = json.loads(
        await _artifact_view(datasette, None, artifact_id="nonexistent")
    )
    assert "error" in view_result


@pytest.mark.asyncio
async def test_artifact_str_replace(datasette):
    result = json.loads(
        await _create_artifact(datasette, None, content="<h1>Hello</h1>", type="html")
    )
    artifact_id = result["artifact_id"]

    replace_result = json.loads(
        await _artifact_str_replace(
            datasette, None,
            artifact_id=artifact_id,
            old_str="Hello",
            new_str="Goodbye",
        )
    )
    assert replace_result["version"] == 2
    assert replace_result["artifact_id"] == artifact_id
    assert "_html" not in replace_result

    # Verify the content was updated
    view_result = json.loads(
        await _artifact_view(datasette, None, artifact_id=artifact_id)
    )
    assert "Goodbye" in view_result["content"]
    assert "Hello" not in view_result["content"]


@pytest.mark.asyncio
async def test_artifact_str_replace_not_found(datasette):
    result = json.loads(
        await _create_artifact(datasette, None, content="<h1>Hello</h1>", type="html")
    )
    artifact_id = result["artifact_id"]

    replace_result = json.loads(
        await _artifact_str_replace(
            datasette, None,
            artifact_id=artifact_id,
            old_str="xyz",
            new_str="abc",
        )
    )
    assert "error" in replace_result
    assert "not found" in replace_result["error"]


@pytest.mark.asyncio
async def test_artifact_str_replace_ambiguous(datasette):
    result = json.loads(
        await _create_artifact(datasette, None, content="<p>hello</p><p>hello</p>", type="html")
    )
    artifact_id = result["artifact_id"]

    replace_result = json.loads(
        await _artifact_str_replace(
            datasette, None,
            artifact_id=artifact_id,
            old_str="hello",
            new_str="bye",
        )
    )
    assert "error" in replace_result
    assert "appears 2 times" in replace_result["error"]


@pytest.mark.asyncio
async def test_artifact_insert(datasette):
    result = json.loads(
        await _create_artifact(datasette, None, content="line1\nline2\n", type="html")
    )
    artifact_id = result["artifact_id"]

    insert_result = json.loads(
        await _artifact_insert(
            datasette, None,
            artifact_id=artifact_id,
            insert_line=1,
            insert_text="new line\n",
        )
    )
    assert insert_result["version"] == 2
    assert "_html" not in insert_result

    # Verify the content was updated
    view_result = json.loads(
        await _artifact_view(datasette, None, artifact_id=artifact_id)
    )
    assert "1:\tline1" in view_result["content"]
    assert "2:\tnew line" in view_result["content"]
    assert "3:\tline2" in view_result["content"]


@pytest.mark.asyncio
async def test_artifact_insert_beginning(datasette):
    result = json.loads(
        await _create_artifact(datasette, None, content="line1\nline2\n", type="html")
    )
    artifact_id = result["artifact_id"]

    insert_result = json.loads(
        await _artifact_insert(
            datasette, None,
            artifact_id=artifact_id,
            insert_line=0,
            insert_text="first\n",
        )
    )
    assert insert_result["version"] == 2
    assert "_html" not in insert_result

    view_result = json.loads(
        await _artifact_view(datasette, None, artifact_id=artifact_id)
    )
    assert "1:\tfirst" in view_result["content"]
    assert "2:\tline1" in view_result["content"]


@pytest.mark.asyncio
async def test_version_history(datasette):
    """Multiple edits create incrementing versions."""
    result = json.loads(
        await _create_artifact(datasette, None, content="v1 content", type="html")
    )
    artifact_id = result["artifact_id"]

    r2 = json.loads(
        await _artifact_str_replace(
            datasette, None,
            artifact_id=artifact_id,
            old_str="v1",
            new_str="v2",
        )
    )
    assert r2["version"] == 2

    r3 = json.loads(
        await _artifact_str_replace(
            datasette, None,
            artifact_id=artifact_id,
            old_str="v2",
            new_str="v3",
        )
    )
    assert r3["version"] == 3

    # Verify all versions exist in the database
    db = datasette.get_internal_database()
    versions = (
        await db.execute(
            "SELECT version, content FROM datasette_agent_artifact_versions "
            "WHERE artifact_id = ? ORDER BY version",
            [artifact_id],
        )
    ).rows
    assert len(versions) == 3
    assert versions[0]["content"] == "v1 content"
    assert versions[1]["content"] == "v2 content"
    assert versions[2]["content"] == "v3 content"


@pytest.mark.asyncio
async def test_edit_preserves_type(datasette):
    """Editing a markdown artifact should re-render as markdown."""
    result = json.loads(
        await _create_artifact(datasette, None, content="# Hello\n\nWorld", type="markdown")
    )
    artifact_id = result["artifact_id"]

    replace_result = json.loads(
        await _artifact_str_replace(
            datasette, None,
            artifact_id=artifact_id,
            old_str="World",
            new_str="Universe",
        )
    )
    assert "_html" not in replace_result

    # Render should produce an iframe with markdown styling
    render_result = json.loads(
        await _artifact_render(datasette, None, artifact_id=artifact_id)
    )
    assert "_html" in render_result
    assert "<iframe" in render_result["_html"]

    view_result = json.loads(
        await _artifact_view(datasette, None, artifact_id=artifact_id)
    )
    assert view_result["type"] == "markdown"
    assert "Universe" in view_result["content"]


@pytest.mark.asyncio
async def test_edit_svg_artifact(datasette):
    svg = '<svg><rect width="100" height="100" fill="red"/></svg>'
    result = json.loads(
        await _create_artifact(datasette, None, content=svg, type="svg")
    )
    artifact_id = result["artifact_id"]

    replace_result = json.loads(
        await _artifact_str_replace(
            datasette, None,
            artifact_id=artifact_id,
            old_str='fill="red"',
            new_str='fill="blue"',
        )
    )
    assert replace_result["version"] == 2

    view_result = json.loads(
        await _artifact_view(datasette, None, artifact_id=artifact_id)
    )
    assert view_result["type"] == "svg"
    assert 'fill="blue"' in view_result["content"]


@pytest.mark.asyncio
async def test_artifact_render(datasette):
    result = json.loads(
        await _create_artifact(datasette, None, content="<h1>Hello</h1>", type="html")
    )
    artifact_id = result["artifact_id"]

    render_result = json.loads(
        await _artifact_render(datasette, None, artifact_id=artifact_id)
    )
    assert "_html" in render_result
    assert "<iframe" in render_result["_html"]
    assert render_result["artifact_id"] == artifact_id


@pytest.mark.asyncio
async def test_artifact_render_not_found(datasette):
    await ensure_tables(datasette)
    render_result = json.loads(
        await _artifact_render(datasette, None, artifact_id="nonexistent")
    )
    assert "error" in render_result


@pytest.mark.asyncio
async def test_edit_then_render(datasette):
    """Edit tools don't render; artifact_render does."""
    result = json.loads(
        await _create_artifact(datasette, None, content="<p>old</p>", type="html")
    )
    artifact_id = result["artifact_id"]

    # Edit without rendering
    r1 = json.loads(
        await _artifact_str_replace(datasette, None, artifact_id=artifact_id, old_str="old", new_str="new")
    )
    assert "_html" not in r1

    # Render shows updated content
    render = json.loads(
        await _artifact_render(datasette, None, artifact_id=artifact_id)
    )
    assert "_html" in render
    assert "&lt;p&gt;new&lt;/p&gt;" in render["_html"]


@pytest.mark.asyncio
async def test_artifact_edit_batch(datasette):
    """Multiple edits applied in one call, one version, one render."""
    result = json.loads(
        await _create_artifact(datasette, None, content="<h1>Hello</h1>\n<p>World</p>\n", type="html")
    )
    artifact_id = result["artifact_id"]

    edit_result = json.loads(
        await _artifact_edit(
            datasette, None,
            artifact_id=artifact_id,
            edits=[
                {"operation": "str_replace", "old_str": "Hello", "new_str": "Goodbye"},
                {"operation": "str_replace", "old_str": "World", "new_str": "Universe"},
            ],
        )
    )
    assert edit_result["edits_applied"] == 2
    assert edit_result["version"] == 2
    assert "_html" in edit_result

    # Verify both edits applied
    view = json.loads(await _artifact_view(datasette, None, artifact_id=artifact_id))
    assert "Goodbye" in view["content"]
    assert "Universe" in view["content"]
    assert "Hello" not in view["content"]
    assert "World" not in view["content"]


@pytest.mark.asyncio
async def test_artifact_edit_with_insert(datasette):
    result = json.loads(
        await _create_artifact(datasette, None, content="line1\nline2\n", type="html")
    )
    artifact_id = result["artifact_id"]

    edit_result = json.loads(
        await _artifact_edit(
            datasette, None,
            artifact_id=artifact_id,
            edits=[
                {"operation": "str_replace", "old_str": "line1", "new_str": "first"},
                {"operation": "insert", "insert_line": 1, "insert_text": "middle\n"},
            ],
        )
    )
    assert edit_result["edits_applied"] == 2
    assert edit_result["version"] == 2

    view = json.loads(await _artifact_view(datasette, None, artifact_id=artifact_id))
    assert "1:\tfirst" in view["content"]
    assert "2:\tmiddle" in view["content"]
    assert "3:\tline2" in view["content"]


@pytest.mark.asyncio
async def test_artifact_edit_partial_failure(datasette):
    """If an edit fails, earlier edits are NOT saved."""
    result = json.loads(
        await _create_artifact(datasette, None, content="<p>hello</p>", type="html")
    )
    artifact_id = result["artifact_id"]

    edit_result = json.loads(
        await _artifact_edit(
            datasette, None,
            artifact_id=artifact_id,
            edits=[
                {"operation": "str_replace", "old_str": "hello", "new_str": "bye"},
                {"operation": "str_replace", "old_str": "nonexistent", "new_str": "x"},
            ],
        )
    )
    assert "error" in edit_result
    assert "Edit #2 failed" in edit_result["error"]
    assert edit_result["applied"] == ["str_replace #1: OK"]

    # Content should be unchanged (no version saved)
    view = json.loads(await _artifact_view(datasette, None, artifact_id=artifact_id))
    assert "hello" in view["content"]


@pytest.mark.asyncio
async def test_artifact_edit_unknown_operation(datasette):
    result = json.loads(
        await _create_artifact(datasette, None, content="test", type="html")
    )
    artifact_id = result["artifact_id"]

    edit_result = json.loads(
        await _artifact_edit(
            datasette, None,
            artifact_id=artifact_id,
            edits=[{"operation": "delete"}],
        )
    )
    assert "error" in edit_result
    assert "unknown operation" in edit_result["error"]

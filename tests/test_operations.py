import pytest

from datasette_agent_edit import (
    EditApplyError,
    EditError,
    apply_edits,
    insert,
    str_replace,
    view_lines,
)


def test_view_lines_basic():
    content = "line one\nline two\nline three\n"
    assert view_lines(content) == "1:\tline one\n2:\tline two\n3:\tline three"


def test_view_lines_range():
    assert view_lines("a\nb\nc\nd\ne\n", "2,4") == "2:\tb\n3:\tc\n4:\td"


def test_view_lines_range_to_end():
    assert view_lines("a\nb\nc\nd\ne\n", "3,-1") == "3:\tc\n4:\td\n5:\te"


def test_str_replace_basic():
    assert str_replace("hello world", "hello", "goodbye") == "goodbye world"


def test_str_replace_not_found():
    with pytest.raises(EditError, match="not found"):
        str_replace("hello world", "xyz", "abc")


def test_str_replace_ambiguous():
    with pytest.raises(ValueError, match="appears 2 times"):
        str_replace("hello hello", "hello", "bye")


def test_insert_basic():
    assert insert("line1\nline2\n", 1, "new\n") == "line1\nnew\nline2\n"


def test_insert_beginning():
    assert insert("line1\nline2\n", 0, "new\n") == "new\nline1\nline2\n"


def test_insert_end():
    assert insert("line1\nline2\n", 2, "new\n") == "line1\nline2\nnew\n"


def test_apply_edits_sequential():
    content, applied = apply_edits(
        "<h1>Hello</h1>\n<p>World</p>\n",
        [
            {"operation": "str_replace", "old_str": "Hello", "new_str": "Goodbye"},
            {"operation": "insert", "insert_line": 1, "insert_text": "middle\n"},
        ],
    )
    assert "Goodbye" in content
    assert "middle" in content
    assert applied == ["str_replace #1: OK", "insert #2: OK"]


def test_apply_edits_partial_failure():
    with pytest.raises(EditApplyError) as exc:
        apply_edits(
            "<p>hello</p>",
            [
                {"operation": "str_replace", "old_str": "hello", "new_str": "bye"},
                {"operation": "str_replace", "old_str": "nope", "new_str": "x"},
            ],
        )
    assert exc.value.index == 1
    assert exc.value.applied == ["str_replace #1: OK"]


def test_apply_edits_unknown_operation():
    with pytest.raises(EditError, match="unknown operation"):
        apply_edits("test", [{"operation": "delete"}])


# --- Line handling ---


def test_view_lines_keeps_trailing_whitespace():
    assert view_lines("a  \n\tb\t\n") == "1:\ta  \n2:\t\tb\t"


def test_view_lines_only_breaks_on_newlines():
    assert view_lines("a\x0cb c\r\nd\n") == "1:\ta\x0cb c\n2:\td"


@pytest.mark.parametrize(
    "view_range,match",
    [
        ("2", "invalid view_range"),
        ("x,y", "invalid view_range"),
        ("1,2,3", "invalid view_range"),
        ("0,2", "start must be 1 or greater"),
        ("3,2", "before start"),
        ("9,-1", "past the end"),
    ],
)
def test_view_lines_bad_range(view_range, match):
    with pytest.raises(EditError, match=match):
        view_lines("a\nb\nc\n", view_range)


def test_view_lines_range_on_empty_content():
    assert view_lines("", "1,-1") == ""


def test_insert_adds_newline_before_following_line():
    assert insert("a\nb\n", 1, "X") == "a\nX\nb\n"


def test_insert_after_last_line_without_trailing_newline():
    assert insert("a\nb", 2, "c") == "a\nb\nc"


def test_insert_matches_crlf_content():
    assert insert("a\r\nb\r\n", 1, "X") == "a\r\nX\r\nb\r\n"


def test_insert_into_empty_content():
    assert insert("", 0, "X") == "X"


@pytest.mark.parametrize("insert_line", [-1, 3, 99, "1", 1.0, True, None])
def test_insert_rejects_bad_line(insert_line):
    with pytest.raises(EditError, match="insert_line"):
        insert("a\nb\n", insert_line, "X\n")


# --- Malformed edits ---


@pytest.mark.parametrize(
    "edit,match",
    [
        ({"operation": "str_replace", "old_str": "a"}, "missing required.*new_str"),
        ({"operation": "insert", "insert_text": "x"}, "missing required.*insert_line"),
        ({"operation": "str_replace", "old_str": "a", "new_str": 1}, "must be strings"),
        ({"operation": "insert", "insert_line": "1", "insert_text": "x"}, "integer"),
        ("str_replace", "must be an object"),
    ],
)
def test_apply_edits_malformed_edit(edit, match):
    with pytest.raises(EditApplyError, match=match) as exc:
        apply_edits("abc", [edit])
    assert exc.value.index == 0


def test_apply_edits_rejects_non_list():
    with pytest.raises(EditError, match="array"):
        apply_edits("abc", {"operation": "insert"})

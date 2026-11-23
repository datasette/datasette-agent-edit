"""Pure, storage-agnostic text-editing primitives.

These functions operate on plain strings and never touch a storage backend,
an event loop, or a Datasette instance. They are the in-memory "surgery" that
a backend's atomic ``edit()`` critical section applies to content. Keeping
them pure is what lets the same editing behaviour run unchanged inside a
SQLite write thread, a disk file lock, or an S3/GitHub compare-and-set loop.
"""


class EditError(ValueError):
    """Raised when an edit cannot be applied to the given content.

    Subclasses ``ValueError`` so existing ``except ValueError`` handlers (and
    the test-suite's ``pytest.raises(ValueError, ...)`` expectations) keep
    working.
    """


class EditApplyError(EditError):
    """Raised by :func:`apply_edits` when one edit in a batch fails.

    Carries the list of human-readable descriptions of the edits that did
    succeed before the failure, plus the zero-based index of the edit that
    failed, so callers can report partial progress without re-deriving it.
    """

    def __init__(self, message, applied, index):
        super().__init__(message)
        self.applied = applied
        self.index = index


def _split_lines(content):
    """Split ``content`` into lines, keeping line endings.

    Only a line feed (so also CRLF) ends a line. ``str.splitlines`` also
    breaks on form feeds and Unicode separators, which would make line
    numbers disagree with every editor.
    """
    pieces = content.split("\n")
    lines = [piece + "\n" for piece in pieces[:-1]]
    if pieces[-1]:
        lines.append(pieces[-1])
    return lines


def _parse_view_range(view_range, total_lines):
    """Return ``(start, end)`` for a ``"start,end"`` string, or raise."""
    parts = [part.strip() for part in str(view_range).split(",")]
    try:
        if len(parts) != 2:
            raise ValueError
        start, end = int(parts[0]), int(parts[1])
    except ValueError:
        raise EditError(
            f'invalid view_range {view_range!r}; use "start,end" '
            "(1-indexed, -1 for end-of-file)"
        ) from None
    if start < 1:
        raise EditError(f"view_range start must be 1 or greater, got {start}")
    if start > max(total_lines, 1):
        raise EditError(
            f"view_range start ({start}) is past the end; "
            f"content has {total_lines} lines"
        )
    if end == -1:
        end = max(total_lines, start)
    if end < start:
        raise EditError(
            f"view_range end ({end}) is before start ({start}); "
            f"content has {total_lines} lines"
        )
    return start, end


def view_lines(content, view_range=""):
    """Format ``content`` as numbered lines, optionally sliced to a range.

    ``view_range`` is a string ``"start,end"`` (1-indexed, inclusive). An end
    of ``-1`` means end-of-file. An empty string returns every line. A
    malformed or impossible range raises :class:`EditError`. Trailing
    whitespace is preserved so the output can be copied into ``str_replace``.
    """
    all_lines = _split_lines(content)

    if view_range:
        start, end = _parse_view_range(view_range, len(all_lines))
        lines = all_lines[start - 1 : end]
        start_num = start
    else:
        lines = all_lines
        start_num = 1

    numbered = []
    for i, line in enumerate(lines, start=start_num):
        text = line.rstrip("\r\n")
        numbered.append(f"{i}:\t{text}")
    return "\n".join(numbered)


def str_replace(content, old_str, new_str):
    """Replace exactly one occurrence of ``old_str`` in ``content``.

    Raises :class:`EditError` if ``old_str`` is absent or appears more than
    once (the caller should add surrounding context to disambiguate).
    """
    count = content.count(old_str)
    if count == 0:
        raise EditError("old_str not found in content")
    if count > 1:
        raise EditError(f"old_str appears {count} times in content; must be unique")
    return content.replace(old_str, new_str, 1)


def insert(content, insert_line, insert_text):
    """Insert ``insert_text`` as whole lines after line number ``insert_line``.

    ``insert_line=0`` inserts at the very beginning of the content, and the
    number of lines in the content appends at the end; anything outside that
    range raises :class:`EditError`. Line breaks are added where needed so the
    inserted text never runs into the line before or after it.
    """
    if isinstance(insert_line, bool) or not isinstance(insert_line, int):
        raise EditError(f"insert_line must be an integer, got {insert_line!r}")
    if not isinstance(insert_text, str):
        raise EditError("insert_text must be a string")
    lines = _split_lines(content)
    if not 0 <= insert_line <= len(lines):
        raise EditError(
            f"insert_line {insert_line} is out of range; content has "
            f"{len(lines)} lines (use 0 to {len(lines)})"
        )
    if not insert_text:
        return content
    before, after = lines[:insert_line], lines[insert_line:]
    newline = "\r\n" if lines and lines[0].endswith("\r\n") else "\n"
    if before and not before[-1].endswith("\n"):
        before[-1] += newline
    if after and not insert_text.endswith("\n"):
        insert_text += newline
    return "".join(before) + insert_text + "".join(after)


def apply_edit(content, edit):
    """Apply a single ``{"operation": ..., ...}`` edit dict to ``content``.

    Supported operations:

    - ``str_replace``: keys ``old_str``, ``new_str``
    - ``insert``: keys ``insert_line``, ``insert_text``
    """
    op = edit.get("operation")
    if op == "str_replace":
        return str_replace(content, edit["old_str"], edit["new_str"])
    if op == "insert":
        return insert(content, edit["insert_line"], edit["insert_text"])
    raise EditError(
        f"unknown operation {op!r}. Use 'str_replace' or 'insert'."
    )


def apply_edits(content, edits):
    """Apply a sequence of edit dicts, returning ``(new_content, applied)``.

    Edits are applied in order, so each edit sees the result of the previous
    one. If any edit fails, an :class:`EditApplyError` is raised before any
    result is returned, so callers can treat the batch as all-or-nothing.
    ``applied`` is a list of strings like ``"str_replace #1: OK"``.
    """
    applied = []
    for i, edit in enumerate(edits):
        op = edit.get("operation")
        try:
            content = apply_edit(content, edit)
        except EditError as e:
            raise EditApplyError(str(e), applied=applied, index=i) from e
        applied.append(f"{op} #{i + 1}: OK")
    return content, applied

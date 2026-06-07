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


def view_lines(content, view_range=""):
    """Format ``content`` as numbered lines, optionally sliced to a range.

    ``view_range`` is a string ``"start,end"`` (1-indexed, inclusive). An end
    of ``-1`` means end-of-file. An empty string returns every line.
    """
    all_lines = content.splitlines(True)
    total_lines = len(all_lines)

    if view_range:
        parts = view_range.split(",")
        start = int(parts[0])
        end = int(parts[1])
        if end == -1:
            end = total_lines
        lines = all_lines[start - 1 : end]
        start_num = start
    else:
        lines = all_lines
        start_num = 1

    numbered = []
    for i, line in enumerate(lines, start=start_num):
        numbered.append(f"{i}:\t{line.rstrip()}")
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
    """Insert ``insert_text`` after line number ``insert_line``.

    ``insert_line=0`` inserts at the very beginning of the content.
    """
    lines = content.splitlines(True)
    insert_pieces = insert_text.splitlines(True)
    new_lines = lines[:insert_line] + insert_pieces + lines[insert_line:]
    return "".join(new_lines)


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

"""Layer 3: turn an :class:`~datasette_agent_edit.store.EditStore` into a set
of Datasette Agent tools with a single, consistent JSON envelope.

The toolset is storage- *and* presentation-agnostic. Two extension points
absorb everything plugin-specific:

- ``id_codec`` maps between the internal ``ref`` and the id the model sees
  (e.g. the ``artifact-`` prefix).
- ``render`` is an optional hook ``(Editable) -> dict`` (sync or async) whose
  result is merged into the tool output, letting a plugin inject e.g. an
  ``_html`` preview. With no hook, no ``*_render`` tool is registered and
  edits simply report status — which is exactly what a plain file editor
  wants.

Handler methods (``view``, ``str_replace``, ``insert``, ``edit``, ``render``)
each return a JSON string and are directly callable for testing. :meth:`tools`
wraps them as ``AgentTool`` instances; that import is deferred so the rest of
the package works without ``datasette-agent`` installed.
"""

import inspect
import json

from . import operations
from .operations import EditApplyError, EditError
from .store import IdCodec, NotFound


class EditToolset:
    def __init__(
        self,
        store,
        *,
        name_prefix="file",
        id_field="id",
        id_codec=None,
        render=None,
        descriptions=None,
    ):
        self.store = store
        self.name_prefix = name_prefix
        self.id_field = id_field
        self.codec = id_codec or IdCodec()
        self.render_hook = render
        self.descriptions = {**self._default_descriptions(), **(descriptions or {})}

    # ------------------------------------------------------------------ #
    # Naming / formatting helpers
    # ------------------------------------------------------------------ #
    def _name(self, op):
        return f"{self.name_prefix}_{op}"

    def _ok(self, external_id, **extra):
        return json.dumps({self.id_field: external_id, **extra})

    def _err(self, message, **extra):
        return json.dumps({"error": message, **extra})

    async def _render_payload(self, editable):
        if self.render_hook is None:
            return {}
        result = self.render_hook(editable)
        if inspect.isawaitable(result):
            result = await result
        return result or {}

    async def _read(self, external_id):
        """Return ``(editable, None)`` or ``(None, error_json)``."""
        try:
            ref = self.codec.to_internal(external_id)
            return await self.store.read(ref), None
        except NotFound:
            return None, self._err(f"Content not found: {external_id}")
        except ValueError as e:
            return None, self._err(str(e))

    async def _edit(self, external_id, transform):
        """Run ``transform`` through the store.

        Returns ``(editable, None)`` or ``(None, error_json)``. Every
        ``ValueError`` -- a failed edit, or a store rejecting the ref or the
        content -- becomes an error envelope rather than an exception.
        """
        try:
            ref = self.codec.to_internal(external_id)
            return await self.store.edit(ref, transform), None
        except NotFound:
            return None, self._err(f"Content not found: {external_id}")
        except EditApplyError as e:
            return None, self._err(
                f"Edit #{e.index + 1} failed: {e}", applied=e.applied
            )
        except ValueError as e:
            return None, self._err(str(e))

    # ------------------------------------------------------------------ #
    # Handlers (directly callable; each returns a JSON string)
    # ------------------------------------------------------------------ #
    async def view(self, external_id, view_range=""):
        editable, error = await self._read(external_id)
        if error:
            return error
        try:
            content = operations.view_lines(editable.content, view_range)
        except EditError as e:
            return self._err(str(e))
        return self._ok(
            external_id,
            version=editable.version,
            metadata=editable.metadata,
            content=content,
        )

    async def str_replace(self, external_id, old_str, new_str):
        def transform(content):
            return operations.str_replace(content, old_str, new_str)

        editable, error = await self._edit(external_id, transform)
        if error:
            return error
        return self._ok(
            external_id,
            version=editable.version,
            status=(
                f"Replacement applied. Call {self._name('render')} to display "
                "the updated content."
                if self.render_hook
                else "Replacement applied."
            ),
        )

    async def insert(self, external_id, insert_line, insert_text):
        def transform(content):
            return operations.insert(content, insert_line, insert_text)

        editable, error = await self._edit(external_id, transform)
        if error:
            return error
        return self._ok(
            external_id,
            version=editable.version,
            status=(
                f"Inserted text after line {insert_line}. Call "
                f"{self._name('render')} to display the updated content."
                if self.render_hook
                else f"Inserted text after line {insert_line}."
            ),
        )

    async def edit(self, external_id, edits):
        if not isinstance(edits, (list, tuple)) or not edits:
            return self._err("edits must be a non-empty array of edit objects")
        holder = {}

        def transform(content):
            new_content, applied = operations.apply_edits(content, edits)
            holder["applied"] = applied
            return new_content

        editable, error = await self._edit(external_id, transform)
        if error:
            return error

        applied = holder.get("applied", [])
        payload = await self._render_payload(editable)
        return self._ok(
            external_id,
            version=editable.version,
            edits_applied=len(applied),
            status=f"Applied {len(applied)} edits.",
            **payload,
        )

    async def render(self, external_id):
        if self.render_hook is None:
            return self._err("This toolset has no render hook configured.")
        editable, error = await self._read(external_id)
        if error:
            return error
        try:
            payload = await self._render_payload(editable)
        except Exception as e:
            return self._err(f"Rendering failed: {type(e).__name__}: {e}")
        return self._ok(external_id, status="Rendered.", **payload)

    # ------------------------------------------------------------------ #
    # AgentTool construction
    # ------------------------------------------------------------------ #
    def _id_arg(self):
        return {
            self.id_field: {
                "type": "string",
                "description": "The id of the content to operate on",
            }
        }

    def tools(self):
        """Return the list of ``AgentTool`` instances for this toolset."""
        from datasette_agent.tools import AgentTool

        idf = self.id_field

        def adapt(handler):
            async def fn(datasette, actor, **kwargs):
                external_id = kwargs.pop(idf)
                return await handler(external_id, **kwargs)

            return fn

        tools = [
            AgentTool(
                name=self._name("view"),
                description=self.descriptions["view"],
                input_schema={
                    "type": "object",
                    "properties": {
                        **self._id_arg(),
                        "view_range": {
                            "type": "string",
                            "description": 'Optional line range "start,end" '
                            "(1-indexed, -1 for end-of-file)",
                        },
                    },
                    "required": [idf],
                },
                fn=adapt(self.view),
            ),
            AgentTool(
                name=self._name("str_replace"),
                description=self.descriptions["str_replace"],
                input_schema={
                    "type": "object",
                    "properties": {
                        **self._id_arg(),
                        "old_str": {
                            "type": "string",
                            "description": "Exact text to find (must appear once)",
                        },
                        "new_str": {
                            "type": "string",
                            "description": "Replacement text",
                        },
                    },
                    "required": [idf, "old_str", "new_str"],
                },
                fn=adapt(self.str_replace),
            ),
            AgentTool(
                name=self._name("insert"),
                description=self.descriptions["insert"],
                input_schema={
                    "type": "object",
                    "properties": {
                        **self._id_arg(),
                        "insert_line": {
                            "type": "integer",
                            "description": "Line number after which to insert "
                            "(0 for beginning)",
                        },
                        "insert_text": {
                            "type": "string",
                            "description": "Text to insert as whole lines",
                        },
                    },
                    "required": [idf, "insert_line", "insert_text"],
                },
                fn=adapt(self.insert),
            ),
            AgentTool(
                name=self._name("edit"),
                description=self.descriptions["edit"],
                input_schema={
                    "type": "object",
                    "properties": {
                        **self._id_arg(),
                        "edits": {
                            "type": "array",
                            "description": "Edit operations applied sequentially",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "operation": {
                                        "type": "string",
                                        "enum": ["str_replace", "insert"],
                                    },
                                    "old_str": {"type": "string"},
                                    "new_str": {"type": "string"},
                                    "insert_line": {"type": "integer"},
                                    "insert_text": {"type": "string"},
                                },
                                "required": ["operation"],
                                "description": "str_replace needs old_str and "
                                "new_str; insert needs insert_line and "
                                "insert_text",
                            },
                        },
                    },
                    "required": [idf, "edits"],
                },
                fn=adapt(self.edit),
            ),
        ]

        if self.render_hook is not None:
            tools.append(
                AgentTool(
                    name=self._name("render"),
                    description=self.descriptions["render"],
                    input_schema={
                        "type": "object",
                        "properties": self._id_arg(),
                        "required": [idf],
                    },
                    fn=adapt(self.render),
                )
            )
        return tools

    # ------------------------------------------------------------------ #
    def _default_descriptions(self):
        p = self.name_prefix
        renders = self.render_hook is not None
        edit_note = (
            f" The {p}_edit tool renders once after applying all edits; the "
            f"single-edit tools do not render, so call {p}_render when done."
            if renders
            else ""
        )
        return {
            "view": (
                f"View the current content with line numbers. Use before "
                f"editing with {p}_str_replace or {p}_insert. Optionally pass "
                'view_range as "start,end" (1-indexed, -1 for end-of-file).'
            ),
            "str_replace": (
                "Replace an exact string. old_str must appear exactly once "
                "(add surrounding context to disambiguate)." + edit_note
            ),
            "insert": (
                "Insert text as new lines after a given line number (0 for "
                "the beginning of the content)." + edit_note
            ),
            "edit": (
                "Apply multiple edits in one atomic operation. Pass an array "
                'of {"operation": "str_replace"|"insert", ...} objects applied '
                "sequentially; if any edit fails, none are saved."
            ),
            "render": (
                "Render the current content and display it. Call once after "
                "all edits are complete."
            ),
        }

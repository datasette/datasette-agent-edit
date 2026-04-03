import html as html_module
import json


from datasette import hookimpl
from ulid import ULID

ARTIFACT_TYPES = ["html", "markdown", "svg"]
ARTIFACT_ID_PREFIX = "artifact-"


def _external_id(ulid_str):
    """Convert a raw ULID to the external artifact ID the LLM sees."""
    return ARTIFACT_ID_PREFIX + ulid_str


def _internal_id(external_id):
    """Strip the artifact- prefix to get the raw ULID for database lookups."""
    if external_id.startswith(ARTIFACT_ID_PREFIX):
        return external_id[len(ARTIFACT_ID_PREFIX):]
    return external_id

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS datasette_agent_artifacts (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    type TEXT NOT NULL DEFAULT 'html',
    current_version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS datasette_agent_artifact_versions (
    artifact_id TEXT NOT NULL REFERENCES datasette_agent_artifacts(id),
    version INTEGER NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (artifact_id, version)
);
"""


async def ensure_tables(datasette):
    db = datasette.get_internal_database()
    await db.execute_write_script(SCHEMA_SQL)


CREATE_TOOL_DESCRIPTION = """\
Create an artifact that will be rendered inline. Supported types:

- "html": A single-page HTML+JS+CSS application rendered in a sandboxed \
iframe. Use for dashboards, data visualizations, interactive tools, \
calculators, games, or any rich interactive UI. Pass a complete HTML \
document starting with <!DOCTYPE html> with inline <style> and <script> \
tags. Can load external libraries from CDNs.

- "markdown": A markdown document rendered as HTML. Use for reports, \
documentation, notes, or any structured text content. Supports standard \
markdown syntax.

- "svg": An SVG image. Use for diagrams, illustrations, charts, or any \
vector graphics. Pass the complete SVG markup starting with <svg>.

Design clean, polished output with good typography, spacing, and color.

SQL QUERY BRIDGE (html type only): Your HTML artifact can query the \
Datasette database. All queries are READ-ONLY.

IMPORTANT: Before embedding SQL in an artifact, first use the sql_query \
tool to test and prototype your queries. Use list_databases_and_tables \
to discover available database names and tables.

To execute a SQL query from inside your HTML artifact, call:
  parent.postMessage({database: 'my_database', sql: 'SELECT ...'}, '*');

Listen for results with:
  window.addEventListener('message', function(e) {
    if (e.data && e.data.results) {
      // e.data.results is an array of row objects
    }
    if (e.data && e.data.error) {
      // e.data.error is an error message string
    }
  });

You can make multiple SQL queries. Include a requestId in your messages \
if you need to distinguish responses from different queries.

The tool returns an artifact_id. Use the artifact editing tools \
(artifact_view, artifact_str_replace, artifact_insert) to modify \
existing artifacts instead of recreating them from scratch."""

RESIZE_SCRIPT = (
    "<script>(function(){"
    "var lastH=0;new ResizeObserver(()=>{"
    "var h=document.documentElement.scrollHeight;"
    "if(h!==lastH){lastH=h;parent.postMessage({htmlPreviewHeight:h},\"*\");}"
    "}).observe(document.documentElement);"
    "})();</script>"
)

BRIDGE_SCRIPT_TAG = (
    '<script src="/-/static-plugins/datasette-agent-artifacts/artifact-bridge.js"></script>'
)

MARKDOWN_TEMPLATE = """\
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; line-height: 1.6; color: #1f2328; max-width: 800px; margin: 0 auto; padding: 16px; }}
h1, h2, h3, h4, h5, h6 {{ margin-top: 24px; margin-bottom: 16px; font-weight: 600; line-height: 1.25; }}
h1 {{ font-size: 2em; border-bottom: 1px solid #d1d9e0; padding-bottom: .3em; }}
h2 {{ font-size: 1.5em; border-bottom: 1px solid #d1d9e0; padding-bottom: .3em; }}
code {{ background: #eff1f3; padding: .2em .4em; border-radius: 6px; font-size: 85%; }}
pre {{ background: #f6f8fa; padding: 16px; border-radius: 6px; overflow: auto; }}
pre code {{ background: none; padding: 0; }}
blockquote {{ border-left: .25em solid #d1d9e0; padding: 0 1em; color: #656d76; margin: 0; }}
table {{ border-collapse: collapse; width: 100%; }}
th, td {{ border: 1px solid #d1d9e0; padding: 6px 13px; }}
th {{ background: #f6f8fa; font-weight: 600; }}
img {{ max-width: 100%; }}
a {{ color: #0969da; }}
</style>
</head>
<body><div id="content"></div>
<script>document.getElementById("content").innerHTML = marked.parse({markdown_json});</script>
</body>
</html>"""


def _render_markdown(content):
    """Build an HTML page that renders markdown client-side using marked.js."""
    markdown_json = json.dumps(content)
    return MARKDOWN_TEMPLATE.format(markdown_json=markdown_json)


def _build_iframe_html(content, artifact_type="html"):
    """Build the iframe HTML for any artifact type."""
    if artifact_type == "markdown":
        inner_html = _render_markdown(content)
    elif artifact_type == "svg":
        inner_html = (
            "<!DOCTYPE html><html><head><meta charset='UTF-8'>"
            "<style>body{margin:0;display:flex;justify-content:center;align-items:center;min-height:100vh;}</style>"
            "</head><body>" + content + "</body></html>"
        )
    else:
        inner_html = content

    full_inner_html = inner_html + RESIZE_SCRIPT
    escaped_html = html_module.escape(full_inner_html, quote=True)
    iframe_tag = (
        f'{BRIDGE_SCRIPT_TAG}\n'
        f'<iframe class="artifact-iframe" srcdoc="{escaped_html}" '
        f'sandbox="allow-scripts" '
        f'style="width: 100%; height: 401px; display: block"></iframe>'
    )
    return iframe_tag


def _view_lines(content, view_range=""):
    """Format content as numbered lines, optionally sliced to a range."""
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


def _str_replace_content(content, old_str, new_str):
    """Replace exactly one occurrence of old_str in content."""
    count = content.count(old_str)
    if count == 0:
        raise ValueError("old_str not found in artifact")
    if count > 1:
        raise ValueError(
            f"old_str appears {count} times in artifact; must be unique"
        )
    return content.replace(old_str, new_str, 1)


def _insert_content(content, insert_line, insert_text):
    """Insert text after a given line number."""
    lines = content.splitlines(True)
    insert_lines = insert_text.splitlines(True)
    new_lines = lines[:insert_line] + insert_lines + lines[insert_line:]
    return "".join(new_lines)


async def _get_artifact(datasette, artifact_id):
    """Fetch the current version's content and type for an artifact."""
    db = datasette.get_internal_database()
    row = (
        await db.execute(
            "SELECT a.type, av.content FROM datasette_agent_artifact_versions av "
            "JOIN datasette_agent_artifacts a ON a.id = av.artifact_id "
            "WHERE av.artifact_id = ? AND av.version = a.current_version",
            [artifact_id],
        )
    ).first()
    if not row:
        raise ValueError(f"Artifact not found: {artifact_id}")
    return row["type"], row["content"]


async def _save_new_version(datasette, artifact_id, new_content):
    """Save a new version of an artifact and return the version number."""
    from datetime import datetime, timezone

    db = datasette.get_internal_database()
    now = datetime.now(timezone.utc).isoformat()

    def _write(conn):
        row = conn.execute(
            "SELECT current_version FROM datasette_agent_artifacts WHERE id = ?",
            [artifact_id],
        ).fetchone()
        if not row:
            raise ValueError(f"Artifact not found: {artifact_id}")
        new_version = row[0] + 1
        conn.execute(
            "INSERT INTO datasette_agent_artifact_versions "
            "(artifact_id, version, content, created_at) VALUES (?, ?, ?, ?)",
            [artifact_id, new_version, new_content, now],
        )
        conn.execute(
            "UPDATE datasette_agent_artifacts "
            "SET current_version = ?, updated_at = ? WHERE id = ?",
            [new_version, now, artifact_id],
        )
        return new_version

    return await db.execute_write_fn(_write)


async def _create_artifact(datasette, actor, content, type="html"):
    from datetime import datetime, timezone

    if type not in ARTIFACT_TYPES:
        return json.dumps({"error": f"Invalid type: {type}. Must be one of: {', '.join(ARTIFACT_TYPES)}"})

    await ensure_tables(datasette)
    db = datasette.get_internal_database()
    now = datetime.now(timezone.utc).isoformat()

    ulid_str = str(ULID())
    ext_id = _external_id(ulid_str)

    await db.execute_write(
        "INSERT INTO datasette_agent_artifacts "
        "(id, conversation_id, type, current_version, created_at, updated_at) "
        "VALUES (?, ?, ?, 1, ?, ?)",
        [ulid_str, "", type, now, now],
    )
    await db.execute_write(
        "INSERT INTO datasette_agent_artifact_versions "
        "(artifact_id, version, content, created_at) VALUES (?, 1, ?, ?)",
        [ulid_str, content, now],
    )

    result_html = _build_iframe_html(content, artifact_type=type)
    return json.dumps(
        {
            "_html": result_html,
            "artifact_id": ext_id,
            "type": type,
            "status": "Artifact created successfully",
            "tip": f"Use ID {ext_id} in subsequent tool calls to edit this artifact",
        }
    )


async def _artifact_view(datasette, actor, artifact_id, view_range=""):
    await ensure_tables(datasette)
    db_id = _internal_id(artifact_id)
    try:
        artifact_type, current_content = await _get_artifact(datasette, db_id)
    except ValueError as e:
        return json.dumps({"error": str(e)})
    return json.dumps(
        {
            "artifact_id": artifact_id,
            "type": artifact_type,
            "content": _view_lines(current_content, view_range),
        }
    )


async def _artifact_str_replace(datasette, actor, artifact_id, old_str, new_str):
    await ensure_tables(datasette)
    db_id = _internal_id(artifact_id)
    try:
        artifact_type, current_content = await _get_artifact(datasette, db_id)
        new_content = _str_replace_content(current_content, old_str, new_str)
        new_version = await _save_new_version(datasette, db_id, new_content)
    except ValueError as e:
        return json.dumps({"error": str(e)})

    return json.dumps(
        {
            "artifact_id": artifact_id,
            "version": new_version,
            "status": "Replacement applied. Call artifact_render to display the updated artifact.",
        }
    )


async def _artifact_insert(datasette, actor, artifact_id, insert_line, insert_text):
    await ensure_tables(datasette)
    db_id = _internal_id(artifact_id)
    try:
        artifact_type, current_content = await _get_artifact(datasette, db_id)
        new_content = _insert_content(current_content, insert_line, insert_text)
        new_version = await _save_new_version(datasette, db_id, new_content)
    except ValueError as e:
        return json.dumps({"error": str(e)})

    return json.dumps(
        {
            "artifact_id": artifact_id,
            "version": new_version,
            "status": f"Inserted text after line {insert_line}. Call artifact_render to display the updated artifact.",
        }
    )


async def _artifact_edit(datasette, actor, artifact_id, edits):
    """Apply multiple edits in a single version bump and render the result."""
    await ensure_tables(datasette)
    db_id = _internal_id(artifact_id)
    try:
        artifact_type, current_content = await _get_artifact(datasette, db_id)
    except ValueError as e:
        return json.dumps({"error": str(e)})

    applied = []
    for i, edit in enumerate(edits):
        op = edit.get("operation")
        try:
            if op == "str_replace":
                current_content = _str_replace_content(
                    current_content, edit["old_str"], edit["new_str"]
                )
                applied.append(f"str_replace #{i + 1}: OK")
            elif op == "insert":
                current_content = _insert_content(
                    current_content, edit["insert_line"], edit["insert_text"]
                )
                applied.append(f"insert #{i + 1}: OK")
            else:
                return json.dumps(
                    {"error": f"Edit #{i + 1}: unknown operation '{op}'. Use 'str_replace' or 'insert'."}
                )
        except ValueError as e:
            return json.dumps({"error": f"Edit #{i + 1} failed: {e}", "applied": applied})

    new_version = await _save_new_version(datasette, db_id, current_content)
    result_html = _build_iframe_html(current_content, artifact_type=artifact_type)
    return json.dumps(
        {
            "_html": result_html,
            "artifact_id": artifact_id,
            "version": new_version,
            "edits_applied": len(applied),
            "status": f"Applied {len(applied)} edits and rendered artifact.",
        }
    )


async def _artifact_render(datasette, actor, artifact_id):
    await ensure_tables(datasette)
    db_id = _internal_id(artifact_id)
    try:
        artifact_type, current_content = await _get_artifact(datasette, db_id)
    except ValueError as e:
        return json.dumps({"error": str(e)})

    result_html = _build_iframe_html(current_content, artifact_type=artifact_type)
    return json.dumps(
        {
            "_html": result_html,
            "artifact_id": artifact_id,
            "status": "Artifact rendered",
        }
    )


ARTIFACT_VIEW_DESCRIPTION = """\
View the current source of an existing artifact with line numbers. \
Use this to inspect the artifact before making edits with \
artifact_str_replace or artifact_insert.

Optionally pass view_range as "start,end" (1-indexed) to see specific \
lines. Use -1 for end to mean end-of-file. Examples: "1,20" shows the \
first 20 lines, "50,-1" shows from line 50 to the end."""

ARTIFACT_STR_REPLACE_DESCRIPTION = """\
Replace an exact string in an existing artifact. The old_str must \
appear exactly once in the artifact (if it appears multiple times, provide \
more surrounding context to make it unique). A new version is created.

This tool does NOT re-render the artifact. Make all your edits first, \
then call artifact_render once when you are done to display the result.

Use artifact_view first to see the current content with line numbers, \
then use this tool to make targeted replacements."""

ARTIFACT_INSERT_DESCRIPTION = """\
Insert text after a given line number in an existing artifact. \
Use insert_line=0 to insert at the very beginning of the file. A new \
version is created.

This tool does NOT re-render the artifact. Make all your edits first, \
then call artifact_render once when you are done to display the result.

Use artifact_view first to see the current content with line numbers."""

ARTIFACT_EDIT_DESCRIPTION = """\
Apply multiple edits to an artifact in a single operation and render \
the result. This is more efficient than calling artifact_str_replace \
or artifact_insert multiple times — all edits are applied as one \
version and the artifact is rendered once.

Pass an array of edits. Each edit is an object with an "operation" \
field ("str_replace" or "insert") and the corresponding parameters:

For str_replace: {"operation": "str_replace", "old_str": "...", "new_str": "..."}
For insert: {"operation": "insert", "insert_line": N, "insert_text": "..."}

Edits are applied sequentially, so later edits see the result of \
earlier ones. If any edit fails, the remaining edits are skipped \
and no version is saved."""

ARTIFACT_RENDER_DESCRIPTION = """\
Render an existing artifact and display it to the user. Call this after \
you have finished making edits with artifact_str_replace or \
artifact_insert. Only call this once, after all edits are complete."""


@hookimpl
def register_agent_tools(datasette):
    from datasette_agent.tools import AgentTool

    return [
        AgentTool(
            name="create_artifact",
            description=CREATE_TOOL_DESCRIPTION,
            input_schema={
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "The artifact content: HTML document, markdown text, or SVG markup",
                    },
                    "type": {
                        "type": "string",
                        "enum": ARTIFACT_TYPES,
                        "description": "Artifact type: html (interactive app), markdown (document), or svg (vector image)",
                    },
                },
                "required": ["content", "type"],
            },
            fn=_create_artifact,
        ),
        AgentTool(
            name="artifact_view",
            description=ARTIFACT_VIEW_DESCRIPTION,
            input_schema={
                "type": "object",
                "properties": {
                    "artifact_id": {
                        "type": "string",
                        "description": "The artifact ID returned by create_artifact",
                    },
                    "view_range": {
                        "type": "string",
                        "description": 'Optional line range as "start,end" (1-indexed, -1 for end-of-file)',
                    },
                },
                "required": ["artifact_id"],
            },
            fn=_artifact_view,
        ),
        AgentTool(
            name="artifact_str_replace",
            description=ARTIFACT_STR_REPLACE_DESCRIPTION,
            input_schema={
                "type": "object",
                "properties": {
                    "artifact_id": {
                        "type": "string",
                        "description": "The artifact ID returned by create_artifact",
                    },
                    "old_str": {
                        "type": "string",
                        "description": "The exact text to find (must appear exactly once)",
                    },
                    "new_str": {
                        "type": "string",
                        "description": "The replacement text",
                    },
                },
                "required": ["artifact_id", "old_str", "new_str"],
            },
            fn=_artifact_str_replace,
        ),
        AgentTool(
            name="artifact_insert",
            description=ARTIFACT_INSERT_DESCRIPTION,
            input_schema={
                "type": "object",
                "properties": {
                    "artifact_id": {
                        "type": "string",
                        "description": "The artifact ID returned by create_artifact",
                    },
                    "insert_line": {
                        "type": "integer",
                        "description": "Line number after which to insert (0 for beginning of file)",
                    },
                    "insert_text": {
                        "type": "string",
                        "description": "The text to insert",
                    },
                },
                "required": ["artifact_id", "insert_line", "insert_text"],
            },
            fn=_artifact_insert,
        ),
        AgentTool(
            name="artifact_edit",
            description=ARTIFACT_EDIT_DESCRIPTION,
            input_schema={
                "type": "object",
                "properties": {
                    "artifact_id": {
                        "type": "string",
                        "description": "The artifact ID to edit",
                    },
                    "edits": {
                        "type": "array",
                        "description": "Array of edit operations to apply sequentially",
                        "items": {
                            "type": "object",
                            "properties": {
                                "operation": {
                                    "type": "string",
                                    "enum": ["str_replace", "insert"],
                                    "description": "The edit operation type",
                                },
                                "old_str": {
                                    "type": "string",
                                    "description": "For str_replace: the exact text to find",
                                },
                                "new_str": {
                                    "type": "string",
                                    "description": "For str_replace: the replacement text",
                                },
                                "insert_line": {
                                    "type": "integer",
                                    "description": "For insert: line number after which to insert",
                                },
                                "insert_text": {
                                    "type": "string",
                                    "description": "For insert: the text to insert",
                                },
                            },
                            "required": ["operation"],
                        },
                    },
                },
                "required": ["artifact_id", "edits"],
            },
            fn=_artifact_edit,
        ),
        AgentTool(
            name="artifact_render",
            description=ARTIFACT_RENDER_DESCRIPTION,
            input_schema={
                "type": "object",
                "properties": {
                    "artifact_id": {
                        "type": "string",
                        "description": "The artifact ID to render",
                    },
                },
                "required": ["artifact_id"],
            },
            fn=_artifact_render,
        ),
    ]

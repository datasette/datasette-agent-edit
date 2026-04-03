import html
import json
import uuid

from datasette import hookimpl

TOOL_DESCRIPTION = """\
Create an interactive artifact: a single-page HTML+JS+CSS application rendered \
in a sandboxed iframe. Use this for dashboards, data visualizations, interactive \
tools, calculators, games, or any rich interactive UI.

Pass a complete HTML document as the `html` parameter, starting with <!DOCTYPE html>. \
Include inline <style> and <script> tags. You can use any standard browser APIs. \
External libraries can be loaded from CDNs if needed.

Design clean, polished UIs with good typography, spacing, and color. \
Handle loading states and errors gracefully.

SQL QUERY BRIDGE: Your artifact can query the Datasette database. \
All queries are READ-ONLY.

IMPORTANT: Before embedding SQL in an artifact, first use the sql_query tool to \
test and prototype your queries. Use list_databases_and_tables to discover \
available database names and tables.

To execute a SQL query from inside your artifact, call:
  parent.postMessage({database: 'my_database', sql: 'SELECT * FROM my_table LIMIT 10'}, '*');

Listen for results with:
  window.addEventListener('message', function(e) {
    if (e.data && e.data.results) {
      // e.data.results is an array of row objects
      // e.g. [{"name": "Alice", "age": 30}, {"name": "Bob", "age": 25}]
    }
    if (e.data && e.data.error) {
      // e.data.error is an error message string
    }
  });

You can make multiple SQL queries. Each postMessage call will get its own \
response. Include identifying information in your messages if you need to \
distinguish responses from different queries - for example by wrapping the \
postMessage call and listener in a helper function that adds and checks for \
a unique request ID."""

RESIZE_SCRIPT = (
    "<script>(function(){"
    "var lastH=0;new ResizeObserver(()=>{"
    "var h=document.documentElement.scrollHeight;"
    "if(h!==lastH){lastH=h;parent.postMessage({htmlPreviewHeight:h},\"*\");}"
    "}).observe(document.documentElement);"
    "})();</script>"
)

OUTER_SCRIPT_TEMPLATE = """\
<script>
(function(){{
  window.addEventListener("message", function(e){{
    var iframe = document.getElementById("{iframe_id}");
    if (!iframe || e.source !== iframe.contentWindow) return;
    if (e.data && e.data.htmlPreviewHeight){{
      var newH = e.data.htmlPreviewHeight + "px";
      if (iframe.style.height !== newH) iframe.style.height = newH;
    }}
    if (e.data && e.data.sql && e.data.database){{
      var msg = e.data;
      fetch("/" + msg.database + "/-/query.json?sql=" + encodeURIComponent(msg.sql) + "&_shape=array")
        .then(function(r){{ return r.json() }})
        .then(function(data){{ var r = {{results: data}}; if (msg.requestId) r.requestId = msg.requestId; iframe.contentWindow.postMessage(r, "*") }})
        .catch(function(err){{ var r = {{error: err.message}}; if (msg.requestId) r.requestId = msg.requestId; iframe.contentWindow.postMessage(r, "*") }})
    }}
  }});
}})();
</script>"""


def _build_iframe_html(artifact_html, iframe_id):
    full_inner_html = artifact_html + RESIZE_SCRIPT
    escaped_html = html.escape(full_inner_html, quote=True)
    outer_script = OUTER_SCRIPT_TEMPLATE.format(iframe_id=iframe_id)
    iframe_tag = (
        f'<iframe id="{iframe_id}" srcdoc="{escaped_html}" '
        f'sandbox="allow-scripts" '
        f'style="width: 100%; height: 401px; display: block"></iframe>'
    )
    return outer_script + "\n" + iframe_tag


async def _create_artifact(datasette, actor, html):
    iframe_id = f"artifact-{uuid.uuid4().hex[:12]}"
    result_html = _build_iframe_html(html, iframe_id)
    return json.dumps(
        {
            "_html": result_html,
            "status": "Artifact rendered successfully",
        }
    )


@hookimpl
def register_agent_tools(datasette):
    from datasette_agent.tools import AgentTool

    return [
        AgentTool(
            name="create_artifact",
            description=TOOL_DESCRIPTION,
            input_schema={
                "type": "object",
                "properties": {
                    "html": {
                        "type": "string",
                        "description": "Complete HTML document for the artifact",
                    },
                },
                "required": ["html"],
            },
            fn=_create_artifact,
        ),
    ]

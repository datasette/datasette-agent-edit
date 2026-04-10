import html as html_module
import json

from datasette.app import Datasette
import pytest

from datasette_agent_artifacts import (
    ARTIFACT_TYPES,
    CREATE_TOOL_DESCRIPTION,
    DATASETTE_SQL_SCRIPT,
    _build_iframe_html,
    _create_artifact,
)


@pytest.mark.asyncio
async def test_plugin_is_installed():
    datasette = Datasette(memory=True)
    response = await datasette.client.get("/-/plugins.json")
    assert response.status_code == 200
    installed_plugins = {p["name"] for p in response.json()}
    assert "datasette-agent-artifacts" in installed_plugins


@pytest.mark.asyncio
async def test_create_artifact_html():
    datasette = Datasette(memory=True)
    result = json.loads(
        await _create_artifact(datasette, None, content="<h1>Hello</h1>", type="html")
    )
    assert result["type"] == "html"
    assert result["artifact_id"]
    html_output = result["_html"]
    assert "<iframe" in html_output
    assert 'sandbox="allow-scripts"' in html_output
    assert "&lt;h1&gt;Hello&lt;/h1&gt;" in html_output
    assert "htmlPreviewHeight" in html_output


@pytest.mark.asyncio
async def test_create_artifact_markdown():
    datasette = Datasette(memory=True)
    result = json.loads(
        await _create_artifact(
            datasette, None, content="# Hello\n\nWorld", type="markdown"
        )
    )
    assert result["type"] == "markdown"
    assert result["artifact_id"]
    html_output = result["_html"]
    assert "<iframe" in html_output


@pytest.mark.asyncio
async def test_create_artifact_svg():
    datasette = Datasette(memory=True)
    svg = '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"><circle cx="50" cy="50" r="40"/></svg>'
    result = json.loads(
        await _create_artifact(datasette, None, content=svg, type="svg")
    )
    assert result["type"] == "svg"
    assert result["artifact_id"]
    html_output = result["_html"]
    assert "<iframe" in html_output


@pytest.mark.asyncio
async def test_create_artifact_invalid_type():
    datasette = Datasette(memory=True)
    result = json.loads(
        await _create_artifact(datasette, None, content="test", type="invalid")
    )
    assert "error" in result


@pytest.mark.asyncio
async def test_create_artifact_id_prefix_and_tip():
    datasette = Datasette(memory=True)
    result = json.loads(
        await _create_artifact(datasette, None, content="<p>test</p>", type="html")
    )
    assert result["artifact_id"].startswith("artifact-")
    assert "tip" in result
    assert result["artifact_id"] in result["tip"]


@pytest.mark.asyncio
async def test_create_artifact_unique_ids():
    datasette = Datasette(memory=True)
    result1 = json.loads(
        await _create_artifact(datasette, None, content="<p>One</p>", type="html")
    )
    result2 = json.loads(
        await _create_artifact(datasette, None, content="<p>Two</p>", type="html")
    )
    assert result1["artifact_id"] != result2["artifact_id"]


def test_html_escaping():
    result_html = _build_iframe_html('<div class="test">&amp;</div>')
    assert "&lt;div class=&quot;test&quot;&gt;&amp;amp;&lt;/div&gt;" in result_html


def test_bridge_script_tag():
    result_html = _build_iframe_html("<p>test</p>")
    assert "artifact-bridge.js" in result_html
    assert 'class="artifact-iframe"' in result_html


def test_resize_script_in_iframe():
    result_html = _build_iframe_html("<p>test</p>")
    assert "htmlPreviewHeight" in result_html


def test_tool_description_mentions_sql_bridge():
    assert "datasette.query(" in CREATE_TOOL_DESCRIPTION
    assert "database" in CREATE_TOOL_DESCRIPTION
    assert "sql_query" in CREATE_TOOL_DESCRIPTION
    assert "READ-ONLY" in CREATE_TOOL_DESCRIPTION
    assert "list_databases_and_tables" in CREATE_TOOL_DESCRIPTION
    assert "await" in CREATE_TOOL_DESCRIPTION
    assert "result.columns" in CREATE_TOOL_DESCRIPTION
    assert "result.rows" in CREATE_TOOL_DESCRIPTION
    assert "result.truncated" in CREATE_TOOL_DESCRIPTION
    assert "result.error" in CREATE_TOOL_DESCRIPTION


def test_iframe_style():
    result_html = _build_iframe_html("<p>test</p>")
    assert 'style="width: 100%; height: 401px; display: block"' in result_html


def test_artifact_types():
    assert "html" in ARTIFACT_TYPES
    assert "markdown" in ARTIFACT_TYPES
    assert "svg" in ARTIFACT_TYPES


def test_markdown_rendering():
    result_html = _build_iframe_html("# Hello", artifact_type="markdown")
    assert "<iframe" in result_html


def test_svg_rendering():
    svg = '<svg><circle cx="50" cy="50" r="40"/></svg>'
    result_html = _build_iframe_html(svg, artifact_type="svg")
    assert "<iframe" in result_html
    assert "body" in result_html


def test_datasette_query_script_injected_for_html():
    result_html = _build_iframe_html("<p>test</p>", artifact_type="html")
    assert "datasette" in result_html
    assert "query" in result_html


def test_datasette_query_script_not_injected_for_markdown():
    result_html = _build_iframe_html("# Hello", artifact_type="markdown")
    assert "datasette={" not in result_html


def test_datasette_query_script_not_injected_for_svg():
    svg = '<svg><circle cx="50" cy="50" r="40"/></svg>'
    result_html = _build_iframe_html(svg, artifact_type="svg")
    assert "datasette={" not in result_html


def test_datasette_query_script_before_content():
    """The datasette.query() helper must be available before artifact scripts run."""
    content = '<script>datasette.query("db", "SELECT 1")</script>'
    result_html = _build_iframe_html(content, artifact_type="html")
    # The script defining datasette.query should appear before the artifact content
    escaped = html_module.escape(content, quote=True)
    datasette_pos = result_html.find("datasette=")
    content_pos = result_html.find(escaped)
    assert datasette_pos < content_pos


def test_datasette_prompt_script_injected_for_html():
    result_html = _build_iframe_html("<p>test</p>", artifact_type="html")
    assert "prompt:function" in result_html


def test_datasette_prompt_script_not_injected_for_markdown():
    result_html = _build_iframe_html("# Hello", artifact_type="markdown")
    assert "prompt:function" not in result_html


def test_tool_description_mentions_prompt_bridge():
    assert "datasette.prompt(" in CREATE_TOOL_DESCRIPTION
    assert "result.text" in CREATE_TOOL_DESCRIPTION
    assert "fenced code block" in CREATE_TOOL_DESCRIPTION


PROMPT_CONFIG = {
    "plugins": {
        "datasette-llm": {
            "default_model": "echo",
            "require_keys": False,
            "purposes": {
                "artifact-prompt": {
                    "model": "echo",
                },
            },
        },
    },
}

PROMPT_PERMISSIONS = {
    "permissions": {
        "datasette-agent": {"id": "user"},
    },
}


def _prompt_datasette():
    ds = Datasette(memory=True, metadata=PROMPT_CONFIG, config=PROMPT_PERMISSIONS)
    return ds, {"ds_actor": ds.client.actor_cookie({"id": "user"})}


@pytest.mark.asyncio
async def test_prompt_api_endpoint():
    ds, cookies = _prompt_datasette()
    response = await ds.client.post(
        "/-/artifact-prompt",
        content=json.dumps({"prompt": "Hello LLM"}),
        headers={"Content-Type": "application/json"},
        cookies=cookies,
    )
    assert response.status_code == 200
    data = response.json()
    # The echo model returns JSON containing the prompt
    echo_output = json.loads(data["text"])
    assert echo_output["prompt"] == "Hello LLM"


@pytest.mark.asyncio
async def test_prompt_api_requires_permission():
    ds, _ = _prompt_datasette()
    response = await ds.client.post(
        "/-/artifact-prompt",
        content=json.dumps({"prompt": "Hello LLM"}),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_prompt_api_empty_prompt():
    ds, cookies = _prompt_datasette()
    response = await ds.client.post(
        "/-/artifact-prompt",
        content=json.dumps({"prompt": ""}),
        headers={"Content-Type": "application/json"},
        cookies=cookies,
    )
    assert response.status_code == 400
    assert response.json()["error"] == "prompt is required"


@pytest.mark.asyncio
async def test_prompt_api_missing_prompt():
    ds, cookies = _prompt_datasette()
    response = await ds.client.post(
        "/-/artifact-prompt",
        content=json.dumps({}),
        headers={"Content-Type": "application/json"},
        cookies=cookies,
    )
    assert response.status_code == 400
    assert response.json()["error"] == "prompt is required"


@pytest.mark.asyncio
async def test_prompt_api_get_not_allowed():
    ds = Datasette(memory=True)
    response = await ds.client.get("/-/artifact-prompt")
    assert response.status_code == 405
    assert response.json()["error"] == "POST required"

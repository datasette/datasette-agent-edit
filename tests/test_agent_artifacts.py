import json

from datasette.app import Datasette
import pytest

from datasette_agent_artifacts import (
    ARTIFACT_TYPES,
    CREATE_TOOL_DESCRIPTION,
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
        await _create_artifact(datasette, None, content="# Hello\n\nWorld", type="markdown")
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
    assert "parent.postMessage" in CREATE_TOOL_DESCRIPTION
    assert "database" in CREATE_TOOL_DESCRIPTION
    assert "sql_query" in CREATE_TOOL_DESCRIPTION
    assert "READ-ONLY" in CREATE_TOOL_DESCRIPTION
    assert "list_databases_and_tables" in CREATE_TOOL_DESCRIPTION


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

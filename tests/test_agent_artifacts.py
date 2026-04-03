import json

from datasette.app import Datasette
import pytest

from datasette_agent_artifacts import (
    TOOL_DESCRIPTION,
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
async def test_create_artifact_basic():
    datasette = Datasette(memory=True)
    result = json.loads(
        await _create_artifact(datasette, None, html="<h1>Hello</h1>")
    )
    assert "status" in result
    html_output = result["_html"]
    assert "<iframe" in html_output
    assert 'sandbox="allow-scripts"' in html_output
    assert 'srcdoc="' in html_output
    # Content should be HTML-escaped in srcdoc
    assert "&lt;h1&gt;Hello&lt;/h1&gt;" in html_output
    # Resize script should be inside srcdoc (escaped)
    assert "htmlPreviewHeight" in html_output


@pytest.mark.asyncio
async def test_create_artifact_unique_ids():
    datasette = Datasette(memory=True)
    result1 = json.loads(
        await _create_artifact(datasette, None, html="<p>One</p>")
    )
    result2 = json.loads(
        await _create_artifact(datasette, None, html="<p>Two</p>")
    )
    # Extract iframe IDs
    html1 = result1["_html"]
    html2 = result2["_html"]
    id1 = html1.split('id="')[1].split('"')[0]
    id2 = html2.split('id="')[1].split('"')[0]
    assert id1 != id2
    assert id1.startswith("artifact-")
    assert id2.startswith("artifact-")


def test_html_escaping():
    result_html = _build_iframe_html('<div class="test">&amp;</div>', "test-id")
    # The srcdoc should have the content HTML-escaped
    # < becomes &lt;, > becomes &gt;, " becomes &quot;, & becomes &amp;
    assert "&lt;div class=&quot;test&quot;&gt;&amp;amp;&lt;/div&gt;" in result_html


def test_sql_bridge_in_outer_script():
    result_html = _build_iframe_html("<p>test</p>", "test-id")
    assert "query.json" in result_html
    assert "encodeURIComponent(msg.sql)" in result_html
    assert "msg.database" in result_html
    assert "msg.requestId" in result_html
    assert "postMessage" in result_html


def test_resize_handling_in_outer_script():
    result_html = _build_iframe_html("<p>test</p>", "test-id")
    assert "htmlPreviewHeight" in result_html
    assert 'getElementById("test-id")' in result_html


def test_tool_description_mentions_sql_bridge():
    assert "parent.postMessage" in TOOL_DESCRIPTION
    assert "database" in TOOL_DESCRIPTION
    assert "sql_query" in TOOL_DESCRIPTION
    assert "READ-ONLY" in TOOL_DESCRIPTION
    assert "list_databases_and_tables" in TOOL_DESCRIPTION


def test_iframe_style():
    result_html = _build_iframe_html("<p>test</p>", "test-id")
    assert 'style="width: 100%; height: 401px; display: block"' in result_html

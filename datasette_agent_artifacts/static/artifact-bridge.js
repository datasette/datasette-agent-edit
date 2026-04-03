/*
 * Artifact bridge: wires up sandboxed artifact iframes with the parent page.
 *
 * Handles two message types from iframes with class "artifact-iframe":
 *   1. {htmlPreviewHeight: N} — resize the iframe (capped at 800px)
 *   2. {database, sql} — proxy a SQL query via the Datasette JSON API
 *      and post {results} or {error} back; forwards requestId if present.
 *
 * Uses a single global message listener that matches e.source against
 * all live artifact iframes, so it works regardless of how many artifacts
 * are on the page or when they were added.
 */

(function () {
  "use strict";

  var MAX_HEIGHT = 800;

  function getAllArtifactIframes() {
    return document.querySelectorAll("iframe.artifact-iframe");
  }

  function findIframeBySource(source) {
    var iframes = getAllArtifactIframes();
    for (var i = 0; i < iframes.length; i++) {
      try {
        if (iframes[i].contentWindow === source) return iframes[i];
      } catch (e) {
        // cross-origin access may throw
      }
    }
    return null;
  }

  window.addEventListener("message", function (e) {
    if (!e.data || !e.source) return;

    var iframe = findIframeBySource(e.source);
    if (!iframe) return;

    // Resize handling
    if (e.data.htmlPreviewHeight) {
      var h = Math.min(e.data.htmlPreviewHeight, MAX_HEIGHT);
      var newH = h + "px";
      if (iframe.style.height !== newH) iframe.style.height = newH;
    }

    // SQL query bridge
    if (e.data.sql && e.data.database) {
      var msg = e.data;
      fetch(
        "/" +
          msg.database +
          "/-/query.json?sql=" +
          encodeURIComponent(msg.sql) +
          "&_shape=array"
      )
        .then(function (r) {
          return r.json();
        })
        .then(function (data) {
          var r = { results: data };
          if (msg.requestId) r.requestId = msg.requestId;
          iframe.contentWindow.postMessage(r, "*");
        })
        .catch(function (err) {
          var r = { error: err.message };
          if (msg.requestId) r.requestId = msg.requestId;
          iframe.contentWindow.postMessage(r, "*");
        });
    }
  });
})();

/*
 * Artifact bridge: wires up sandboxed artifact iframes with the parent page.
 *
 * Handles two message types from iframes with class "artifact-iframe":
 *   1. {htmlPreviewHeight: N} — resize the iframe (capped at 800px)
 *   2. {database, sql} — proxy a SQL query via the Datasette JSON API
 *      and post {results} or {error} back; forwards requestId if present.
 *
 * Also adds a fullscreen toggle button below each artifact iframe.
 *
 * Uses a single global message listener that matches e.source against
 * all live artifact iframes, so it works regardless of how many artifacts
 * are on the page or when they were added.
 */

(function () {
  "use strict";

  var MAX_HEIGHT = 800;

  // --- Fullscreen modal (created once, reused) ---

  var modal = document.createElement("div");
  modal.className = "artifact-fullscreen-modal";
  modal.style.cssText =
    "display:none;position:fixed;inset:0;z-index:10000;" +
    "background:rgba(0,0,0,0.85);flex-direction:column;";

  var toolbar = document.createElement("div");
  toolbar.style.cssText =
    "display:flex;justify-content:flex-end;padding:8px 12px;background:rgba(0,0,0,0.4);";

  var closeBtn = document.createElement("button");
  closeBtn.textContent = "\u2715 Close";
  closeBtn.style.cssText =
    "background:#fff;color:#333;border:none;border-radius:6px;" +
    "padding:6px 16px;font-size:14px;cursor:pointer;font-weight:600;";
  toolbar.appendChild(closeBtn);
  modal.appendChild(toolbar);

  var modalBody = document.createElement("div");
  modalBody.style.cssText = "flex:1;overflow:hidden;";
  modal.appendChild(modalBody);

  document.body.appendChild(modal);

  var originalParent = null;
  var originalNextSibling = null;
  var activeIframe = null;

  function openFullscreen(iframe) {
    activeIframe = iframe;
    originalParent = iframe.parentNode;
    originalNextSibling = iframe.nextSibling;

    // Move iframe into modal
    modalBody.appendChild(iframe);
    iframe.style.cssText = "width:100%;height:100%;display:block;border:none;";

    modal.style.display = "flex";
    document.body.style.overflow = "hidden";
  }

  function closeFullscreen() {
    if (!activeIframe) return;

    // Restore iframe to original position
    if (originalNextSibling) {
      originalParent.insertBefore(activeIframe, originalNextSibling);
    } else {
      originalParent.appendChild(activeIframe);
    }
    activeIframe.style.cssText =
      "width:100%;height:" + activeIframe.dataset.height + ";display:block;";

    modal.style.display = "none";
    document.body.style.overflow = "";
    activeIframe = null;
    originalParent = null;
    originalNextSibling = null;
  }

  closeBtn.addEventListener("click", closeFullscreen);

  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && activeIframe) closeFullscreen();
  });

  // --- Fullscreen button injection ---

  var BUTTON_SVG =
    '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
    '<polyline points="15 3 21 3 21 9"/><polyline points="9 21 3 21 3 15"/>' +
    '<line x1="21" y1="3" x2="14" y2="10"/><line x1="3" y1="21" x2="10" y2="14"/></svg>';

  function addFullscreenButton(iframe) {
    if (iframe.dataset.hasFullscreenBtn) return;
    iframe.dataset.hasFullscreenBtn = "1";
    iframe.dataset.height = iframe.style.height || "401px";

    var btn = document.createElement("button");
    btn.className = "artifact-fullscreen-btn";
    btn.innerHTML = BUTTON_SVG + " Full screen";
    btn.style.cssText =
      "display:inline-flex;align-items:center;gap:4px;" +
      "margin-top:4px;padding:4px 10px;font-size:12px;" +
      "background:#f0f0f0;border:1px solid #ddd;border-radius:4px;" +
      "cursor:pointer;color:#555;";
    btn.addEventListener("click", function () {
      openFullscreen(iframe);
    });
    iframe.insertAdjacentElement("afterend", btn);
  }

  function wireUpIframes() {
    var iframes = document.querySelectorAll("iframe.artifact-iframe");
    for (var i = 0; i < iframes.length; i++) {
      addFullscreenButton(iframes[i]);
    }
  }

  // Wire up existing iframes
  wireUpIframes();

  // Watch for new iframes added dynamically (streaming)
  var observer = new MutationObserver(function () {
    wireUpIframes();
  });
  observer.observe(document.body, { childList: true, subtree: true });

  // --- Message handling ---

  function findIframeBySource(source) {
    var iframes = document.querySelectorAll("iframe.artifact-iframe");
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

    // Resize handling (only when not in fullscreen)
    if (e.data.htmlPreviewHeight && iframe !== activeIframe) {
      var h = Math.min(e.data.htmlPreviewHeight, MAX_HEIGHT);
      var newH = h + "px";
      if (iframe.style.height !== newH) {
        iframe.style.height = newH;
        iframe.dataset.height = newH;
      }
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

(function () {
  'use strict';

  // ---- per-page scroll restoration ----
  // Save the user's scroll position keyed by pathname so navigating away and
  // back lands them where they were.
  var scrollKey = 'pg-scroll:' + location.pathname;
  if ('scrollRestoration' in history) history.scrollRestoration = 'manual';

  function restoreScroll() {
    var saved = sessionStorage.getItem(scrollKey);
    if (saved === null) return;
    var y = parseInt(saved, 10);
    if (isNaN(y)) return;
    requestAnimationFrame(function () { window.scrollTo(0, y); });
  }
  // Restore on `load` (and re-assert next frame): the per-page fold-restore
  // scripts run on DOMContentLoaded and change page height, so restoring earlier
  // lands at the wrong offset. Skip when a hash anchor owns the scroll.
  function scheduleScrollRestore() {
    if (location.hash) return;
    restoreScroll();
    requestAnimationFrame(restoreScroll);
  }
  if (document.readyState === 'complete') {
    scheduleScrollRestore();
  } else {
    window.addEventListener('load', scheduleScrollRestore);
  }

  function saveScroll() { sessionStorage.setItem(scrollKey, String(window.scrollY)); }
  var scrollDebounce;
  window.addEventListener('scroll', function () {
    clearTimeout(scrollDebounce);
    scrollDebounce = setTimeout(saveScroll, 100);
  }, { passive: true });
  window.addEventListener('beforeunload', saveScroll);
  window.addEventListener('pagehide', saveScroll);

  // ---- live updates via cheap polling ----
  // Every 1s, fetch the project's updated_at. If it has advanced, reload —
  // unless the user is typing in a form, in which case defer until blur.
  var POLL_MS = 1000;
  var lastSeen = null;
  var pendingReload = false;

  function isEditing() {
    var el = document.activeElement;
    if (!el) return false;
    if (el.isContentEditable) return true;
    var tag = (el.tagName || '').toUpperCase();
    return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT';
  }

  // An inline editor/menu the operator opened (observations, context, upload,
  // action menu) sets aria-expanded="true" on its toggle. Treat any open panel
  // as "busy" so a live reload never closes it under the operator.
  function hasOpenPanel() {
    return !!document.querySelector('[aria-expanded="true"]');
  }

  function isBusy() { return isEditing() || hasOpenPanel(); }

  function performReload() {
    if (isBusy()) {
      pendingReload = true;
      return;
    }
    saveScroll();
    window.location.reload();
  }

  document.addEventListener('focusout', function () {
    if (!pendingReload) return;
    setTimeout(function () {
      if (isBusy()) return;
      pendingReload = false;
      performReload();
    }, 0);
  });

  function poll() {
    // A change was seen earlier but deferred because the operator was busy;
    // retry now that a tick has passed (performReload re-checks and re-defers).
    if (pendingReload) {
      if (!isBusy()) { pendingReload = false; performReload(); }
      return;
    }
    fetch('/project/heartbeat', { credentials: 'same-origin', cache: 'no-store' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        if (!data) return;
        var ts = data.updated_at || 0;
        if (lastSeen === null) {
          lastSeen = ts;
          return;
        }
        if (ts > lastSeen) {
          lastSeen = ts;
          performReload();
        }
      })
      .catch(function () { /* network blip — try again next tick */ });
  }

  // Don't poll while the tab is hidden; resume when it comes back.
  var pollTimer;
  function startPolling() {
    stopPolling();
    pollTimer = setInterval(poll, POLL_MS);
  }
  function stopPolling() {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  }
  document.addEventListener('visibilitychange', function () {
    if (document.hidden) stopPolling(); else { poll(); startPolling(); }
  });
  if (!document.hidden) startPolling();

  // ---- rich tooltips ----
  // Upgrades [data-tooltip] elements to a single floating tooltip that respects
  // newlines and bolds lines that start with `#`. Suppresses the CSS ::after
  // fallback by tagging <body> with `js-tooltips`.
  function installRichTooltips() {
    if (!document.body) return;
    var tip = document.createElement('div');
    tip.className = 'rich-tooltip';
    tip.setAttribute('role', 'tooltip');
    document.body.appendChild(tip);
    document.body.classList.add('js-tooltips');

    var ESC_MAP = {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'};
    function esc(s) { return String(s).replace(/[&<>"']/g, function (c) { return ESC_MAP[c]; }); }
    function formatTooltip(text) {
      return String(text).split('\n').map(function (line) {
        var m = line.match(/^\s*(#+)\s*(.*)$/);
        if (m && m[2]) return '<strong>' + esc(m[2]) + '</strong>';
        // Escape first so HTML can't slip in, then turn **inline** into <strong>.
        // Asterisks are not HTML-special so they survive escaping unchanged.
        var escaped = esc(line);
        var bolded = escaped.replace(/\*\*([^*]+?)\*\*/g, '<strong>$1</strong>');
        var italicized = bolded.replace(/\*([^*\n]+?)\*/g, '<em>$1</em>');
        return italicized || '&nbsp;';
      }).join('<br>');
    }

    var hideTimer = null;
    function show(el) {
      var text = el.getAttribute('data-tooltip');
      if (!text) return;
      clearTimeout(hideTimer);
      tip.innerHTML = formatTooltip(text);
      tip.style.left = '-9999px';
      tip.style.top = '0px';
      tip.setAttribute('data-visible', 'true');
      var rect = el.getBoundingClientRect();
      var tipRect = tip.getBoundingClientRect();
      var top = rect.top + window.scrollY - tipRect.height - 8;
      if (top < window.scrollY + 4) top = rect.bottom + window.scrollY + 8;
      var left = rect.left + window.scrollX;
      var maxLeft = window.scrollX + document.documentElement.clientWidth - tipRect.width - 8;
      if (left > maxLeft) left = maxLeft;
      if (left < window.scrollX + 4) left = window.scrollX + 4;
      tip.style.left = left + 'px';
      tip.style.top = top + 'px';
    }
    function hide() {
      clearTimeout(hideTimer);
      hideTimer = setTimeout(function () { tip.setAttribute('data-visible', 'false'); }, 60);
    }
    function handle(e, fn) {
      var el = e.target && e.target.closest && e.target.closest('[data-tooltip]');
      if (el) fn(el);
    }
    document.addEventListener('mouseover', function (e) { handle(e, show); });
    document.addEventListener('mouseout', function (e) { handle(e, hide); });
    document.addEventListener('focusin', function (e) { handle(e, show); });
    document.addEventListener('focusout', function (e) { handle(e, hide); });
    window.addEventListener('scroll', hide, { passive: true });
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', installRichTooltips);
  } else {
    installRichTooltips();
  }
})();

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
  // Twice a second, fetch the project's updated_at. If it has advanced, reload —
  // unless the user is typing in a form or reading a tooltip, in which case
  // defer until they're idle again.
  var POLL_MS = 500;
  var lastSeen = null;
  var pendingReload = false;
  // Set true while a rich tooltip is on screen (see installRichTooltips). A
  // reload mid-hover would tear out the element under the cursor and drop the
  // tooltip, forcing the operator to re-hover — so we treat it as "busy".
  var tooltipVisible = false;

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

  function isBusy() { return isEditing() || hasOpenPanel() || tooltipVisible; }

  // "Update waiting" pill: a quiet, non-blocking cue that a fresh update is being
  // held back because the operator is busy (reading a tooltip, editing, a panel
  // open). Without it, a parked mouse looks like nothing is happening. Clicking
  // loads now — though moving the mouse toward it already un-hovers the item and
  // lets the deferred update through. Created lazily so it's safe before <body>.
  var waitingBadge = null;
  function showWaitingBadge() {
    if (!waitingBadge) {
      if (!document.body) return;
      waitingBadge = document.createElement('button');
      waitingBadge.type = 'button';
      waitingBadge.className = 'update-waiting';
      waitingBadge.innerHTML =
        '<span class="update-waiting-dot" aria-hidden="true"></span><span>Update waiting</span>';
      waitingBadge.addEventListener('click', function () {
        pendingReload = false;
        performReload();
      });
      document.body.appendChild(waitingBadge);
    }
    waitingBadge.classList.add('is-visible');
  }
  function hideWaitingBadge() {
    if (waitingBadge) waitingBadge.classList.remove('is-visible');
  }

  function performReload() {
    if (isBusy()) {
      pendingReload = true;
      showWaitingBadge();
      return;
    }
    hideWaitingBadge();
    saveScroll();
    // Mark this reload as agent-update-driven so the fresh page can play the
    // logo roll-off once (see maybeRollLogo). Manual navigation never sets this.
    try { sessionStorage.setItem('sg:rollOnLoad', '1'); } catch (e) { /* ignore */ }
    window.location.reload();
  }

  // ---- header logo: the lone blue tile ----
  // #sg-blue is the blue tile inside the inlined header logo. It plays one
  // one-shot animation (.sg-lift: wiggle → float to the sky → glide back onto
  // its spot), driven by toggling a CSS class. Triggered when a fresh agent
  // update is pulled, and when the operator clicks the tile.
  var ROLL_MIN_GAP_MS = 40000;

  function blueTile() { return document.getElementById('sg-blue'); }

  function playLift() {
    var b = blueTile();
    if (!b) return;
    // Restart cleanly even if it's already animating.
    b.classList.remove('sg-lift');
    void b.getBoundingClientRect(); // force reflow so the animation replays
    b.classList.add('sg-lift');
    b.addEventListener('animationend', function () {
      b.classList.remove('sg-lift');
    }, { once: true });
  }

  // Play the lift once if this page loaded as the result of an agent update,
  // rate-limited to once per 40s so a burst of updates doesn't spin it constantly.
  function maybeLiftLogo() {
    var flagged = null;
    try { flagged = sessionStorage.getItem('sg:rollOnLoad'); } catch (e) { /* ignore */ }
    if (!flagged) return;
    try { sessionStorage.removeItem('sg:rollOnLoad'); } catch (e) { /* ignore */ }

    var now = Date.now();
    var last = 0;
    try { last = parseInt(localStorage.getItem('sg:lastRoll'), 10) || 0; } catch (e) { /* ignore */ }
    if (now - last < ROLL_MIN_GAP_MS) return;

    try { localStorage.setItem('sg:lastRoll', String(now)); } catch (e) { /* ignore */ }
    playLift();
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', maybeLiftLogo);
  } else {
    maybeLiftLogo();
  }

  // Clicking the blue tile only plays the animation — it must NOT follow the
  // logo's link. Every other part of the logo is a plain <a href="/"> and still
  // navigates home.
  document.addEventListener('click', function (e) {
    var b = e.target && e.target.closest && e.target.closest('#sg-blue');
    if (!b) return;
    e.preventDefault();   // don't navigate
    e.stopPropagation();
    playLift();
  });

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
      tooltipVisible = true;
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
      hideTimer = setTimeout(function () {
        tip.setAttribute('data-visible', 'false');
        tooltipVisible = false;
        // A reload deferred while the tooltip was up can run now the operator has
        // looked away — don't make them wait for the next poll tick.
        if (pendingReload && !isBusy()) { pendingReload = false; performReload(); }
      }, 60);
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

  // ---- inline runs editors ----
  // A .runs-toggle reveals its sibling .runs-editor (a tiny form to change how
  // many times a phase / check / endpoint runs). Delegated so it works on every
  // page. Capture phase so it fires even when the control sits inside a <summary>
  // (the endpoint card top bar), whose click handler calls stopPropagation to
  // keep the card from toggling. aria-expanded marks it open, so the live-reload
  // poll (hasOpenPanel) won't yank the editor closed while the operator edits.
  document.addEventListener('click', function (e) {
    var toggle = e.target && e.target.closest && e.target.closest('.runs-toggle');
    if (!toggle) return;
    var control = toggle.closest('.runs-control');
    if (!control) return;
    var editor = control.querySelector('.runs-editor');
    if (!editor) return;
    var open = !editor.hidden;
    // Close any other editor that's already open so only one shows at a time.
    closeRunsEditors(editor);
    editor.hidden = open;
    toggle.setAttribute('aria-expanded', String(!open));
    if (!open) {
      var input = editor.querySelector('.runs-input');
      if (input) { input.focus(); input.select(); }
    }
  }, true);

  // Close open runs editors when the operator clicks anywhere outside a
  // .runs-control (the control itself calls stopPropagation, so clicks on the
  // toggle / input / Set button never reach here). `except` keeps the editor
  // being toggled open untouched.
  function closeRunsEditors(except) {
    var editors = document.querySelectorAll('.runs-editor:not([hidden])');
    for (var i = 0; i < editors.length; i++) {
      if (editors[i] === except) continue;
      editors[i].hidden = true;
      var ctrl = editors[i].closest('.runs-control');
      var tog = ctrl && ctrl.querySelector('.runs-toggle');
      if (tog) tog.setAttribute('aria-expanded', 'false');
    }
  }
  document.addEventListener('click', function (e) {
    if (e.target && e.target.closest && e.target.closest('.runs-control')) return;
    closeRunsEditors(null);
  });
})();

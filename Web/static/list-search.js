/* List search: client-side filter + highlight for the endpoints, checklist and
   findings pages. Pure DOM, no server round-trips.

   Markup contract (declarative, so each page only adds attributes):
     <button data-list-search-toggle="ID">            magnifier tile in the toolbar
     <div id="ID" class="list-search-bar"
          data-items=".endpoint-card"                 one hit-test unit per match
          data-groups=".feature-group"                optional: wrappers hidden when empty
          data-group-head=".feature-group-title"      optional: a group's title (match = keep all)
          hidden>
       <input type="search" class="list-search-input">
       <span class="list-search-count"></span>
       <button class="list-search-clear">

   Matching is a case-insensitive substring test across everything the operator
   can read on an item: its text, the selected option of each <select>, text
   field / textarea values, and non-empty rich tooltips (data-tooltip). Hits in
   plain text get wrapped in <mark>; hits in form fields and tooltip icons get
   a .list-search-hit ring. Every change is recorded so it can be undone exactly.

   Items are hidden with a class (not the `hidden` attribute) so the pages' own
   status filters, which toggle `hidden`, stay fully independent. Groups are
   hidden by a CSS :has() rule keyed on that class, so they react to both. */
(function () {
  'use strict';

  var SKIP_TAGS = { SCRIPT: 1, STYLE: 1, TEXTAREA: 1, SELECT: 1, OPTION: 1, NOSCRIPT: 1, TEMPLATE: 1 };
  var DEBOUNCE_MS = 90;

  function initBar(bar) {
    var toggle = document.querySelector('[data-list-search-toggle="' + bar.id + '"]');
    var input = bar.querySelector('.list-search-input');
    var count = bar.querySelector('.list-search-count');
    var clearBtn = bar.querySelector('.list-search-clear');
    var itemSel = bar.dataset.items;
    var groupSel = bar.dataset.groups || '';
    if (!toggle || !input || !itemSel) return;

    var root = document.documentElement;
    var storageKey = 'sg:list-search:' + location.pathname;
    var marks = [];      // [{original: Text, nodes: [Node...]}]
    var rings = [];      // elements given .list-search-hit
    var opened = [];     // <details> we opened to reveal a hit
    var timer = null;

    function items() { return Array.prototype.slice.call(document.querySelectorAll(itemSel)); }
    function groups() { return groupSel ? Array.prototype.slice.call(document.querySelectorAll(groupSel)) : []; }

    // Tag items / groups once so the CSS can target them generically.
    items().forEach(function (el) { el.setAttribute('data-ls-item', ''); });
    groups().forEach(function (el) { el.setAttribute('data-ls-group', ''); });

    // ---- text collection -------------------------------------------------
    function textNodes(scope) {
      var out = [];
      var walker = document.createTreeWalker(scope, NodeFilter.SHOW_TEXT, {
        acceptNode: function (n) {
          for (var p = n.parentNode; p && p !== scope; p = p.parentNode) {
            if (SKIP_TAGS[p.nodeName]) return NodeFilter.FILTER_REJECT;
          }
          return n.data.trim() ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_SKIP;
        }
      });
      for (var n = walker.nextNode(); n; n = walker.nextNode()) out.push(n);
      return out;
    }

    // Field-ish things that carry readable text outside of text nodes.
    // Returns [{el, text}] so a hit can ring the right element.
    function fieldTexts(scope) {
      var out = [];
      scope.querySelectorAll('textarea, input[type=text], input:not([type]), select, [data-tooltip]').forEach(function (el) {
        var t = '';
        if (el.tagName === 'SELECT') {
          var opt = el.options[el.selectedIndex];
          t = opt ? opt.text : '';
        } else if (el.tagName === 'TEXTAREA' || el.tagName === 'INPUT') {
          t = el.value;
        } else if (el.dataset.empty !== 'true') {
          t = el.dataset.tooltip;
        }
        if (t) out.push({ el: el, text: t });
      });
      return out;
    }

    // A group's own title (data-group-head inside its <summary>; the counts
    // and buttons next to it are left out so "3" doesn't match "3 checks").
    var groupHeadSel = bar.dataset.groupHead || '';
    function groupHead(group) {
      var sum = group.querySelector(':scope > summary');
      if (!sum) return null;
      return groupHeadSel ? sum.querySelector(groupHeadSel) : sum;
    }

    // ---- undo ------------------------------------------------------------
    function restore() {
      for (var i = marks.length - 1; i >= 0; i--) {
        var m = marks[i];
        var first = m.nodes[0];
        if (first && first.parentNode) {
          first.parentNode.insertBefore(m.original, first);
          m.nodes.forEach(function (n) { if (n.parentNode) n.parentNode.removeChild(n); });
        }
      }
      marks = [];
      rings.forEach(function (el) { el.classList.remove('list-search-hit'); });
      rings = [];
      // Close in reverse so nested <details> fire their toggle in a sane order.
      for (var j = opened.length - 1; j >= 0; j--) opened[j].open = false;
      opened = [];
      items().forEach(function (el) { el.classList.remove('list-search-miss'); });
      groups().forEach(function (el) { el.classList.remove('list-search-group-hit'); });
    }

    // ---- highlight -------------------------------------------------------
    function highlightNode(node, q) {
      var text = node.data;
      var lower = text.toLowerCase();
      var idx = lower.indexOf(q);
      if (idx === -1) return null;
      var frag = document.createDocumentFragment();
      var created = [];
      var pos = 0;
      while (idx !== -1) {
        if (idx > pos) created.push(frag.appendChild(document.createTextNode(text.slice(pos, idx))));
        var mark = document.createElement('mark');
        mark.className = 'list-search-mark';
        mark.textContent = text.slice(idx, idx + q.length);
        created.push(frag.appendChild(mark));
        pos = idx + q.length;
        idx = lower.indexOf(q, pos);
      }
      if (pos < text.length) created.push(frag.appendChild(document.createTextNode(text.slice(pos))));
      var parent = node.parentNode;
      parent.insertBefore(frag, node);
      parent.removeChild(node);
      marks.push({ original: node, nodes: created });
      return created;
    }

    // Open closed <details> between a hit and the document so it is visible —
    // except an item-level <details> whose own summary already shows a hit
    // (its title matched; no need to unfold the whole card). Hits inside a
    // `hidden` editor are left alone: the visible preview/tooltip covers them.
    function reveal(el, item, summaryHit) {
      for (var p = el.parentNode; p && p !== document; p = p.parentNode) {
        if (p.hidden && p !== item) return;
      }
      for (var d = el.parentNode; d && d !== document; d = d.parentNode) {
        if (d.tagName !== 'DETAILS' || d.open) continue;
        if (d === item && summaryHit) continue;
        // A hit inside this details' own summary is already visible.
        var sum = d.querySelector(':scope > summary');
        if (sum && sum.contains(el)) continue;
        d.open = true;
        opened.push(d);
      }
    }

    // ---- search ----------------------------------------------------------
    function run() {
      restore();
      var raw = input.value;
      var q = raw.trim().toLowerCase();
      try {
        if (q) sessionStorage.setItem(storageKey, raw); else sessionStorage.removeItem(storageKey);
      } catch (e) { /* ignore */ }

      if (!q) {
        root.classList.remove('list-searching');
        count.textContent = '';
        bar.classList.remove('no-results');
        return;
      }
      root.classList.add('list-searching');

      // A group whose own heading matches keeps all of its items.
      var groupHits = groups().filter(function (g) {
        var head = groupHead(g);
        return !!head && head.textContent.toLowerCase().indexOf(q) !== -1;
      });
      groupHits.forEach(function (g) {
        g.classList.add('list-search-group-hit');
        textNodes(groupHead(g)).forEach(function (n) { highlightNode(n, q); });
      });

      var all = items();
      var matched = 0;
      all.forEach(function (item) {
        var nodes = textNodes(item).filter(function (n) { return n.data.toLowerCase().indexOf(q) !== -1; });
        var fields = fieldTexts(item).filter(function (f) { return f.text.toLowerCase().indexOf(q) !== -1; });
        var inGroupHit = groupHits.some(function (g) { return g.contains(item); });
        if (!nodes.length && !fields.length) {
          if (!inGroupHit) item.classList.add('list-search-miss');
          else matched++;
          return;
        }
        matched++;
        var summary = item.tagName === 'DETAILS' ? item.querySelector(':scope > summary') : null;
        var summaryHit = !!summary && nodes.some(function (n) { return summary.contains(n); });
        var revealAt = [];
        nodes.forEach(function (n) {
          var created = highlightNode(n, q);
          if (created) revealAt.push(created[0]);
        });
        fields.forEach(function (f) {
          f.el.classList.add('list-search-hit');
          rings.push(f.el);
          revealAt.push(f.el);
        });
        revealAt.forEach(function (el) { reveal(el, item, summaryHit); });
      });

      count.textContent = matched + ' / ' + all.length;
      bar.classList.toggle('no-results', matched === 0);
    }

    function schedule() {
      clearTimeout(timer);
      timer = setTimeout(run, DEBOUNCE_MS);
    }

    // ---- open / close ----------------------------------------------------
    function open(focus) {
      bar.hidden = false;
      toggle.classList.add('active');
      toggle.setAttribute('aria-pressed', 'true');
      if (focus) { input.focus(); input.select(); }
    }
    function close() {
      clearTimeout(timer);
      input.value = '';
      run();
      bar.hidden = true;
      toggle.classList.remove('active');
      toggle.setAttribute('aria-pressed', 'false');
    }

    toggle.addEventListener('click', function () {
      if (bar.hidden) open(true); else close();
    });
    input.addEventListener('input', schedule);
    input.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(); toggle.focus(); }
      else if (e.key === 'Enter') { e.preventDefault(); clearTimeout(timer); run(); }
    });
    if (clearBtn) {
      clearBtn.addEventListener('click', function () {
        input.value = '';
        run();
        input.focus();
      });
    }
    // "/" opens search from anywhere on the page (unless already typing).
    document.addEventListener('keydown', function (e) {
      if (e.key !== '/' || e.ctrlKey || e.metaKey || e.altKey) return;
      var a = document.activeElement;
      var tag = a ? a.tagName : '';
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || (a && a.isContentEditable)) return;
      e.preventDefault();
      open(true);
    });

    // The live poll reloads the page on agent updates; keep the query across it.
    var saved = null;
    try { saved = sessionStorage.getItem(storageKey); } catch (e) { saved = null; }
    if (saved) {
      input.value = saved;
      open(false);
      run();
    }
  }

  function boot() {
    document.querySelectorAll('.list-search-bar[data-items]').forEach(initBar);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();

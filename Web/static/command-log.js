/* Command-log popup for workflow steps.
 *
 * Agents send every command they executed with POST /workflow/.../finish
 * (`command_log: [{ts, title, command}]`). This file renders them so an operator
 * can scan quickly what was done:
 *   - a tiny bash-ish tokenizer classifies words (program, subcommand, flag,
 *     string, URL, host, path, variable, operator, redirect);
 *   - long commands are laid out one flag (+ its value) per line with `\`
 *     continuations, pipelines/&&-chains break onto their own lines;
 *   - huge single arguments (payloads, cookies, base64) are folded behind a
 *     "+N chars" toggle;
 *   - each entry gets program + target chips, a precise timestamp and the delta
 *     to the previous command. Copy always copies the verbatim command.
 */
(function () {
  'use strict';

  var dataEl = document.getElementById('step-command-logs');
  var modal = document.getElementById('cmdlog-modal');
  if (!dataEl || !modal) return;

  var LOGS = {};
  try { LOGS = JSON.parse(dataEl.textContent || '{}') || {}; } catch (e) { LOGS = {}; }

  var listEl = document.getElementById('cmdlog-list');
  var stepEl = document.getElementById('cmdlog-step');
  var summaryEl = document.getElementById('cmdlog-summary');
  var filterEl = document.getElementById('cmdlog-filter');
  var closeBtn = document.getElementById('cmdlog-close');

  var ONE_LINE_MAX = 90;   // segments longer than this get the multi-line layout
  var FOLD_AT = 140;       // single tokens longer than this get folded
  var FOLD_KEEP = 90;      // ...showing this many leading chars

  var ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  function esc(s) { return String(s).replace(/[&<>"']/g, function (c) { return ESC[c]; }); }

  // ---------------------------------------------------------------- tokenizer
  var SEP_OPS = { '|': 1, '||': 1, '&&': 1, ';': 1, '&': 1, '|&': 1, '\n': 1 };
  var REDIRECT_RE = /^(\d*)(&>>|&>|>>|>\||>|<<<|<<|<)(&\d+|&-)?/;

  function readWord(src, i) {
    var n = src.length, raw = '';
    while (i < n) {
      var c = src[i];
      if (c === ' ' || c === '\t' || c === '\n' || c === '\r') break;
      if (c === '|' || c === ';' || c === '<' || c === '>') break;
      if (c === '&') break;
      if (c === '\\') {
        if (src[i + 1] === '\n') { i += 2; continue; }
        raw += src.substr(i, 2); i += 2; continue;
      }
      if (c === "'") {
        var j = src.indexOf("'", i + 1);
        if (j < 0) j = n - 1;
        raw += src.slice(i, j + 1); i = j + 1; continue;
      }
      if (c === '"') {
        var k = i + 1;
        while (k < n && src[k] !== '"') { if (src[k] === '\\') k++; k++; }
        raw += src.slice(i, k + 1); i = k + 1; continue;
      }
      if (c === '$' && src[i + 1] === '(') {
        var depth = 0, m = i + 1;
        for (; m < n; m++) {
          if (src[m] === '(') depth++;
          else if (src[m] === ')') { depth--; if (depth === 0) break; }
        }
        raw += src.slice(i, m + 1); i = m + 1; continue;
      }
      raw += c; i++;
    }
    return { raw: raw, end: i };
  }

  function tokenize(src) {
    var toks = [], i = 0, n = src.length;
    while (i < n) {
      var c = src[i];
      if (c === '\\' && src[i + 1] === '\n') { i += 2; continue; }
      if (c === '\\' && src[i + 1] === '\r' && src[i + 2] === '\n') { i += 3; continue; }
      if (c === ' ' || c === '\t' || c === '\r') { i++; continue; }
      if (c === '\n') { toks.push({ t: 'op', raw: '\n' }); i++; continue; }
      var rm = src.slice(i, i + 8).match(REDIRECT_RE); // every alternative contains < or >
      if (rm) { toks.push({ t: 'redir', raw: rm[0] }); i += rm[0].length; continue; }
      if (c === '&' && src[i + 1] === '&') { toks.push({ t: 'op', raw: '&&' }); i += 2; continue; }
      if (c === '|' && src[i + 1] === '|') { toks.push({ t: 'op', raw: '||' }); i += 2; continue; }
      if (c === '|' && src[i + 1] === '&') { toks.push({ t: 'op', raw: '|&' }); i += 2; continue; }
      if (c === '|' || c === ';' || c === '&') { toks.push({ t: 'op', raw: c }); i++; continue; }
      var w = readWord(src, i);
      if (w.end === i) { toks.push({ t: 'word', raw: c }); i++; continue; }
      toks.push({ t: 'word', raw: w.raw });
      i = w.end;
    }
    return toks;
  }

  // ---------------------------------------------------------- classification
  var WRAPPERS = {
    sudo: 1, time: 1, env: 1, nohup: 1, proxychains: 1, proxychains4: 1, timeout: 1,
    stdbuf: 1, exec: 1, command: 1, nice: 1, torsocks: 1, watch: 1, xargs: 1, doas: 1
  };

  function unquote(raw) {
    if (raw.length >= 2 && (raw[0] === "'" || raw[0] === '"') && raw[raw.length - 1] === raw[0]) {
      return raw.slice(1, -1);
    }
    return raw;
  }
  function basename(raw) { var u = unquote(raw); return u.split('/').pop(); }

  var IPV4 = /^(\d{1,3}\.){3}\d{1,3}(\/\d{1,2})?(:\d+)?$/;
  var HOSTPORT = /^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+(:\d+)?$/i;

  function valueClass(raw) {
    var u = unquote(raw);
    if (/^[a-z][a-z0-9+.-]*:\/\//i.test(u)) return 'url';
    if (IPV4.test(u) || /^\[[0-9a-f:]+\](:\d+)?$/i.test(u)) return 'host';
    if (raw[0] === '$') return 'var';
    if (raw[0] === "'" || raw[0] === '"') return 'str';
    if (/^\d+([,\-:]\d+)*$/.test(u)) return 'num';
    if (/^(\/|\.\/|\.\.\/|~\/)/.test(u) || (/\//.test(u) && !/\s/.test(u))) return 'path';
    if (HOSTPORT.test(u) && /[a-z]/i.test(u.split('.').pop())) return 'host';
    return 'arg';
  }

  // Split tokens into segments separated by pipeline/list operators and classify
  // every word. A segment = {op: leading operator or '', words: [{raw, cls}]}.
  function parse(command) {
    var toks = tokenize(command);
    var segs = [], cur = { op: '', words: [] };
    var seenCmd = false, inWrapper = false, afterCmd = false, redirNext = false;
    function flush(op) {
      if (cur.words.length || cur.op) segs.push(cur);
      cur = { op: op, words: [] };
      seenCmd = false; inWrapper = false; afterCmd = false; redirNext = false;
    }
    toks.forEach(function (tk) {
      if (tk.t === 'op') { flush(tk.raw === '\n' ? ';' : tk.raw); return; }
      if (tk.t === 'redir') { cur.words.push({ raw: tk.raw, cls: 'redir' }); redirNext = !/&(\d+|-)$/.test(tk.raw); return; }
      var raw = tk.raw, cls;
      if (redirNext) { cls = 'path'; redirNext = false; }
      else if (!seenCmd && /^[A-Za-z_][A-Za-z0-9_]*=/.test(raw)) cls = 'env';
      else if (!seenCmd && inWrapper && (/^-/.test(raw) || /^\d+[smhd]?$/.test(raw))) cls = /^-/.test(raw) ? 'flag' : 'num';
      else if (!seenCmd) {
        if (WRAPPERS[basename(raw)]) { cls = 'wrap'; inWrapper = true; }
        else { cls = 'cmd'; seenCmd = true; afterCmd = true; cur.words.push({ raw: raw, cls: cls }); return; }
      }
      else if (/^--?[A-Za-z0-9?]/.test(raw)) cls = 'flag';
      else if (afterCmd && /^[a-z][a-z0-9_-]*$/.test(raw)) cls = 'sub';
      else cls = valueClass(raw);
      afterCmd = false;
      cur.words.push({ raw: raw, cls: cls });
    });
    flush('');
    return segs.filter(function (s) { return s.words.length; });
  }

  // -------------------------------------------------------------- rendering
  var foldId = 0;
  function renderToken(raw, cls) {
    if (cls === 'flag' && raw.indexOf('=') > 0) {
      var at = raw.indexOf('=');
      return renderToken(raw.slice(0, at), 'flag') + '<span class="cl-t-op">=</span>' +
        renderToken(raw.slice(at + 1), valueClass(raw.slice(at + 1)));
    }
    var body;
    if (raw.length > FOLD_AT) {
      var id = 'clf' + (++foldId);
      body = '<span class="cl-fold-short" id="' + id + 's">' + esc(raw.slice(0, FOLD_KEEP)) + '</span>' +
        '<span class="cl-fold-full" id="' + id + 'f" hidden>' + esc(raw) + '</span>' +
        '<button type="button" class="cl-fold-btn" data-fold="' + id + '">… +' + (raw.length - FOLD_KEEP) + ' chars</button>';
    } else {
      body = esc(raw);
    }
    return '<span class="cl-t-' + cls + '">' + body + '</span>';
  }

  // Group a segment's words into display lines: head (env/wrapper/cmd/sub),
  // then one group per flag (+ its value) / positional / redirect.
  var PLAIN = { arg: 1, sub: 1, num: 1 };
  function groupSegment(words) {
    var head = [], groups = [], i = 0;
    while (i < words.length && /^(env|wrap|cmd|sub|num|flag)$/.test(words[i].cls)) {
      var w = words[i];
      head.push(w); i++;
      if (w.cls === 'cmd') {
        if (words[i] && words[i].cls === 'sub') { head.push(words[i]); i++; }
        break;
      }
    }
    for (; i < words.length; i++) {
      var cw = words[i];
      if (cw.cls === 'redir') {
        var g = [cw];
        if (words[i + 1] && words[i + 1].cls !== 'redir') { g.push(words[i + 1]); i++; }
        groups.push(g);
      } else if (cw.cls === 'flag') {
        var grp = [cw], nx = words[i + 1];
        var isLast = i + 1 === words.length - 1;
        if (cw.raw.indexOf('=') < 0 && nx && nx.cls !== 'flag' && nx.cls !== 'redir' &&
            !(isLast && (nx.cls === 'url' || nx.cls === 'host'))) {
          grp.push(nx); i++;
        }
        groups.push(grp);
      } else if (PLAIN[cw.cls] && groups.length && groups[groups.length - 1].plain) {
        // Runs of plain words (prose, subcommand chains) stay on one wrapping
        // line instead of exploding into one word per line.
        groups[groups.length - 1].push(cw);
      } else {
        var one = [cw];
        one.plain = !!PLAIN[cw.cls];
        groups.push(one);
      }
    }
    return { head: head, groups: groups };
  }

  function joinTokens(ws) {
    return ws.map(function (w) { return renderToken(w.raw, w.cls); }).join(' ');
  }
  function rawLen(ws) { return ws.reduce(function (a, w) { return a + w.raw.length + 1; }, 0); }

  function renderPretty(segs) {
    var lines = [];
    segs.forEach(function (seg) {
      var opHtml = seg.op ? '<span class="cl-t-op">' + esc(seg.op) + '</span> ' : '';
      // Short, or no flags at all (prose / plain argument list): one wrapping line.
      var hasFlags = seg.words.some(function (w) { return w.cls === 'flag'; });
      if (rawLen(seg.words) <= ONE_LINE_MAX || !hasFlags) {
        lines.push(opHtml + joinTokens(seg.words));
        return;
      }
      var g = groupSegment(seg.words);
      var segLines = [opHtml + joinTokens(g.head)];
      g.groups.forEach(function (grp) { segLines.push('<span class="cl-indent">  </span>' + joinTokens(grp)); });
      lines.push(segLines.join(' <span class="cl-t-cont">\\</span>\n'));
    });
    return lines.join(' <span class="cl-t-cont">\\</span>\n');
  }

  function summarize(segs) {
    var progs = [], target = null;
    segs.forEach(function (s) {
      s.words.forEach(function (w) {
        if (w.cls === 'cmd') {
          var b = basename(w.raw);
          if (progs.indexOf(b) < 0) progs.push(b);
        }
        if (!target && (w.cls === 'url' || w.cls === 'host')) target = unquote(w.raw);
        if (!target && w.cls === 'flag' && w.raw.indexOf('=') > 0) {
          var v = w.raw.slice(w.raw.indexOf('=') + 1), vc = valueClass(v);
          if (vc === 'url' || vc === 'host') target = unquote(v);
        }
      });
    });
    return { progs: progs, target: target };
  }

  // ------------------------------------------------------- script highlighting
  // Small Python-flavoured highlighter for attached scripts (works acceptably
  // for bash/JS too: comments, strings, numbers, calls). Returns one HTML string
  // per source line so line numbers and key-line marks can be added.
  var PY_KW = {};
  ('False None True and as assert async await break class continue def del elif else except finally for ' +
   'from global if import in is lambda nonlocal not or pass raise return try while with yield match case')
    .split(' ').forEach(function (k) { PY_KW[k] = 1; });
  var CODE_RE = /(#[^\n]*)|([rRbBfFuU]{0,2}(?:"""[\s\S]*?"""|'''[\s\S]*?'''|"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*'))|(\b\d[\d_]*(?:\.\d+)?(?:[eE][+-]?\d+)?\b|\b0[xX][0-9a-fA-F]+\b)|(@[A-Za-z_][\w.]*)|([A-Za-z_]\w*)(\s*\()?/g;

  function highlightCode(src) {
    var toks = [], last = 0, m;
    CODE_RE.lastIndex = 0;
    while ((m = CODE_RE.exec(src))) {
      if (m.index > last) toks.push(['', src.slice(last, m.index)]);
      if (m[1]) toks.push(['cmt', m[1]]);
      else if (m[2]) toks.push(['str', m[2]]);
      else if (m[3]) toks.push(['num', m[3]]);
      else if (m[4]) toks.push(['deco', m[4]]);
      else if (m[5]) {
        var w = m[5];
        var cls = PY_KW[w] ? 'kw' : (m[6] ? 'fn' : (/^(self|cls)$/.test(w) ? 'self' : ''));
        toks.push([cls, w]);
        if (m[6]) toks.push(['', m[6]]);
      }
      last = CODE_RE.lastIndex;
    }
    if (last < src.length) toks.push(['', src.slice(last)]);
    // Split tokens on newlines so multi-line strings still yield per-line HTML.
    var lines = [''];
    toks.forEach(function (t) {
      t[1].split('\n').forEach(function (part, k) {
        if (k > 0) lines.push('');
        if (part) lines[lines.length - 1] += t[0] ? '<span class="cl-py-' + t[0] + '">' + esc(part) + '</span>' : esc(part);
      });
    });
    return lines;
  }

  var ICON_FILE = '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M14 3H6a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8z"/><path d="M14 3v5h5"/><path d="M9.5 13l-2 2 2 2M14.5 13l2 2-2 2"/></svg>';

  // Strip the indentation all lines share (key lines are often deep in a block).
  function dedent(code) {
    var lines = code.split('\n');
    var min = Math.min.apply(null, lines.filter(function (l) { return l.trim(); })
      .map(function (l) { return l.match(/^[ \t]*/)[0].length; }));
    return isFinite(min) && min > 0 ? lines.map(function (l) { return l.slice(min); }).join('\n') : code;
  }

  function renderScript(sc) {
    var kls = (sc.key_lines || []).map(function (kl) {
      var codeLines = highlightCode(dedent(kl.code || ''));
      var lineNo = kl.end_line && kl.end_line > kl.line ? kl.line + '–' + kl.end_line : String(kl.line);
      return '<li class="cl-kl">' +
        '<span class="cl-kl-no">L' + esc(lineNo) + '</span>' +
        '<div class="cl-kl-body">' +
          '<div class="cl-kl-why">' + esc(kl.why) + '</div>' +
          '<pre class="cl-kl-code">' + codeLines.join('\n') + '</pre>' +
          (kl.values ? '<div class="cl-kl-values"><span>values</span>' + esc(kl.values) + '</div>' : '') +
        '</div>' +
      '</li>';
    }).join('');
    return '<div class="cl-script">' +
      '<div class="cl-script-head">' + ICON_FILE +
        '<span class="cl-script-name">' + esc(sc.name) + '</span>' +
        '<span class="muted">' + (sc.line_count || 0) + ' lines · ' + (sc.key_lines || []).length + ' key line' +
          ((sc.key_lines || []).length === 1 ? '' : 's') + '</span>' +
        '<button type="button" class="cl-act cl-script-view" aria-expanded="false">view script</button>' +
      '</div>' +
      '<ol class="cl-kls">' + kls + '</ol>' +
      '<div class="cl-script-src" hidden></div>' +
    '</div>';
  }

  var scriptCache = {};
  function scriptUrl(key, sha) {
    var parts = key.split('/');
    return (modal.getAttribute('data-script-url') || '')
      .replace('__P__', encodeURIComponent(parts[0]))
      .replace('__S__', encodeURIComponent(parts.slice(1).join('/')))
      .replace('__H__', encodeURIComponent(sha));
  }
  function renderSource(text, sc) {
    var marks = {};
    (sc.key_lines || []).forEach(function (kl) {
      for (var n = kl.line; n <= (kl.end_line || kl.line); n++) marks[n] = kl.why;
    });
    var rows = highlightCode(text).map(function (html, i) {
      var n = i + 1, why = marks[n];
      return '<div class="cl-src-row' + (why ? ' is-key' : '') + '"' + (why ? ' title="' + esc(why) + '"' : '') + '>' +
        '<span class="cl-src-no">' + n + '</span><span class="cl-src-code">' + (html || ' ') + '</span></div>';
    });
    return '<pre class="cl-src">' + rows.join('') + '</pre>';
  }
  function toggleSource(btn, entry) {
    var box = btn.closest('.cl-script').querySelector('.cl-script-src');
    var opening = box.hidden;
    box.hidden = !opening;
    btn.setAttribute('aria-expanded', String(opening));
    btn.textContent = opening ? 'hide script' : 'view script';
    if (!opening || box.getAttribute('data-loaded')) return;
    var sc = entry.script;
    box.innerHTML = '<div class="muted small cl-src-loading">Loading…</div>';
    var p = scriptCache[sc.sha256] || (scriptCache[sc.sha256] = fetch(scriptUrl(currentKey, sc.sha256))
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.text(); }));
    p.then(function (text) {
      box.innerHTML = renderSource(text, sc);
      box.setAttribute('data-loaded', '1');
      var firstKey = box.querySelector('.cl-src-row.is-key');
      var pre = box.querySelector('.cl-src');
      if (firstKey && pre) pre.scrollTop = Math.max(0, firstKey.offsetTop - pre.offsetTop - 60);
    }).catch(function () {
      delete scriptCache[sc.sha256];
      box.innerHTML = '<div class="muted small cl-src-loading">Could not load the script source.</div>';
    });
  }

  // ---------------------------------------------------------------- time
  function pad(n, w) { n = String(n); while (n.length < (w || 2)) n = '0' + n; return n; }
  function clock(d) {
    return pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds()) +
      '<span class="cl-ms">.' + pad(d.getMilliseconds(), 3) + '</span>';
  }
  function dayLabel(d) {
    return d.toLocaleDateString(undefined, { weekday: 'short', year: 'numeric', month: 'short', day: 'numeric' });
  }
  function dur(ms) {
    if (ms < 0) ms = 0;
    if (ms < 1000) return ms + 'ms';
    var s = ms / 1000;
    if (s < 60) return (s < 10 ? s.toFixed(1) : Math.round(s)) + 's';
    var m = Math.floor(s / 60); s = Math.round(s % 60);
    if (m < 60) return m + 'm' + (s ? ' ' + s + 's' : '');
    var h = Math.floor(m / 60); m = m % 60;
    return h + 'h' + (m ? ' ' + m + 'm' : '');
  }
  function tzLabel() {
    var off = -new Date().getTimezoneOffset();
    var sign = off >= 0 ? '+' : '-'; off = Math.abs(off);
    return 'UTC' + sign + Math.floor(off / 60) + (off % 60 ? ':' + pad(off % 60) : '');
  }

  // ---------------------------------------------------------------- popup
  var ICON_COPY = '<svg viewBox="0 0 24 24" width="13" height="13" aria-hidden="true"><rect x="9" y="9" width="10" height="10" rx="2" fill="none" stroke="currentColor" stroke-width="2"/><path d="M5 15H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v1" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>';

  var currentEntries = [];
  var currentKey = '';

  function render(entries) {
    foldId = 0;
    if (!entries.length) {
      listEl.innerHTML = '<li class="cmdlog-empty">No commands logged for this step yet.<br>' +
        '<span class="muted small">The agent records every command it ran (with timestamp and a short why) when it finishes the step.</span></li>';
      summaryEl.innerHTML = '';
      return;
    }
    var first = new Date(entries[0].ts), last = new Date(entries[entries.length - 1].ts);
    var agents = [];
    entries.forEach(function (e) { var a = e.agent && e.agent.alias; if (a && agents.indexOf(a) < 0) agents.push(a); });
    var runs = entries.reduce(function (s, e) { s[e.run || 1] = 1; return s; }, {});
    var multiRun = Object.keys(runs).length > 1;
    summaryEl.innerHTML =
      '<span><strong>' + entries.length + '</strong> command' + (entries.length === 1 ? '' : 's') + '</span>' +
      '<span>' + clock(first).replace(/<[^>]+>/g, '') + ' → ' + clock(last).replace(/<[^>]+>/g, '') +
      ' <span class="muted">(' + dur(last - first) + ')</span></span>' +
      (agents.length ? '<span>by ' + agents.map(esc).join(', ') + '</span>' : '') +
      '<span class="muted">times in ' + tzLabel() + ' · hover for UTC</span>';

    var html = [], prevDay = null, prevRun = null, prev = null;
    entries.forEach(function (e, idx) {
      var d = new Date(e.ts);
      var day = dayLabel(d);
      if (multiRun && (e.run || 1) !== prevRun) {
        html.push('<li class="cmdlog-sep cmdlog-sep-run">Run ' + (e.run || 1) + '</li>');
        prevRun = e.run || 1;
      }
      if (day !== prevDay) { html.push('<li class="cmdlog-sep">' + esc(day) + '</li>'); prevDay = day; }
      var segs = parse(e.command);
      var sum = summarize(segs);
      var chips = sum.progs.map(function (p) { return '<span class="cl-chip cl-chip-cmd">' + esc(p) + '</span>'; }).join('');
      if (sum.target) {
        var t = sum.target.length > 52 ? sum.target.slice(0, 50) + '…' : sum.target;
        chips += '<span class="cl-chip cl-chip-target" title="' + esc(sum.target) + '">' + esc(t) + '</span>';
      }
      var delta = prev ? '+' + dur(d - prev) : 'start';
      prev = d;
      html.push(
        '<li class="cl-entry" data-idx="' + idx + '">' +
          '<div class="cl-time" title="' + esc(e.ts) + ' (UTC)">' +
            '<span class="cl-clock">' + clock(d) + '</span>' +
            '<span class="cl-delta">' + delta + '</span>' +
          '</div>' +
          '<div class="cl-body">' +
            '<div class="cl-title-row">' +
              '<span class="cl-num">#' + (idx + 1) + '</span>' +
              '<span class="cl-title">' + esc(e.title) + '</span>' +
              '<span class="cl-chips">' + chips + '</span>' +
              '<span class="cl-actions">' +
                '<button type="button" class="cl-act cl-raw-toggle" aria-pressed="false" title="Show the command exactly as executed">raw</button>' +
                '<button type="button" class="cl-act copy-icon-btn cl-copy" title="Copy command" aria-label="Copy command">' + ICON_COPY + '</button>' +
              '</span>' +
            '</div>' +
            '<pre class="cl-code cl-pretty">' + renderPretty(segs) + '</pre>' +
            '<pre class="cl-code cl-raw" hidden>' + esc(e.command) + '</pre>' +
            (e.script ? renderScript(e.script) : '') +
          '</div>' +
        '</li>'
      );
    });
    listEl.innerHTML = html.join('');
  }

  function applyFilter() {
    var q = (filterEl.value || '').trim().toLowerCase();
    listEl.querySelectorAll('.cl-entry').forEach(function (li) {
      var e = currentEntries[+li.getAttribute('data-idx')];
      var hay = e.title + '\n' + e.command;
      if (e.script) {
        hay += '\n' + e.script.name + (e.script.key_lines || []).map(function (k) {
          return '\n' + k.code + '\n' + k.why + '\n' + (k.values || '');
        }).join('');
      }
      hay = hay.toLowerCase();
      li.hidden = !!q && hay.indexOf(q) < 0;
    });
    listEl.querySelectorAll('.cmdlog-sep').forEach(function (sep) { sep.hidden = !!q; });
  }

  var lastTrigger = null;
  function open(key, title, trigger) {
    currentKey = key;
    currentEntries = (LOGS[key] || []).slice().sort(function (a, b) { return a.ts < b.ts ? -1 : a.ts > b.ts ? 1 : 0; });
    stepEl.textContent = title || key;
    filterEl.value = '';
    filterEl.hidden = currentEntries.length < 4;
    render(currentEntries);
    lastTrigger = trigger || null;
    modal.hidden = false;
    document.body.style.overflow = 'hidden';
    (currentEntries.length >= 4 ? filterEl : closeBtn).focus();
  }
  function close() {
    modal.hidden = true;
    document.body.style.overflow = '';
    if (lastTrigger) lastTrigger.focus();
  }

  document.querySelectorAll('.step-log-btn').forEach(function (btn) {
    btn.addEventListener('click', function () {
      open(btn.getAttribute('data-log-key'), btn.getAttribute('data-step-title'), btn);
    });
  });
  closeBtn.addEventListener('click', close);
  modal.addEventListener('mousedown', function (ev) { if (ev.target === modal) close(); });
  document.addEventListener('keydown', function (ev) { if (ev.key === 'Escape' && !modal.hidden) close(); });
  filterEl.addEventListener('input', applyFilter);

  listEl.addEventListener('click', function (ev) {
    var fold = ev.target.closest('.cl-fold-btn');
    if (fold) {
      var id = fold.getAttribute('data-fold');
      var s = document.getElementById(id + 's'), f = document.getElementById(id + 'f');
      var expanding = f.hidden;
      f.hidden = !expanding; s.hidden = expanding;
      fold.textContent = expanding ? '‹ fold' : '… +' + (f.textContent.length - FOLD_KEEP) + ' chars';
      return;
    }
    var li = ev.target.closest('.cl-entry');
    if (!li) return;
    var entry = currentEntries[+li.getAttribute('data-idx')];
    var viewBtn = ev.target.closest('.cl-script-view');
    if (viewBtn) { toggleSource(viewBtn, entry); return; }
    if (ev.target.closest('.cl-raw-toggle')) {
      var tgl = ev.target.closest('.cl-raw-toggle');
      var showRaw = tgl.getAttribute('aria-pressed') !== 'true';
      tgl.setAttribute('aria-pressed', String(showRaw));
      li.querySelector('.cl-raw').hidden = !showRaw;
      li.querySelector('.cl-pretty').hidden = showRaw;
      return;
    }
    var copy = ev.target.closest('.cl-copy');
    if (copy && navigator.clipboard) {
      navigator.clipboard.writeText(entry.command).then(function () {
        copy.classList.remove('copied'); void copy.offsetWidth; copy.classList.add('copied');
        setTimeout(function () { copy.classList.remove('copied'); }, 1200);
      });
    }
  });
})();

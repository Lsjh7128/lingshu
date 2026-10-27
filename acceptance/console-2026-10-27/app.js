/* LINGSHU 控制台 · 跨环境观测
 * 原生 JS，无框架、无构建、无外部依赖。
 * 数据契约：console/API.md v1（只读、服务端脱敏）。
 * 安全约束：所有动态文本只走 textContent，绝不拼接 innerHTML。
 */
(function () {
  'use strict';

  var SVG_NS = 'http://www.w3.org/2000/svg';
  var RING_R = 54;
  var RING_C = 2 * Math.PI * RING_R; // ≈ 339.292
  var SPARK_MAX = 60;
  var POLL_MS = 1500;
  var SSE_RETRY_MS = 30000;
  var MAX_LOG_ROWS = 15;
  var MAX_BARS = 8;

  var PHASE_COLOR = {
    normal: '#22c55e',
    shift: '#f59e0b',
    critical: '#ef4444',
    hard: '#ef4444'
  };
  var PHASE_UNKNOWN_COLOR = '#64748b';
  var PHASE_GLOSS = { normal: '正常', shift: '相变', critical: '临界', hard: '硬阈值' };
  var PHASE_TOKEN = { normal: 'normal', shift: 'shift', critical: 'critical', hard: 'critical' };

  var WARN_LABEL = {
    legacy_flat_dir: '使用了旧扁平目录',
    unparsable_lines: '存在无法解析的行',
    count_truncated: '计数被截断',
    state_missing: 'state.json 缺失',
    source_fallback: '使用了回退数据源'
  };

  // 与「放行 / 拦截」判定相关的事件类型（audit.recent 过滤用）。
  var STPA_KINDS = {
    pre_step: true,
    stpa_observe: true,
    preflight_block: true,
    preflight_downgrade: true,
    tool_gate: true,
    apply: true,
    hook_error: true,
    section_error: true
  };

  var KIND_COLOR = {
    pre_step: '#2563eb',
    stpa_observe: '#0891b2',
    preflight_block: '#dc2626',
    preflight_downgrade: '#d97706',
    tool_gate: '#7c3aed',
    apply: '#0d9488',
    hook_error: '#b91c1c',
    section_error: '#b91c1c'
  };
  var KIND_PALETTE = ['#2563eb', '#7c3aed', '#0d9488', '#0891b2', '#b45309', '#be123c', '#475569'];

  var state = {
    profiles: [],
    selectedId: null,
    refreshHintMs: POLL_MS,
    token: 0,
    switching: false,
    es: null,
    pollTimer: null,
    sseRetryTimer: null,
    spark: [],
    lastSampleKey: null,
    lastSampleAt: 0,
    lastAsof: null,
    serverAgeSec: null,
    serverStale: false,
    staleAfterSec: null,
    snapshot: null
  };

  // ── 基础工具 ─────────────────────────────────────────

  function $(id) {
    return document.getElementById(id);
  }

  function setText(id, value) {
    var node = $(id);
    if (!node) return;
    if (value === null || value === undefined) node.textContent = '—';
    else node.textContent = String(value);
  }

  function setTag(id, text, extraClass) {
    var node = $(id);
    if (!node) return;
    node.textContent = text === null || text === undefined ? '—' : String(text);
    node.className = 'tag' + (extraClass ? ' ' + extraClass : '');
  }

  function setStatClass(id, cls) {
    var node = $(id);
    if (!node) return;
    node.className = 'v' + (cls ? ' ' + cls : '');
  }

  function fmtInt(n) {
    if (typeof n !== 'number' || !isFinite(n)) return '—';
    return String(Math.round(n)).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  }

  function fmtFixed(n, digits) {
    if (typeof n !== 'number' || !isFinite(n)) return '—';
    return n.toFixed(digits);
  }

  function fmtPct(n, digits) {
    if (typeof n !== 'number' || !isFinite(n)) return '—';
    return (n * 100).toFixed(digits === undefined ? 1 : digits) + '%';
  }

  function fmtSigned(n) {
    if (typeof n !== 'number' || !isFinite(n)) return '—';
    var abs = Math.abs(n).toFixed(3).replace(/\.?0+$/, '');
    if (abs === '') abs = '0';
    return (n > 0 ? '+' : n < 0 ? '-' : '') + abs;
  }

  function fmtBytes(n) {
    if (typeof n !== 'number' || !isFinite(n)) return '—';
    if (n < 1024) return String(n) + ' B';
    if (n < 1048576) return (n / 1024).toFixed(1) + ' KB';
    return (n / 1048576).toFixed(1) + ' MB';
  }

  function pad2(n) {
    return n < 10 ? '0' + n : String(n);
  }

  function fmtTime(ts) {
    if (typeof ts !== 'number' || !isFinite(ts)) return '—';
    var d = new Date(ts);
    if (isNaN(d.getTime())) return '—';
    return pad2(d.getHours()) + ':' + pad2(d.getMinutes()) + ':' + pad2(d.getSeconds());
  }

  function fmtDateTime(ts) {
    if (typeof ts !== 'number' || !isFinite(ts)) return '—';
    var d = new Date(ts);
    if (isNaN(d.getTime())) return '—';
    return d.getFullYear() + '-' + pad2(d.getMonth() + 1) + '-' + pad2(d.getDate()) + ' ' + fmtTime(ts);
  }

  function fmtEventTime(ev) {
    if (ev && typeof ev.iso === 'string' && ev.iso.length >= 19) {
      return ev.iso.slice(11, 19);
    }
    return fmtTime(ev && ev.ts);
  }

  function ageFromAsof(asof) {
    if (typeof asof !== 'number' || !isFinite(asof)) return null;
    var age = (Date.now() - asof) / 1000;
    if (!isFinite(age)) return null;
    return age < 0 ? 0 : Math.round(age);
  }

  function clamp01(x) {
    if (typeof x !== 'number' || !isFinite(x)) return 0;
    return x < 0 ? 0 : x > 1 ? 1 : x;
  }

  function tokenOf(str, fallback) {
    var s = String(str === null || str === undefined ? fallback : str).toLowerCase();
    s = s.replace(/[^a-z0-9_-]/g, '');
    return s || String(fallback);
  }

  function kindColor(kind) {
    if (KIND_COLOR[kind]) return KIND_COLOR[kind];
    var h = 0;
    var s = String(kind);
    for (var i = 0; i < s.length; i += 1) h = (h * 31 + s.charCodeAt(i)) % 997;
    return KIND_PALETTE[h % KIND_PALETTE.length];
  }

  // ── 连接状态 / 横幅 ──────────────────────────────────

  function setConn(kind, text) {
    var badge = $('connBadge');
    if (badge) {
      badge.className = 'conn conn-' + kind;
      badge.title = text;
    }
    setText('connText', text);
  }

  function showBanner(which, text) {
    var box = $('banner' + which);
    var span = $('banner' + which + 'Text');
    if (!box || !span) return;
    span.textContent = text || '';
    box.hidden = !text;
  }

  function showError(message, hint) {
    var text = message || '未知错误';
    if (hint) text += ' · ' + hint;
    showBanner('Error', text);
  }

  function showApiError(body) {
    var code = body && typeof body.code === 'string' ? '[' + body.code + '] ' : '';
    var msg = body && typeof body.message === 'string' ? body.message : '服务返回错误';
    showError(code + msg, body && typeof body.hint === 'string' ? body.hint : '');
  }

  function showNetworkError(err) {
    var msg = err && err.message ? err.message : String(err);
    showError('请求失败：' + msg, '确认控制台服务是否在运行（python server.py --port 3011）');
  }

  function hideError() {
    showBanner('Error', '');
  }

  // ── HTTP ─────────────────────────────────────────────

  function fetchJson(url) {
    return fetch(url, {
      cache: 'no-store',
      headers: { Accept: 'application/json' }
    }).then(function (res) {
      return res.json().catch(function () {
        return { ok: false, code: 'internal', message: '响应不是合法 JSON（HTTP ' + res.status + '）' };
      });
    });
  }

  // ── profile 下拉 ─────────────────────────────────────

  function fillProfileSelect() {
    var sel = $('profileSelect');
    if (!sel) return;
    sel.textContent = '';
    if (!state.profiles.length) {
      var opt0 = document.createElement('option');
      opt0.value = '';
      opt0.textContent = '无可用 profile';
      sel.appendChild(opt0);
      sel.disabled = true;
      return;
    }
    state.profiles.forEach(function (p) {
      var opt = document.createElement('option');
      opt.value = String(p.id || '');
      var label = p.label || p.id || '(未命名)';
      opt.textContent = p.available === false ? label + ' · 未接入' : label;
      opt.title = 'id: ' + (p.id || '') + ' · kind: ' + (p.kind || '?') + ' · mode: ' + (p.mode || '?');
      sel.appendChild(opt);
    });
    sel.disabled = false;
  }

  function renderHeader(profile) {
    if (profile && profile.id != null) {
      var sel = $('profileSelect');
      if (sel && sel.value !== String(profile.id)) sel.value = String(profile.id);
    }

    var mode = profile && typeof profile.mode === 'string' ? profile.mode : 'unknown';
    var modeBadge = $('modeBadge');
    if (modeBadge) {
      modeBadge.textContent = mode;
      modeBadge.className = 'badge badge-mode badge-mode-' + tokenOf(mode, 'unknown');
    }

    var kind = profile && typeof profile.kind === 'string' ? profile.kind : '';
    var kindBadge = $('kindBadge');
    if (kindBadge) {
      if (kind === 'demo' || kind === 'lab') {
        kindBadge.textContent = kind;
        kindBadge.className = 'badge badge-mode badge-mode-' + tokenOf(kind, 'lab');
        kindBadge.hidden = false;
      } else {
        kindBadge.hidden = true;
      }
    }

    var sub = $('subLine');
    if (sub) {
      var bits = [];
      if (profile && profile.label) bits.push(profile.label);
      if (kind) bits.push('kind=' + kind);
      if (profile && profile.source && profile.source.type) bits.push('source=' + profile.source.type);
      sub.textContent = bits.length ? bits.join(' · ') : '本地只读观测 · 数据由 console 服务提供';
    }
  }

  // ── 相变环形图 ───────────────────────────────────────

  function renderTicks(thresholds) {
    var g = $('ringTicks');
    if (!g) return;
    g.textContent = '';
    if (!thresholds || typeof thresholds !== 'object') return;
    var values = [thresholds.shift, thresholds.hard];
    var seen = {};
    values.forEach(function (val) {
      if (typeof val !== 'number' || !isFinite(val) || val <= 0 || val >= 1) return;
      var key = val.toFixed(4);
      if (seen[key]) return;
      seen[key] = true;
      var angle = (-90 + val * 360) * Math.PI / 180;
      var r1 = RING_R - 9;
      var r2 = RING_R + 9;
      var line = document.createElementNS(SVG_NS, 'line');
      line.setAttribute('x1', (80 + r1 * Math.cos(angle)).toFixed(2));
      line.setAttribute('y1', (80 + r1 * Math.sin(angle)).toFixed(2));
      line.setAttribute('x2', (80 + r2 * Math.cos(angle)).toFixed(2));
      line.setAttribute('y2', (80 + r2 * Math.sin(angle)).toFixed(2));
      line.setAttribute('class', 'ring-tick');
      g.appendChild(line);
    });
  }

  function fmtThresholds(th) {
    if (!th || typeof th !== 'object') return '阈值未提供';
    var parts = [];
    if (typeof th.shift === 'number') parts.push('shift ' + fmtPct(th.shift, 0));
    if (typeof th.hard === 'number') parts.push('hard ' + fmtPct(th.hard, 0));
    return parts.length ? parts.join(' / ') : '阈值未提供';
  }

  function renderPhase(data) {
    var phase = data && data.phase ? data.phase : null;
    var arc = $('ringArc');
    var thresholds = phase && phase.thresholds ? phase.thresholds : null;
    renderTicks(thresholds);

    var observed = !!(phase && phase.observed === true && typeof phase.ratio === 'number');

    if (!observed) {
      if (arc) {
        arc.style.stroke = '#334155';
        arc.style.strokeDashoffset = String(RING_C);
      }
      setText('ringPct', '未观测');
      setText('ringPhase', 'ratio 未记录');
      setTag('phaseTag', '未观测', 'tag-muted');
      setText('phaseUsage', '未观测');
      setText('phaseThresholds', fmtThresholds(thresholds));
      setText('phaseTrend', '—');
      setText('phaseAlerts', '—');
      setText('phaseSource', phase && typeof phase.source === 'string' ? phase.source : '—');
      return;
    }

    var ratio = clamp01(phase.ratio);
    var phaseName = typeof phase.phase === 'string' ? phase.phase : 'unknown';
    var color = PHASE_COLOR[phaseName] || PHASE_UNKNOWN_COLOR;

    if (arc) {
      arc.style.stroke = color;
      arc.style.strokeDashoffset = String(RING_C * (1 - ratio));
    }

    setText('ringPct', fmtPct(ratio, 1));
    setText('ringPhase', phaseName);
    var gloss = PHASE_GLOSS[phaseName];
    setTag('phaseTag', phaseName + (gloss ? ' · ' + gloss : ''), 'tag-phase-' + (PHASE_TOKEN[phaseName] || 'unknown'));

    var used = phase.used_tokens_est;
    var windowSize = phase.context_window;
    setText(
      'phaseUsage',
      (typeof used === 'number' ? fmtInt(used) : '未记录') + ' / ' +
      (typeof windowSize === 'number' ? fmtInt(windowSize) : '未记录')
    );
    setText('phaseThresholds', fmtThresholds(thresholds));
    setText('phaseTrend', fmtSigned(phase.trend));

    var alerts = typeof phase.alerts === 'number' ? phase.alerts : null;
    var alertText = alerts === null ? '—' : fmtInt(alerts) + ' 次';
    if (alerts !== null && typeof phase.last_alert_at === 'number') {
      alertText += ' · 最近 ' + fmtTime(phase.last_alert_at);
    }
    setText('phaseAlerts', alertText);

    var src = typeof phase.source === 'string' ? phase.source : '—';
    if (src === 'plugin_state') src = 'plugin_state（插件状态）';
    else if (src === 'derived') src = 'derived（估算）';
    setText('phaseSource', src);
  }

  // ── 记忆 ─────────────────────────────────────────────

  function toBars(list, labelKey) {
    if (!Array.isArray(list)) return [];
    var out = [];
    list.forEach(function (item) {
      if (!item || typeof item !== 'object') return;
      var label = item[labelKey];
      if (label === undefined || label === null) label = item.key;
      if (label === undefined || label === null) label = item.kind;
      var n = item.n;
      if (typeof n !== 'number' || !isFinite(n)) return;
      out.push({ label: String(label === undefined || label === null ? '?' : label), n: n });
    });
    return out;
  }

  function renderBars(containerId, entries, maxItems) {
    var box = $(containerId);
    if (!box) return;
    box.textContent = '';
    var list = Array.isArray(entries) ? entries.filter(function (e) {
      return e && typeof e.n === 'number' && isFinite(e.n);
    }) : [];
    if (!list.length) {
      var empty = document.createElement('p');
      empty.className = 'muted small';
      empty.textContent = '无数据';
      box.appendChild(empty);
      return;
    }
    list.sort(function (a, b) { return b.n - a.n; });
    var limit = maxItems || MAX_BARS;
    var top = list.slice(0, limit);
    var max = 1;
    top.forEach(function (e) { if (e.n > max) max = e.n; });

    top.forEach(function (e) {
      var row = document.createElement('div');
      row.className = 'bar-row';

      var label = document.createElement('span');
      label.className = 'bar-label';
      label.textContent = e.label;
      label.title = e.label;

      var track = document.createElement('span');
      track.className = 'bar-track';
      var fill = document.createElement('span');
      fill.className = 'bar-fill';
      fill.style.width = Math.max(2, (e.n / max) * 100).toFixed(1) + '%';
      track.appendChild(fill);

      var value = document.createElement('span');
      value.className = 'bar-value';
      value.textContent = fmtInt(e.n);

      row.appendChild(label);
      row.appendChild(track);
      row.appendChild(value);
      box.appendChild(row);
    });

    if (list.length > top.length) {
      var note = document.createElement('p');
      note.className = 'muted small';
      note.textContent = '另有 ' + (list.length - top.length) + ' 项未显示';
      box.appendChild(note);
    }
  }

  function maybeSample(mem, revision) {
    if (!mem || mem.observed !== true) return;
    if (typeof mem.active !== 'number' || !isFinite(mem.active)) return;
    // 每收到一帧快照就采样一次；只丢弃 1 秒内 revision 未变的重复帧（避免一次渲染重复计数）。
    // 历史只存在于本页内存，服务端与本地都不存历史，因此不补造任何点。
    var key = typeof revision === 'string' && revision ? revision : 'active:' + mem.active;
    var now = Date.now();
    if (key === state.lastSampleKey && now - state.lastSampleAt < 1000) return;
    state.lastSampleKey = key;
    state.lastSampleAt = now;
    state.spark.push({ active: mem.active });
    while (state.spark.length > SPARK_MAX) state.spark.shift();
  }

  function renderSpark() {
    var line = $('sparkLine');
    var dot = $('sparkDot');
    if (!line || !dot) return;
    var pts = state.spark;
    var W = 240;
    var H = 48;
    var PAD = 6;

    if (!pts.length) {
      line.setAttribute('points', '');
      dot.setAttribute('cx', '-10');
      dot.setAttribute('cy', '-10');
      setText('sparkMeta', '暂无采样');
      return;
    }

    var vals = pts.map(function (p) { return p.active; });
    var min = Math.min.apply(null, vals);
    var max = Math.max.apply(null, vals);
    var flat = max === min;
    var span = flat ? 1 : max - min;

    function xAt(i) {
      return pts.length === 1 ? W / 2 : PAD + (i * (W - PAD * 2)) / (pts.length - 1);
    }
    function yAt(v) {
      return flat ? H / 2 : H - PAD - ((v - min) / span) * (H - PAD * 2);
    }

    var coords = pts.map(function (p, i) {
      return xAt(i).toFixed(1) + ',' + yAt(p.active).toFixed(1);
    });
    line.setAttribute('points', coords.join(' '));

    var last = pts.length - 1;
    dot.setAttribute('cx', xAt(last).toFixed(1));
    dot.setAttribute('cy', yAt(pts[last].active).toFixed(1));

    var meta = pts.length + '/' + SPARK_MAX + ' 帧 · 最近 ' + fmtInt(pts[last].active) +
      (flat ? ' · 区间恒定' : ' · 低 ' + fmtInt(min) + ' / 高 ' + fmtInt(max));
    setText('sparkMeta', meta);
  }

  function renderMemory(data) {
    var mem = data && data.memory ? data.memory : null;
    var observed = !!(mem && mem.observed === true);

    if (!observed) {
      setTag('memTag', '未观测', 'tag-muted');
      setText('memActive', '未观测');
      setText('memUnique', '未观测');
      setText('memRows', '未观测');
      setText('memSub', mem ? 'memory 未观测' : '无 memory 数据');
      maybeSample(mem, data && data.revision);
      renderSpark();
      renderBars('memOrigin', []);
      return;
    }

    setTag('memTag', '已观测', 'tag-ok');
    setText('memActive', fmtInt(mem.active));
    setText('memUnique', fmtInt(mem.unique));
    setText('memRows', fmtInt(mem.total_rows));

    var parts = [];
    if (typeof mem.superseded === 'number') parts.push('superseded ' + fmtInt(mem.superseded));
    if (typeof mem.suppressed === 'number') parts.push('suppressed ' + fmtInt(mem.suppressed));
    if (typeof mem.unparsable === 'number') parts.push('unparsable ' + fmtInt(mem.unparsable));
    if (mem.truncated === true) parts.push('计数已截断');
    if (typeof mem.last_write_ts === 'number') parts.push('最后写入 ' + fmtDateTime(mem.last_write_ts));
    setText('memSub', parts.join(' · '));

    maybeSample(mem, data && data.revision);
    renderSpark();
    renderBars('memOrigin', toBars(mem.by_origin, 'key'));
  }

  // ── STPA ─────────────────────────────────────────────

  function formatScalar(v) {
    if (v === null) return 'null';
    if (typeof v === 'boolean') return v ? 'true' : 'false';
    if (typeof v === 'number') {
      if (!isFinite(v)) return String(v);
      return Number.isInteger(v) ? String(v) : String(Math.round(v * 10000) / 10000);
    }
    if (typeof v === 'string') return v.length > 24 ? v.slice(0, 24) + '…' : v;
    if (Array.isArray(v)) {
      var items = [];
      for (var i = 0; i < v.length && i < 8; i += 1) {
        var s = formatAtomic(v[i]);
        if (s !== null) items.push(s);
      }
      return '[' + items.join(', ') + (v.length > 8 ? ', …' : '') + ']';
    }
    return null;
  }

  function formatAtomic(v) {
    if (v === null) return 'null';
    if (typeof v === 'boolean') return v ? 'true' : 'false';
    if (typeof v === 'number') return isFinite(v) ? String(v) : String(v);
    if (typeof v === 'string') return v.length > 24 ? v.slice(0, 24) + '…' : v;
    return null; // 嵌套对象一律丢弃（契约也不提供）
  }

  function formatFields(fields) {
    if (!fields || typeof fields !== 'object' || Array.isArray(fields)) return '—';
    var keys = Object.keys(fields);
    var parts = [];
    for (var i = 0; i < keys.length && i < 24; i += 1) {
      var key = keys[i];
      var value = formatScalar(fields[key]);
      if (value === null) continue;
      parts.push(key + '=' + value);
    }
    return parts.length ? parts.join('  ') : '—';
  }

  function renderStpaLog(data) {
    var tbody = $('stpaLog');
    if (!tbody) return;
    tbody.textContent = '';
    var audit = data && data.audit ? data.audit : null;
    var recent = audit && Array.isArray(audit.recent) ? audit.recent : [];
    var rows = recent.filter(function (ev) {
      return ev && typeof ev.kind === 'string' && STPA_KINDS[ev.kind] === true;
    });
    var shown = rows.slice(0, MAX_LOG_ROWS);

    if (!shown.length) {
      setText('logMeta', recent.length ? 'recent 中无相关事件' : '');
      var trEmpty = document.createElement('tr');
      var tdEmpty = document.createElement('td');
      tdEmpty.colSpan = 3;
      tdEmpty.className = 'muted small';
      tdEmpty.textContent = '暂无放行/拦截相关事件';
      trEmpty.appendChild(tdEmpty);
      tbody.appendChild(trEmpty);
      return;
    }

    setText('logMeta', '相关 ' + rows.length + ' / recent ' + recent.length + ' 条');

    shown.forEach(function (ev) {
      var tr = document.createElement('tr');

      var tdTime = document.createElement('td');
      tdTime.className = 'log-time';
      tdTime.textContent = fmtEventTime(ev);
      if (typeof ev.iso === 'string') tdTime.title = ev.iso;

      var tdKind = document.createElement('td');
      var badge = document.createElement('span');
      badge.className = 'kind-badge';
      badge.textContent = ev.kind;
      badge.style.borderColor = kindColor(ev.kind);
      tdKind.appendChild(badge);

      var tdFields = document.createElement('td');
      tdFields.className = 'log-fields';
      tdFields.textContent = formatFields(ev.fields);

      tr.appendChild(tdTime);
      tr.appendChild(tdKind);
      tr.appendChild(tdFields);
      tbody.appendChild(tr);
    });
  }

  function renderStpa(data) {
    var stpa = data && data.stpa ? data.stpa : null;
    var observed = !!(stpa && stpa.observed === true);

    if (!observed) {
      setTag('stpaTag', '未观测', 'tag-muted');
      setText('stpaVerdict', '未观测');
      setStatClass('stpaVerdict', 'v-muted');
      setText('stpaUcas', '—');
      setText('stpaDowngrades', '—');
      setText('stpaFailMode', '—');
      setText('stpaBlocked', '—');
      setText('stpaSub', stpa && typeof stpa.mode === 'string' ? 'mode ' + stpa.mode : '无 STPA 记录');
      renderStpaLog(data);
      return;
    }

    var verdict = typeof stpa.verdict === 'string' ? stpa.verdict : 'unknown';
    var ucas = Array.isArray(stpa.ucas) ? stpa.ucas.map(function (x) { return String(x); }) : [];
    var downgrades = Array.isArray(stpa.downgrades) ? stpa.downgrades.map(function (x) { return String(x); }) : [];
    var failMode = stpa.fail_mode || stpa.failMode || null;

    setText('stpaVerdict', verdict);
    var vClass = 'v-muted';
    if (verdict === 'allow') vClass = 'v-ok';
    else if (verdict === 'deny' || verdict === 'block') vClass = 'v-bad';
    else if (verdict === 'downgrade') vClass = 'v-warn';
    setStatClass('stpaVerdict', vClass);

    setText('stpaUcas', ucas.length ? ucas.length + ' 项' : '0');
    setText('stpaDowngrades', downgrades.length ? downgrades.length + ' 项' : '0');
    setText('stpaFailMode', failMode || '—');
    setText('stpaBlocked', stpa.blocked === true ? '已拦截' : '未拦截');

    var tagClass = verdict === 'allow' ? 'tag-ok' : (verdict === 'deny' || verdict === 'block' ? 'tag-bad' : 'tag-warn');
    if (stpa.blocked === true) tagClass = 'tag-bad';
    setTag('stpaTag', verdict, tagClass);

    var sub = [];
    if (ucas.length) sub.push('ucas: ' + ucas.join(' / '));
    if (downgrades.length) sub.push('downgrades: ' + downgrades.join(' / '));
    if (typeof stpa.mode === 'string') sub.push('mode ' + stpa.mode);
    if (stpa.sources && typeof stpa.sources === 'object') {
      var srcBits = [];
      ['user_message', 'injected_prompt'].forEach(function (k) {
        if (typeof stpa.sources[k] === 'boolean') srcBits.push(k + '=' + (stpa.sources[k] ? 'true' : 'false'));
      });
      if (srcBits.length) sub.push('sources ' + srcBits.join(' '));
    }
    if (typeof stpa.last_at === 'number') sub.push('最近 ' + fmtDateTime(stpa.last_at));
    setText('stpaSub', sub.join(' · '));

    renderStpaLog(data);
  }

  // ── 审计 ─────────────────────────────────────────────

  function renderAudit(data) {
    var audit = data && data.audit ? data.audit : null;
    var turns = data && data.turns ? data.turns : null;
    // 契约未强制 audit.observed，但服务端提供了；缺省按「已观测」处理。
    var auditObserved = !!(audit && audit.observed !== false);

    if (!auditObserved) {
      setTag('auditTag', '未观测', 'tag-muted');
      setText('auditTotal', '未观测');
      setText('auditSub', '');
      renderBars('auditKinds', []);
    } else {
      setTag('auditTag', '已观测', 'tag-ok');
      setText('auditTotal', fmtInt(audit.total));
      renderBars('auditKinds', toBars(audit.by_kind, 'kind'), MAX_BARS);
      var sub = [];
      if (typeof audit.unparsable === 'number' && audit.unparsable > 0) sub.push('unparsable ' + fmtInt(audit.unparsable));
      if (typeof audit.errors === 'number' && audit.errors > 0) sub.push('错误 ' + fmtInt(audit.errors));
      if (audit.truncated === true) sub.push('事件已截断');
      if (typeof audit.events_limit === 'number') sub.push('recent 上限 ' + fmtInt(audit.events_limit));
      setText('auditSub', sub.join(' · '));
    }

    setText('turnsCount', turns ? fmtInt(turns.count) : '—');
    setText('stateWrites', turns ? fmtInt(turns.state_writes) : '—');
    setText('stageValue', turns && typeof turns.stage === 'string' ? turns.stage : '—');
  }

  // ── 数据源 / 新鲜度 ──────────────────────────────────

  function renderFiles(profile) {
    var tbody = $('dsFiles');
    if (!tbody) return;
    tbody.textContent = '';
    var files = profile && profile.files && typeof profile.files === 'object' ? profile.files : null;
    if (!files) {
      var tr0 = document.createElement('tr');
      var td0 = document.createElement('td');
      td0.colSpan = 4;
      td0.className = 'muted small';
      td0.textContent = '无文件信息';
      tr0.appendChild(td0);
      tbody.appendChild(tr0);
      return;
    }

    var known = ['state.json', 'memory.jsonl', 'audit.jsonl'];
    var names = known.slice();
    Object.keys(files).forEach(function (k) {
      if (names.indexOf(k) === -1) names.push(k);
    });

    names.forEach(function (name) {
      var f = files[name];
      if (!f || typeof f !== 'object') return;
      var tr = document.createElement('tr');

      var tdName = document.createElement('td');
      tdName.textContent = name;

      var tdExists = document.createElement('td');
      tdExists.textContent = f.exists === true ? '是' : '否';
      tdExists.className = f.exists === true ? 'v-ok' : 'v-muted';

      var tdBytes = document.createElement('td');
      tdBytes.textContent = f.exists === true ? fmtInt(f.bytes) + ' B' : '—';
      if (f.exists === true && typeof f.bytes === 'number') tdBytes.title = fmtBytes(f.bytes);

      var tdMtime = document.createElement('td');
      tdMtime.textContent = typeof f.mtime === 'number' ? fmtDateTime(f.mtime) : '—';

      tr.appendChild(tdName);
      tr.appendChild(tdExists);
      tr.appendChild(tdBytes);
      tr.appendChild(tdMtime);
      tbody.appendChild(tr);
    });
  }

  function renderSource(data) {
    var profile = data && data.profile ? data.profile : null;
    var source = profile && profile.source ? profile.source : null;

    setText('dsLabel', profile && (profile.label || profile.id) ? (profile.label || profile.id) : '—');

    var display = '—';
    if (source) {
      if (typeof source.display === 'string' && source.display) display = source.display;
      else if (typeof source.resolved_dir === 'string' && source.resolved_dir) display = source.resolved_dir;
      else if (source.type === 'sqlite' && typeof source.path === 'string') display = source.path;
      else if (typeof source.type === 'string') display = '（' + source.type + ' · 目录未解析）';
    }
    setText('dsDisplay', display);

    setText('dsRevision', data && typeof data.revision === 'string' ? data.revision : '—');

    state.staleAfterSec = profile && typeof profile.stale_after_sec === 'number' ? profile.stale_after_sec : null;
    setText('dsStaleAfter', state.staleAfterSec === null ? '—' : state.staleAfterSec + ' 秒');

    renderFiles(profile);
  }

  function collectWarnings(profile, data) {
    var out = [];
    function push(arr) {
      if (!Array.isArray(arr)) return;
      arr.forEach(function (w) {
        if (typeof w === 'string' && out.indexOf(w) === -1) out.push(w);
      });
    }
    push(profile && profile.warnings);
    push(data && data.warnings);
    return out;
  }

  function renderWarnings(profile, data) {
    var box = $('dsWarnings');
    if (!box) return;
    box.textContent = '';
    var warns = collectWarnings(profile, data);
    if (!warns.length) {
      var none = document.createElement('span');
      none.className = 'muted small';
      none.textContent = '无';
      box.appendChild(none);
      showBanner('Warn', '');
      return;
    }
    var labels = [];
    warns.forEach(function (w) {
      var chip = document.createElement('span');
      chip.className = 'chip';
      chip.textContent = WARN_LABEL[w] ? WARN_LABEL[w] + ' · ' + w : w;
      chip.title = w;
      box.appendChild(chip);
      labels.push(WARN_LABEL[w] ? WARN_LABEL[w] + '(' + w + ')' : w);
    });
    showBanner('Warn', '数据源告警：' + labels.join('；'));
  }

  function updateFreshness(data) {
    var fresh = data && data.freshness ? data.freshness : null;
    state.lastAsof = fresh && typeof fresh.data_asof === 'number' ? fresh.data_asof : null;
    state.serverAgeSec = fresh && typeof fresh.data_age_sec === 'number' ? fresh.data_age_sec : null;
    state.serverStale = !!(fresh && fresh.stale === true);

    setText('dsAsof', state.lastAsof === null ? '未记录' : fmtDateTime(state.lastAsof));
    var asofNode = $('dsAsof');
    if (asofNode && state.lastAsof !== null) asofNode.title = new Date(state.lastAsof).toISOString();

    var ageNode = $('dsAge');
    if (ageNode) {
      ageNode.title = typeof state.serverAgeSec === 'number'
        ? '服务端 data_age_sec=' + state.serverAgeSec + ' 秒（抓取时刻）；此处按 data_asof 本地递增'
        : '';
    }

    updateAge();
  }

  function updateAge() {
    // 还没有收到任何快照、或当前 profile 未接入时，不要凭空空谈 fresh / stale。
    var snap = state.snapshot;
    if (!snap || (snap.profile && snap.profile.available === false)) return;

    var age = ageFromAsof(state.lastAsof);
    var stale = state.serverStale;

    if (age === null) {
      if (state.serverAgeSec !== null) {
        setText('dsAge', '约 ' + state.serverAgeSec + ' 秒（服务端）');
      } else {
        setText('dsAge', '—');
      }
    } else {
      setText('dsAge', age + ' 秒');
      if (typeof state.staleAfterSec === 'number' && age > state.staleAfterSec) stale = true;
    }

    var freshTag = $('freshTag');
    if (freshTag) {
      freshTag.textContent = stale ? 'stale' : 'fresh';
      freshTag.className = 'tag tag-' + (stale ? 'stale' : 'fresh');
    }

    if (stale) {
      var text;
      if (age === null) text = '数据陈旧（时长未知）';
      else text = '数据陈旧 ' + age + ' 秒';
      if (typeof state.staleAfterSec === 'number') text += '（阈值 ' + state.staleAfterSec + ' 秒）';
      showBanner('Stale', text);
    } else {
      showBanner('Stale', '');
    }
  }

  // ── 空态 ─────────────────────────────────────────────

  function resetPanels() {
    [
      'phaseUsage', 'phaseThresholds', 'phaseTrend', 'phaseAlerts', 'phaseSource',
      'memActive', 'memUnique', 'memRows', 'memSub',
      'stpaVerdict', 'stpaUcas', 'stpaDowngrades', 'stpaFailMode', 'stpaBlocked', 'stpaSub',
      'auditTotal', 'turnsCount', 'stateWrites', 'stageValue', 'auditSub',
      'dsLabel', 'dsDisplay', 'dsAsof', 'dsAge', 'dsStaleAfter', 'dsRevision'
    ].forEach(function (id) { setText(id, null); });

    ['phaseTag', 'memTag', 'stpaTag', 'auditTag', 'freshTag'].forEach(function (id) {
      setTag(id, '—', 'tag-muted');
    });

    setText('ringPct', '—');
    setText('ringPhase', '未观测');
    setText('sparkMeta', '暂无采样');
    setText('logMeta', '');
    setText('subLine', '正在加载…');
    setText('dsAge', '—');
    var arc = $('ringArc');
    if (arc) {
      arc.style.stroke = '#334155';
      arc.style.strokeDashoffset = String(RING_C);
    }
    var ticks = $('ringTicks');
    if (ticks) ticks.textContent = '';
    var tbody = $('stpaLog');
    if (tbody) {
      tbody.textContent = '';
      var tr = document.createElement('tr');
      var td = document.createElement('td');
      td.colSpan = 3;
      td.className = 'muted small';
      td.textContent = '—';
      tr.appendChild(td);
      tbody.appendChild(tr);
    }
    renderBars('auditKinds', []);
    renderBars('memOrigin', []);
    state.spark = [];
    state.lastSampleKey = null;
    state.lastSampleAt = 0;
    renderSpark();
  }

  function renderEmpty(profile, data) {
    var p = profile || {};
    var empty = $('emptyState');
    var grid = $('dataGrid');
    if (grid) grid.hidden = true;
    if (empty) empty.hidden = false;

    setText('emptyLabel', p.label || p.id || '未命名 profile');
    setText('emptyReason', p.reason || 'data_dir_missing');
    var source = '—';
    if (p.source) {
      if (typeof p.source.display === 'string' && p.source.display) source = p.source.display;
      else if (typeof p.source.resolved_dir === 'string' && p.source.resolved_dir) source = p.source.resolved_dir;
      else if (typeof p.source.type === 'string') source = '（' + p.source.type + ' 目录未解析）';
    }
    setText('emptyDisplay', source);
    setText('emptyHint', p.hint || '该 profile 的数据目录尚未接入。');

    var list = $('emptyWarnings');
    if (list) {
      list.textContent = '';
      var warns = collectWarnings(p, data);
      if (!warns.length) {
        list.hidden = true;
      } else {
        list.hidden = false;
        warns.forEach(function (w) {
          var li = document.createElement('li');
          li.textContent = WARN_LABEL[w] ? WARN_LABEL[w] + ' · ' + w : w;
          list.appendChild(li);
        });
      }
    }

    renderHeader(p);
    setTag('phaseTag', '未观测', 'tag-muted');
    setTag('memTag', '未观测', 'tag-muted');
    setTag('stpaTag', '未观测', 'tag-muted');
    setTag('auditTag', '未观测', 'tag-muted');
    setText('dsLabel', p.label || p.id || '—');
    setText('dsDisplay', source);
    setText('dsStaleAfter', typeof p.stale_after_sec === 'number' ? p.stale_after_sec + ' 秒' : '—');
    setText('dsRevision', data && typeof data.revision === 'string' ? data.revision : '—');
    setText('dsAsof', '未记录');
    setText('dsAge', '—');
    setTag('freshTag', '未接入', 'tag-warn');
    hideError();
  }

  // ── 快照渲染入口 ─────────────────────────────────────

  function applySnapshot(data) {
    if (!data || data.ok !== true) {
      if (data) showApiError(data);
      return;
    }
    state.snapshot = data;
    var profile = data.profile || null;
    var unavailable = !profile || profile.available === false;

    if (unavailable) {
      state.lastAsof = null;
      state.serverStale = false;
      showBanner('Stale', '');
      renderEmpty(profile, data);
      return;
    }

    var grid = $('dataGrid');
    var empty = $('emptyState');
    if (grid) grid.hidden = false;
    if (empty) empty.hidden = true;

    renderHeader(profile);
    renderPhase(data);
    renderMemory(data);
    renderStpa(data);
    renderAudit(data);
    renderSource(data);
    updateFreshness(data);
    renderWarnings(profile, data);
    hideError();
  }

  // ── 连接：SSE / 轮询 ─────────────────────────────────

  function closeStream() {
    if (state.sseRetryTimer) {
      clearTimeout(state.sseRetryTimer);
      state.sseRetryTimer = null;
    }
    if (state.es) {
      try { state.es.close(); } catch (e) { /* 已关闭 */ }
      state.es = null;
    }
  }

  function stopPolling() {
    if (state.pollTimer) {
      clearInterval(state.pollTimer);
      state.pollTimer = null;
    }
  }

  function scheduleSseRetry(profileId) {
    if (state.sseRetryTimer) clearTimeout(state.sseRetryTimer);
    state.sseRetryTimer = setTimeout(function () {
      state.sseRetryTimer = null;
      if (state.selectedId !== profileId) return;
      openStream(profileId, true);
    }, SSE_RETRY_MS);
  }

  function openStream(profileId, isRetry) {
    closeStream();
    if (typeof EventSource !== 'function') {
      startPolling(profileId);
      return;
    }

    var es;
    try {
      es = new EventSource('/api/stream?profile=' + encodeURIComponent(profileId));
    } catch (e) {
      startPolling(profileId);
      return;
    }
    state.es = es;
    if (!isRetry) setConn('connecting', '连接中…');

    es.addEventListener('open', function () {
      if (state.es !== es) return;
      stopPolling();
      setConn('live', '实时（SSE）');
      showBanner('Info', '');
    });

    function onFrame(ev) {
      if (state.es !== es) return;
      var payload;
      try {
        payload = JSON.parse(ev.data);
      } catch (e) {
        return;
      }
      if (payload && payload.ok === true) {
        applySnapshot(payload);
      } else if (payload) {
        showApiError(payload);
      }
    }

    es.addEventListener('snapshot', onFrame);
    es.addEventListener('message', onFrame);

    es.addEventListener('error', function () {
      if (state.es !== es) return;
      closeStream();
      setConn('poll', '轮询（SSE 不可用）');
      startPolling(profileId);
      scheduleSseRetry(profileId);
    });
  }

  function startPolling(profileId) {
    stopPolling();
    setConn('poll', '轮询（SSE 不可用，' + (state.refreshHintMs / 1000) + 's）');
    if (typeof EventSource === 'function') {
      showBanner('Info', 'SSE 连接不可用，已自动降级为每 ' + (state.refreshHintMs / 1000) + ' 秒轮询；后台会定期重试实时流。');
    } else {
      showBanner('Info', '浏览器不支持 EventSource，已使用每 ' + (state.refreshHintMs / 1000) + ' 秒轮询。');
    }

    function tick() {
      if (state.selectedId !== profileId) return;
      fetchJson('/api/snapshot?profile=' + encodeURIComponent(profileId)).then(function (data) {
        if (state.selectedId !== profileId) return;
        if (data && data.ok === true) {
          applySnapshot(data);
        } else if (data) {
          showApiError(data);
          setConn('error', '数据不可用');
        }
      }).catch(function (err) {
        if (state.selectedId !== profileId) return;
        showNetworkError(err);
        setConn('error', '连接中断，重试中…');
      });
    }

    state.pollTimer = setInterval(tick, state.refreshHintMs);
    tick();
  }

  // ── 切换 profile ─────────────────────────────────────

  function selectProfile(profileId) {
    if (!profileId || state.switching) return;
    state.switching = true;
    state.token += 1;
    var token = state.token;
    var sel = $('profileSelect');
    if (sel) sel.disabled = true;

    closeStream();
    stopPolling();
    setConn('connecting', '切换中…');
    hideError();
    showBanner('Stale', '');

    state.selectedId = profileId;
    state.snapshot = null;
    state.lastAsof = null;
    state.serverAgeSec = null;
    state.serverStale = false;
    state.staleAfterSec = null;
    resetPanels();

    var grid = $('dataGrid');
    var empty = $('emptyState');
    if (grid) grid.hidden = false;
    if (empty) empty.hidden = true;

    fetchJson('/api/snapshot?profile=' + encodeURIComponent(profileId)).then(function (data) {
      if (token !== state.token) return;
      if (data && data.ok === true) {
        applySnapshot(data);
        if (state.selectedId === profileId) openStream(profileId);
      } else if (data) {
        showApiError(data);
        setConn('error', '数据不可用');
      }
    }).catch(function (err) {
      if (token !== state.token) return;
      showNetworkError(err);
      setConn('error', '连接失败');
    }).then(function () {
      if (token !== state.token) return;
      state.switching = false;
      if (sel) sel.disabled = false;
    });
  }

  function onSelectChange(ev) {
    var id = ev && ev.target ? ev.target.value : '';
    if (!id || id === state.selectedId) return;
    selectProfile(id);
  }

  // ── 启动 / 退出 ──────────────────────────────────────

  function boot() {
    fetchJson('/api/profiles').then(function (data) {
      if (!data || data.ok !== true) {
        if (data) showApiError(data);
        setConn('error', 'profile 列表不可用');
        var sel = $('profileSelect');
        if (sel) {
          sel.textContent = '';
          var opt = document.createElement('option');
          opt.value = '';
          opt.textContent = '无法读取 /api/profiles';
          sel.appendChild(opt);
          sel.disabled = true;
        }
        return;
      }

      state.profiles = Array.isArray(data.profiles) ? data.profiles : [];
      if (typeof data.refresh_hint_ms === 'number' && data.refresh_hint_ms >= 500) {
        state.refreshHintMs = data.refresh_hint_ms;
      }

      var privacy = data.privacy;
      var privacyBadge = $('privacyBadge');
      if (privacyBadge && privacy) {
        var bits = [];
        if (typeof privacy.policy === 'string') bits.push(privacy.policy);
        if (privacy.raw_text === false) bits.push('raw_text=false');
        if (bits.length) privacyBadge.title = '隐私策略：' + bits.join(' · ');
      }

      fillProfileSelect();

      var def = typeof data.default_profile === 'string' && data.default_profile
        ? data.default_profile
        : (state.profiles[0] && state.profiles[0].id ? String(state.profiles[0].id) : null);

      if (!def) {
        showError('注册表中没有可用 profile', '请检查 console/profiles.json');
        setConn('error', '无可用 profile');
        return;
      }

      selectProfile(def);
    }).catch(function (err) {
      showNetworkError(err);
      setConn('error', '无法连接控制台服务');
    });
  }

  function teardown() {
    closeStream();
    stopPolling();
  }

  function init() {
    var sel = $('profileSelect');
    if (sel) sel.addEventListener('change', onSelectChange);
    window.addEventListener('pagehide', teardown);
    window.addEventListener('beforeunload', teardown);
    setInterval(updateAge, 1000);
    boot();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
}());


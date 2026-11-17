/* shotplan web client. No build step, no framework, no dependencies. */
(function () {
  "use strict";

  var state = {
    result: null, tab: "plan", stats: null, config: null,
    statsLoading: false, catalog: null, understanding: null,
    compare: null, compareLoading: false
  };
  var el = function (id) { return document.getElementById(id); };
  var esc = function (value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  };

  var PRESETS = {
    standard: { hours: 6, budget: 1500, style: "tang", mode: "good", preference: "傍晚光线", avoid: "" },
    "tight-time": { hours: 3, budget: 1500, style: "song", mode: "good", preference: "时间紧", avoid: "" },
    "tight-budget": { hours: 6, budget: 800, style: "republic", mode: "good", preference: "控制预算", avoid: "" },
    avoid: { hours: 6, budget: 1500, style: "tang", mode: "good", preference: "人少", avoid: "datang_furong_garden,big_wild_goose_pagoda" }
  };

  function applyPreset(name) {
    var p = PRESETS[name];
    if (!p) return;
    Object.keys(p).forEach(function (key) { if (el(key)) el(key).value = p[key]; });
  }

  function splitList(value) {
    return String(value || "").split(",").map(function (x) { return x.trim(); }).filter(Boolean);
  }

  /* The knowledge base is the single source of truth for what users can pick.
     Hardcoded dropdowns used to hide 4 of the 5 cities; a new city had to be
     added twice (data + HTML) or it silently stayed unreachable. */
  function cityNote(cityId) {
    if (!state.catalog) return "";
    var city = state.catalog.cities.find(function (c) { return c.id === cityId; });
    if (!city) return "";
    return city.name + " · " + city.location_count + " 个地点 · " +
      city.indoor_count + " 个室内备选 · 资料核验至 " + (city.source_freshness || "—");
  }

  function updateCityNote() {
    var note = el("city-note");
    if (note) note.textContent = cityNote(el("city").value);
  }

  async function loadCatalog() {
    try {
      var response = await fetch("/api/catalog");
      if (!response.ok) throw new Error("HTTP " + response.status);
      var data = await response.json();
      if (!data.cities || !data.cities.length || !data.styles || !data.styles.length) {
        throw new Error("empty catalog");
      }
      state.catalog = data;

      var citySelect = el("city");
      var previousCity = citySelect.value;
      citySelect.innerHTML = "";
      data.cities.forEach(function (city) {
        var option = document.createElement("option");
        option.value = city.id;
        option.textContent = city.name;
        citySelect.appendChild(option);
      });
      if (data.cities.some(function (c) { return c.id === previousCity; })) {
        citySelect.value = previousCity;
      }

      var styleSelect = el("style");
      var previousStyle = styleSelect.value;
      styleSelect.innerHTML = "";
      data.styles.forEach(function (style) {
        var option = document.createElement("option");
        option.value = style.id;
        option.textContent = style.name + (style.era ? "（" + style.era + "）" : "");
        styleSelect.appendChild(option);
      });
      if (data.styles.some(function (s) { return s.id === previousStyle; })) {
        styleSelect.value = previousStyle;
      }

      var note = el("catalog-note");
      if (note) {
        note.textContent = "知识库目录：" + data.city_count + " 座城市 · " +
          data.location_count + " 个地点 · " + data.style_count + " 种风格" +
          (data.knowledge_version ? " · 版本 " + data.knowledge_version : "");
      }
      updateCityNote();
    } catch (error) {
      var note = el("catalog-note");
      if (note) note.textContent = "目录未加载，使用内置选项：" + error.message;
    }
  }

  /* --- Understand one sentence of human intent -------------------------- */
  var FIELD_NAMES = {
    city: "城市", date: "日期", hours: "时长", budget_cny: "预算",
    styles: "风格", party_size: "人数", preferences: "偏好",
    avoid: "排除", atmosphere: "气质"
  };
  var SOURCE_NAMES = { stated: "明说", inferred: "推断", default: "默认" };

  function displayValue(value) {
    if (Array.isArray(value)) return value.length ? value.join("、") : "无";
    if (value === null || value === undefined || value === "") return "—";
    if (typeof value === "number" && String(value).length > 6) return String(value);
    return String(value);
  }

  function fillForm(request) {
    if (!request) return;
    var map = {
      city: "city", date: "date", hours: "hours", budget_cny: "budget",
      party_size: "party"
    };
    Object.keys(map).forEach(function (key) {
      if (request[key] !== undefined && request[key] !== null && el(map[key])) {
        el(map[key]).value = request[key];
      }
    });
    if (request.styles && request.styles.length && el("style")) {
      el("style").value = request.styles[0];
    }
    if (request.preferences && el("preference")) {
      el("preference").value = request.preferences.join(",");
    }
    if (request.avoid && el("avoid")) el("avoid").value = request.avoid.join(",");
    updateCityNote();
  }

  function renderUnderstanding(data) {
    state.understanding = data;
    var html = "<h4>我做了这些假设 · 明说 " + data.stated_count +
      " · 推断 " + data.inferred_count + " · 默认 " + data.default_count + "</h4>";
    data.assumptions.forEach(function (a) {
      html += '<div class="assumption"><span class="field">' +
        esc(FIELD_NAMES[a.field] || a.field) + '</span><span class="value">' +
        esc(displayValue(a.value)) +
        (a.evidence || a.note
          ? '<span class="evidence">' + esc([a.evidence, a.note].filter(Boolean).join(" · ")) + '</span>'
          : "") +
        '</span><span class="tag ' + esc(a.source) + '">' +
        esc(SOURCE_NAMES[a.source] || a.source) + '</span></div>';
    });

    if (data.clarifications && data.clarifications.length) {
      html += "<h4>想跟你确认</h4>";
      data.clarifications.forEach(function (q) {
        html += '<div class="clarify"><b>' + esc(q.why) + '</b>' +
          esc(q.question) + '<br>建议：' + esc(q.suggestion) + '</div>';
      });
    }

    if (data.negotiations && data.negotiations.length) {
      html += "<h4>当前条件不可行，但这样改就行（每项都已实跑预检）</h4>";
      data.negotiations.forEach(function (option, index) {
        html += '<div class="negotiation"><span class="label">' + esc(option.label) +
          '<span class="tradeoff">' + esc(option.tradeoff) + '</span></span>' +
          '<button data-negotiation="' + index + '">采用</button></div>';
      });
    }

    if (data.headroom && data.headroom.length) {
      html += "<h4>当前条件可行，但离下限很近（每项都已实跑预检）</h4>";
      data.headroom.forEach(function (option, index) {
        html += '<div class="negotiation"><span class="label">' + esc(option.label) +
          '<span class="tradeoff">' + esc(option.tradeoff) + '</span></span>' +
          '<button data-headroom="' + index + '">采用</button></div>';
      });
    }

    el("brief-result").innerHTML = html;
    fillForm(data.request);
  }

  async function understandBrief() {
    var text = el("brief").value.trim();
    if (!text) {
      el("brief-result").innerHTML =
        '<div class="clarify"><b>还没输入</b>先随便描述一句，例如「国庆去西安拍唐风，预算一千五」。</div>';
      return;
    }
    var button = el("understand");
    button.disabled = true;
    button.textContent = "正在理解…";
    try {
      var response = await fetch("/api/understand", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: text })
      });
      if (!response.ok) throw new Error("HTTP " + response.status);
      renderUnderstanding(await response.json());
    } catch (error) {
      el("brief-result").innerHTML = '<div class="issue hard"><code>理解失败</code><p>' +
        esc(error.message) + '</p></div>';
    } finally {
      button.disabled = false;
      button.textContent = "理解这句话";
    }
  }

  function buildBody() {
    return {
      city: el("city").value,
      date: el("date").value,
      hours: Number(el("hours").value),
      budget_cny: Number(el("budget").value),
      party_size: Number(el("party").value),
      styles: [el("style").value],
      preferences: splitList(el("preference").value),
      avoid: splitList(el("avoid").value),
      mock_mode: el("mode").value === "server" ? null : el("mode").value
    };
  }

  function setStatus(text, ok) {
    var badge = el("badge-status");
    badge.textContent = text;
    badge.style.background = ok ? "var(--good-soft)" : "var(--bad-soft)";
    badge.style.color = ok ? "var(--good)" : "var(--bad)";
  }

  function infeasibleHtml(r) {
    var f = r.feasibility || {};
    var html = '<div class="issue hard"><code>需求不可满足</code><p>' +
      esc(f.headline || "当前条件无法生成可执行方案，已停止生成以避免硬凑行程。") + '</p></div>';
    html += '<div class="alternatives"><h4>原因与建议</h4><ul>';
    (f.reasons || []).forEach(function (reason) {
      html += '<li><code>' + esc(reason.code) + '</code> · ' + esc(reason.message) +
        '<br>缺口：' + esc(reason.shortfall) + ' ｜ 建议：' + esc(reason.suggestion) + '</li>';
    });
    html += '</ul></div>';
    var m = f.minimums || {};
    html += '<div class="summary"><div class="stat"><b>' + esc(m.min_budget_cny) +
      ' 元</b><span>最低预算</span></div><div class="stat"><b>' + esc(m.min_hours) +
      ' 小时</b><span>最短时长</span></div></div>';
    return html;
  }

  function renderPlan(r) {
    if (r.status === "infeasible") return infeasibleHtml(r);
    var plan = r.plan;
    var html = '<div class="summary">' +
      '<div class="stat"><b>' + esc(plan.slots.length) + '</b><span>时间块</span></div>' +
      '<div class="stat"><b>' + esc(plan.total_cost_cny) + ' 元</b><span>总花费 / 预算 ' + esc(r.request.budget_cny) + '</span></div>' +
      '<div class="stat ' + (r.validation.hard_pass ? "ok" : "fail") + '"><b>' +
        (r.validation.hard_pass ? "通过" : "未通过") + '</b><span>硬约束</span></div>' +
      '<div class="stat"><b>' + esc(r.validation.score.toFixed(3)) + '</b><span>综合得分</span></div>' +
      '</div>';

    html += '<div class="timeline">';
    plan.slots.forEach(function (slot) {
      html += '<div class="slot">' +
        '<div class="slot-head"><span class="slot-time">' + esc(slot.start) + ' — ' + esc(slot.end) +
        '</span><span class="slot-kind">' + esc(slot.activity) + '</span></div>' +
        '<div class="slot-title">' + esc(slot.label) + '</div>' +
        '<div class="slot-notes">' + esc(slot.notes || "—") + '</div>' +
        '<div class="slot-meta">' + esc(slot.location_id) + ' · ' + esc(slot.cost_cny) +
        ' 元 · ' + esc((slot.source_refs || []).join(", ") || "无引用") + '</div>' +
        '</div>';
    });
    html += "</div>";

    if (r.explanations && r.explanations.length) {
      html += '<div class="alternatives"><h4>为什么这样排</h4><ul>';
      r.explanations.forEach(function (e) {
        html += "<li><b>" + esc(e.start) + "–" + esc(e.end) + " " + esc(e.label) + "</b>";
        e.reasons.forEach(function (reason) {
          html += "<br>· " + esc(reason);
        });
        html += "</li>";
      });
      html += "</ul></div>";
    }

    if (plan.alternatives && plan.alternatives.length) {
      html += '<div class="alternatives"><h4>备选方案</h4><ul>';
      plan.alternatives.forEach(function (a) {
        html += "<li>[" + esc(a.trigger) + "] " + esc(a.description) + "</li>";
      });
      html += "</ul></div>";
    }
    if (plan.assumptions && plan.assumptions.length) {
      html += '<div class="alternatives"><h4>方案假设</h4><ul>';
      plan.assumptions.forEach(function (a) { html += "<li>" + esc(a) + "</li>"; });
      html += "</ul></div>";
    }
    return html;
  }

  function renderValidation(r) {
    var v = r.validation;
    var html = '<div class="summary"><div class="stat ' + (v.hard_pass ? "ok" : "fail") +
      '"><b>' + esc(v.score.toFixed(3)) + '</b><span>加权总分</span></div>' +
      '<div class="stat"><b>' + esc(v.issues.length) + '</b><span>问题数（硬 ' +
      esc(v.issues.filter(function (i) { return i.severity === "hard"; }).length) + '）</span></div></div>';
    html += "<h4 style='margin:0 0 12px;font-size:13px'>分项得分</h4>";
    Object.keys(v.scores).forEach(function (key) {
      var value = v.scores[key];
      html += '<div class="score-row"><span>' + esc(key) + '</span>' +
        '<div class="bar"><i style="width:' + Math.round(value * 100) + '%"></i></div>' +
        "<b>" + esc(value.toFixed(3)) + "</b></div>";
    });
    html += "<h4 style='margin:22px 0 12px;font-size:13px'>校验明细</h4>";
    if (!v.issues.length) {
      html += '<div class="issue soft" style="background:var(--good-soft);border-color:var(--good)">全部约束通过，没有发现问题。</div>';
    }
    v.issues.forEach(function (issue) {
      html += '<div class="issue ' + esc(issue.severity) + '"><code>[' +
        esc(issue.severity.toUpperCase()) + '] ' + esc(issue.code) + "</code> · " + esc(issue.message) +
        "<p>期望：" + esc(issue.expected || "—") + " ｜ 修复建议：" + esc(issue.suggestion || "—") + "</p></div>";
    });
    return html;
  }

  function renderTrace(r) {
    var t = r.trace;
    var html = '<div class="summary">' +
      '<div class="stat"><b>' + esc(t.total_duration_ms.toFixed(1)) + ' ms</b><span>总耗时</span></div>' +
      '<div class="stat"><b>' + esc(t.total_cost_cny.toFixed(6)) + '</b><span>估算成本（元）</span></div>' +
      '<div class="stat"><b>' + esc(t.repair_count) + '</b><span>修复轮次</span></div></div>';
    html += "<table><thead><tr><th>#</th><th>步骤</th><th class='num'>耗时 ms</th>" +
      "<th class='num'>token in</th><th class='num'>token out</th><th>细节</th></tr></thead><tbody>";
    t.events.forEach(function (e) {
      var detail = Object.keys(e.detail).map(function (k) {
        return k + "=" + e.detail[k];
      }).join(", ");
      html += "<tr><td>" + esc(e.step) + "</td><td>" + esc(e.name) +
        "</td><td class='num'>" + esc(e.duration_ms.toFixed(1)) + "</td><td class='num'>" +
        esc(e.tokens_in) + "</td><td class='num'>" + esc(e.tokens_out) +
        "</td><td>" + esc(detail || "—") + "</td></tr>";
    });
    html += "</tbody></table>";
    html += "<h4 style='margin:22px 0 10px;font-size:13px'>运行标识</h4><pre>" +
      esc(t.run_id + "  |  " + t.provider + "  |  " + t.prompt_version + "  |  " + t.created_at) +
      "</pre>";
    return html;
  }

  async function loadStats() {
    if (state.statsLoading) return;
    state.statsLoading = true;
    try {
      var results = await Promise.all([
        fetch("/api/stats").then(function (r) {
          if (!r.ok) throw new Error("HTTP " + r.status);
          return r.json();
        }),
        // /api/config carries persist_mode and the write-behind queue stats.
        // A missing or older config endpoint must not break the stats view.
        fetch("/api/config").then(function (r) {
          return r.ok ? r.json() : null;
        }).catch(function () { return null; })
      ]);
      state.stats = results[0];
      state.config = results[1];
    } catch (error) {
      state.stats = { error: error.message };
    } finally {
      state.statsLoading = false;
      if (state.tab === "stats") render();
    }
  }

  function renderPersistence(cfg) {
    if (!cfg) return "";
    var mode = cfg.persist_mode || "sync";
    var modeNote = {
      sync: "同步写入：每个响应返回前已提交，无积压概念。",
      async: "写后置队列：响应先返回，后台线程批量提交。",
      off: "写入已关闭：本页统计不会增长。"
    }[mode] || mode;
    var html = '<div class="alternatives"><h4>持久化 · <code>' + esc(mode) + '</code></h4>';
    var w = cfg.writer;
    if (mode === "async" && w) {
      // pending is the honest answer to "did my write land yet": non-zero is
      // normal for a moment after a burst, stuck-at-non-zero is a problem.
      html += '<div class="summary">' +
        '<div class="stat ' + (w.pending > 0 ? "warn" : "ok") + '"><b>' + esc(w.pending) +
        '</b><span>排队中（未落库）</span></div>' +
        '<div class="stat ok"><b>' + esc(w.written) + '</b><span>已落库</span></div>' +
        '<div class="stat ' + (w.failed > 0 ? "fail" : "ok") + '"><b>' + esc(w.failed) +
        '</b><span>写入失败</span></div>' +
        '<div class="stat ' + (w.sync_fallbacks > 0 ? "warn" : "ok") + '"><b>' +
        esc(w.sync_fallbacks) + '</b><span>队列满转同步</span></div>' +
        '<div class="stat"><b>' + esc(w.batches) + '</b><span>批次（最大 ' +
        esc(w.max_batch) + ' 行）</span></div>' +
        '</div>';
      html += '<p class="hint small">队列满时退化为同步写，不丢行；' +
        '进程被硬杀时未提交的行会丢（窗口已实测，见 docs/persistence.md）。' +
        'pending 长时间不为 0 说明写线程跟不上。</p>';
    } else {
      html += '<p class="hint small">' + esc(modeNote) +
        ' 设 SHOTPLAN_PERSIST_MODE=async 可在此看到写入积压。</p>';
    }
    html += '<button class="ghost" id="refresh-stats">刷新统计</button></div>';
    return html;
  }

  function renderStats() {
    if (!state.stats) {
      loadStats();
      return '<div class="empty"><p>正在读取运行统计…</p></div>';
    }
    var d = state.stats;
    if (d.error) {
      return '<div class="issue hard"><code>无法读取 /api/stats</code><p>' +
        esc(d.error) + '</p></div>';
    }
    var o = d.overall || {};
    var html = '<div class="summary">' +
      '<div class="stat"><b>' + esc(d.total_runs) + '</b><span>运行数（窗口 ' +
        esc(d.window) + '）</span></div>' +
      '<div class="stat ' + (o.hard_pass_rate >= 0.9 ? "ok" : "fail") + '"><b>' +
        esc(((o.hard_pass_rate || 0) * 100).toFixed(1)) + '%</b><span>硬约束通过率</span></div>' +
      '<div class="stat"><b>' + esc(o.avg_score || 0) + '</b><span>平均得分</span></div>' +
      '<div class="stat"><b>' + esc(o.p95_duration_ms || 0) + ' ms</b><span>P95 耗时</span></div>' +
      '</div>';
    var statuses = d.by_status || {};
    html += '<div class="alternatives"><h4>结果状态</h4><ul>';
    Object.keys(statuses).forEach(function (k) {
      html += '<li>' + esc(k) + '：' + esc(statuses[k]) + ' 次</li>';
    });
    html += '</ul></div>';
    html += "<h4 style='margin:22px 0 12px;font-size:13px'>按 provider / prompt 版本</h4>";
    html += "<table><thead><tr><th>provider</th><th>prompt</th><th class='num'>运行</th>" +
      "<th class='num'>通过率</th><th class='num'>均分</th><th class='num'>P50/P95 ms</th>" +
      "<th class='num'>修复</th><th class='num'>不可行</th></tr></thead><tbody>";
    (d.groups || []).forEach(function (g) {
      html += "<tr><td>" + esc(g.provider) + "</td><td>" + esc(g.prompt_version) + "</td>" +
        "<td class='num'>" + esc(g.runs) + "</td>" +
        "<td class='num'>" + esc(((g.hard_pass_rate || 0) * 100).toFixed(1)) + "%</td>" +
        "<td class='num'>" + esc(g.avg_score || 0) + "</td>" +
        "<td class='num'>" + esc(g.p50_duration_ms || 0) + " / " +
        esc(g.p95_duration_ms || 0) + "</td>" +
        "<td class='num'>" + esc(g.total_repairs || 0) + "</td>" +
        "<td class='num'>" + esc(g.infeasible || 0) + "</td></tr>";
    });
    html += "</tbody></table>";
    html += renderPersistence(state.config);
    html += '<p class="hint small">数据来自本机 SQLite 运行历史；' +
      'infeasible 运行按硬约束失败计入，离线 provider 估算成本为 0。</p>';
    return html;
  }

  /* --- Three strategies, three real plans, one evidenced recommendation --- */
  function renderComparison(data) {
    var rec = data.recommendation;
    var html = '<div class="compare-head"><h3>同一个需求，三种取舍</h3>' +
      '<p>每份都走完整流水线并经过同一套校验器；下面写的差异是实际算出来的，不是文案。</p>' +
      '<p><b>推荐：' + esc(rec.id) + '</b> · ' + esc(rec.reason) +
      (rec.evidence && rec.evidence.length ? ' · 依据：' + esc(rec.evidence.join("、")) : '') +
      '</p>' +
      (rec.note ? '<p class="compare-note">' + esc(rec.note) + '</p>' : '') +
      '<button id="refresh-compare" class="secondary">按当前表单重新对比</button></div>';
    html += '<div class="compare-grid">';
    data.strategies.forEach(function (s, index) {
      var m = s.metrics;
      var recommended = s.id === rec.id;
      html += '<div class="compare-card' + (recommended ? ' recommended' : '') + '">' +
        (recommended ? '<span class="ribbon">推荐</span>' : '') +
        '<h4>' + esc(s.label) + '</h4><p class="intent">' + esc(s.intent) + '</p>' +
        '<div class="compare-metrics">' +
          '<div><b>' + esc(m.hard_pass ? "通过" : "未通过") + '</b><span>硬约束</span></div>' +
          '<div><b>' + esc(m.score) + '</b><span>综合得分</span></div>' +
          '<div><b>' + esc(m.total_cost_cny) + ' 元</b><span>方案费用</span></div>' +
          '<div><b>' + esc(m.shoot_count) + ' 处</b><span>拍摄点</span></div>' +
          '<div><b>' + esc(m.travel_count) + ' 次</b><span>转场</span></div>' +
          '<div><b>' + esc(m.shoot_minutes) + ' 分</b><span>有效拍摄</span></div>' +
          '<div><b>' + esc(m.light_minutes) + ' 分</b><span>黄金窗口内</span></div>' +
        '</div>' +
        '<h5>放弃了什么</h5><ul>' +
        s.tradeoffs.map(function (t) { return '<li>' + esc(t) + '</li>'; }).join("") +
        '</ul>' +
        (s.identical_to && s.identical_to.length
          ? '<p class="compare-note">与 ' + esc(s.identical_to.join("、")) + ' 产出相同方案</p>'
          : '') +
        '<p class="compare-params">时长 ' + esc(s.request.hours) + 'h · 预算 ' +
          esc(s.request.budget_cny) + ' 元' +
          (s.request.avoid && s.request.avoid.length
            ? ' · 排除 ' + esc(s.request.avoid.length) + ' 个地点' : '') + '</p>' +
        '<button data-compare="' + index + '">用这个方向生成</button>' +
      '</div>';
    });
    html += '</div>';
    return html;
  }

  async function loadCompare() {
    if (state.compareLoading) return;
    state.compareLoading = true;
    try {
      var response = await fetch("/api/compare", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ request: buildBody() })
      });
      if (!response.ok) throw new Error("HTTP " + response.status);
      state.compare = await response.json();
    } catch (error) {
      state.compare = { error: error.message };
    } finally {
      state.compareLoading = false;
      if (state.tab === "compare") render();
    }
  }

  function render() {
    var r = state.result;
    var view = el("view");
    if (state.tab === "stats") { view.innerHTML = renderStats(); return; }
    if (state.tab === "compare") {
      if (!state.compare) {
        view.innerHTML = '<div class="empty"><p>还没有对比</p><span>点一下「按当前表单重新对比」生成三种取向的方案</span>' +
          '<br><button id="refresh-compare" class="secondary">按当前表单重新对比</button></div>';
      } else if (state.compare.error) {
        view.innerHTML = '<div class="issue hard"><code>对比失败</code><p>' +
          esc(state.compare.error) + '</p></div>' +
          '<button id="refresh-compare" class="secondary">重试</button>';
      } else {
        view.innerHTML = renderComparison(state.compare);
      }
      return;
    }
    if (!r) return;
    if (state.tab === "plan") view.innerHTML = renderPlan(r);
    else if (state.tab === "validation") view.innerHTML = renderValidation(r);
    else if (state.tab === "trace") view.innerHTML = renderTrace(r);
    else view.innerHTML = "<pre>" + esc(JSON.stringify(r, null, 2)) + "</pre>";
  }

  async function generate() {
    var button = el("run");
    button.disabled = true;
    button.textContent = "生成中…";
    setStatus("运行中", true);
    try {
      var response = await fetch("/api/plan", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(buildBody())
      });
      if (!response.ok) {
        var detail = await response.text();
        throw new Error("HTTP " + response.status + " " + detail.slice(0, 200));
      }
      state.result = await response.json();
      el("badge-provider").textContent = "provider: " + state.result.trace.provider;
      if (state.result.status === "infeasible") {
        setStatus("需求不可满足", false);
      } else {
        setStatus(state.result.validation.hard_pass ? "硬约束通过" : "发现问题",
                  state.result.validation.hard_pass);
      }
      render();
    } catch (error) {
      setStatus("请求失败", false);
      el("view").innerHTML = '<div class="issue hard"><code>无法连接服务端</code><p>' +
        esc(error.message) + '<br>请先运行 <b>python -m shotplan.cli serve</b>，再刷新本页面。</p></div>';
    } finally {
      button.disabled = false;
      button.textContent = "生成方案";
    }
  }

  el("run").addEventListener("click", generate);
  el("city").addEventListener("change", updateCityNote);
  el("understand").addEventListener("click", understandBrief);
  el("brief").addEventListener("keydown", function (event) {
    if ((event.ctrlKey || event.metaKey) && event.key === "Enter") understandBrief();
  });
  document.addEventListener("click", function (event) {
    var target = event.target;
    if (target && target.dataset && target.dataset.negotiation !== undefined &&
        state.understanding) {
      var option = state.understanding.negotiations[Number(target.dataset.negotiation)];
      if (option) {
        fillForm(option.request);
        setStatus("已采用调整，可重新生成", true);
      }
    }
    if (target && target.dataset && target.dataset.headroom !== undefined &&
        state.understanding) {
      var headroom = state.understanding.headroom[Number(target.dataset.headroom)];
      if (headroom) {
        fillForm(headroom.request);
        setStatus("已采纳余量建议，可重新生成", true);
      }
    }
  });
  loadCatalog();
  document.addEventListener("click", function (event) {
    if (event.target && event.target.id === "refresh-stats") loadStats();
    if (event.target && event.target.id === "refresh-compare") {
      state.compare = null;
      loadCompare();
    }
    if (event.target && event.target.dataset &&
        event.target.dataset.compare !== undefined && state.compare && state.compare.strategies) {
      var chosen = state.compare.strategies[Number(event.target.dataset.compare)];
      if (chosen) {
        fillForm(chosen.request);
        state.tab = "plan";
        document.querySelectorAll(".tab").forEach(function (t) {
          t.classList.toggle("active", t.dataset.tab === "plan");
        });
        generate();
      }
    }
  });
  document.querySelectorAll(".tab").forEach(function (tab) {
    tab.addEventListener("click", function () {
      document.querySelectorAll(".tab").forEach(function (t) { t.classList.remove("active"); });
      tab.classList.add("active");
      state.tab = tab.dataset.tab;
      render();
      // Re-fetch on every visit so queue stats are current, not cached.
      if (state.tab === "stats") loadStats();
      if (state.tab === "compare" && !state.compare) loadCompare();
    });
  });
  document.querySelectorAll("[data-preset]").forEach(function (button) {
    button.addEventListener("click", function () { applyPreset(button.dataset.preset); });
  });

  fetch("/api/health").then(function (r) { return r.json(); }).then(function (data) {
    el("badge-provider").textContent = "provider: " + data.provider;
  }).catch(function () { /* offline preview: generate() explains what to do */ });
})();

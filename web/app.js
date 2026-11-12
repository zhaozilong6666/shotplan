/* shotplan web client. No build step, no framework, no dependencies. */
(function () {
  "use strict";

  var state = { result: null, tab: "plan" };
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

  function renderPlan(r) {
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

  function render() {
    var r = state.result;
    if (!r) return;
    var view = el("view");
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
      setStatus(state.result.validation.hard_pass ? "硬约束通过" : "发现问题", state.result.validation.hard_pass);
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
  document.querySelectorAll(".tab").forEach(function (tab) {
    tab.addEventListener("click", function () {
      document.querySelectorAll(".tab").forEach(function (t) { t.classList.remove("active"); });
      tab.classList.add("active");
      state.tab = tab.dataset.tab;
      render();
    });
  });
  document.querySelectorAll("[data-preset]").forEach(function (button) {
    button.addEventListener("click", function () { applyPreset(button.dataset.preset); });
  });

  fetch("/api/health").then(function (r) { return r.json(); }).then(function (data) {
    el("badge-provider").textContent = "provider: " + data.provider;
  }).catch(function () { /* offline preview: generate() explains what to do */ });
})();

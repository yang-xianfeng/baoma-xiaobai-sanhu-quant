// Market Sentiment & Flow Dashboard
// Simple, data-first rendering for the 0-100 behavior indices and style buckets.

const DATA_STATUS_LABEL = {
  finalized: "收盘定稿",
  provisional: "盘中预览",
};

function isNum(value) {
  return typeof value === "number" && Number.isFinite(value);
}

function fmt(value, digits, suffix) {
  if (!isNum(value)) return "N/A";
  return value.toFixed(digits === undefined ? 2 : digits) + (suffix || "");
}

function fmtSigned(value, digits, suffix) {
  if (!isNum(value)) return "N/A";
  return (value > 0 ? "+" : "") + value.toFixed(digits === undefined ? 2 : digits) + (suffix || "");
}

function valueClass(value) {
  if (!isNum(value)) return "na";
  return value >= 0 ? "val-up" : "val-down";
}

function esc(value) {
  return String(value === undefined || value === null ? "" : value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

document.addEventListener("DOMContentLoaded", async () => {
  const savedTheme = localStorage.getItem("msf-theme");
  if (savedTheme) document.documentElement.setAttribute("data-theme", savedTheme);

  let indexData = window.INDEX_DATA || null;
  let historyData = window.HISTORY_DATA || null;

  if (!indexData) {
    try {
      const resp = await fetch("./data/latest.json");
      if (resp.ok) indexData = await resp.json();
    } catch (e) {
      console.warn("Could not fetch latest.json", e);
    }
  }
  if (!historyData) {
    try {
      const resp = await fetch("./data/history.json");
      if (resp.ok) historyData = await resp.json();
    } catch (e) {
      console.warn("Could not fetch history.json", e);
    }
  }

  if (!indexData) {
    document.body.innerHTML = "<div style='color:var(--text);text-align:center;padding:60px;'>数据加载失败，请检查 data.js 或 latest.json。</div>";
    return;
  }

  renderHeader(indexData);
  renderStatusBanner(indexData.status, indexData);
  renderGauges(indexData.scores || {});
  renderIndexStrip(indexData.indices_overview || {});
  renderStyles(indexData.styles || []);
  renderMicrostructure(indexData.microstructure || {});
  initFlowChart(indexData.sectors || []);
  if (historyData && historyData.length) initTrendChart(historyData);
  renderSectors(indexData.sectors || [], indexData.categories || []);
  setupInteractivity();
});

function renderHeader(data) {
  const dateEl = document.getElementById("marketDateText");
  if (dateEl) {
    const status = DATA_STATUS_LABEL[data.data_status] || data.data_status || "";
    const session = data.session_title ? " · " + data.session_title : "";
    dateEl.textContent = (data.market_date || "--") + " · " + status + session;
  }

  const cb = document.getElementById("confidenceBadge");
  if (cb) {
    const value = isNum(data.confidence) ? data.confidence : 0;
    cb.textContent = "置信度 " + value.toFixed(1) + "%";
    cb.classList.remove("confidence-ok", "confidence-warn", "confidence-bad");
    cb.classList.add(value >= 75 ? "confidence-ok" : value >= 55 ? "confidence-warn" : "confidence-bad");
    const coverage = data.coverage || {};
    cb.title = "因子覆盖率 · 散户 " + Math.round((coverage.retail || 0) * 100) +
      "% / 宝妈 " + Math.round((coverage.baoma || 0) * 100) +
      "% / 小白 " + Math.round((coverage.novice || 0) * 100) + "%";
  }
}

function renderStatusBanner(status, data) {
  status = status || {};
  const tagEl = document.getElementById("statusTag");
  const summaryEl = document.getElementById("statusSummary");
  const actionEl = document.getElementById("statusAction");
  const noteEl = document.getElementById("dataQualityNote");

  if (tagEl) {
    tagEl.textContent = status.tag || "数据加载中";
    tagEl.className = "badge-status " + (status.badge_class || "badge-info");
  }
  if (summaryEl) summaryEl.textContent = status.summary || "";
  if (actionEl) actionEl.textContent = "风控建议: " + (status.action || "--");

  if (noteEl) {
    const quality = data.data_quality || {};
    const missing = quality.missing_factors || [];
    const degraded = (quality.sources || [])
      .filter(item => item.status && item.status !== "ok")
      .map(item => item.name + "(" + item.status + ")");
    const parts = [];
    if (missing.length) parts.push("缺失/降权因子: " + missing.join(", "));
    if (degraded.length) parts.push("降级数据源: " + degraded.join(", "));
    if (data.sectors_stale) parts.push("板块矩阵沿用 " + data.sectors_source_date + " 定稿");
    noteEl.textContent = parts.length
      ? "数据质量 · " + parts.join("  |  ")
      : "数据质量 · 核心因子在线，横截面与交易日已对齐。";
  }
}

function renderGauges(scores) {
  const pairs = [
    ["scoreComposite", "arcComposite", scores.composite],
    ["scoreBaoma", "arcBaoma", scores.baoma],
    ["scoreNovice", "arcNovice", scores.novice],
    ["scoreRetail", "arcRetail", scores.retail],
  ];
  pairs.forEach(([scoreId, arcId, value]) => {
    const score = Math.round(isNum(value) ? value : 50);
    const scoreEl = document.getElementById(scoreId);
    const arcEl = document.getElementById(arcId);
    if (scoreEl) scoreEl.textContent = score;
    if (arcEl) {
      setTimeout(() => arcEl.setAttribute("stroke-dasharray", score + ", 100"), 80);
    }
  });
}

function renderIndexStrip(indices) {
  const el = document.getElementById("indexStrip");
  if (!el) return;
  const order = [
    ["000001", "上证指数"],
    ["000300", "沪深300"],
    ["399673", "创业板50"],
    ["000688", "科创50"],
    ["399303", "国证2000"],
  ];
  const tiles = order.map(([code, fallbackName]) => {
    const item = indices[code];
    if (!item) return "";
    const pct = item.pct;
    const cls = isNum(pct) ? (pct >= 0 ? "val-up" : "val-down") : "metric-val na";
    return "<div class='index-tile'>" +
      "<div class='index-name'>" + esc(item.name || fallbackName) + "</div>" +
      "<div class='index-close'>" + fmt(item.close, 2) + "</div>" +
      "<div class='index-pct " + cls + "'>" + fmtSigned(pct, 2, "%") + "</div>" +
      "</div>";
  }).join("");
  el.innerHTML = tiles || "<div class='empty-state'>指数数据暂缺</div>";
}

function renderStyles(styles) {
  const el = document.getElementById("styleGrid");
  if (!el) return;
  if (!styles || !styles.length) {
    el.innerHTML = "<div class='empty-state'>风格数据暂缺</div>";
    return;
  }
  el.innerHTML = styles.map(style => {
    const pct = style.pct;
    const pctCls = isNum(pct) ? (pct >= 0 ? "val-up" : "val-down") : "metric-val na";
    const pctText = fmtSigned(pct, 2, "%");
    let flowText = "N/A";
    if (isNum(style.net_inflow_billion)) {
      flowText = "净流入 " + fmtSigned(style.net_inflow_billion, 2, " 亿");
    } else if (style.flow_quality === "none") {
      flowText = style.id === "speculation" ? "微盘 / 涨停生态" : "指数涨跌";
    }
    const heat = isNum(style.heat) ? Math.round(style.heat) : 0;
    const members = (style.members || []).join(" · ");
    return "<div class='style-card" + (style.available ? "" : " is-empty") + "' title='" + esc(style.detail || members) + "'>" +
      "<div class='style-top'>" +
        "<div><div class='style-name'>" + esc(style.name) + "</div>" +
        "<div class='style-sub'>" + esc(style.subtitle) + "</div></div>" +
        "<span class='style-tag " + esc(style.tag_class || "tag-neutral") + "'>" + esc(style.tag || "数据暂缺") + "</span>" +
      "</div>" +
      "<div class='style-main'>" +
        "<span class='style-pct " + pctCls + "'>" + pctText + "</span>" +
        "<span class='style-flow'>" + flowText + "</span>" +
      "</div>" +
      "<div class='style-bar'><i class='style-bar-fill' data-heat='" + heat + "'></i></div>" +
      "<div class='style-foot'><span>热度 " + heat + "</span>" +
        "<span class='style-members'>" + esc(members) + "</span></div>" +
    "</div>";
  }).join("");

  setTimeout(() => {
    el.querySelectorAll(".style-bar-fill").forEach(bar => {
      bar.style.width = (parseInt(bar.getAttribute("data-heat"), 10) || 0) + "%";
    });
  }, 60);
}

let flowChartInstance = null;
function initFlowChart(sectors) {
  const el = document.getElementById("flowChart");
  if (!el) return;
  const rows = (sectors || []).filter(item => item.available && isNum(item.net_inflow_billion) && item.net_inflow_billion !== 0);
  if (rows.length < 2) {
    el.innerHTML = "<div class='empty-state'>资金流数据暂缺，等待下次收盘定稿。</div>";
    return;
  }

  const positives = rows.filter(item => item.net_inflow_billion > 0)
    .sort((a, b) => b.net_inflow_billion - a.net_inflow_billion).slice(0, 5);
  const negatives = rows.filter(item => item.net_inflow_billion < 0)
    .sort((a, b) => a.net_inflow_billion - b.net_inflow_billion).slice(0, 5);
  const picked = negatives.slice().sort((a, b) => a.net_inflow_billion - b.net_inflow_billion)
    .concat(positives.slice().sort((a, b) => a.net_inflow_billion - b.net_inflow_billion));

  const names = picked.map(item => item.name);
  const values = picked.map(item => item.net_inflow_billion);
  const maxAbs = Math.max.apply(null, values.map(v => Math.abs(v))) || 1;

  flowChartInstance = echarts.init(el, null, { renderer: "canvas" });
  flowChartInstance.setOption({
    backgroundColor: "transparent",
    animationDuration: 550,
    grid: { left: 92, right: 54, top: 16, bottom: 22 },
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "shadow" },
      backgroundColor: "rgba(15, 20, 27, 0.96)",
      borderColor: "rgba(255,255,255,0.12)",
      textStyle: { color: "#e7edf5", fontSize: 11 },
      formatter: params => {
        const item = picked[params[0].dataIndex] || {};
        return "<b>" + esc(item.name) + "</b><br/>净流入 " + fmtSigned(item.net_inflow_billion, 2, " 亿") +
          "<br/>涨跌 " + fmtSigned(item.pct, 2, "%") + " · 热度 " + fmt(item.sector_heat, 1);
      },
    },
    xAxis: {
      type: "value",
      min: -maxAbs * 1.18,
      max: maxAbs * 1.18,
      axisLine: { show: false },
      axisTick: { show: false },
      splitLine: { lineStyle: { color: "rgba(148,163,184,0.09)" } },
      axisLabel: { color: "#6b7889", fontSize: 9.5, formatter: value => value + "亿" },
    },
    yAxis: {
      type: "category",
      data: names,
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: { color: "#9ba8ba", fontSize: 10.5, width: 84, overflow: "truncate" },
    },
    series: [{
      type: "bar",
      data: values,
      barWidth: 11,
      itemStyle: {
        borderRadius: 4,
        color: params => params.value >= 0 ? "#58b98a" : "#e26a7a",
      },
      label: {
        show: true,
        position: "right",
        color: "#9ba8ba",
        fontSize: 9.5,
        formatter: params => (params.value > 0 ? "+" : "") + params.value.toFixed(1) + "亿",
      },
      markLine: {
        silent: true,
        symbol: "none",
        lineStyle: { color: "rgba(148,163,184,0.32)", width: 1 },
        data: [{ xAxis: 0 }],
      },
    }],
  });
  window.addEventListener("resize", () => flowChartInstance && flowChartInstance.resize());
}

function renderMicrostructure(ms) {
  const mainFlowEl = document.getElementById("valMainFlow");
  const retailFlowEl = document.getElementById("valRetailFlow");
  const flowDivEl = document.getElementById("valFlowDiv");

  if (mainFlowEl) {
    mainFlowEl.textContent = isNum(ms.main_net_flow_billion) ? fmtSigned(ms.main_net_flow_billion, 2, " 亿") : "N/A";
    mainFlowEl.className = "metric-val " + valueClass(ms.main_net_flow_billion);
  }
  if (retailFlowEl) {
    retailFlowEl.textContent = isNum(ms.retail_net_flow_billion) ? fmtSigned(ms.retail_net_flow_billion, 2, " 亿") : "N/A";
    retailFlowEl.className = "metric-val " + valueClass(ms.retail_net_flow_billion);
  }
  if (flowDivEl) {
    flowDivEl.textContent = isNum(ms.flow_divergence_billion) ? fmtSigned(ms.flow_divergence_billion, 2, " 亿") : "N/A";
    flowDivEl.className = "metric-val " + valueClass(ms.flow_divergence_billion);
  }

  const marginBal = document.getElementById("valMarginBal");
  if (marginBal) marginBal.textContent = fmt(ms.margin_balance_billion, 2, " 亿");
  const marginBuy = document.getElementById("valMarginBuy");
  if (marginBuy) marginBuy.textContent = fmt(ms.margin_buy_billion, 2, " 亿");
  const marginRatio = document.getElementById("valMarginRatio");
  if (marginRatio) {
    const stale = ms.margin_stale_days > 0 ? " · T-" + ms.margin_stale_days : "";
    marginRatio.textContent = fmt(ms.margin_buy_ratio_pct, 2, "%") + " (" + esc(ms.margin_date || "N/A") + stale + ")";
  }

  const zt = document.getElementById("valZtDtCount");
  if (zt) zt.textContent = (ms.limit_up_count || 0) + " / " + (ms.limit_down_count || 0) + " 家";
  const zb = document.getElementById("valZbCount");
  if (zb) zb.textContent = (ms.broken_board_count || 0) + " 家";
  const zbRate = document.getElementById("valZbRate");
  if (zbRate) {
    zbRate.textContent = fmt(ms.broken_rate_pct, 2, "%") + " / " + (ms.max_consecutive_board || 1) + " 连板" +
      (ms.limit_pool_estimated ? " · 估算" : "");
  }

  const total = document.getElementById("valTotalTurnover");
  if (total) {
    total.textContent = fmt(ms.total_turnover_billion, 2, " 亿") +
      (isNum(ms.market_turnover_rate_pct) ? " · 换手 " + ms.market_turnover_rate_pct + "%" : "");
  }
  const advDec = document.getElementById("valAdvDec");
  if (advDec) {
    const adv = ms.advances || 0;
    const dec = ms.declines || 0;
    const ratio = adv + dec > 0 ? Math.round(adv / (adv + dec) * 100) : 0;
    advDec.textContent = adv + " / " + dec + " (" + ratio + "%)";
  }
  const biasEl = document.getElementById("valBias60");
  if (biasEl) {
    biasEl.textContent = fmtSigned(ms.bias60_pct, 2, "%");
    biasEl.className = "metric-val " + valueClass(ms.bias60_pct);
  }
}

let chartInstance = null;
function initTrendChart(history) {
  const el = document.getElementById("trendChart");
  if (!el) return;
  chartInstance = echarts.init(el, null, { renderer: "canvas" });

  function updateChart(days) {
    const count = Math.min(days, history.length);
    const subset = history.slice(-count);
    const dates = subset.map(item => item.date);
    const composite = subset.map(item => item.composite);
    const baoma = subset.map(item => item.baoma);
    const novice = subset.map(item => item.novice);
    const retail = subset.map(item => item.retail);
    const closes = subset.map(item => item.close);

    const titleEl = document.getElementById("trendTitle");
    if (titleEl) titleEl.textContent = "情绪与指数 · 近" + count + "个交易日";

    chartInstance.setOption({
      backgroundColor: "transparent",
      animationDuration: 500,
      tooltip: {
        trigger: "axis",
        backgroundColor: "rgba(15, 20, 27, 0.96)",
        borderColor: "rgba(255,255,255,0.12)",
        textStyle: { color: "#e7edf5", fontSize: 11 },
        formatter: params => {
          const point = subset[params[0].dataIndex] || {};
          let tip = "<div style='font-weight:600;margin-bottom:4px;'>" + params[0].axisValue +
            (point.estimated ? " · 回溯估算" : " · 实际") + "</div>";
          params.forEach(item => {
            tip += "<div style='display:flex;justify-content:space-between;gap:18px;'>" +
              "<span>" + item.marker + " " + item.seriesName + "</span>" +
              "<span style='font-family:monospace;font-weight:600;'>" + item.value + "</span></div>";
          });
          return tip;
        },
      },
      legend: {
        data: ["综合情绪", "宝妈入场", "小白跟风", "散户情绪", "上证指数"],
        textStyle: { color: "#9ba8ba", fontSize: 10.5 },
        top: 0,
        itemWidth: 14,
        itemHeight: 2,
      },
      grid: { left: 42, right: 54, top: 36, bottom: 26 },
      xAxis: {
        type: "category",
        data: dates,
        boundaryGap: false,
        axisLine: { lineStyle: { color: "rgba(148,163,184,0.18)" } },
        axisTick: { show: false },
        axisLabel: { color: "#6b7889", fontSize: 9.5, interval: "auto" },
      },
      yAxis: [
        {
          type: "value", name: "情绪", min: 0, max: 100, position: "left",
          nameTextStyle: { color: "#6b7889", fontSize: 9.5 },
          axisLine: { show: false }, axisTick: { show: false },
          splitLine: { lineStyle: { color: "rgba(148,163,184,0.08)" } },
          axisLabel: { color: "#6b7889", fontSize: 9.5 },
        },
        {
          type: "value", name: "上证", scale: true, position: "right",
          nameTextStyle: { color: "#6b7889", fontSize: 9.5 },
          axisLine: { show: false }, axisTick: { show: false },
          splitLine: { show: false },
          axisLabel: { color: "#6b7889", fontSize: 9.5 },
        },
      ],
      series: [
        {
          name: "综合情绪", type: "line", yAxisIndex: 0, data: composite,
          smooth: true, showSymbol: false,
          lineStyle: { width: 2.6, color: "#d8ab63" }, itemStyle: { color: "#d8ab63" },
          markLine: {
            silent: true, symbol: "none",
            lineStyle: { color: "rgba(148,163,184,0.28)", type: "dashed", width: 1 },
            data: [{ yAxis: 50, label: { formatter: "中性 50", color: "#6b7889", fontSize: 9 } }],
          },
        },
        { name: "宝妈入场", type: "line", yAxisIndex: 0, data: baoma, smooth: true, showSymbol: false, lineStyle: { width: 1.7, color: "#e26a7a" }, itemStyle: { color: "#e26a7a" } },
        { name: "小白跟风", type: "line", yAxisIndex: 0, data: novice, smooth: true, showSymbol: false, lineStyle: { width: 1.7, color: "#5fb3c7" }, itemStyle: { color: "#5fb3c7" } },
        { name: "散户情绪", type: "line", yAxisIndex: 0, data: retail, smooth: true, showSymbol: false, lineStyle: { width: 1.7, color: "#a08ad8" }, itemStyle: { color: "#a08ad8" } },
        { name: "上证指数", type: "line", yAxisIndex: 1, data: closes, smooth: true, showSymbol: false, lineStyle: { width: 1.2, type: "dashed", color: "#6b7889" }, itemStyle: { color: "#6b7889" } },
      ],
    }, true);
  }

  updateChart(120);
  window.addEventListener("resize", () => chartInstance && chartInstance.resize());

  const buttons = document.querySelectorAll(".time-btn");
  buttons.forEach(button => {
    button.addEventListener("click", () => {
      buttons.forEach(item => item.classList.remove("active"));
      button.classList.add("active");
      updateChart(parseInt(button.getAttribute("data-days"), 10) || 120);
    });
  });
}

function renderSectors(sectors, categories) {
  const pills = document.getElementById("categoryPills");
  const grid = document.getElementById("sectorGrid");
  if (!pills || !grid) return;

  pills.innerHTML = "<button class='cat-pill active' data-cat='all'>全部</button>";
  (categories || []).forEach(category => {
    const button = document.createElement("button");
    button.className = "cat-pill";
    button.textContent = category;
    button.setAttribute("data-cat", category);
    pills.appendChild(button);
  });

  let current = "all";

  function flowHtml(sector) {
    if (sector.flow_quality === "order_flow") {
      return "<span>主力 <b class='" + valueClass(sector.main_net_billion) + "'>" +
        fmtSigned(sector.main_net_billion, 2, "亿") + "</b></span>" +
        "<span>散户 <b class='" + valueClass(sector.retail_net_billion) + "'>" +
        fmtSigned(sector.retail_net_billion, 2, "亿") + "</b></span>";
    }
    if (sector.flow_quality === "aggregate") {
      return "<span>净流入 <b class='" + valueClass(sector.net_inflow_billion) + "'>" +
        fmtSigned(sector.net_inflow_billion, 2, "亿") + "</b><span class='flow-badge'>聚合</span></span>";
    }
    return "<span>资金流 N/A</span>";
  }

  function draw() {
    grid.innerHTML = "";
    const list = current === "all" ? sectors : sectors.filter(item => item.category === current);
    list.forEach(sector => {
      const card = document.createElement("div");
      card.className = "sector-card" + (sector.available ? "" : " unavailable");
      const pctCls = isNum(sector.pct) ? (sector.pct >= 0 ? "val-up" : "val-down") : "na";
      const pctText = isNum(sector.pct) ? fmtSigned(sector.pct, 2, "%") : "N/A";
      const companies = (sector.typical_companies || [])
        .map(name => "<span class='sc-company-tag'>" + esc(name) + "</span>").join("");
      const leader = sector.leader_name
        ? "<span class='sc-company-tag' style='color:var(--gold);border-color:rgba(216,171,99,.3);'>领涨 " + esc(sector.leader_name) + "</span>"
        : "";
      const stale = sector.stale ? "<span class='flow-badge'>沿用</span>" : "";
      card.innerHTML =
        "<div class='sc-header'>" +
          "<div><div class='sc-name'>" + esc(sector.name) + "</div>" +
          "<div style='font-size:9.5px;color:var(--muted);margin-top:2px;'>" + esc(sector.category) + stale + "</div></div>" +
          "<div style='text-align:right;'><div class='sc-pct " + pctCls + "'>" + pctText + "</div>" +
          "<span class='sc-tag " + esc(sector.tag_class || "tag-neutral") + "'>" + esc(sector.tag || "数据暂缺") + "</span></div>" +
        "</div>" +
        "<div class='sc-flow-bar'>" + flowHtml(sector) +
          "<span>换手 <b>" + fmt(sector.turnover, 1, "%") + "</b></span></div>" +
        "<div class='sc-desc'>" + esc(sector.description) + "</div>" +
        "<div class='sc-companies'><span style='color:var(--muted);'>代表股</span>" + companies + leader + "</div>";
      grid.appendChild(card);
    });
  }

  draw();
  pills.addEventListener("click", event => {
    if (event.target.classList.contains("cat-pill")) {
      pills.querySelectorAll(".cat-pill").forEach(item => item.classList.remove("active"));
      event.target.classList.add("active");
      current = event.target.getAttribute("data-cat");
      draw();
    }
  });
}

function setupInteractivity() {
  const modal = document.getElementById("whitepaperModal");
  const openBtn = document.getElementById("whitepaperBtn");
  const closeBtn = document.getElementById("modalCloseBtn");

  if (openBtn && modal) openBtn.addEventListener("click", () => modal.classList.add("show"));
  if (closeBtn && modal) closeBtn.addEventListener("click", () => modal.classList.remove("show"));
  if (modal) {
    modal.addEventListener("click", event => {
      if (event.target === modal) modal.classList.remove("show");
    });
  }

  const themeBtn = document.getElementById("themeBtn");
  if (themeBtn) {
    const current = document.documentElement.getAttribute("data-theme") || "dark";
    themeBtn.textContent = current === "light" ? "☾" : "☀";
    themeBtn.addEventListener("click", () => {
      const now = document.documentElement.getAttribute("data-theme") || "dark";
      const next = now === "light" ? "dark" : "light";
      document.documentElement.setAttribute("data-theme", next);
      localStorage.setItem("msf-theme", next);
      themeBtn.textContent = next === "light" ? "☾" : "☀";
      if (chartInstance) chartInstance.resize();
      if (flowChartInstance) flowChartInstance.resize();
    });
  }
}

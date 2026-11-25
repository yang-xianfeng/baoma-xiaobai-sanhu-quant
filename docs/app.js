// A-Share Quant Sentiment Dashboard - QUANT 2.1
// Backward-compatible with QUANT 2.0 payloads; adds confidence and data-quality views.

const DATA_STATUS_LABEL = {
  finalized: "收盘定稿",
  provisional: "盘中预览",
};

document.addEventListener("DOMContentLoaded", async () => {
  let indexData = window.INDEX_DATA || null;
  let historyData = window.HISTORY_DATA || null;

  if (!indexData) {
    try {
      const resp = await fetch("./data/latest.json");
      if (resp.ok) indexData = await resp.json();
    } catch (e) {
      console.warn("Could not fetch ./data/latest.json", e);
    }
  }
  if (!historyData) {
    try {
      const resp = await fetch("./data/history.json");
      if (resp.ok) historyData = await resp.json();
    } catch (e) {
      console.warn("Could not fetch ./data/history.json", e);
    }
  }

  if (!indexData) {
    document.body.innerHTML = "<div style='color:white;text-align:center;padding:50px;'>数据加载失败，请检查 data.js 或 latest.json 是否存在。</div>";
    return;
  }

  renderHeader(indexData);
  renderStatusBanner(indexData.status, indexData);
  renderGauges(indexData.scores);
  renderMicrostructure(indexData.microstructure);
  if (historyData && historyData.length > 0) initTrendChart(historyData);
  renderSectors(indexData.sectors || [], indexData.categories || []);
  setupInteractivity();
});

function isNum(v) {
  return typeof v === "number" && Number.isFinite(v);
}

function fmt(v, digits = 2, suffix = "") {
  if (!isNum(v)) return "N/A";
  return v.toFixed(digits) + suffix;
}

function fmtSigned(v, digits = 2, suffix = "") {
  if (!isNum(v)) return "N/A";
  return (v > 0 ? "+" : "") + v.toFixed(digits) + suffix;
}

function valueClass(v) {
  if (!isNum(v)) return "na";
  return v >= 0 ? "val-up" : "val-down";
}

function renderHeader(data) {
  const dateEl = document.getElementById("marketDateText");
  if (dateEl) {
    const status = DATA_STATUS_LABEL[data.data_status] || data.data_status || "";
    const session = data.session_title ? " · " + data.session_title : "";
    dateEl.textContent = data.market_date + " 交易日 · " + status + session;
  }

  const cb = document.getElementById("confidenceBadge");
  if (cb) {
    const c = isNum(data.confidence) ? data.confidence : 0;
    cb.textContent = "置信度 " + c.toFixed(1) + "%";
    cb.classList.remove("confidence-ok", "confidence-warn", "confidence-bad");
    cb.classList.add(c >= 75 ? "confidence-ok" : c >= 55 ? "confidence-warn" : "confidence-bad");
    const cov = data.coverage || {};
    cb.title =
      "因子覆盖率 · 散户 " + Math.round((cov.retail || 0) * 100) +
      "% / 宝妈 " + Math.round((cov.baoma || 0) * 100) +
      "% / 小白 " + Math.round((cov.novice || 0) * 100) + "%";
  }
}

function renderStatusBanner(status, data) {
  const tagEl = document.getElementById("statusTag");
  const summaryEl = document.getElementById("statusSummary");
  const actionEl = document.getElementById("statusAction");
  const noteEl = document.getElementById("dataQualityNote");

  if (tagEl) {
    tagEl.textContent = status.tag;
    tagEl.className = "badge-status " + (status.badge_class || "badge-info");
  }
  if (summaryEl) summaryEl.textContent = status.summary;
  if (actionEl) actionEl.textContent = "风控建议: " + status.action;

  if (noteEl) {
    const dq = data.data_quality || {};
    const missing = dq.missing_factors || [];
    const degraded = (dq.sources || [])
      .filter(s => s.status && s.status !== "ok")
      .map(s => s.name + "(" + s.status + ")");
    const parts = [];
    if (missing.length) parts.push("缺失/降权因子: " + missing.join(", "));
    if (degraded.length) parts.push("降级数据源: " + degraded.join(", "));
    if (data.sectors_stale) parts.push("板块矩阵沿用 " + data.sectors_source_date + " 定稿（Sina 限流）");
    noteEl.textContent = parts.length
      ? "数据质量 · " + parts.join(" | ")
      : "数据质量 · 核心因子全部在线，横截面与收盘价均已对齐交易日。";
  }
}

function renderGauges(scores) {
  const composite = Math.round(scores.composite || 50);
  const baoma = Math.round(scores.baoma || 50);
  const novice = Math.round(scores.novice || 50);
  const retail = Math.round(scores.retail || 50);

  document.getElementById("scoreComposite").textContent = composite;
  document.getElementById("scoreBaoma").textContent = baoma;
  document.getElementById("scoreNovice").textContent = novice;
  document.getElementById("scoreRetail").textContent = retail;

  setTimeout(() => {
    document.getElementById("arcComposite").setAttribute("stroke-dasharray", composite + ", 100");
    document.getElementById("arcBaoma").setAttribute("stroke-dasharray", baoma + ", 100");
    document.getElementById("arcNovice").setAttribute("stroke-dasharray", novice + ", 100");
    document.getElementById("arcRetail").setAttribute("stroke-dasharray", retail + ", 100");
  }, 100);
}

function renderMicrostructure(ms) {
  if (!ms) return;

  const mainFlowEl = document.getElementById("valMainFlow");
  const retailFlowEl = document.getElementById("valRetailFlow");
  const flowDivEl = document.getElementById("valFlowDiv");

  if (mainFlowEl) {
    mainFlowEl.textContent = isNum(ms.main_net_flow_billion)
      ? fmtSigned(ms.main_net_flow_billion, 2, " 亿")
      : "N/A";
    mainFlowEl.className = "metric-val " + valueClass(ms.main_net_flow_billion);
  }
  if (retailFlowEl) {
    retailFlowEl.textContent = isNum(ms.retail_net_flow_billion)
      ? fmtSigned(ms.retail_net_flow_billion, 2, " 亿")
      : "N/A";
    retailFlowEl.className = "metric-val " + valueClass(ms.retail_net_flow_billion);
  }
  if (flowDivEl) {
    flowDivEl.textContent = isNum(ms.flow_divergence_billion)
      ? fmtSigned(ms.flow_divergence_billion, 2, " 亿")
      : "N/A（RSI 已降权）";
    flowDivEl.className = "metric-val " + valueClass(ms.flow_divergence_billion);
  }

  const marginBal = document.getElementById("valMarginBal");
  if (marginBal) marginBal.textContent = fmt(ms.margin_balance_billion, 2, " 亿");

  const marginBuy = document.getElementById("valMarginBuy");
  if (marginBuy) marginBuy.textContent = fmt(ms.margin_buy_billion, 2, " 亿");

  const marginRatio = document.getElementById("valMarginRatio");
  if (marginRatio) {
    const stale = ms.margin_stale_days > 0 ? " · T-" + ms.margin_stale_days + "待校准" : "";
    marginRatio.textContent = fmt(ms.margin_buy_ratio_pct, 2, "%") + " (" + (ms.margin_date || "N/A") + stale + ")";
  }

  const zt = document.getElementById("valZtDtCount");
  if (zt) zt.textContent = (ms.limit_up_count ?? 0) + " 家 / " + (ms.limit_down_count ?? 0) + " 家";
  const zb = document.getElementById("valZbCount");
  if (zb) zb.textContent = (ms.broken_board_count ?? 0) + " 家";
  const zbRate = document.getElementById("valZbRate");
  if (zbRate) {
    zbRate.textContent = fmt(ms.broken_rate_pct, 2, "%") + " (" + (ms.max_consecutive_board ?? 1) + "连板)"
      + (ms.limit_pool_estimated ? " · 估算" : "");
  }

  const total = document.getElementById("valTotalTurnover");
  if (total) {
    total.textContent = fmt(ms.total_turnover_billion, 2, " 亿元")
      + (isNum(ms.market_turnover_rate_pct) ? " (换手 " + ms.market_turnover_rate_pct + "%)" : "");
  }

  const advDec = document.getElementById("valAdvDec");
  if (advDec) {
    const a = ms.advances ?? 0;
    const d = ms.declines ?? 0;
    const ratio = a + d > 0 ? Math.round((a / (a + d)) * 100) : 0;
    advDec.textContent = a + " / " + d + " (" + ratio + "%)";
  }

  const biasEl = document.getElementById("valBias60");
  if (biasEl) {
    biasEl.textContent = fmtSigned(ms.bias60_pct, 2, "%");
    biasEl.className = "metric-val " + valueClass(ms.bias60_pct);
  }
}

let chartInstance = null;
function initTrendChart(history) {
  const chartDom = document.getElementById("trendChart");
  if (!chartDom) return;

  chartInstance = echarts.init(chartDom, null, { renderer: "canvas" });

  function updateChart(days) {
    const dataSubset = history.slice(-days);
    const dates = dataSubset.map(d => d.date);
    const composite = dataSubset.map(d => d.composite);
    const baoma = dataSubset.map(d => d.baoma);
    const novice = dataSubset.map(d => d.novice);
    const retail = dataSubset.map(d => d.retail);
    const closes = dataSubset.map(d => d.close);

    const option = {
      backgroundColor: "transparent",
      tooltip: {
        trigger: "axis",
        backgroundColor: "rgba(18, 24, 38, 0.95)",
        borderColor: "#26354f",
        textStyle: { color: "#f1f5f9", fontSize: 12 },
        formatter: (params) => {
          const point = dataSubset[params[0].dataIndex] || {};
          let tip = "<div style='font-weight:700;margin-bottom:4px;'>" + params[0].axisValue +
            (point.estimated ? " · 回溯估算" : " · 实际") + "</div>";
          params.forEach(p => {
            tip += "<div style='display:flex;justify-content:space-between;gap:16px;'>" +
              "<span>" + p.marker + " " + p.seriesName + "</span>" +
              "<span style='font-weight:700;font-family:monospace;'>" + p.value + "</span>" +
              "</div>";
          });
          return tip;
        }
      },
      legend: {
        data: ["综合指数", "宝妈入场", "小白跟风", "散户情绪", "上证指数"],
        textStyle: { color: "#94a3b8" },
        top: 0
      },
      grid: { left: "3%", right: "4%", bottom: "8%", top: "14%", containLabel: true },
      xAxis: {
        type: "category",
        data: dates,
        axisLine: { lineStyle: { color: "#26354f" } },
        axisLabel: { color: "#64748b", fontSize: 11 }
      },
      yAxis: [
        {
          type: "value", name: "情绪指数", min: 0, max: 100, position: "left",
          axisLine: { lineStyle: { color: "#26354f" } },
          splitLine: { lineStyle: { color: "rgba(255,255,255,0.05)" } },
          axisLabel: { color: "#94a3b8" }
        },
        {
          type: "value", name: "上证点位", scale: true, position: "right",
          axisLine: { lineStyle: { color: "#26354f" } },
          splitLine: { show: false },
          axisLabel: { color: "#64748b" }
        }
      ],
      series: [
        {
          name: "综合指数", type: "line", yAxisIndex: 0, data: composite,
          smooth: true, showSymbol: false, lineStyle: { width: 3, color: "#f59e0b" },
          itemStyle: { color: "#f59e0b" },
          markLine: {
            silent: true, symbol: "none",
            lineStyle: { color: "rgba(148,163,184,0.35)", type: "dashed", width: 1 },
            data: [{ yAxis: 50, label: { formatter: "中性 50", color: "#64748b" } }]
          }
        },
        { name: "宝妈入场", type: "line", yAxisIndex: 0, data: baoma, smooth: true, showSymbol: false, lineStyle: { width: 2, color: "#f43f5e" }, itemStyle: { color: "#f43f5e" } },
        { name: "小白跟风", type: "line", yAxisIndex: 0, data: novice, smooth: true, showSymbol: false, lineStyle: { width: 2, color: "#06b6d4" }, itemStyle: { color: "#06b6d4" } },
        { name: "散户情绪", type: "line", yAxisIndex: 0, data: retail, smooth: true, showSymbol: false, lineStyle: { width: 2, color: "#8b5cf6" }, itemStyle: { color: "#8b5cf6" } },
        { name: "上证指数", type: "line", yAxisIndex: 1, data: closes, smooth: true, showSymbol: false, lineStyle: { width: 1.5, type: "dashed", color: "#64748b" }, itemStyle: { color: "#64748b" } }
      ]
    };
    chartInstance.setOption(option, true);
  }

  updateChart(60);
  window.addEventListener("resize", () => chartInstance && chartInstance.resize());

  const timeBtns = document.querySelectorAll(".time-btn");
  timeBtns.forEach(btn => {
    btn.addEventListener("click", () => {
      timeBtns.forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      const d = parseInt(btn.getAttribute("data-days"), 10);
      updateChart(d);
    });
  });
}

function renderSectors(sectors, categories) {
  const pillsContainer = document.getElementById("categoryPills");
  const gridContainer = document.getElementById("sectorGrid");
  if (!pillsContainer || !gridContainer) return;

  if (categories) {
    categories.forEach(cat => {
      const btn = document.createElement("button");
      btn.className = "cat-pill";
      btn.textContent = cat;
      btn.setAttribute("data-cat", cat);
      pillsContainer.appendChild(btn);
    });
  }

  let currentCategory = "all";

  function flowHtml(s) {
    if (s.flow_quality === "order_flow") {
      return "<span>主力: <b class='" + valueClass(s.main_net_billion) + "'>" +
        fmtSigned(s.main_net_billion, 2, "亿") + "</b></span>" +
        "<span>散户: <b class='" + valueClass(s.retail_net_billion) + "'>" +
        fmtSigned(s.retail_net_billion, 2, "亿") + "</b></span>";
    }
    if (s.flow_quality === "aggregate") {
      return "<span>净流入: <b class='" + valueClass(s.net_inflow_billion) + "'>" +
        fmtSigned(s.net_inflow_billion, 2, "亿") + "</b>" +
        "<span class='flow-badge'>聚合口径</span></span>";
    }
    return "<span>资金流: N/A</span>";
  }

  function drawCards() {
    gridContainer.innerHTML = "";
    const filtered = currentCategory === "all" ? sectors : sectors.filter(s => s.category === currentCategory);

    filtered.forEach(s => {
      const card = document.createElement("div");
      card.className = "sector-card" + (s.available ? "" : " unavailable");

      const pctColor = isNum(s.pct) ? valueClass(s.pct) : "na";
      const pctText = isNum(s.pct) ? fmtSigned(s.pct, 2, "%") : "N/A";
      const companies = (s.typical_companies || [])
        .map(c => "<span class='sc-company-tag'>" + c + "</span>").join("");
      const leader = s.leader_name
        ? "<span class='sc-company-tag' style='color:var(--accent-gold);border:1px solid rgba(245,158,11,0.3);'>领涨: " + s.leader_name + "</span>"
        : "";

      card.innerHTML =
        "<div class='sc-header'>" +
          "<div><div class='sc-name'>" + s.name + "</div>" +
          "<div style='font-size:11px;color:var(--text-muted);'>" + s.category +
          (s.provider_board ? " · " + s.provider_board : "") + "</div></div>" +
          "<div style='text-align:right;'><div class='sc-pct " + pctColor + "'>" + pctText + "</div>" +
          "<span class='sc-tag " + (s.tag_class || "tag-neutral") + "'>" + (s.tag || "数据暂缺") + "</span></div>" +
        "</div>" +
        "<div class='sc-flow-bar'>" + flowHtml(s) +
          "<span>换手: <b>" + fmt(s.turnover, 1, "%") + "</b></span>" +
        "</div>" +
        "<div class='sc-desc'>" + (s.description || "") + "</div>" +
        "<div class='sc-companies'><span style='color:var(--text-muted);'>代表股:</span>" +
          companies + leader + "</div>";
      gridContainer.appendChild(card);
    });
  }

  drawCards();

  pillsContainer.addEventListener("click", (e) => {
    if (e.target.classList.contains("cat-pill")) {
      pillsContainer.querySelectorAll(".cat-pill").forEach(p => p.classList.remove("active"));
      e.target.classList.add("active");
      currentCategory = e.target.getAttribute("data-cat");
      drawCards();
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
    modal.addEventListener("click", (e) => {
      if (e.target === modal) modal.classList.remove("show");
    });
  }

  const themeBtn = document.getElementById("themeBtn");
  if (themeBtn) {
    themeBtn.addEventListener("click", () => {
      const current = document.documentElement.getAttribute("data-theme");
      const next = current === "light" ? "dark" : "light";
      document.documentElement.setAttribute("data-theme", next);
      themeBtn.textContent = next === "light" ? "☀️" : "🌙";
      if (chartInstance) chartInstance.resize();
    });
  }
}

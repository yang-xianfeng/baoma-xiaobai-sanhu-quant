// A-Share Quant Sentiment Dashboard - Modern Interactive App

document.addEventListener("DOMContentLoaded", async () => {
  let indexData = window.INDEX_DATA || null;
  let historyData = window.HISTORY_DATA || null;

  // Attempt to fetch from local REST json if window data isn't preloaded
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

  // 1. Render Header & Meta
  renderHeader(indexData);

  // 2. Render Risk Alert Banner
  renderStatusBanner(indexData.status);

  // 3. Render 4 Gauges
  renderGauges(indexData.scores);

  // 4. Render Microstructure Metrics
  renderMicrostructure(indexData.microstructure);

  // 5. Render ECharts Trend Chart
  if (historyData && historyData.length > 0) {
    initTrendChart(historyData);
  }

  // 6. Render 30 Sectors Heatmap Matrix
  renderSectors(indexData.sectors, indexData.categories);

  // 7. Setup Interactivity (Modal & Theme)
  setupInteractivity();
});

function renderHeader(data) {
  const dateEl = document.getElementById("marketDateText");
  if (dateEl) {
    dateEl.textContent = `${data.market_date} 交易日 (已校准)`;
  }
}

function renderStatusBanner(status) {
  const tagEl = document.getElementById("statusTag");
  const summaryEl = document.getElementById("statusSummary");
  const actionEl = document.getElementById("statusAction");

  if (tagEl) {
    tagEl.textContent = status.tag;
    tagEl.className = `badge-status ${status.badge_class || "badge-info"}`;
  }
  if (summaryEl) {
    summaryEl.textContent = status.summary;
  }
  if (actionEl) {
    actionEl.textContent = `风控建议: ${status.action}`;
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

  // Animate SVG arcs (stroke-dasharray="percentage, 100")
  setTimeout(() => {
    document.getElementById("arcComposite").setAttribute("stroke-dasharray", `${composite}, 100`);
    document.getElementById("arcBaoma").setAttribute("stroke-dasharray", `${baoma}, 100`);
    document.getElementById("arcNovice").setAttribute("stroke-dasharray", `${novice}, 100`);
    document.getElementById("arcRetail").setAttribute("stroke-dasharray", `${retail}, 100`);
  }, 100);
}

function renderMicrostructure(ms) {
  if (!ms) return;

  // 1. Capital flow
  const mainFlowEl = document.getElementById("valMainFlow");
  const retailFlowEl = document.getElementById("valRetailFlow");
  const flowDivEl = document.getElementById("valFlowDiv");

  mainFlowEl.textContent = `${ms.main_net_flow_billion > 0 ? "+" : ""}${ms.main_net_flow_billion} 亿`;
  mainFlowEl.className = `metric-val ${ms.main_net_flow_billion >= 0 ? "val-up" : "val-down"}`;

  retailFlowEl.textContent = `${ms.retail_net_flow_billion > 0 ? "+" : ""}${ms.retail_net_flow_billion} 亿`;
  retailFlowEl.className = `metric-val ${ms.retail_net_flow_billion >= 0 ? "val-up" : "val-down"}`;

  flowDivEl.textContent = `${ms.flow_divergence_billion > 0 ? "+" : ""}${ms.flow_divergence_billion} 亿`;
  flowDivEl.className = `metric-val ${ms.flow_divergence_billion >= 0 ? "val-up" : "val-down"}`;

  // 2. Margin
  document.getElementById("valMarginBal").textContent = `${ms.margin_balance_billion} 亿`;
  document.getElementById("valMarginBuy").textContent = `${ms.margin_buy_billion} 亿`;
  document.getElementById("valMarginRatio").textContent = `${ms.margin_buy_ratio_pct}%`;

  // 3. Limit pool
  document.getElementById("valZtDtCount").textContent = `${ms.limit_up_count} 家 / ${ms.limit_down_count} 家`;
  document.getElementById("valZbCount").textContent = `${ms.broken_board_count} 家`;
  document.getElementById("valZbRate").textContent = `${ms.broken_rate_pct}% (${ms.max_consecutive_board}连板)`;

  // 4. Market Breadth & Turnover
  document.getElementById("valTotalTurnover").textContent = `${ms.total_turnover_billion} 亿元`;
  const advRatio = Math.round((ms.advances / (ms.advances + ms.declines || 1)) * 100);
  document.getElementById("valAdvDec").textContent = `${ms.advances} / ${ms.declines} (${advRatio}%)`;
  const biasEl = document.getElementById("valBias60");
  biasEl.textContent = `${ms.bias60_pct > 0 ? "+" : ""}${ms.bias60_pct}%`;
  biasEl.className = `metric-val ${ms.bias60_pct >= 0 ? "val-up" : "val-down"}`;
}

let chartInstance = null;
function initTrendChart(history) {
  const chartDom = document.getElementById("trendChart");
  if (!chartDom) return;

  chartInstance = echarts.init(chartDom, null, { renderer: "canvas" });

  let visibleDays = 60;
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
          let tip = `<div style="font-weight:700;margin-bottom:4px;">${params[0].axisValue}</div>`;
          params.forEach(p => {
            tip += `<div style="display:flex;justify-content:space-between;gap:16px;">
              <span>${p.marker} ${p.seriesName}</span>
              <span style="font-weight:700;font-family:monospace;">${p.value}</span>
            </div>`;
          });
          return tip;
        }
      },
      legend: {
        data: ["综合指数", "宝妈入场", "小白跟风", "散户情绪", "上证指数"],
        textStyle: { color: "#94a3b8" },
        top: 0
      },
      grid: {
        left: "3%",
        right: "4%",
        bottom: "8%",
        top: "14%",
        containLabel: true
      },
      xAxis: {
        type: "category",
        data: dates,
        axisLine: { lineStyle: { color: "#26354f" } },
        axisLabel: { color: "#64748b", fontSize: 11 }
      },
      yAxis: [
        {
          type: "value",
          name: "情绪指数",
          min: 0,
          max: 100,
          position: "left",
          axisLine: { lineStyle: { color: "#26354f" } },
          splitLine: { lineStyle: { color: "rgba(255,255,255,0.05)" } },
          axisLabel: { color: "#94a3b8" }
        },
        {
          type: "value",
          name: "上证点位",
          scale: true,
          position: "right",
          axisLine: { lineStyle: { color: "#26354f" } },
          splitLine: { show: false },
          axisLabel: { color: "#64748b" }
        }
      ],
      series: [
        {
          name: "综合指数",
          type: "line",
          yAxisIndex: 0,
          data: composite,
          smooth: true,
          showSymbol: false,
          lineStyle: { width: 3, color: "#f59e0b" },
          itemStyle: { color: "#f59e0b" }
        },
        {
          name: "宝妈入场",
          type: "line",
          yAxisIndex: 0,
          data: baoma,
          smooth: true,
          showSymbol: false,
          lineStyle: { width: 2, color: "#f43f5e" },
          itemStyle: { color: "#f43f5e" }
        },
        {
          name: "小白跟风",
          type: "line",
          yAxisIndex: 0,
          data: novice,
          smooth: true,
          showSymbol: false,
          lineStyle: { width: 2, color: "#06b6d4" },
          itemStyle: { color: "#06b6d4" }
        },
        {
          name: "散户情绪",
          type: "line",
          yAxisIndex: 0,
          data: retail,
          smooth: true,
          showSymbol: false,
          lineStyle: { width: 2, color: "#8b5cf6" },
          itemStyle: { color: "#8b5cf6" }
        },
        {
          name: "上证指数",
          type: "line",
          yAxisIndex: 1,
          data: closes,
          smooth: true,
          showSymbol: false,
          lineStyle: { width: 1.5, type: "dashed", color: "#64748b" },
          itemStyle: { color: "#64748b" }
        }
      ]
    };
    chartInstance.setOption(option, true);
  }

  updateChart(visibleDays);
  window.addEventListener("resize", () => chartInstance && chartInstance.resize());

  // Time buttons
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

  // Add category pills
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

  function drawCards() {
    gridContainer.innerHTML = "";
    const filtered = currentCategory === "all" 
      ? sectors 
      : sectors.filter(s => s.category === currentCategory);

    filtered.forEach(s => {
      const card = document.createElement("div");
      card.className = "sector-card";

      const pctColor = s.pct >= 0 ? "val-up" : "val-down";
      const pctSign = s.pct > 0 ? "+" : "";

      card.innerHTML = `
        <div class="sc-header">
          <div>
            <div class="sc-name">${s.name}</div>
            <div style="font-size:11px;color:var(--text-muted);">${s.category}</div>
          </div>
          <div style="text-align:right;">
            <div class="sc-pct ${pctColor}">${pctSign}${s.pct.toFixed(2)}%</div>
            <span class="sc-tag ${s.tag_class}">${s.tag}</span>
          </div>
        </div>
        <div class="sc-flow-bar">
          <span>主力: <b class="${s.main_net_billion >= 0 ? 'val-up' : 'val-down'}">${s.main_net_billion > 0 ? '+' : ''}${s.main_net_billion}亿</b></span>
          <span>散户: <b class="${s.retail_net_billion >= 0 ? 'val-up' : 'val-down'}">${s.retail_net_billion > 0 ? '+' : ''}${s.retail_net_billion}亿</b></span>
          <span>换手: <b>${s.turnover.toFixed(1)}%</b></span>
        </div>
        <div class="sc-desc">${s.description}</div>
        <div class="sc-companies">
          <span style="color:var(--text-muted);">代表股:</span>
          ${s.typical_companies.map(c => `<span class="sc-company-tag">${c}</span>`).join("")}
          ${s.leader_name ? `<span class="sc-company-tag" style="color:var(--accent-gold);border:1px solid rgba(245,158,11,0.3);">领涨: ${s.leader_name}</span>` : ""}
        </div>
      `;
      gridContainer.appendChild(card);
    });
  }

  drawCards();

  // Handle pill click
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
  // Modal Whitepaper
  const modal = document.getElementById("whitepaperModal");
  const openBtn = document.getElementById("whitepaperBtn");
  const closeBtn = document.getElementById("modalCloseBtn");

  if (openBtn && modal) {
    openBtn.addEventListener("click", () => modal.classList.add("show"));
  }
  if (closeBtn && modal) {
    closeBtn.addEventListener("click", () => modal.classList.remove("show"));
  }
  if (modal) {
    modal.addEventListener("click", (e) => {
      if (e.target === modal) modal.classList.remove("show");
    });
  }

  // Theme Switcher
  const themeBtn = document.getElementById("themeBtn");
  if (themeBtn) {
    themeBtn.addEventListener("click", () => {
      const current = document.documentElement.getAttribute("data-theme");
      const next = current === "light" ? "dark" : "light";
      document.documentElement.setAttribute("data-theme", next);
      themeBtn.textContent = next === "light" ? "☀️" : "🌙";
      if (chartInstance) {
        chartInstance.resize();
      }
    });
  }
}

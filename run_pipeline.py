"""Main Quantitative Pipeline Execution Script.

Fetches market microstructure data, executes the quantitative sentiment model,
builds rolling historical calibrated indices, and exports data for GitHub Pages.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.calendar import (
    coerce_date,
    get_latest_trading_day,
    is_trading_day,
)
from core.fetcher import MarketFetcher
from core.quant_engine import QuantSentimentEngine
from core.sector_catalog import CATEGORIES, SECTOR_DEFINITIONS

DOCS_DIR = PROJECT_ROOT / "docs"
DATA_DIR = DOCS_DIR / "data"

def ensure_dirs():
    DATA_DIR.mkdir(parents=True, exist_ok=True)

def run_pipeline() -> Dict[str, Any]:
    print("=" * 60)
    print("  A股散户情绪与市场博弈量化指数 · 专业量化管线启动")
    print(f"  当前时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    ensure_dirs()
    fetcher = MarketFetcher()
    engine = QuantSentimentEngine()

    # 1. Determine trading day
    latest_trade_day = get_latest_trading_day()
    trade_date_str = latest_trade_day.isoformat()
    print(f"\n[1/5] 交易日历核验: 目标交易日 {trade_date_str}")

    # 2. Fetch market microstructure
    print("\n[2/5] 采集全市场微观结构与量化行情数据...")
    overview = fetcher.fetch_market_overview()
    print(f"  - 全市场总成交额: {overview['total_turnover']/1e8:.2f} 亿元")
    print(f"  - 市场涨跌分布: 上涨 {overview['total_advances']} 家 / 下跌 {overview['total_declines']} 家 / 平盘 {overview['total_unchanged']} 家 (涨跌比: {overview['advance_ratio']}%)")

    capital_flows = fetcher.fetch_capital_flow(limit=60)
    today_flow = capital_flows[-1] if capital_flows else {
        "date": trade_date_str,
        "main_net": 0.0,
        "small_net": 0.0,
        "divergence": 0.0
    }
    print(f"  - 主力资金净流入: {today_flow.get('main_net', 0)/1e8:.2f} 亿元")
    print(f"  - 散户小单净买入: {today_flow.get('small_net', 0)/1e8:.2f} 亿元")
    print(f"  - 多空剪刀差背离: {today_flow.get('divergence', 0)/1e8:.2f} 亿元")

    margins = fetcher.fetch_margin_data(page_size=60)
    today_margin = margins[0] if margins else None
    if today_margin:
        print(f"  - 两融全市场融资余额: {today_margin['rzye']/1e8:.2f} 亿元 (当日融资买入: {today_margin['rzmre']/1e8:.2f} 亿元)")
    else:
        print("  - 两融数据暂缺，使用中性中枢估算")

    limit_pool = fetcher.fetch_limit_up_down_pool(trade_date=trade_date_str)
    print(f"  - 短线打板博弈: 涨停 {limit_pool['limit_up_count']} 家 / 炸板 {limit_pool['broken_board_count']} 家 (炸板率: {limit_pool['broken_rate']}%) / 跌停 {limit_pool['limit_down_count']} 家")

    historical_klines = fetcher.fetch_index_klines(secid="1.000001", limit=120)
    print(f"  - 成功获取上证综指 {len(historical_klines)} 根日K线数据")

    # 3. Calculate 30-sector matrix
    print("\n[3/5] 计算 8 大类 30 细分板块多维散户情绪与多空博弈状态...")
    raw_sectors = fetcher.fetch_all_sectors_market()
    scored_sectors = engine.evaluate_sector_quant(raw_sectors)
    print(f"  - 30 个核心板块情绪评分完成. 最热板块: {scored_sectors[0]['name']} ({scored_sectors[0]['sector_heat']}分, 评级: {scored_sectors[0]['tag']})")

    # 4. Execute Quantitative Model Evaluation
    print("\n[4/5] 运行多因子行为量化模型计算三大分指数与综合指数...")
    quant_result = engine.evaluate_market_sentiment(
        overview=overview,
        flow=today_flow,
        margin=today_margin,
        limit_pool=limit_pool,
        historical_klines=historical_klines,
        historical_margins=margins,
    )

    print("\n" + "=" * 60)
    print(f"  ★ 量化指数计算结果 (交易日: {trade_date_str}) ★")
    print(f"  综合情绪指数 (Composite):    {quant_result['composite']:5.1f} / 100")
    print(f"  宝妈入场指数 (Baoma Bubble): {quant_result['baoma']:5.1f} / 100")
    print(f"  小白跟风指数 (Novice Chase): {quant_result['novice']:5.1f} / 100")
    print(f"  散户情绪指数 (Retail Sent):  {quant_result['retail']:5.1f} / 100")
    print(f"  当前量化状态: [{quant_result['status']['tag']}]")
    print(f"  风控指引建议: {quant_result['status']['action']}")
    print("=" * 60)

    # 5. Build Rolling Historical Series
    print("\n[5/5] 构建滚动 60 日连续量化历史时间序列与静态发布包...")
    history_series = build_history_series(
        historical_klines=historical_klines,
        capital_flows=capital_flows,
        margins=margins,
        engine=engine,
        latest_quant=quant_result,
        trade_date_str=trade_date_str,
    )

    # Prepare latest structured snapshot
    latest_payload = {
        "version": "2.0_quant",
        "market_date": trade_date_str,
        "updated_at": datetime.now().isoformat(),
        "scores": {
            "composite": quant_result["composite"],
            "baoma": quant_result["baoma"],
            "novice": quant_result["novice"],
            "retail": quant_result["retail"],
        },
        "signals": quant_result["signals"],
        "status": quant_result["status"],
        "microstructure": quant_result["microstructure"],
        "indices_overview": overview["indices"],
        "categories": CATEGORIES,
        "sectors": scored_sectors,
        "history_count": len(history_series),
    }

    # Write latest.json
    latest_file = DATA_DIR / "latest.json"
    with open(latest_file, "w", encoding="utf-8") as f:
        json.dump(latest_payload, f, ensure_ascii=False, indent=2)

    # Write history.json
    history_file = DATA_DIR / "history.json"
    with open(history_file, "w", encoding="utf-8") as f:
        json.dump(history_series, f, ensure_ascii=False, indent=2)

    # Write data.js for zero-CORS browser viewing
    data_js_file = DOCS_DIR / "data.js"
    js_content = f"window.INDEX_DATA = {json.dumps(latest_payload, ensure_ascii=False)};\nwindow.HISTORY_DATA = {json.dumps(history_series, ensure_ascii=False)};"
    with open(data_js_file, "w", encoding="utf-8") as f:
        f.write(js_content)

    print(f"  [OK] 数据成功写入:")
    print(f"       - {latest_file.relative_to(PROJECT_ROOT)}")
    print(f"       - {history_file.relative_to(PROJECT_ROOT)}")
    print(f"       - {data_js_file.relative_to(PROJECT_ROOT)}")
    print("\n管线执行全部圆满完成！")
    return latest_payload

def build_history_series(
    historical_klines: List[Dict[str, Any]],
    capital_flows: List[Dict[str, Any]],
    margins: List[Dict[str, Any]],
    engine: QuantSentimentEngine,
    latest_quant: Dict[str, Any],
    trade_date_str: str,
) -> List[Dict[str, Any]]:
    """Build historical calibrated sentiment time series for chart display."""
    flow_map = {f["date"]: f for f in capital_flows}
    margin_map = {m["date"]: m for m in margins}
    
    history_points = []
    # Use the last 60 klines
    subset = historical_klines[-60:]
    closes = [k["close"] for k in historical_klines]
    
    for i, k in enumerate(subset):
        d = k["date"]
        c = k["close"]
        to = k["turnover_rate"]
        pct = k["change_pct"]
        vol = k["amount"]

        # If it is today, use today's exact quant result
        if d == trade_date_str:
            history_points.append({
                "date": d,
                "composite": latest_quant["composite"],
                "baoma": latest_quant["baoma"],
                "novice": latest_quant["novice"],
                "retail": latest_quant["retail"],
                "close": c,
                "change_pct": pct,
                "turnover_rate": to,
                "amount_billion": round(vol / 1e8, 2),
            })
            continue

        # For historical dates, calculate rolling quant proxy
        # Find 60-day window index for MA60
        idx_in_full = len(historical_klines) - len(subset) + i
        window_closes = closes[max(0, idx_in_full - 59) : idx_in_full + 1]
        ma60 = sum(window_closes) / len(window_closes) if window_closes else c
        bias60 = ((c - ma60) / ma60 * 100.0) if ma60 > 0 else 0.0

        # Margin proxy
        m_row = margin_map.get(d)
        rzye = m_row["rzye"] if m_row else 2.1e12
        rzmre = m_row["rzmre"] if m_row else 1.2e11
        margin_leverage = min(100.0, max(0.0, (rzmre / (vol if vol > 0 else 1e11) * 100 - 7) / 7.0 * 100))

        # Flow proxy
        f_row = flow_map.get(d)
        divergence = f_row["divergence"] if f_row else 0.0
        div_score = min(100.0, max(0.0, 50.0 + (divergence / (vol if vol > 0 else 1e11)) * 100.0 * 7.0))

        # Turnover z-score
        window_tos = [item["turnover_rate"] for item in historical_klines[max(0, idx_in_full - 59) : idx_in_full + 1]]
        avg_to = sum(window_tos) / len(window_tos) if window_tos else to
        to_score = min(100.0, max(0.0, 50.0 + (to - avg_to) * 40.0))

        # Reconstruct sub-indices for history
        breadth_proxy = min(100.0, max(0.0, 50.0 + pct * 15.0))
        hist_rsi = round(0.40 * div_score + 0.35 * breadth_proxy + 0.25 * to_score, 1)

        bias_score = min(100.0, max(0.0, (bias60 + 10.0) / 25.0 * 100.0))
        hist_bmei = round(0.40 * margin_leverage + 0.35 * bias_score + 0.25 * to_score, 1)

        hist_nsi = round(0.50 * to_score + 0.50 * breadth_proxy, 1)
        hist_composite = round(0.40 * hist_rsi + 0.30 * hist_bmei + 0.30 * hist_nsi, 1)

        history_points.append({
            "date": d,
            "composite": hist_composite,
            "baoma": hist_bmei,
            "novice": hist_nsi,
            "retail": hist_rsi,
            "close": c,
            "change_pct": pct,
            "turnover_rate": to,
            "amount_billion": round(vol / 1e8, 2),
        })

    return history_points

if __name__ == "__main__":
    run_pipeline()

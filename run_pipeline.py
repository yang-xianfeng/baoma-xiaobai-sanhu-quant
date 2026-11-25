"""Quantitative pipeline runner.

Modes
-----
close   : run after the 15:00 close, fetch the full cross-section, finalize
          the daily point and append it to the rolling history.
morning : run at 08:30, reuse the last finalized close cross-section, refresh
          the now-settled margin data and recompute the same trade date's
          scores. This is the pre-market briefing.
auto    : choose based on the Beijing-time session.

The pipeline is idempotent: one trade date produces exactly one history point,
and a morning revision updates that point instead of appending a duplicate.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.calendar import (
    coerce_date,
    get_cst_now,
    get_latest_trading_day,
    is_trading_day,
    resolve_session,
)
from core.fetcher import MarketFetcher, align_by_date
from core.quant_engine import QuantSentimentEngine, clamp, tanh_score
from core.sector_catalog import CATEGORIES, SECTOR_DEFINITIONS
from core.style_catalog import build_style_summary

DOCS_DIR = PROJECT_ROOT / "docs"
DATA_DIR = DOCS_DIR / "data"
LATEST_FILE = DATA_DIR / "latest.json"
HISTORY_FILE = DATA_DIR / "history.json"
DATA_JS_FILE = DOCS_DIR / "data.js"

SESSION_TITLES = {
    "pre_market_briefing": "盘前晨报 · 上一交易日收盘定稿 + 两融清算校准",
    "close": "收盘结算版 · 全市场横截面定稿",
    "intraday_preview": "盘中预览 · 未收盘定稿",
    "holiday_review": "节假日复盘 · 最近交易日定稿",
}


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------

def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:  # noqa: BLE001 - corrupt state should not kill a run
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Overview assembly
# ---------------------------------------------------------------------------

def _row_on(market: List[Dict[str, Any]], target: str) -> Dict[str, Any]:
    exact = [r for r in market if r["date"] == target]
    if exact:
        return exact[-1]
    prior = [r for r in market if r["date"] <= target]
    return prior[-1] if prior else {}


def _series_map(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {r["date"]: r for r in rows}


def _is_pre_open_flat(universe: Dict[str, Any], target: date, today: date) -> bool:
    if target >= today or not universe.get("available"):
        return False
    active = int(universe.get("active_count", 0))
    unchanged = int(universe.get("unchanged", 0))
    if active <= 0:
        return False
    return unchanged / active >= 0.90


def build_overview(
    target: date,
    session: str,
    quotes: Dict[str, Any],
    universe: Dict[str, Any],
    market_hist: Dict[str, Any],
    cache: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Assemble a date-aligned market overview.

    Full-market turnover comes from SSE + SZSE composite turnover, not from
    index constituent turnover. Breadth comes from the exact 5,500-stock
    cross-section; if a pre-open provider reset makes every stock flat, the
    last finalized cross-section is reused instead of publishing a fake
    50/50 breadth.
    """
    market = market_hist.get("market", [])
    sse = _series_map(market_hist.get("sse", []))
    szse = _series_map(market_hist.get("szse", []))
    c2000 = _series_map(market_hist.get("csi2000", []))
    c300 = _series_map(market_hist.get("csi300", []))
    target_str = target.isoformat()

    target_row = _row_on(market, target_str)
    total_amount = float(target_row.get("amount", 0.0) or 0.0)
    if total_amount <= 0:
        total_amount = float(universe.get("total_amount", 0.0) or 0.0)

    indices = {k: dict(v) for k, v in (quotes.get("indices") or {}).items()}

    def set_quote(code: str, close: float, pct: float, amount: float = 0.0) -> None:
        if close <= 0:
            return
        item = indices.get(code, {"code": code, "name": code})
        item["close"] = round(close, 2)
        item["pct"] = round(pct, 2)
        if amount > 0:
            item["amount"] = amount
        indices[code] = item

    if target_row:
        set_quote("000001", target_row["close"], target_row["change_pct"], target_row["amount"])
    if target_str in szse:
        set_quote("399106", szse[target_str]["close"], szse[target_str]["change_pct"])
    if target_str in c300:
        set_quote("000300", c300[target_str]["close"], c300[target_str]["change_pct"])
    if target_str in c2000:
        set_quote("399303", c2000[target_str]["close"], c2000[target_str]["change_pct"])

    csi2000_pct = (
        c2000[target_str]["change_pct"]
        if target_str in c2000
        else float(indices.get("399303", {}).get("pct", 0.0) or 0.0)
    )
    csi300_pct = (
        c300[target_str]["change_pct"]
        if target_str in c300
        else float(indices.get("000300", {}).get("pct", 0.0) or 0.0)
    )
    spread = csi2000_pct - csi300_pct
    if abs(spread) < 1e-9 and universe.get("available"):
        spread = float(universe.get("microcap_spread", 0.0) or 0.0)

    cache_ok = bool(cache and cache.get("market_date") == target_str)
    cache_overview = (cache or {}).get("raw_inputs", {}).get("overview", {}) if cache else {}
    cache_universe = cache_overview.get("universe") or {}

    live_breadth_ok = bool(universe.get("breadth_valid")) and not _is_pre_open_flat(
        universe, target, get_cst_now().date()
    )
    if live_breadth_ok:
        advances = int(universe.get("advances", 0))
        declines = int(universe.get("declines", 0))
        unchanged = int(universe.get("unchanged", 0))
        breadth_source = "live_cross_section"
        breadth_universe = universe
    elif cache_ok and cache_overview.get("total_advances", 0) + cache_overview.get(
        "total_declines", 0
    ) > 0:
        advances = int(cache_overview.get("total_advances", 0))
        declines = int(cache_overview.get("total_declines", 0))
        unchanged = int(cache_overview.get("total_unchanged", 0))
        breadth_source = "last_finalized_cross_section"
        breadth_universe = cache_universe
    else:
        advances = declines = unchanged = 0
        breadth_source = "unavailable"
        breadth_universe = {}

    return {
        "date": target_str,
        "total_turnover": total_amount,
        "market_turnover_rate": float(target_row.get("turnover_rate", 0.0) or 0.0),
        "total_advances": advances,
        "total_declines": declines,
        "total_unchanged": unchanged,
        "advance_ratio": round(
            advances / (advances + declines) * 100.0, 2
        ) if (advances + declines) else 50.0,
        "indices": indices,
        "universe": breadth_universe if breadth_source != "unavailable" else {},
        "breadth_source": breadth_source,
        "csi2000_pct": round(csi2000_pct, 3),
        "csi300_pct": round(csi300_pct, 3),
        "microcap_spread": round(spread, 3),
        "session": session,
    }


# ---------------------------------------------------------------------------
# Sector handling
# ---------------------------------------------------------------------------

def _mark_cached_sectors(sectors: List[Dict[str, Any]], source_date: str) -> List[Dict[str, Any]]:
    out = []
    for item in sectors:
        copy = dict(item)
        copy["stale"] = True
        copy["source_date"] = source_date
        out.append(copy)
    return out


def score_sectors(
    fetcher: MarketFetcher,
    engine: QuantSentimentEngine,
    target: date,
    session: str,
    cache: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    cache_sectors = (cache or {}).get("sectors") or []
    cache_date = (cache or {}).get("market_date")
    cache_ok = bool(cache and cache_date == target.isoformat())
    cache_recent = False
    if cache_date:
        try:
            age = (target - coerce_date(cache_date)).days
            cache_recent = 0 <= age <= 10
        except ValueError:
            cache_recent = False

    if session == "pre_market_briefing" and cache_ok and cache_sectors:
        # Pre-open board quotes are frequently reset to zero; reuse the
        # finalized close snapshot for the morning briefing.
        return cache_sectors

    raw_sectors = fetcher.fetch_all_sectors_market()
    scored = engine.evaluate_sector_quant(raw_sectors)
    available = [s for s in scored if s.get("available")]
    cache_available = [s for s in cache_sectors if s.get("available")]

    # Sina's board endpoint rate-limits aggressively. If today's fetch is
    # badly truncated, fall back to the last recent finalized sector matrix
    # rather than showing a mostly-empty dashboard. The reused rows are marked
    # stale and surfaced in the data-quality note.
    if cache_recent and len(available) < 15 and len(cache_available) > len(available):
        return _mark_cached_sectors(cache_sectors, str(cache_date))
    if (
        available
        and cache_recent
        and cache_available
        and all(abs(float(s.get("pct") or 0.0)) < 1e-9 for s in available)
    ):
        return _mark_cached_sectors(cache_sectors, str(cache_date))
    return scored


# ---------------------------------------------------------------------------
# Payload assembly
# ---------------------------------------------------------------------------

def collect_missing_factors(quant: Dict[str, Any]) -> List[str]:
    missing: List[str] = []
    for group, factors in quant.get("factors", {}).items():
        for name, factor in factors.items():
            if not factor.get("available"):
                missing.append(f"{group}.{name}")
    return missing


def build_data_quality(fetcher: MarketFetcher, quant: Dict[str, Any]) -> Dict[str, Any]:
    sources = [
        {"name": name, **meta} for name, meta in sorted(fetcher.quality.items())
    ]
    return {
        "sources": sources,
        "missing_factors": collect_missing_factors(quant),
        "coverage": quant.get("coverage", {}),
        "confidence": quant.get("confidence", 0.0),
    }


def make_payload(
    *,
    target: date,
    session: str,
    data_status: str,
    quant: Dict[str, Any],
    overview: Dict[str, Any],
    scored_sectors: List[Dict[str, Any]],
    history: List[Dict[str, Any]],
    fetcher: MarketFetcher,
    now: datetime,
    revision: str = "",
    extra_note: str = "",
) -> Dict[str, Any]:
    status = dict(quant["status"])
    if data_status == "provisional":
        status["summary"] += "（盘中预览，收盘后会自动定稿，请勿据此执行收盘策略。）"
        status["action"] = "预览模式 · " + status["action"]
    if extra_note:
        status["summary"] += extra_note

    payload = {
        "schema_version": "2.1",
        "version": "2.1_quant",
        "market_date": target.isoformat(),
        "data_status": data_status,
        "session_mode": session,
        "session_title": SESSION_TITLES.get(session, "量化决策版"),
        "revision": revision,
        "updated_at": now.isoformat(),
        "updated_at_cst": now.strftime("%Y-%m-%d %H:%M:%S CST"),
        "scores": {
            "composite": quant["composite"],
            "baoma": quant["baoma"],
            "novice": quant["novice"],
            "retail": quant["retail"],
        },
        "confidence": quant["confidence"],
        "coverage": quant["coverage"],
        "estimated": quant["estimated"],
        "signals": quant["signals"],
        "raw_signals": quant["raw"],
        "factor_detail": quant["factors"],
        "weights_used": quant["weights_used"],
        "status": status,
        "microstructure": quant["microstructure"],
        "indices_overview": overview["indices"],
        "breadth_source": overview["breadth_source"],
        "categories": CATEGORIES,
        "styles": build_style_summary(
            scored_sectors, overview["indices"], quant, quant["microstructure"]
        ),
        "sectors": scored_sectors,
        "sectors_stale": any(s.get("stale") for s in scored_sectors),
        "sectors_source_date": next(
            (s.get("source_date") for s in scored_sectors if s.get("stale")), target.isoformat()
        ),
        "data_quality": build_data_quality(fetcher, quant),
        "raw_inputs": {},
        "history_count": len(history),
    }
    return payload


# ---------------------------------------------------------------------------
# History construction
# ---------------------------------------------------------------------------

def _proxy_factor(score: float, raw: Any, detail: str) -> Dict[str, Any]:
    return {
        "score": round(clamp(score), 1),
        "raw": raw,
        "available": True,
        "estimated": True,
        "detail": detail,
    }


def build_history_series(
    market: List[Dict[str, Any]],
    margins: List[Dict[str, Any]],
    flow_history: List[Dict[str, Any]],
    spread_map: Dict[str, float],
    engine: QuantSentimentEngine,
    latest_quant: Dict[str, Any],
    target: str,
    data_status: str,
    days: int = 120,
) -> List[Dict[str, Any]]:
    """Build a 120-day chart series.

    Dates before the latest finalized point are *backcasts*: they use real
    prices, turnover, margin and microcap spread where available, and are
    explicitly marked estimated. Only the latest point is an actual model
    output.
    """
    rows = [r for r in market if r["date"] <= target]
    start_idx = max(0, len(rows) - days)
    subset = rows[start_idx:]
    amount_by_date = {r["date"]: float(r["amount"]) for r in rows}
    margin_by_date = {str(r.get("date")): r for r in margins}
    flow_by_date = {str(r.get("date")): r for r in flow_history}

    points: List[Dict[str, Any]] = []
    for idx, row in enumerate(subset):
        d = row["date"]
        klines = rows[: start_idx + idx + 1]
        amount = float(row.get("amount", 0.0) or 0.0)
        pct = float(row.get("change_pct", 0.0) or 0.0)

        if d == target:
            points.append(
                {
                    "date": d,
                    "composite": latest_quant["composite"],
                    "baoma": latest_quant["baoma"],
                    "novice": latest_quant["novice"],
                    "retail": latest_quant["retail"],
                    "confidence": latest_quant["confidence"],
                    "estimated": data_status != "finalized",
                    "source": "actual" if data_status == "finalized" else "preview",
                    "close": round(float(row["close"]), 2),
                    "change_pct": round(pct, 2),
                    "turnover_rate": round(float(row.get("turnover_rate", 0.0) or 0.0), 3),
                    "amount_billion": round(amount / 1e8, 2),
                }
            )
            continue

        liquidity = engine.factor_liquidity(klines, d)
        breadth_proxy = _proxy_factor(
            tanh_score(pct, 0.0, 2.0),
            {"index_change_pct": pct},
            "index-return breadth proxy",
        )
        tail = {"score": None, "raw": None, "available": False, "estimated": True, "detail": ""}
        turnover_heat = engine.factor_turnover_heat(liquidity, tail)
        margin_row = margin_by_date.get(d)
        margin_factor = engine.factor_margin(
            margin_row, amount, margins, amount_by_date, d
        )
        bias = engine.factor_bias(klines, d)
        velocity = engine.factor_margin_velocity(margins, d)
        flow_row = flow_by_date.get(d)
        rofi = engine.factor_rofi(flow_row, amount)
        spread_pct = spread_map.get(d)
        spread = engine.factor_spread(spread_pct, spread_map, d)

        spread_val = float(spread_pct) if spread_pct is not None else 0.0
        lock_proxy = _proxy_factor(
            clamp(50.0 + 12.0 * spread_val + 0.35 * (breadth_proxy["score"] - 50.0)),
            {"spread_pct": spread_val},
            "backcast proxy from microcap spread + breadth",
        )
        limit_proxy = _proxy_factor(
            clamp(50.0 + 10.0 * spread_val + 0.50 * (breadth_proxy["score"] - 50.0)),
            {"spread_pct": spread_val},
            "backcast proxy from microcap spread + breadth",
        )

        composed = engine.compose_all(
            {"rofi": rofi, "breadth": breadth_proxy, "liquidity": liquidity},
            {
                "margin": margin_factor,
                "bias": bias,
                "turnover_heat": turnover_heat,
                "margin_velocity": velocity,
            },
            {"lock": lock_proxy, "spread": spread, "limit_heat": limit_proxy},
        )
        points.append(
            {
                "date": d,
                "composite": composed["composite"],
                "baoma": composed["baoma"],
                "novice": composed["novice"],
                "retail": composed["retail"],
                "confidence": composed["confidence"],
                "estimated": True,
                "source": "backcast",
                "close": round(float(row["close"]), 2),
                "change_pct": round(pct, 2),
                "turnover_rate": round(float(row.get("turnover_rate", 0.0) or 0.0), 3),
                "amount_billion": round(amount / 1e8, 2),
            }
        )
    return points


def update_history_point(
    history: List[Dict[str, Any]],
    target: str,
    quant: Dict[str, Any],
) -> List[Dict[str, Any]]:
    updated = False
    for point in history:
        if point.get("date") == target:
            point.update(
                {
                    "composite": quant["composite"],
                    "baoma": quant["baoma"],
                    "novice": quant["novice"],
                    "retail": quant["retail"],
                    "confidence": quant["confidence"],
                    "estimated": False,
                    "source": "actual",
                }
            )
            updated = True
    if not updated:
        return history
    return sorted(history, key=lambda p: p["date"])


# ---------------------------------------------------------------------------
# Full and morning runs
# ---------------------------------------------------------------------------

def run_full(
    target: date,
    session: str,
    now: datetime,
    cache: Optional[Dict[str, Any]],
    fetcher: MarketFetcher,
    engine: QuantSentimentEngine,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    # Fetch the 30 sector boards BEFORE the 56-page universe crawl: Sina's
    # rate limiter is shared by both endpoints, and a universe-first order
    # routinely truncates the sector response to one page.
    print("  [2/5] 采集 30 个细分板块横截面...")
    quotes = fetcher.fetch_index_quotes()
    scored_sectors = score_sectors(fetcher, engine, target, session, cache)
    available_sectors = [s for s in scored_sectors if s.get("available")]
    if available_sectors:
        top = available_sectors[0]
        print(
            f"  - 板块覆盖 {len(available_sectors)}/30 | 最热: {top['name']} "
            f"({top['sector_heat']}分, {top['tag']})"
        )
    else:
        print("  - 板块数据不可用")

    print("  [3/5] 采集全市场微观结构数据...")
    universe = fetcher.fetch_universe()
    market_hist = fetcher.fetch_market_history(limit=180, finalized_through=target.isoformat())
    margins = fetcher.fetch_margin_data(page_size=180)
    flows = fetcher.fetch_capital_flow(limit=180)
    limit_pool = fetcher.fetch_limit_pool(target.isoformat(), fallback=universe)

    margin = align_by_date(margins, target.isoformat())
    flow = next((r for r in flows if r["date"] == target.isoformat()), None)
    if flow and fetcher.quality.get("capital_flow", {}).get("status") == "partial":
        flow = dict(flow)
        flow["estimated"] = True

    overview = build_overview(target, session, quotes, universe, market_hist, cache)
    print(
        f"  - 全市场成交额: {overview['total_turnover']/1e8:.2f} 亿元 | "
        f"上涨 {overview['total_advances']} / 下跌 {overview['total_declines']} "
        f"[{overview['breadth_source']}]"
    )
    if margin:
        stale = overview["date"] > margin["date"]
        print(
            f"  - 两融余额: {margin['rzye']/1e8:.2f} 亿元 | 融资买入 {margin['rzmre']/1e8:.2f} 亿元 "
            f"({margin['date']}{'，T-1 待晨间校准' if stale else ''})"
        )
    else:
        print("  - 两融数据缺失，BMEI 两融因子将降权")
    print(
        f"  - 涨停 {limit_pool['limit_up_count']} / 炸板 {limit_pool['broken_board_count']} "
        f"(炸板率 {limit_pool['broken_rate']}%) | 来源: {limit_pool['source']}"
    )
    print(f"  - 资金流: {'可用' if flow else '不可用（RSI 将降权，不伪造数据）'}")

    print("  [4/5] 运行多因子行为量化模型...")
    quant = engine.evaluate_market_sentiment(
        overview=overview,
        flow=flow,
        margin=margin,
        limit_pool=limit_pool,
        historical_klines=market_hist.get("market", []),
        historical_margins=margins,
        spread_history=market_hist.get("spread", {}),
        market_date=target.isoformat(),
    )

    data_status = "finalized" if session in ("close", "holiday_review") else "provisional"
    print("  [5/5] 构建 120 日滚动历史序列...")
    history = build_history_series(
        market=market_hist.get("market", []),
        margins=margins,
        flow_history=flows,
        spread_map=market_hist.get("spread", {}),
        engine=engine,
        latest_quant=quant,
        target=target.isoformat(),
        data_status=data_status,
        days=120,
    )

    payload = make_payload(
        target=target,
        session=session,
        data_status=data_status,
        quant=quant,
        overview=overview,
        scored_sectors=scored_sectors,
        history=history,
        fetcher=fetcher,
        now=now,
    )
    payload["raw_inputs"] = {
        "overview": overview,
        "flow": flow,
        "margin": margin,
        "limit_pool": limit_pool,
    }

    if data_status == "provisional":
        # Intraday previews are usable on the chart but are explicitly marked;
        # the 15:30 close run replaces this point with the finalized value.
        quant["confidence"] = round(clamp(quant["confidence"] * 0.90), 1)
        payload["confidence"] = quant["confidence"]
        payload["data_quality"]["confidence"] = quant["confidence"]
        for point in history:
            if point.get("date") == target.isoformat():
                point["confidence"] = quant["confidence"]
    return payload, history


def run_morning(
    target: date,
    now: datetime,
    cache: Dict[str, Any],
    fetcher: MarketFetcher,
    engine: QuantSentimentEngine,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Recompute the last close with settled margin data, no live quotes needed."""
    print("  [2/4] 盘前晨报：复用上一交易日收盘横截面，刷新两融清算数据...")
    margins = fetcher.fetch_margin_data(page_size=180)
    market_hist = fetcher.fetch_market_history(limit=180, finalized_through=target.isoformat())
    margin = align_by_date(margins, target.isoformat())

    raw_inputs = cache.get("raw_inputs") or {}
    overview = dict(raw_inputs.get("overview") or {})
    market = market_hist.get("market", [])
    target_row = _row_on(market, target.isoformat())
    if target_row:
        overview["total_turnover"] = target_row["amount"]
        overview["market_turnover_rate"] = target_row.get("turnover_rate", 0.0)
    if not overview.get("indices"):
        raise RuntimeError("morning cache has no index snapshot")

    flow = raw_inputs.get("flow")
    limit_pool = raw_inputs.get("limit_pool") or {
        "available": False,
        "date": target.isoformat(),
        "limit_up_count": 0,
        "broken_board_count": 0,
        "limit_down_count": 0,
        "broken_rate": 0.0,
        "max_consecutive_board": 1,
        "source": "cache_missing",
    }
    if not market_hist.get("available"):
        raise RuntimeError("market history unavailable; cannot refresh morning scores")

    quant = engine.evaluate_market_sentiment(
        overview=overview,
        flow=flow,
        margin=margin,
        limit_pool=limit_pool,
        historical_klines=market,
        historical_margins=margins,
        spread_history=market_hist.get("spread", {}),
        market_date=target.isoformat(),
    )

    scored_sectors = cache.get("sectors") or []
    existing_history = read_json(HISTORY_FILE, [])
    history = existing_history
    if (
        not history
        or len(history) < 100
        or not any(p.get("date") == target.isoformat() for p in history)
    ):
        history = build_history_series(
            market=market,
            margins=margins,
            flow_history=[],
            spread_map=market_hist.get("spread", {}),
            engine=engine,
            latest_quant=quant,
            target=target.isoformat(),
            data_status="finalized",
            days=120,
        )
    else:
        history = update_history_point(history, target.isoformat(), quant)

    # Preserve the finalized data-quality blocks for cached feeds, while
    # replacing the margin/market entries with this morning's refresh.
    cache_quality = (cache.get("data_quality") or {}).get("sources", [])
    merged_quality: Dict[str, Dict[str, Any]] = {
        str(item.get("name")): {k: v for k, v in item.items() if k != "name"}
        for item in cache_quality
    }
    merged_quality.update(fetcher.quality)
    fetcher.quality = merged_quality

    payload = make_payload(
        target=target,
        session="pre_market_briefing",
        data_status="finalized",
        quant=quant,
        overview=overview,
        scored_sectors=scored_sectors,
        history=history,
        fetcher=fetcher,
        now=now,
        revision="margin_settlement",
        extra_note=f" 已用 {margin['date'] if margin else '缺失'} 两融清算数据校准。" if margin else "",
    )
    payload["raw_inputs"] = {
        "overview": overview,
        "flow": flow,
        "margin": margin,
        "limit_pool": limit_pool,
    }
    return payload, history


# ---------------------------------------------------------------------------
# Validation and output
# ---------------------------------------------------------------------------

def validate_payload(payload: Dict[str, Any], history: List[Dict[str, Any]]) -> None:
    target = payload["market_date"]
    if not is_trading_day(target):
        raise ValueError(f"market_date {target} is not an A-share trading day")
    for key, value in payload["scores"].items():
        if not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 100:
            raise ValueError(f"score {key}={value} outside [0,100]")
    dates = [p["date"] for p in history]
    if len(dates) != len(set(dates)):
        raise ValueError("duplicate dates in history")
    if dates != sorted(dates):
        raise ValueError("history dates are not sorted ascending")
    if any(d > target for d in dates):
        raise ValueError("history contains dates after market_date")
    if payload["data_status"] == "finalized" and dates and dates[-1] != target:
        raise ValueError("finalized payload missing latest history point")


def write_outputs(payload: Dict[str, Any], history: List[Dict[str, Any]]) -> None:
    ensure_dirs()
    write_json(LATEST_FILE, payload)
    write_json(HISTORY_FILE, history)
    js = (
        "window.INDEX_DATA = "
        + json.dumps(payload, ensure_ascii=False)
        + ";\nwindow.HISTORY_DATA = "
        + json.dumps(history, ensure_ascii=False)
        + ";\n"
    )
    with open(DATA_JS_FILE, "w", encoding="utf-8") as fh:
        fh.write(js)


def print_result(payload: Dict[str, Any]) -> None:
    scores = payload["scores"]
    micro = payload["microstructure"]
    print("\n" + "=" * 64)
    print(f"  ★ 市场情绪与资金结构 (交易日: {payload['market_date']}) ★")
    print(f"  数据状态: [{payload['data_status']}] · {payload['session_title']}")
    print(f"  综合情绪指数 (Composite):    {scores['composite']:5.1f} / 100")
    print(f"  宝妈入场指数 (Baoma Bubble): {scores['baoma']:5.1f} / 100")
    print(f"  小白跟风指数 (Novice Chase): {scores['novice']:5.1f} / 100")
    print(f"  散户情绪指数 (Retail Sent):  {scores['retail']:5.1f} / 100")
    print(f"  数据置信度 (Confidence):     {payload['confidence']:5.1f}%")
    print(f"  当前量化状态: [{payload['status']['tag']}]")
    print(f"  风控指引建议: {payload['status']['action']}")
    print("-" * 64)
    def num(value: Any, digits: int = 2) -> str:
        if value is None:
            return "N/A"
        try:
            return f"{float(value):,.{digits}f}"
        except (TypeError, ValueError):
            return "N/A"

    print(f"  全市场成交额: {num(micro['total_turnover_billion'])} 亿元")
    print(
        f"  上涨/下跌: {micro['advances']} / {micro['declines']} "
        f"(涨跌比 {micro['advance_ratio_pct']}%) | 数据源 {payload['breadth_source']}"
    )
    print(
        f"  两融融资余额: {num(micro['margin_balance_billion'])} 亿元 | "
        f"融资买入占比 {num(micro['margin_buy_ratio_pct'])}% | 数据日 {micro['margin_date']}"
    )
    print(
        f"  涨停/炸板/跌停: {micro['limit_up_count']} / {micro['broken_board_count']} / "
        f"{micro['limit_down_count']} (炸板率 {micro['broken_rate_pct']}%)"
    )
    print(
        f"  主力净流入: {num(micro['main_net_flow_billion'])} 亿元 | "
        f"散户小单净买入: {num(micro['retail_net_flow_billion'])} 亿元"
    )
    missing = payload["data_quality"]["missing_factors"]
    if missing:
        print(f"  缺失因子（已降权）: {', '.join(missing)}")
    print("=" * 64)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_pipeline(mode: str = "auto") -> Dict[str, Any]:
    ensure_dirs()
    now = get_cst_now()
    target, session = resolve_session(now, mode=mode)

    print("=" * 64)
    print("  A股市场情绪与资金结构 · 盘前晨报 / 收盘定稿管线")
    print(f"  运行模式: {mode} | 北京时间: {now.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  目标交易日: {target.isoformat()} | 会话: {session}")
    print("=" * 64)

    fetcher = MarketFetcher()
    engine = QuantSentimentEngine()
    cache = read_json(LATEST_FILE, None)

    cache_ok = bool(
        cache
        and cache.get("market_date") == target.isoformat()
        and cache.get("data_status") == "finalized"
    )

    if mode == "morning" and session == "pre_market_briefing":
        if not cache_ok:
            print("  [警告] 未找到上一交易日定稿快照，降级为实时抓取（可能无法取得完整横截面）")
            payload, history = run_full(target, session, now, cache, fetcher, engine)
        else:
            try:
                payload, history = run_morning(target, now, cache, fetcher, engine)
            except Exception as exc:  # noqa: BLE001 - fall back to cached snapshot
                print(f"  [警告] 晨间校准失败（{exc}），沿用上一交易日定稿快照")
                payload = dict(cache)
                payload.update(
                    {
                        "session_mode": "pre_market_briefing",
                        "session_title": SESSION_TITLES["pre_market_briefing"],
                        "updated_at": now.isoformat(),
                        "updated_at_cst": now.strftime("%Y-%m-%d %H:%M:%S CST"),
                        "revision": "cached_snapshot",
                    }
                )
                history = read_json(HISTORY_FILE, [])
    else:
        payload, history = run_full(target, session, now, cache, fetcher, engine)

    validate_payload(payload, history)
    write_outputs(payload, history)
    print_result(payload)
    print(f"  [OK] 已写入 docs/data/latest.json、history.json、data.js ({len(history)} 个历史点)")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="A-share quant sentiment pipeline")
    parser.add_argument(
        "--mode",
        choices=["auto", "close", "morning"],
        default="auto",
        help="close=收盘定稿, morning=盘前晨报, auto=按北京时间自动选择",
    )
    args = parser.parse_args()
    try:
        run_pipeline(mode=args.mode)
        return 0
    except Exception as exc:  # noqa: BLE001 - CI boundary
        print(f"\n[FATAL] 管线失败: {exc}", file=sys.stderr)
        import traceback

        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

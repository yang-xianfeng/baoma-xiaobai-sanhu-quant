"""Institutional-grade A-share market data layer.

Design principles
-----------------
1. Every number is traceable to a public source and a trade date.
2. Missing data is reported as missing, never silently fabricated.
3. Market-wide aggregates use the full listed universe or the SSE+SZSE
   composite turnover, not a narrow index constituent subset.
4. All endpoints are plain HTTP JSON/CSV style APIs; no browser scraping,
   no CAPTCHA, no forum crawling.

Primary channels
----------------
- Tencent Finance  : index quotes and daily K-lines (open/high/low/close,
                     volume, turnover rate, turnover amount).
- Sina Finance     : full A-share cross-section (5,500+ stocks) for exact
                     advance/decline breadth, turnover distribution and
                     microcap/bluechip spread; industry/concept money flow.
- Eastmoney        : margin financing (RZRQ) history and limit-up /
                     broken-board / limit-down pools. Optional order-flow
                     feed; unavailable feeds degrade to neutral with a
                     confidence penalty instead of fake values.
"""

from __future__ import annotations

import json
import math
import re
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from statistics import median
from typing import Any, Dict, Iterable, List, Optional, Tuple

from core.calendar import (
    coerce_date,
    get_cst_today,
    get_latest_trading_day,
    get_previous_trading_day,
)

EM_UT = "bd1d9dbb04005c3771421f278111e5a0"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
    "Accept-Encoding": "identity",
}


# ---------------------------------------------------------------------------
# Small utilities
# ---------------------------------------------------------------------------

def safe_float(val: Any, default: float = 0.0) -> float:
    try:
        f = float(val)
        return f if math.isfinite(f) else default
    except (ValueError, TypeError):
        return default


def safe_int(val: Any, default: int = 0) -> int:
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return default


def http_get(
    url: str,
    referer: Optional[str] = None,
    timeout: int = 12,
    retries: int = 2,
) -> Optional[str]:
    headers = dict(DEFAULT_HEADERS)
    if referer:
        headers["Referer"] = referer
    req = urllib.request.Request(url, headers=headers)
    last_error = ""
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status == 200:
                    raw = resp.read()
                    try:
                        return raw.decode("utf-8")
                    except UnicodeDecodeError:
                        return raw.decode("gbk", errors="replace")
                last_error = f"HTTP {resp.status}"
        except Exception as exc:  # noqa: BLE001 - network boundary
            last_error = str(exc)[:120]
            if attempt < retries:
                time.sleep(0.25 * attempt)
    return None


def http_get_json(
    url: str,
    referer: Optional[str] = None,
    timeout: int = 12,
    retries: int = 2,
) -> Any:
    text = http_get(url, referer=referer, timeout=timeout, retries=retries)
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:  # noqa: BLE001 - provider boundary
        return None


def _pct_change(curr: float, prev: float) -> float:
    if prev <= 0:
        return 0.0
    return (curr - prev) / prev * 100.0


def parse_tencent_quotes(text: str) -> Dict[str, Dict[str, Any]]:
    """Parse qt.gtimg.cn s_ index quote lines.

    Field map for v_s_sh000001="1~上证指数~000001~3512.66~-35.71~-1.01~
    249603981~35207018~~613792.85~ZS~":
    3 close, 4 change, 5 pct, 6 volume(hand), 7 amount(10k CNY),
    9 total market cap(100M CNY).
    """
    indices: Dict[str, Dict[str, Any]] = {}
    for line in text.replace("\n", ";").split(";"):
        line = line.strip()
        if not line or "~" not in line:
            continue
        parts = line.split("~")
        if len(parts) < 8:
            continue
        code = parts[2].strip()
        close = safe_float(parts[3])
        if not code or close <= 0:
            continue
        amount_wan = safe_float(parts[7])
        indices[code] = {
            "code": code,
            "name": parts[1].strip(),
            "close": close,
            "change": safe_float(parts[4]),
            "pct": safe_float(parts[5]),
            "volume": safe_float(parts[6]),
            "amount": amount_wan * 1e4,
            "total_market_cap_billion": safe_float(parts[9]) if len(parts) > 9 else 0.0,
        }
    return indices


def limit_rule(code: str, name: str) -> float:
    """Approximate daily price limit for the stock's board (percent)."""
    upper = (name or "").upper()
    is_st = "ST" in upper
    if code.startswith(("688", "689")):
        return 20.0
    if code.startswith(("300", "301")):
        return 20.0
    if code.startswith(("8", "4", "9")):
        return 30.0
    if is_st:
        return 5.0
    return 10.0


def summarize_universe(rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Compute exact cross-sectional breadth and distribution statistics."""
    clean: List[Dict[str, Any]] = []
    seen = set()
    for r in rows:
        code = str(r.get("code", "")).strip()
        if not code or code in seen:
            continue
        trade = safe_float(r.get("trade"))
        prev = safe_float(r.get("settlement"))
        if trade <= 0 or prev <= 0:
            continue
        seen.add(code)
        clean.append(
            {
                "code": code,
                "name": str(r.get("name", "")).strip(),
                "trade": trade,
                "prev": prev,
                "high": safe_float(r.get("high")),
                "low": safe_float(r.get("low")),
                "change_pct": safe_float(r.get("changepercent")),
                "amount": safe_float(r.get("amount")),
                "volume": safe_float(r.get("volume")),
                "turnover": safe_float(r.get("turnoverratio")),
                "mktcap": safe_float(r.get("mktcap")),
                "nmc": safe_float(r.get("nmc")),
            }
        )

    if not clean:
        return {
            "available": False,
            "active_count": 0,
            "advances": 0,
            "declines": 0,
            "unchanged": 0,
            "advance_ratio": 50.0,
            "total_amount": 0.0,
            "median_change": 0.0,
            "cap_weighted_change": 0.0,
            "median_turnover": 0.0,
            "p90_turnover": 0.0,
            "tail_heat_10": 0.0,
            "tail_heat_15": 0.0,
            "microcap_spread": 0.0,
            "limit_up_est": 0,
            "broken_est": 0,
            "limit_down_est": 0,
        }

    advances = sum(1 for r in clean if r["change_pct"] > 0.001)
    declines = sum(1 for r in clean if r["change_pct"] < -0.001)
    unchanged = len(clean) - advances - declines
    denom = advances + declines
    advance_ratio = advances / denom * 100.0 if denom else 50.0

    total_amount = sum(r["amount"] for r in clean)
    changes = sorted(r["change_pct"] for r in clean)
    median_change = median(changes)

    cap_sum = sum(r["mktcap"] for r in clean)
    cap_weighted_change = (
        sum(r["mktcap"] * r["change_pct"] for r in clean) / cap_sum if cap_sum else 0.0
    )

    turnovers = sorted(r["turnover"] for r in clean if r["turnover"] > 0)
    median_turnover = median(turnovers) if turnovers else 0.0
    p90_turnover = (
        turnovers[min(len(turnovers) - 1, int(len(turnovers) * 0.90))] if turnovers else 0.0
    )
    tail_heat_10 = (
        sum(1 for t in turnovers if t >= 10.0) / len(turnovers) * 100.0 if turnovers else 0.0
    )
    tail_heat_15 = (
        sum(1 for t in turnovers if t >= 15.0) / len(turnovers) * 100.0 if turnovers else 0.0
    )

    # Speculative microcap vs bluechip spread, equal-weight within cap cohorts.
    cap_sorted = sorted(clean, key=lambda r: r["nmc"] or r["mktcap"])
    n = len(cap_sorted)
    cut = max(1, int(n * 0.30))
    small = cap_sorted[:cut]
    large = cap_sorted[-cut:]
    microcap_spread = median([r["change_pct"] for r in small]) - median(
        [r["change_pct"] for r in large]
    )

    limit_up_est = 0
    broken_est = 0
    limit_down_est = 0
    for r in clean:
        limit = limit_rule(r["code"], r["name"])
        tol = max(0.011, r["prev"] * 0.0006)
        up_price = round(r["prev"] * (1.0 + limit / 100.0), 2)
        down_price = round(r["prev"] * (1.0 - limit / 100.0), 2)
        if r["trade"] >= up_price - tol:
            limit_up_est += 1
        elif r["high"] >= up_price - tol:
            broken_est += 1
        if r["trade"] <= down_price + tol:
            limit_down_est += 1

    return {
        "available": True,
        "active_count": len(clean),
        "advances": advances,
        "declines": declines,
        "unchanged": unchanged,
        "advance_ratio": round(advance_ratio, 2),
        "total_amount": total_amount,
        "median_change": round(median_change, 3),
        "cap_weighted_change": round(cap_weighted_change, 3),
        "median_turnover": round(median_turnover, 3),
        "p90_turnover": round(p90_turnover, 3),
        "tail_heat_10": round(tail_heat_10, 2),
        "tail_heat_15": round(tail_heat_15, 2),
        "microcap_spread": round(microcap_spread, 3),
        "limit_up_est": limit_up_est,
        "broken_est": broken_est,
        "limit_down_est": limit_down_est,
    }


def _match_score(board_name: str, keywords: List[str]) -> Tuple[int, str]:
    best = 0
    best_kw = ""
    for kw in keywords:
        if not kw:
            continue
        if board_name == kw:
            score = 100 + len(kw)
        elif kw in board_name:
            # Short generic keywords such as 电子 / 银行 / 证券 are extremely
            # prone to false positives (e.g. 电子纸概念 for 半导体). They only
            # count as weak hints and fall below the 30-point match threshold
            # unless the exact board exists.
            score = (50 + len(kw)) if len(kw) >= 3 else (20 + len(kw))
        elif board_name in kw:
            score = 30 + len(board_name)
        else:
            score = 0
        if score > best:
            best, best_kw = score, kw
    return best, best_kw


# ---------------------------------------------------------------------------
# Fetcher
# ---------------------------------------------------------------------------

class MarketFetcher:
    def __init__(self) -> None:
        self.quality: Dict[str, Dict[str, Any]] = {}

    # -- quality bookkeeping -------------------------------------------------
    def _set_quality(
        self,
        name: str,
        status: str,
        source: str = "",
        trade_date: str = "",
        detail: str = "",
    ) -> None:
        self.quality[name] = {
            "status": status,  # ok / partial / unavailable
            "source": source,
            "date": trade_date,
            "detail": detail[:200],
        }

    # -- index quotes --------------------------------------------------------
    def fetch_index_quotes(self) -> Dict[str, Any]:
        symbols = [
            "s_sh000001",
            "s_sz399001",
            "s_sz399006",
            "s_sh000300",
            "s_sz399303",
            "s_sh000688",
            "s_sz399106",
        ]
        url = "https://qt.gtimg.cn/q=" + ",".join(symbols)
        text = http_get(url, referer="https://gu.qq.com/")
        if not text:
            self._set_quality("index_quotes", "unavailable", "Tencent")
            return {"available": False, "indices": {}}
        indices = parse_tencent_quotes(text)
        if not indices:
            self._set_quality("index_quotes", "unavailable", "Tencent")
            return {"available": False, "indices": {}}
        self._set_quality("index_quotes", "ok", "Tencent", get_cst_today().isoformat())
        return {"available": True, "indices": indices}

    # -- full A-share universe ----------------------------------------------
    def _sina_universe_page(self, page: int) -> List[Dict[str, Any]]:
        url = (
            "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
            "Market_Center.getHQNodeData?"
            + urllib.parse.urlencode(
                {
                    "page": page,
                    "num": 100,
                    "sort": "symbol",
                    "asc": 1,
                    "node": "hs_a",
                }
            )
        )
        data = http_get_json(
            url,
            referer="https://vip.stock.finance.sina.com.cn/",
            timeout=15,
            retries=2,
        )
        return data if isinstance(data, list) else []

    def fetch_universe(self) -> Dict[str, Any]:
        """Fetch the full Shanghai+Shenzhen A-share cross-section from Sina.

        Sina exposes 100 rows per page; 56-58 concurrent pages return the
        complete 5,500+ stock universe in roughly 10-15 seconds.
        """
        count_text = http_get(
            "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
            "Market_Center.getHQNodeStockCount?node=hs_a",
            referer="https://vip.stock.finance.sina.com.cn/",
            timeout=10,
        )
        count = safe_int((count_text or "").strip().strip('"'))
        if count <= 0:
            count = 5600
        pages = max(1, math.ceil(count / 100))

        rows: List[Dict[str, Any]] = []
        failed_pages: List[int] = []
        try:
            with ThreadPoolExecutor(max_workers=8) as pool:
                future_map = {
                    pool.submit(self._sina_universe_page, p): p for p in range(1, pages + 1)
                }
                for fut in as_completed(future_map):
                    page = future_map[fut]
                    try:
                        page_rows = fut.result()
                        if page_rows:
                            rows.extend(page_rows)
                        else:
                            failed_pages.append(page)
                    except Exception:  # noqa: BLE001 - provider boundary
                        failed_pages.append(page)
        except Exception as exc:  # noqa: BLE001
            self._set_quality("universe", "unavailable", "Sina", detail=str(exc))
            return summarize_universe([])

        summary = summarize_universe(rows)
        coverage = len({str(r.get("code")) for r in rows}) / max(1, count)
        summary["coverage"] = round(min(1.0, coverage), 3)
        if not summary["available"]:
            self._set_quality("universe", "unavailable", "Sina", detail="empty cross-section")
        elif coverage < 0.85 or failed_pages:
            self._set_quality(
                "universe",
                "partial",
                "Sina",
                get_cst_today().isoformat(),
                f"coverage={coverage:.1%}, failed_pages={len(failed_pages)}",
            )
        else:
            self._set_quality(
                "universe",
                "ok",
                "Sina",
                get_cst_today().isoformat(),
                f"{summary['active_count']} active stocks",
            )
        summary["breadth_valid"] = summary["available"] and coverage >= 0.85
        return summary

    # -- margin financing ----------------------------------------------------
    def fetch_margin_data(self, page_size: int = 90) -> List[Dict[str, Any]]:
        url = (
            "https://datacenter-web.eastmoney.com/api/data/v1/get?"
            + urllib.parse.urlencode(
                {
                    "reportName": "RPTA_RZRQ_LSHJ",
                    "columns": "ALL",
                    "sortColumns": "DIM_DATE",
                    "sortTypes": -1,
                    "pageNumber": 1,
                    "pageSize": page_size,
                    "source": "WEB",
                    "client": "WEB",
                }
            )
        )
        res = http_get_json(url, referer="https://data.eastmoney.com/", timeout=15)
        rows = (res or {}).get("result", {}).get("data", []) if isinstance(res, dict) else []
        results: List[Dict[str, Any]] = []
        for r in rows:
            raw_date = str(r.get("DIM_DATE", ""))
            clean_date = raw_date.split(" ")[0] if raw_date else ""
            if not clean_date:
                continue
            results.append(
                {
                    "date": clean_date,
                    "rzye": safe_float(r.get("RZYE")),
                    "rzmre": safe_float(r.get("RZMRE")),
                    "rzche": safe_float(r.get("RZCHE")),
                    "rqye": safe_float(r.get("RQYE")),
                    "rzrqye": safe_float(r.get("RZRQYE")),
                    "ltsz": safe_float(r.get("LTSZ")),
                    "rzyezb": safe_float(r.get("RZYEZB")),
                }
            )
        if results:
            self._set_quality("margin", "ok", "Eastmoney datacenter", results[0]["date"])
        else:
            self._set_quality("margin", "unavailable", "Eastmoney datacenter")
        return results

    # -- limit-up / broken-board / limit-down pools --------------------------
    def _fetch_pool(self, kind: str, trade_date: str) -> Optional[Dict[str, Any]]:
        pool = {"ZT": "getTopicZTPool", "ZB": "getTopicZBPool", "DT": "getTopicDTPool"}[kind]
        url = (
            f"https://push2ex.eastmoney.com/{pool}?"
            + urllib.parse.urlencode(
                {
                    "ut": "7eea3edcaed734bea9cbfc24409ed989",
                    "dpt": "wz.ztzt",
                    "Pageindex": 0,
                    "pagesize": 1000,
                    "sort": "fbt:asc",
                    "date": trade_date.replace("-", ""),
                }
            )
        )
        return http_get_json(url, referer="https://quote.eastmoney.com/", timeout=15)

    def fetch_limit_pool(
        self,
        trade_date: Optional[str] = None,
        fallback: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if not trade_date:
            trade_date = get_latest_trading_day().isoformat()
        target = coerce_date(trade_date)
        date_compact = target.strftime("%Y%m%d")

        zt = self._fetch_pool("ZT", date_compact)
        zb = self._fetch_pool("ZB", date_compact)
        dt = self._fetch_pool("DT", date_compact)

        def block(res: Any) -> Dict[str, Any]:
            data = (res or {}).get("data") if isinstance(res, dict) else None
            return data if isinstance(data, dict) else {}

        zt_data, zb_data, dt_data = block(zt), block(zb), block(dt)
        zt_count = safe_int(zt_data.get("tc"))
        zb_count = safe_int(zb_data.get("tc"))
        dt_count = safe_int(dt_data.get("tc"))

        max_lbc = 1
        for p in zt_data.get("pool", []) or []:
            lbc = safe_int(p.get("lbc"), 1)
            streak = safe_int((p.get("zttj") or {}).get("ct"), 1)
            max_lbc = max(max_lbc, lbc, streak)

        pool_ok = bool(zt_data or zb_data or dt_data)
        estimated = False
        source = "Eastmoney push2ex"

        # A trading-day pool can legitimately have zero limit-ups, but three
        # empty blocks together usually means the endpoint is blocked. Only
        # then fall back to the cross-section estimate.
        if not pool_ok and fallback and fallback.get("available"):
            zt_count = safe_int(fallback.get("limit_up_est"))
            zb_count = safe_int(fallback.get("broken_est"))
            dt_count = safe_int(fallback.get("limit_down_est"))
            estimated = True
            source = "Sina cross-section estimate"
        elif not pool_ok:
            self._set_quality("limit_pool", "unavailable", source, target.isoformat())
            return {
                "available": False,
                "estimated": False,
                "date": target.isoformat(),
                "limit_up_count": 0,
                "broken_board_count": 0,
                "limit_down_count": 0,
                "broken_rate": 0.0,
                "max_consecutive_board": 1,
                "source": source,
            }

        broken_rate = (
            zb_count / (zt_count + zb_count) * 100.0 if (zt_count + zb_count) > 0 else 0.0
        )
        status = "ok" if pool_ok and not estimated else ("partial" if estimated else "unavailable")
        self._set_quality("limit_pool", status, source, target.isoformat())
        return {
            "available": True,
            "estimated": estimated,
            "date": target.isoformat(),
            "limit_up_count": zt_count,
            "broken_board_count": zb_count,
            "limit_down_count": dt_count,
            "broken_rate": round(broken_rate, 2),
            "max_consecutive_board": max_lbc,
            "source": source,
        }

    # -- index / market history ---------------------------------------------
    def _newkline(self, symbol: str, limit: int) -> List[Dict[str, Any]]:
        url = (
            "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newkline/newkline?"
            + urllib.parse.urlencode({"param": f"{symbol},day,,,{limit}"})
        )
        res = http_get_json(url, referer="https://gu.qq.com/", timeout=15)
        data = (res or {}).get("data", {}).get(symbol, {}) if isinstance(res, dict) else {}
        rows = data.get("day", []) if isinstance(data, dict) else []
        out: List[Dict[str, Any]] = []
        prev_close = 0.0
        for r in rows:
            if not isinstance(r, list) or len(r) < 6:
                continue
            d = str(r[0])
            close = safe_float(r[2])
            if not d or close <= 0:
                continue
            # Newkline layout: date, open, close, high, low, volume,
            # {}, turnover_rate(%), amount(10k CNY), ...
            turnover_rate = safe_float(r[7]) if len(r) > 7 else 0.0
            amount = safe_float(r[8]) * 1e4 if len(r) > 8 else 0.0
            pct = _pct_change(close, prev_close) if prev_close > 0 else 0.0
            out.append(
                {
                    "date": d,
                    "open": safe_float(r[1]),
                    "close": close,
                    "high": safe_float(r[3]),
                    "low": safe_float(r[4]),
                    "volume": safe_float(r[5]),
                    "turnover_rate": turnover_rate,
                    "amount": amount,
                    "change_pct": round(pct, 3),
                }
            )
            prev_close = close
        return out

    def fetch_market_history(
        self,
        limit: int = 90,
        finalized_through: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Fetch SSE/SZSE composite, CSI300 and CNI2000 daily history.

        Turnover amount is the real exchange turnover from Tencent's newkline
        feed (field 8, unit 10k CNY). SSE + SZSE composite amounts form the
        full-market turnover denominator used by the factor model.
        """
        symbols = {
            "sse": "sh000001",
            "szse": "sz399106",
            "csi2000": "sz399303",
            "csi300": "sh000300",
        }
        raw: Dict[str, List[Dict[str, Any]]] = {}
        with ThreadPoolExecutor(max_workers=4) as pool:
            future_map = {
                pool.submit(self._newkline, symbol, limit): key
                for key, symbol in symbols.items()
            }
            for fut in as_completed(future_map):
                key = future_map[fut]
                try:
                    raw[key] = fut.result()
                except Exception:  # noqa: BLE001
                    raw[key] = []

        if finalized_through:
            cutoff = coerce_date(finalized_through).isoformat()
            for key in raw:
                raw[key] = [r for r in raw[key] if r["date"] <= cutoff]

        sse = raw.get("sse", [])
        szse = raw.get("szse", [])
        csi2000 = raw.get("csi2000", [])
        csi300 = raw.get("csi300", [])

        sse_by_date = {r["date"]: r for r in sse}
        szse_by_date = {r["date"]: r for r in szse}
        market: List[Dict[str, Any]] = []
        for d in sorted(set(sse_by_date) & set(szse_by_date)):
            a, b = sse_by_date[d], szse_by_date[d]
            amount = a["amount"] + b["amount"]
            vol = a["volume"] + b["volume"]
            to = (
                (a["turnover_rate"] * a["amount"] + b["turnover_rate"] * b["amount"]) / amount
                if amount > 0
                else 0.0
            )
            market.append(
                {
                    "date": d,
                    "close": a["close"],
                    "open": a["open"],
                    "high": a["high"],
                    "low": a["low"],
                    "change_pct": a["change_pct"],
                    "volume": vol,
                    "turnover_rate": round(to, 4),
                    "amount": amount,
                }
            )

        c2000_by_date = {r["date"]: r for r in csi2000}
        c300_by_date = {r["date"]: r for r in csi300}
        spreads: Dict[str, float] = {}
        for d in set(c2000_by_date) & set(c300_by_date):
            spreads[d] = c2000_by_date[d]["change_pct"] - c300_by_date[d]["change_pct"]

        available = bool(market)
        status = "ok" if available and len(market) >= 30 else ("partial" if available else "unavailable")
        self._set_quality(
            "market_history",
            status,
            "Tencent newkline",
            market[-1]["date"] if market else "",
            f"{len(market)} trading days",
        )
        return {
            "available": available,
            "market": market,
            "sse": sse,
            "szse": szse,
            "csi2000": csi2000,
            "csi300": csi300,
            "spread": spreads,
        }

    def fetch_index_klines(self, secid: str = "1.000001", limit: int = 120) -> List[Dict[str, Any]]:
        """Backward-compatible wrapper returning the market K-line list."""
        hist = self.fetch_market_history(limit=limit)
        return hist.get("market", [])

    # -- order flow ----------------------------------------------------------
    def _fetch_fflow(self, secid: str, limit: int) -> List[Dict[str, Any]]:
        url = (
            "https://push2.eastmoney.com/api/qt/stock/fflow/kline/get?"
            + urllib.parse.urlencode(
                {
                    "ut": EM_UT,
                    "lmt": limit,
                    "klt": 101,
                    "secid": secid,
                    "fields1": "f1,f2,f3,f7",
                    "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
                }
            )
        )
        res = http_get_json(url, referer="https://quote.eastmoney.com/", timeout=12)
        lines = (res or {}).get("data", {}).get("klines", []) if isinstance(res, dict) else []
        out: List[Dict[str, Any]] = []
        for line in lines:
            parts = line.split(",")
            if len(parts) >= 6:
                out.append(
                    {
                        "date": parts[0],
                        "main_net": safe_float(parts[1]),
                        "small_net": safe_float(parts[2]),
                        "mid_net": safe_float(parts[3]),
                        "super_net": safe_float(parts[4]),
                        "large_net": safe_float(parts[5]),
                    }
                )
        return out

    def fetch_capital_flow(self, limit: int = 90) -> List[Dict[str, Any]]:
        """Fetch order-flow history for SH + SZ and aggregate by date.

        If Eastmoney's push2 endpoint is unavailable (common from overseas CI
        IPs), return an empty list. The model treats the factor as missing and
        reduces confidence; it never invents small/large order flow.
        """
        with ThreadPoolExecutor(max_workers=2) as pool:
            f_sh = pool.submit(self._fetch_fflow, "1.000001", limit)
            f_sz = pool.submit(self._fetch_fflow, "0.399106", limit)
            sh_rows, sz_rows = f_sh.result(), f_sz.result()
        if not sz_rows:
            # Some deployments only expose the SZSE Component index flow.
            sz_rows = self._fetch_fflow("0.399001", limit)

        maps = []
        if sh_rows:
            maps.append({r["date"]: r for r in sh_rows})
        if sz_rows:
            maps.append({r["date"]: r for r in sz_rows})
        if not maps:
            self._set_quality("capital_flow", "unavailable", "Eastmoney push2")
            return []

        merged: List[Dict[str, Any]] = []
        for d in sorted(set().union(*[set(m) for m in maps])):
            rows = [m[d] for m in maps if d in m]
            main_net = sum(r["main_net"] for r in rows)
            small_net = sum(r["small_net"] for r in rows)
            merged.append(
                {
                    "date": d,
                    "main_net": main_net,
                    "small_net": small_net,
                    "mid_net": sum(r["mid_net"] for r in rows),
                    "super_net": sum(r["super_net"] for r in rows),
                    "large_net": sum(r["large_net"] for r in rows),
                    "divergence": small_net - main_net,
                }
            )
        status = "ok" if len(maps) == 2 else "partial"
        self._set_quality(
            "capital_flow",
            status,
            "Eastmoney push2",
            merged[-1]["date"] if merged else "",
            "SH+SZ" if len(maps) == 2 else "single market",
        )
        return merged

    # -- sectors -------------------------------------------------------------
    def _fetch_eastmoney_boards(self) -> List[Dict[str, Any]]:
        boards: List[Dict[str, Any]] = []
        seen = set()
        for fs, kind in (("m:90+t:2+f:!50", "industry"), ("m:90+t:3+f:!50", "concept")):
            url = (
                "https://push2.eastmoney.com/api/qt/clist/get?"
                + urllib.parse.urlencode(
                    {
                        "ut": EM_UT,
                        "pn": 1,
                        "pz": 500,
                        "po": 1,
                        "np": 1,
                        "fltt": 2,
                        "invt": 2,
                        "fid": "f62",
                        "fs": fs,
                        "fields": "f2,f3,f6,f8,f12,f14,f62,f84,f184,f204,f205",
                    }
                )
            )
            diff = []
            for attempt in range(2):
                res = http_get_json(url, referer="https://quote.eastmoney.com/", timeout=12)
                diff = (res or {}).get("data", {}).get("diff", []) if isinstance(res, dict) else []
                if diff:
                    break
                time.sleep(0.4 * (attempt + 1))
            for b in diff or []:
                name = str(b.get("f14", "")).strip()
                if not name or name in seen:
                    continue
                seen.add(name)
                boards.append(
                    {
                        "name": name,
                        "kind": kind,
                        "pct": safe_float(b.get("f3")),
                        "turnover": safe_float(b.get("f8")),
                        "amount": safe_float(b.get("f6")),
                        "main_net": safe_float(b.get("f62")),
                        "retail_net": safe_float(b.get("f84")),
                        "net_inflow": safe_float(b.get("f62")),
                        "flow_quality": "order_flow",
                        "leader_name": str(b.get("f204", "")).strip(),
                        "leader_code": str(b.get("f205", "")).strip(),
                        "source": "Eastmoney",
                    }
                )
            time.sleep(0.25)
        return boards

    def _fetch_sina_boards(self) -> List[Dict[str, Any]]:
        boards: List[Dict[str, Any]] = []
        # fenlei=0: Sina industries, 1: Sina concepts, 2: CSRC industries.
        # One request per category is deliberately used: this endpoint rate
        # limits aggressively, and 3 requests per run is far safer than 8
        # paginated requests. A best-of-two retry recovers truncated lists.
        for fenlei, kind in ((0, "industry"), (1, "concept"), (2, "csrc")):
            url = (
                "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
                "MoneyFlow.ssl_bkzj_bk?"
                + urllib.parse.urlencode(
                    {"page": 1, "num": 300, "sort": "netamount", "asc": 0, "fenlei": fenlei}
                )
            )
            best: List[Dict[str, Any]] = []
            for attempt in range(2):
                data = http_get_json(url, referer="https://finance.sina.com.cn/", timeout=20)
                if isinstance(data, list) and len(data) > len(best):
                    best = data
                expected = 40 if fenlei == 0 else 100
                if len(best) >= expected:
                    break
                time.sleep(1.0)
            for b in best:
                name = str(b.get("name", "")).strip()
                if not name:
                    continue
                in_amt = safe_float(b.get("inamount"))
                out_amt = safe_float(b.get("outamount"))
                boards.append(
                    {
                        "name": name,
                        "kind": kind,
                        "pct": safe_float(b.get("avg_changeratio")) * 100.0,
                        "turnover": safe_float(b.get("turnover")),
                        "amount": in_amt + out_amt,
                        "main_net": None,
                        "retail_net": None,
                        "net_inflow": safe_float(b.get("netamount")),
                        "flow_quality": "aggregate",
                        "leader_name": str(b.get("ts_name", "")).strip(),
                        "leader_code": str(b.get("ts_symbol", "")).strip(),
                        "source": "Sina",
                    }
                )
            time.sleep(0.8)
        return boards

    def fetch_sector_boards(self) -> Dict[str, Any]:
        east = self._fetch_eastmoney_boards()
        boards = list(east)
        source = "Eastmoney" if east else ""
        sina: List[Dict[str, Any]] = []
        if len(east) < 300:
            # Eastmoney sometimes returns only one of the industry/concept
            # lists. Supplement with Sina aggregate-flow boards so the
            # 30-sector matrix stays complete; order-flow boards still win
            # on equal keyword quality.
            sina = self._fetch_sina_boards()
            existing = {b["name"] for b in boards}
            for b in sina:
                if b["name"] not in existing:
                    boards.append(b)
                    existing.add(b["name"])
            if sina:
                source = "Eastmoney+Sina" if east else "Sina"
        if not boards:
            self._set_quality("sectors", "unavailable", "Eastmoney/Sina")
            return {"available": False, "boards": [], "source": ""}
        status = "ok" if len(east) >= 300 else "partial"
        flow_quality = (
            "order_flow" if east else (boards[0]["flow_quality"] if boards else "unavailable")
        )
        self._set_quality(
            "sectors",
            status,
            source,
            get_cst_today().isoformat(),
            f"{len(east)} EM + {len(sina)} Sina; flow={flow_quality}",
        )
        return {"available": True, "boards": boards, "source": source}

    def match_sectors(self, boards: List[Dict[str, Any]], definitions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Match the 30 curated sectors to the best provider board."""
        used: set = set()
        matched: List[Dict[str, Any]] = []
        for s_def in definitions:
            best = None
            best_score = 0
            best_name = ""
            for b in boards:
                score, kw = _match_score(b["name"], s_def["board_keywords"])
                if b["name"] in used:
                    continue
                better = score > best_score
                # On an exact keyword tie, prefer real order-flow data over
                # Sina's aggregate net inflow.
                if (
                    score == best_score
                    and best is not None
                    and b.get("flow_quality") == "order_flow"
                    and best.get("flow_quality") != "order_flow"
                ):
                    better = True
                if better:
                    best, best_score, best_name = b, score, kw
            if best is None or best_score < 30:
                matched.append(
                    {
                        "id": s_def["id"],
                        "name": s_def["name"],
                        "category": s_def["category"],
                        "description": s_def["description"],
                        "typical_companies": s_def["typical_companies"],
                        "available": False,
                        "pct": None,
                        "turnover": None,
                        "amount": None,
                        "main_net": None,
                        "retail_net": None,
                        "net_inflow": None,
                        "flow_quality": "unavailable",
                        "leader_name": "",
                        "leader_code": "",
                        "source": "",
                    }
                )
                continue
            used.add(best["name"])
            matched.append(
                {
                    "id": s_def["id"],
                    "name": s_def["name"],
                    "category": s_def["category"],
                    "description": s_def["description"],
                    "typical_companies": s_def["typical_companies"],
                    "available": True,
                    "provider_board": best["name"],
                    "match_keyword": best_name,
                    "pct": round(best["pct"], 3),
                    "turnover": round(best["turnover"], 3),
                    "amount": best["amount"],
                    "main_net": best["main_net"],
                    "retail_net": best["retail_net"],
                    "net_inflow": best["net_inflow"],
                    "flow_quality": best["flow_quality"],
                    "leader_name": best["leader_name"],
                    "leader_code": best["leader_code"],
                    "source": best["source"],
                }
            )
        return matched

    def fetch_all_sectors_market(self) -> List[Dict[str, Any]]:
        from core.sector_catalog import SECTOR_DEFINITIONS

        result = self.fetch_sector_boards()
        return self.match_sectors(result["boards"], SECTOR_DEFINITIONS)


# ---------------------------------------------------------------------------
# Pure helpers used by the pipeline and tests
# ---------------------------------------------------------------------------

def align_by_date(rows: List[Dict[str, Any]], target: str) -> Optional[Dict[str, Any]]:
    """Return the row whose date equals target, else the latest row <= target.

    Providers are inconsistent about sort order (Eastmoney margin history is
    descending, Tencent K-lines are ascending), so never rely on list order.
    """
    exact = [r for r in rows if r.get("date") == target]
    if exact:
        return exact[0]
    prior = [r for r in rows if str(r.get("date", "")) <= target]
    return max(prior, key=lambda r: str(r.get("date", ""))) if prior else None


def latest_row(rows: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    return rows[-1] if rows else None

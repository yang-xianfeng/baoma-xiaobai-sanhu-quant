"""Behavioral-finance factor engine for A-share retail / speculation heat.

All sub-indices are scaled to [0, 100]:
    0   = extreme panic / capitulation
    50  = neutral baseline
    100 = extreme euphoria / bubble

Important design choices
------------------------
1. Weight redistribution, not fabrication. If a feed (e.g. order flow or the
   limit-up pool) is unavailable, its weight is redistributed to the remaining
   factors and the sub-index reports a lower coverage / confidence.
2. Rolling, robust scaling. Liquidity, margin and speculative-spread factors
   use rolling z-scores or percentile ranks where history permits; bounded
   tanh mappings are used only when history is too short, and the result is
   flagged as estimated.
3. Full-market denominator. Turnover and margin ratios use SSE + SZSE
   composite turnover, never a single index constituent aggregate.
"""

from __future__ import annotations

import math
from statistics import median
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:  # Python 3.8+
    from statistics import fmean as _mean
except ImportError:  # pragma: no cover
    _mean = lambda values: sum(values) / len(values)  # type: ignore


# ---------------------------------------------------------------------------
# Math helpers
# ---------------------------------------------------------------------------

def clamp(val: float, low: float = 0.0, high: float = 100.0) -> float:
    if not math.isfinite(val):
        return low
    return max(low, min(high, float(val)))


def safe_mean(values: Iterable[float]) -> float:
    clean = [v for v in values if isinstance(v, (int, float)) and math.isfinite(v)]
    return sum(clean) / len(clean) if clean else 0.0


def safe_std(values: Iterable[float], ddof: int = 1) -> float:
    clean = [v for v in values if isinstance(v, (int, float)) and math.isfinite(v)]
    if len(clean) <= ddof:
        return 0.0
    m = safe_mean(clean)
    variance = sum((x - m) ** 2 for x in clean) / (len(clean) - ddof)
    return math.sqrt(variance)


def percentile_rank(value: float, history: Iterable[float]) -> float:
    """Percentile rank in [0, 100], with ties split evenly."""
    clean = [v for v in history if isinstance(v, (int, float)) and math.isfinite(v)]
    if not clean:
        return 50.0
    lower = sum(1 for v in clean if v < value)
    equal = sum(1 for v in clean if abs(v - value) < 1e-9)
    return clamp((lower + 0.5 * equal) / len(clean) * 100.0)


def z_score_to_scale(z: float, center: float = 50.0, scale: float = 16.67) -> float:
    """Map a z-score to [0, 100]; z=0 -> 50, z=+3 -> 100, z=-3 -> 0."""
    return clamp(center + z * scale)


def tanh_score(value: float, center: float, scale: float) -> float:
    """Bounded monotonic mapping centred at 50.

    scale is the half-width that moves the score by roughly 46 points
    (tanh(1) = 0.76). This avoids arbitrary linear clipping.
    """
    if scale <= 1e-9:
        return 50.0
    return clamp(50.0 + 50.0 * math.tanh((value - center) / scale))


def _factor(
    score: Optional[float],
    raw: Any = None,
    available: bool = True,
    estimated: bool = False,
    detail: str = "",
) -> Dict[str, Any]:
    return {
        "score": None if score is None else round(clamp(score), 1),
        "raw": raw,
        "available": bool(available and score is not None),
        "estimated": bool(estimated),
        "detail": detail,
    }


def _weighted_component(
    factors: Dict[str, Dict[str, Any]],
    weights: Dict[str, float],
) -> Dict[str, Any]:
    """Weighted average with missing-factor weight redistribution."""
    available = {
        k: w
        for k, w in weights.items()
        if k in factors and factors[k].get("available") and factors[k].get("score") is not None
    }
    coverage = sum(available.values())
    if coverage <= 1e-9:
        return {
            "score": 50.0,
            "coverage": 0.0,
            "estimated": True,
            "weights_used": {},
            "components": factors,
        }
    score = sum(factors[k]["score"] * w for k, w in available.items()) / coverage
    estimated = any(factors[k].get("estimated") for k in available)
    return {
        "score": round(clamp(score), 1),
        "coverage": round(coverage, 4),
        "estimated": estimated,
        "weights_used": {k: round(w / coverage, 4) for k, w in available.items()},
        "components": factors,
    }


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class QuantSentimentEngine:
    # -- individual factors -------------------------------------------------
    def factor_rofi(self, flow: Optional[Dict[str, Any]], total_amount: float) -> Dict[str, Any]:
        """Retail order-flow imbalance: small orders buy while main orders sell."""
        if not flow or total_amount <= 0:
            return _factor(None, None, False, False, "order flow unavailable")
        small = float(flow.get("small_net", 0.0))
        main = float(flow.get("main_net", 0.0))
        if abs(small) < 1e-6 and abs(main) < 1e-6:
            return _factor(None, None, False, False, "zero net flow")
        divergence_ratio = (small - main) / total_amount * 100.0
        retail_ratio = small / total_amount * 100.0
        # Divergence dominates; raw retail buying adds directional information.
        composite_raw = 0.70 * divergence_ratio + 0.30 * retail_ratio
        score = tanh_score(composite_raw, 0.0, 1.5)
        return _factor(
            score,
            {
                "divergence_ratio_pct": round(divergence_ratio, 4),
                "retail_ratio_pct": round(retail_ratio, 4),
            },
            True,
            bool(flow.get("estimated", False)),
            "small-minus-main net flow / full-market turnover",
        )

    def factor_breadth(self, advances: int, declines: int) -> Dict[str, Any]:
        denom = int(advances) + int(declines)
        if denom <= 0:
            return _factor(None, None, False, False, "breadth unavailable")
        score = int(advances) / denom * 100.0
        return _factor(
            score,
            {"advances": int(advances), "declines": int(declines)},
            True,
            False,
            "advance / (advance + decline)",
        )

    def factor_liquidity(
        self,
        klines: List[Dict[str, Any]],
        target_date: str,
    ) -> Dict[str, Any]:
        """Free-float turnover z-score versus the prior 60 trading days."""
        rows = [r for r in klines if str(r.get("date", "")) <= target_date]
        if not rows:
            return _factor(None, None, False, False, "no turnover history")
        current = rows[-1]
        curr_to = float(current.get("turnover_rate", 0.0) or 0.0)
        hist = [float(r.get("turnover_rate", 0.0) or 0.0) for r in rows[-60:]]
        if curr_to <= 0:
            # Fall back to real turnover amount z-score if rate is missing.
            curr_amt = float(current.get("amount", 0.0) or 0.0)
            amts = [float(r.get("amount", 0.0) or 0.0) for r in rows[-60:]]
            mean_amt, std_amt = safe_mean(amts), safe_std(amts)
            if std_amt <= 1e-9 or curr_amt <= 0:
                return _factor(50.0, {"turnover_rate": 0.0}, True, True, "flat turnover")
            z = (curr_amt - mean_amt) / std_amt
            return _factor(
                z_score_to_scale(z),
                {"z": round(z, 3), "turnover_rate": 0.0, "metric": "amount"},
                True,
                True,
                "turnover amount z-score proxy",
            )
        if len(hist) < 20:
            return _factor(50.0, {"turnover_rate": curr_to}, True, True, "insufficient history")
        mean_to, std_to = safe_mean(hist), safe_std(hist)
        z = (curr_to - mean_to) / std_to if std_to > 1e-9 else 0.0
        return _factor(
            z_score_to_scale(z),
            {"z": round(z, 3), "turnover_rate": round(curr_to, 4), "mean60": round(mean_to, 4)},
            True,
            False,
            "market turnover-rate z-score (60d)",
        )

    def factor_tail_heat(self, universe: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if not universe or not universe.get("available"):
            return _factor(None, None, False, False, "cross-section unavailable")
        tail = float(universe.get("tail_heat_10", 0.0) or 0.0)
        # Baseline: ~6% of active stocks turn over more than 10% on a normal day.
        score = tanh_score(tail, 6.0, 5.0)
        return _factor(
            score,
            {"tail_heat_10_pct": round(tail, 2), "p90_turnover": universe.get("p90_turnover")},
            True,
            False,
            "share of stocks with turnover >= 10%",
        )

    def factor_turnover_heat(
        self,
        liquidity: Dict[str, Any],
        tail: Dict[str, Any],
    ) -> Dict[str, Any]:
        parts = []
        if liquidity.get("available"):
            parts.append((liquidity, 0.60))
        if tail.get("available"):
            parts.append((tail, 0.40))
        if not parts:
            return _factor(None, None, False, False, "turnover heat unavailable")
        total_w = sum(w for _, w in parts)
        score = sum(f["score"] * w for f, w in parts) / total_w
        return _factor(
            score,
            {
                "liquidity": liquidity.get("raw"),
                "tail": tail.get("raw"),
            },
            True,
            any(f.get("estimated") for f, _ in parts),
            "60% liquidity z + 40% turnover tail heat",
        )

    def factor_margin(
        self,
        margin: Optional[Dict[str, Any]],
        total_amount: float,
        history: List[Dict[str, Any]],
        amount_by_date: Dict[str, float],
        target_date: str,
    ) -> Dict[str, Any]:
        if not margin or margin.get("rzmre", 0) <= 0:
            return _factor(None, None, False, False, "margin data unavailable")
        # Use the turnover of the margin record's own trade date. Margin data
        # is normally published T-1 and must not be divided by an intraday or
        # next-day partial turnover.
        denom = amount_by_date.get(str(margin.get("date", ""))) or total_amount
        if denom <= 0:
            return _factor(None, None, False, False, "margin denominator unavailable")
        ratio = float(margin["rzmre"]) / denom * 100.0
        ratios: List[float] = []
        for row in history:
            row_date = str(row.get("date", ""))
            if row_date > target_date:
                continue
            amt = amount_by_date.get(row_date)
            if amt and amt > 0 and row.get("rzmre", 0) > 0:
                ratios.append(float(row["rzmre"]) / amt * 100.0)
        ratios = ratios[-60:]
        if len(ratios) >= 10:
            score = percentile_rank(ratio, ratios)
            method = "rolling percentile (60d)"
            estimated = False
        else:
            score = tanh_score(ratio, 8.5, 3.0)
            method = "bounded tanh (history < 10d)"
            estimated = True
        return _factor(
            score,
            {
                "margin_buy_ratio_pct": round(ratio, 3),
                "history_n": len(ratios),
                "margin_date": margin.get("date"),
            },
            True,
            estimated,
            method,
        )

    def factor_bias(self, klines: List[Dict[str, Any]], target_date: str) -> Dict[str, Any]:
        rows = [r for r in klines if str(r.get("date", "")) <= target_date]
        closes = [float(r.get("close", 0.0) or 0.0) for r in rows if r.get("close", 0) > 0]
        if len(closes) < 20:
            return _factor(None, None, False, False, "insufficient price history")
        window = closes[-60:]
        ma60 = safe_mean(window)
        curr = closes[-1]
        bias = (curr - ma60) / ma60 * 100.0 if ma60 > 0 else 0.0
        score = tanh_score(bias, 0.0, 8.0)
        return _factor(
            score,
            {"bias60_pct": round(bias, 3), "ma60": round(ma60, 2)},
            True,
            len(window) < 60,
            "BIAS60 with tanh(+-8pp) mapping",
        )

    def factor_margin_velocity(self, history: List[Dict[str, Any]], target_date: str) -> Dict[str, Any]:
        rows = [r for r in history if str(r.get("date", "")) <= target_date]
        rows = sorted(rows, key=lambda r: str(r.get("date", "")))
        if len(rows) < 6:
            return _factor(None, None, False, False, "margin history < 6d")
        curr = float(rows[-1].get("rzye", 0.0) or 0.0)
        base = float(rows[-6].get("rzye", 0.0) or 0.0)
        if curr <= 0 or base <= 0:
            return _factor(None, None, False, False, "invalid margin balance")
        velocity = (curr / base - 1.0) * 100.0
        score = tanh_score(velocity, 0.0, 2.0)
        return _factor(
            score,
            {"margin_velocity_5d_pct": round(velocity, 3), "base_date": rows[-6].get("date")},
            True,
            False,
            "5-trading-day margin balance change",
        )

    def factor_lock(self, limit_pool: Dict[str, Any]) -> Dict[str, Any]:
        if not limit_pool or not limit_pool.get("available"):
            return _factor(None, None, False, False, "limit pool unavailable")
        broken = float(limit_pool.get("broken_rate", 0.0) or 0.0)
        total = int(limit_pool.get("limit_up_count", 0)) + int(
            limit_pool.get("broken_board_count", 0)
        )
        if total <= 0:
            return _factor(None, None, False, False, "no limit-up events")
        score = clamp(100.0 - broken)
        return _factor(
            score,
            {"broken_rate_pct": round(broken, 2), "events": total},
            True,
            bool(limit_pool.get("estimated", False)),
            "100 - broken-board rate",
        )

    def factor_spread(
        self,
        spread_pct: Optional[float],
        spread_history: Optional[Dict[str, float]],
        target_date: str,
    ) -> Dict[str, Any]:
        if spread_pct is None:
            return _factor(None, None, False, False, "microcap spread unavailable")
        z = None
        hist_vals = []
        if spread_history:
            hist_vals = [
                float(v)
                for d, v in spread_history.items()
                if str(d) <= target_date and isinstance(v, (int, float))
            ][-60:]
        absolute_score = tanh_score(float(spread_pct), 0.0, 2.0)
        if len(hist_vals) >= 20:
            mean_s, std_s = safe_mean(hist_vals), safe_std(hist_vals)
            if std_s > 1e-9:
                z = (float(spread_pct) - mean_s) / std_s
                score = 0.60 * absolute_score + 0.40 * z_score_to_scale(z)
                return _factor(
                    score,
                    {"spread_pct": round(float(spread_pct), 3), "z": round(z, 3)},
                    True,
                    False,
                    "60% absolute + 40% rolling z (60d)",
                )
        return _factor(
            absolute_score,
            {"spread_pct": round(float(spread_pct), 3), "z": None},
            True,
            len(hist_vals) < 20,
            "bounded absolute spread",
        )

    def factor_limit_heat(self, limit_pool: Dict[str, Any]) -> Dict[str, Any]:
        if not limit_pool or not limit_pool.get("available"):
            return _factor(None, None, False, False, "limit pool unavailable")
        zt = int(limit_pool.get("limit_up_count", 0))
        height = int(limit_pool.get("max_consecutive_board", 1))
        count_score = clamp((zt - 10) / 110.0 * 100.0)
        height_score = clamp((height - 1) / 7.0 * 100.0)
        score = 0.70 * count_score + 0.30 * height_score
        return _factor(
            score,
            {"limit_up_count": zt, "max_consecutive_board": height},
            True,
            bool(limit_pool.get("estimated", False)),
            "70% breadth of limit-ups + 30% board height",
        )

    # -- composition --------------------------------------------------------
    def compose_all(
        self,
        retail_factors: Dict[str, Dict[str, Any]],
        baoma_factors: Dict[str, Dict[str, Any]],
        novice_factors: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Any]:
        r = _weighted_component(
            retail_factors, {"rofi": 0.40, "breadth": 0.35, "liquidity": 0.25}
        )
        b = _weighted_component(
            baoma_factors,
            {"margin": 0.35, "bias": 0.30, "turnover_heat": 0.20, "margin_velocity": 0.15},
        )
        n = _weighted_component(
            novice_factors, {"lock": 0.40, "spread": 0.35, "limit_heat": 0.25}
        )

        composite = 0.40 * r["score"] + 0.30 * b["score"] + 0.30 * n["score"]

        # Sub-index confidence: coverage x estimation penalty.
        def sub_conf(x: Dict[str, Any]) -> float:
            return x["coverage"] * (0.90 if x["estimated"] else 1.0)

        confidence = (
            0.40 * sub_conf(r) + 0.30 * sub_conf(b) + 0.30 * sub_conf(n)
        ) * 100.0

        signals = {}
        for key, comp in (("retail", r), ("baoma", b), ("novice", n)):
            for name, factor in comp["components"].items():
                signals[f"{key}.{name}"] = factor.get("score")

        return {
            "retail": round(r["score"], 1),
            "baoma": round(b["score"], 1),
            "novice": round(n["score"], 1),
            "composite": round(clamp(composite), 1),
            "confidence": round(clamp(confidence), 1),
            "coverage": {
                "retail": round(r["coverage"], 3),
                "baoma": round(b["coverage"], 3),
                "novice": round(n["coverage"], 3),
            },
            "estimated": {
                "retail": r["estimated"],
                "baoma": b["estimated"],
                "novice": n["estimated"],
            },
            "components": {
                "retail": r["components"],
                "baoma": b["components"],
                "novice": n["components"],
            },
            "weights_used": {
                "retail": r["weights_used"],
                "baoma": b["weights_used"],
                "novice": n["weights_used"],
            },
            "signals": signals,
        }

    # -- public evaluation --------------------------------------------------
    def evaluate_market_sentiment(
        self,
        overview: Dict[str, Any],
        flow: Optional[Dict[str, Any]],
        margin: Optional[Dict[str, Any]],
        limit_pool: Dict[str, Any],
        historical_klines: List[Dict[str, Any]],
        historical_margins: List[Dict[str, Any]],
        spread_history: Optional[Dict[str, float]] = None,
        market_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        target_date = (
            market_date
            or overview.get("date")
            or (historical_klines[-1]["date"] if historical_klines else "")
        )
        rows = [r for r in historical_klines if str(r.get("date", "")) <= target_date]
        target_row = rows[-1] if rows else {}
        total_amount = float(overview.get("total_turnover", 0.0) or 0.0)
        if total_amount <= 0:
            total_amount = float(target_row.get("amount", 0.0) or 0.0)

        universe = overview.get("universe") or {}
        advances = int(overview.get("total_advances", 0) or 0)
        declines = int(overview.get("total_declines", 0) or 0)
        if advances + declines == 0 and universe.get("available"):
            advances = int(universe.get("advances", 0))
            declines = int(universe.get("declines", 0))

        amount_by_date = {
            str(r.get("date")): float(r.get("amount", 0.0) or 0.0) for r in historical_klines
        }
        spread_pct = overview.get("microcap_spread")
        if spread_pct is None and spread_history:
            spread_pct = spread_history.get(target_date)

        margin_sorted = sorted(
            [r for r in historical_margins if str(r.get("date", "")) <= target_date],
            key=lambda r: str(r.get("date", "")),
        )
        margin_stale_days = 0
        if margin and margin.get("date"):
            margin_stale_days = sum(
                1 for r in rows if str(r.get("date", "")) > str(margin["date"])
            )

        liquidity = self.factor_liquidity(historical_klines, target_date)
        tail = self.factor_tail_heat(universe)
        turnover_heat = self.factor_turnover_heat(liquidity, tail)
        margin_factor = self.factor_margin(
            margin, total_amount, margin_sorted, amount_by_date, target_date
        )
        if margin_stale_days > 0 and margin_factor["available"]:
            margin_factor["estimated"] = True
            margin_factor["detail"] += f"; stale {margin_stale_days}d"

        retail_factors = {
            "rofi": self.factor_rofi(flow, total_amount),
            "breadth": self.factor_breadth(advances, declines),
            "liquidity": liquidity,
        }
        baoma_factors = {
            "margin": margin_factor,
            "bias": self.factor_bias(historical_klines, target_date),
            "turnover_heat": turnover_heat,
            "margin_velocity": self.factor_margin_velocity(margin_sorted, target_date),
        }
        novice_factors = {
            "lock": self.factor_lock(limit_pool),
            "spread": self.factor_spread(spread_pct, spread_history, target_date),
            "limit_heat": self.factor_limit_heat(limit_pool),
        }

        composed = self.compose_all(retail_factors, baoma_factors, novice_factors)
        status_info = self._get_status_interpretation(
            composed["composite"],
            composed["retail"],
            composed["baoma"],
            composed["novice"],
            composed["confidence"],
        )

        # Flat signal map kept for dashboard compatibility, keyed by factor.
        signals = {
            "order_flow_imbalance": retail_factors["rofi"]["score"] or 50.0,
            "advance_breadth": retail_factors["breadth"]["score"] or 50.0,
            "liquidity_turnover": liquidity["score"] or 50.0,
            "margin_leverage": margin_factor["score"] or 50.0,
            "bias60": baoma_factors["bias"]["score"] or 50.0,
            "turnover_heat": turnover_heat["score"] or 50.0,
            "margin_velocity": baoma_factors["margin_velocity"]["score"] or 50.0,
            "lock_success": novice_factors["lock"]["score"] or 50.0,
            "microcap_premium": novice_factors["spread"]["score"] or 50.0,
            "limit_up_heat": novice_factors["limit_heat"]["score"] or 50.0,
        }

        raw = {
            "rofi": retail_factors["rofi"]["raw"],
            "breadth": retail_factors["breadth"]["raw"],
            "liquidity": liquidity["raw"],
            "margin_leverage": margin_factor["raw"],
            "bias60": baoma_factors["bias"]["raw"],
            "turnover_heat": turnover_heat["raw"],
            "margin_velocity": baoma_factors["margin_velocity"]["raw"],
            "lock_success": novice_factors["lock"]["raw"],
            "microcap_premium": novice_factors["spread"]["raw"],
            "limit_heat": novice_factors["limit_heat"]["raw"],
        }

        micro = {
            "total_turnover_billion": round(total_amount / 1e8, 2),
            "market_turnover_rate_pct": round(
                float(target_row.get("turnover_rate", 0.0) or 0.0), 3
            ),
            "liquidity_z": liquidity["raw"].get("z") if liquidity["raw"] else None,
            "advances": advances,
            "declines": declines,
            "unchanged": int(overview.get("total_unchanged", 0) or 0),
            "advance_ratio_pct": round(
                advances / (advances + declines) * 100.0, 2
            ) if (advances + declines) else 50.0,
            "retail_tail_heat_pct": universe.get("tail_heat_10"),
            "main_net_flow_billion": round(float(flow.get("main_net", 0.0)) / 1e8, 2)
            if flow
            else None,
            "retail_net_flow_billion": round(float(flow.get("small_net", 0.0)) / 1e8, 2)
            if flow
            else None,
            "flow_divergence_billion": round(float(flow.get("divergence", 0.0)) / 1e8, 2)
            if flow
            else None,
            "flow_available": bool(flow),
            "margin_balance_billion": round(float(margin.get("rzye", 0.0)) / 1e8, 2)
            if margin
            else None,
            "margin_buy_billion": round(float(margin.get("rzmre", 0.0)) / 1e8, 2)
            if margin
            else None,
            "margin_buy_ratio_pct": margin_factor["raw"].get("margin_buy_ratio_pct")
            if margin_factor["raw"]
            else None,
            "margin_date": margin.get("date") if margin else None,
            "margin_stale_days": margin_stale_days,
            "limit_up_count": int(limit_pool.get("limit_up_count", 0) or 0),
            "broken_board_count": int(limit_pool.get("broken_board_count", 0) or 0),
            "limit_down_count": int(limit_pool.get("limit_down_count", 0) or 0),
            "broken_rate_pct": round(float(limit_pool.get("broken_rate", 0.0) or 0.0), 2),
            "max_consecutive_board": int(limit_pool.get("max_consecutive_board", 1) or 1),
            "limit_pool_estimated": bool(limit_pool.get("estimated", False)),
            "bias60_pct": baoma_factors["bias"]["raw"].get("bias60_pct")
            if baoma_factors["bias"]["raw"]
            else None,
            "csi2000_pct": round(float(overview.get("csi2000_pct", 0.0) or 0.0), 2),
            "csi300_pct": round(float(overview.get("csi300_pct", 0.0) or 0.0), 2),
            "microcap_spread_pct": round(float(spread_pct), 3) if spread_pct is not None else None,
            "active_stocks": universe.get("active_count"),
            "universe_coverage": universe.get("coverage"),
        }

        return {
            "composite": composed["composite"],
            "baoma": composed["baoma"],
            "novice": composed["novice"],
            "retail": composed["retail"],
            "confidence": composed["confidence"],
            "coverage": composed["coverage"],
            "estimated": composed["estimated"],
            "signals": signals,
            "raw": raw,
            "weights_used": composed["weights_used"],
            "factors": composed["components"],
            "status": status_info,
            "microstructure": micro,
        }

    # -- status -------------------------------------------------------------
    def _get_status_interpretation(
        self,
        composite: float,
        rsi: float,
        bmei: float,
        nsi: float,
        confidence: float = 100.0,
    ) -> Dict[str, str]:
        if composite >= 80.0 or bmei >= 85.0:
            base = {
                "level": "extreme_euphoria",
                "tag": "极度狂热 / 见顶预警",
                "badge_class": "badge-danger",
                "summary": "宝妈入场与场外追涨情绪达历史极值，主力大单派发，两融与投机杠杆偏离度过高，进入顶部防御区间。",
                "action": "防守减仓 · 警惕诱多接盘",
            }
        elif composite >= 65.0:
            base = {
                "level": "overheated",
                "tag": "投机升温 / 赚钱效应扩散",
                "badge_class": "badge-warning",
                "summary": "市场广度与赚钱效应活跃，散户跟风买入意愿充沛，短线题材弹性放大，注意去弱留强。",
                "action": "顺势跟踪 · 严设止盈保护",
            }
        elif composite >= 45.0:
            base = {
                "level": "neutral",
                "tag": "情绪平衡 / 常态博弈",
                "badge_class": "badge-primary",
                "summary": "多空力量相对均衡，主力与散户资金流动处于正常区间，板块轮动分化，重在结构性精选。",
                "action": "中性均衡 · 聚焦主线低吸",
            }
        elif composite >= 25.0:
            base = {
                "level": "fear",
                "tag": "情绪低迷 / 磨底震荡",
                "badge_class": "badge-info",
                "summary": "成交与换手收缩，散户观望情绪占主导，市场赚钱效应偏弱，风险逐步释放，等待流动性拐点。",
                "action": "耐心潜伏 · 跟踪企稳信号",
            }
        else:
            base = {
                "level": "extreme_panic",
                "tag": "极度恐慌 / 冰点割肉",
                "badge_class": "badge-success",
                "summary": "恐慌割肉盘涌现，换手与散户买盘双降至冰点，往往对应中长期筹码出清与逆向布局窗口。",
                "action": "逆向思维 · 逢低分批布局",
            }
        if confidence < 65.0:
            base = dict(base)
            base["summary"] += (
                f"（当前数据覆盖率 {confidence:.0f}%，部分因子缺失，信号置信度偏低，仅供参考。）"
            )
            base["action"] = "谨慎参考 · " + base["action"]
            base["confidence_warning"] = True
        else:
            base["confidence_warning"] = False
        return base

    # -- sector scoring -----------------------------------------------------
    def evaluate_sector_quant(self, sectors: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Cross-sectional sector heat with aggregate/order-flow aware tags."""
        available = [s for s in sectors if s.get("available") and s.get("pct") is not None]
        pct_values = [float(s["pct"]) for s in available]
        to_values = [
            float(s["turnover"]) for s in available if s.get("turnover") is not None
        ]
        flow_intensities = []
        for s in available:
            amt = float(s.get("amount") or 0.0)
            flow = s.get("main_net")
            if flow is None:
                flow = s.get("net_inflow")
            if amt > 0 and flow is not None:
                flow_intensities.append(float(flow) / amt * 100.0)

        results: List[Dict[str, Any]] = []
        for s in sectors:
            pct = s.get("pct")
            turnover = s.get("turnover")
            amount = float(s.get("amount") or 0.0)
            main_net = s.get("main_net")
            retail_net = s.get("retail_net")
            net_inflow = s.get("net_inflow")
            flow_quality = s.get("flow_quality", "unavailable")

            pct_score = percentile_rank(float(pct), pct_values) if pct is not None and pct_values else None
            to_score = (
                percentile_rank(float(turnover), to_values)
                if turnover is not None and to_values
                else None
            )

            flow_value = main_net if flow_quality == "order_flow" else net_inflow
            flow_intensity = (
                float(flow_value) / amount * 100.0 if flow_value is not None and amount > 0 else None
            )
            flow_score = (
                percentile_rank(flow_intensity, flow_intensities)
                if flow_intensity is not None and flow_intensities
                else None
            )

            weights: List[Tuple[float, float]] = []
            if pct_score is not None:
                weights.append((pct_score, 0.45))
            if to_score is not None:
                weights.append((to_score, 0.25))
            if flow_score is not None:
                weights.append((flow_score, 0.30))
            if weights:
                total_w = sum(w for _, w in weights)
                heat = sum(v * w for v, w in weights) / total_w
            else:
                heat = 50.0

            tag, tag_class = self._sector_tag(
                pct=pct,
                flow_value=flow_value,
                amount=amount,
                flow_quality=flow_quality,
                main_net=main_net,
                retail_net=retail_net,
            )

            results.append(
                {
                    **s,
                    "sector_heat": round(clamp(heat), 1),
                    "retail_sentiment": round(clamp(flow_score), 1) if flow_score is not None else None,
                    "flow_intensity_pct": round(flow_intensity, 3) if flow_intensity is not None else None,
                    "tag": tag,
                    "tag_class": tag_class,
                    "main_net_billion": round(float(main_net) / 1e8, 2) if main_net is not None else None,
                    "retail_net_billion": round(float(retail_net) / 1e8, 2) if retail_net is not None else None,
                    "net_inflow_billion": round(float(net_inflow) / 1e8, 2) if net_inflow is not None else None,
                }
            )

        results.sort(key=lambda x: x["sector_heat"], reverse=True)
        return results

    def _sector_tag(
        self,
        pct: Optional[float],
        flow_value: Optional[float],
        amount: float,
        flow_quality: str,
        main_net: Optional[float],
        retail_net: Optional[float],
    ) -> Tuple[str, str]:
        intensity = (
            float(flow_value) / amount * 100.0
            if flow_value is not None and amount > 0
            else None
        )
        p = float(pct) if pct is not None else 0.0

        if flow_quality == "order_flow" and main_net is not None and retail_net is not None:
            main_int = float(main_net) / amount * 100.0 if amount > 0 else 0.0
            if main_int > 1.0 and retail_net < 0:
                return "机构洗盘吸筹", "tag-accumulate"
            if main_int > 0.3 and retail_net > 0:
                return "主升共振做多", "tag-bull"
            if main_int < -1.0 and retail_net > 0:
                return "散户追高接盘", "tag-trap"
            if main_int < -0.3 and retail_net < 0:
                return "多空合力杀跌", "tag-bear"
            return "中性整理蓄势", "tag-neutral"

        if flow_quality == "aggregate" and intensity is not None:
            if intensity > 1.5 and p > 0:
                return "资金回流做多", "tag-bull"
            if intensity < -1.5 and p < 0:
                return "资金流出承压", "tag-bear"
            if p > 2.0 and intensity <= 0:
                return "价涨量缩背离", "tag-trap"
            if p < -2.0 and intensity > 0:
                return "逆势资金承接", "tag-accumulate"
            return "中性整理蓄势", "tag-neutral"

        return "数据暂缺", "tag-neutral"

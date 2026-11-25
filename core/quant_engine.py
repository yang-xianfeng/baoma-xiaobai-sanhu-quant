"""Institutional Quantitative Model Engine for A-Share Retail Sentiment & Speculation Overheat.

Unified sentiment scaling:
0 = Extreme Panic (极度恐慌/冰点割肉)
50 = Neutral Baseline (中性均衡)
100 = Extreme Euphoria (极度狂热/见顶泡沫)

Implements rigorous financial engineering & behavioral finance factor models:
1. Microstructure Retail Order Flow & Divergence (散户买盘与多空剪刀差)
2. Advance/Decline Market Breadth (全市场赚钱效应广度)
3. Rolling Free-float Turnover z-score (活跃资金换手偏离)
4. Margin Financing Leverage Participation & Velocity (两融杠杆追高强度)
5. Price-Trend Moving Average Divergence (BIAS60 乖离率)
6. Limit-Up Success & Continuity (打板赚钱效应与封板成功率)
7. Microcap vs Bluechip Speculative Spread (小微盘/题材投机溢价)
"""

from __future__ import annotations

import math
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

def clamp(val: float, low: float = 0.0, high: float = 100.0) -> float:
    if not math.isfinite(val):
        return low
    return max(low, min(high, float(val)))

def safe_mean(values: List[float]) -> float:
    clean = [v for v in values if math.isfinite(v)]
    return sum(clean) / len(clean) if clean else 0.0

def safe_std(values: List[float], ddof: int = 1) -> float:
    clean = [v for v in values if math.isfinite(v)]
    if len(clean) <= ddof:
        return 0.0
    m = safe_mean(clean)
    variance = sum((x - m) ** 2 for x in clean) / (len(clean) - ddof)
    return math.sqrt(variance)

def percentile_rank(value: float, history: List[float]) -> float:
    """Compute percentile rank [0, 100] of value within history."""
    clean = [v for v in history if math.isfinite(v)]
    if not clean:
        return 50.0
    lower_count = sum(1 for v in clean if v < value)
    equal_count = sum(1 for v in clean if abs(v - value) < 1e-7)
    pct = (lower_count + 0.5 * equal_count) / len(clean) * 100.0
    return clamp(pct, 0.0, 100.0)

def z_score_to_scale(z: float, center: float = 50.0, scale: float = 16.67) -> float:
    """Map z-score to roughly [0, 100] centered at 50 (z=0 -> 50, z=+3 -> 100, z=-3 -> 0)."""
    return clamp(center + z * scale)

class QuantSentimentEngine:
    def __init__(self):
        pass

    def evaluate_market_sentiment(
        self,
        overview: Dict[str, Any],
        flow: Dict[str, Any],
        margin: Optional[Dict[str, Any]],
        limit_pool: Dict[str, Any],
        historical_klines: List[Dict[str, Any]],
        historical_margins: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Compute the full quantitative model metrics and sub-indices."""
        total_turnover = overview.get("total_turnover", 1.0)
        if total_turnover <= 0:
            total_turnover = 1.0

        # 1. Microstructure Retail Order Flow (ROFI)
        small_net = flow.get("small_net", 0.0)
        main_net = flow.get("main_net", 0.0)
        # Retail net ratio (-3% to +3% of turnover)
        retail_net_ratio = (small_net / total_turnover) * 100.0
        # Flow divergence (retail buying while main dumping is euphoric)
        divergence_ratio = ((small_net - main_net) / total_turnover) * 100.0
        # Combined flow score: 50 is neutral, >50 is retail greedy, <50 is retail panic selling
        flow_score = clamp(50.0 + retail_net_ratio * 10.0 + divergence_ratio * 4.0)

        # 2. Market Advance/Decline Breadth (MAB)
        advances = overview.get("total_advances", 2500)
        declines = overview.get("total_declines", 2500)
        total_adv_dec = advances + declines
        adv_ratio = (advances / total_adv_dec * 100.0) if total_adv_dec > 0 else 50.0
        breadth_score = clamp(adv_ratio)

        # 3. Free-float Turnover z-score (TO_Z)
        sh_kline = historical_klines[-1] if historical_klines else {}
        curr_turnover = sh_kline.get("turnover_rate", 0.8)
        hist_turnovers = [k.get("turnover_rate", 0.8) for k in historical_klines[-60:]]
        mean_to = safe_mean(hist_turnovers) if hist_turnovers else curr_turnover
        std_to = safe_std(hist_turnovers) if len(hist_turnovers) > 5 else 0.2
        to_z = (curr_turnover - mean_to) / std_to if std_to > 1e-4 else 0.0
        turnover_score = z_score_to_scale(to_z)

        # =====================================================================
        # INDEX 1: 散户情绪指数 (Retail Sentiment Index - RSI)
        # 40% Order Flow + 35% Advance/Decline Breadth + 25% Turnover z-score
        # =====================================================================
        rsi = round(0.40 * flow_score + 0.35 * breadth_score + 0.25 * turnover_score, 1)

        # =====================================================================
        # INDEX 2: 宝妈入场指数 (Out-of-Circle Bubble Euphoria Index - BMEI)
        # =====================================================================
        # Margin buy ratio: RZMRE / Total Turnover (Normal A-share range: 7% to 14%)
        rzmre = margin.get("rzmre", 0.0) if margin else 0.0
        margin_buy_ratio = (rzmre / total_turnover * 100.0) if total_turnover > 0 and rzmre > 0 else 9.5
        margin_leverage_score = clamp((margin_buy_ratio - 6.5) / (14.0 - 6.5) * 100.0)

        # BIAS60: Price deviation from 60-day moving average (-10% to +15%)
        closes = [k.get("close", 3000.0) for k in historical_klines]
        curr_close = closes[-1] if closes else 3000.0
        ma60 = safe_mean(closes[-60:]) if len(closes) >= 30 else curr_close
        bias60 = ((curr_close - ma60) / ma60 * 100.0) if ma60 > 0 else 0.0
        bias_score = clamp((bias60 + 10.0) / 25.0 * 100.0)

        # Margin Velocity (5-day margin balance change rate)
        rzye_curr = margin.get("rzye", 0.0) if margin else 0.0
        rzye_5d = historical_margins[4].get("rzye", rzye_curr) if len(historical_margins) >= 5 else rzye_curr
        margin_velocity = ((rzye_curr - rzye_5d) / rzye_5d * 100.0) if rzye_5d > 0 else 0.0
        margin_velocity_score = clamp((margin_velocity + 2.0) / 5.0 * 100.0)

        bmei = round(
            0.35 * margin_leverage_score +
            0.30 * bias_score +
            0.20 * turnover_score +
            0.15 * margin_velocity_score,
            1
        )

        # =====================================================================
        # INDEX 3: 小白跟风博弈指数 (Novice Speculation & Chasing Index - NSI)
        # =====================================================================
        # Limit-up success rate = 100 - broken_rate
        broken_rate = limit_pool.get("broken_rate", 20.0)
        lock_success_score = clamp(100.0 - broken_rate * 1.2)

        # Microcap vs Bluechip Speculative Spread
        indices = overview.get("indices", {})
        csi2000_pct = indices.get("399303", {}).get("pct", 0.0)
        csi300_pct = indices.get("000300", {}).get("pct", 0.0)
        spread_return = csi2000_pct - csi300_pct
        spread_score = clamp(50.0 + spread_return * 12.0)

        # Limit-up absolute enthusiasm (20 freeze to 120 climax)
        zt_count = limit_pool.get("limit_up_count", 40)
        zt_score = clamp((zt_count - 15) / (115 - 15) * 100.0)

        nsi = round(
            0.40 * lock_success_score +
            0.35 * spread_score +
            0.25 * zt_score,
            1
        )

        # =====================================================================
        # COMPOSITE SENTIMENT INDEX (综合情绪指数 - CMSI)
        # =====================================================================
        composite = round(0.40 * rsi + 0.30 * bmei + 0.30 * nsi, 1)

        # Interpret quantitative risk status
        status_info = self._get_status_interpretation(composite, rsi, bmei, nsi)

        return {
            "composite": composite,
            "baoma": bmei,
            "novice": nsi,
            "retail": rsi,
            "signals": {
                "order_flow_imbalance": round(flow_score, 1),
                "advance_breadth": round(breadth_score, 1),
                "turnover_z": round(turnover_score, 1),
                "margin_leverage": round(margin_leverage_score, 1),
                "bias60": round(bias_score, 1),
                "lock_success": round(lock_success_score, 1),
                "microcap_premium": round(spread_score, 1),
                "limit_up_heat": round(zt_score, 1),
            },
            "status": status_info,
            "microstructure": {
                "total_turnover_billion": round(total_turnover / 1e8, 2),
                "main_net_flow_billion": round(main_net / 1e8, 2),
                "retail_net_flow_billion": round(small_net / 1e8, 2),
                "flow_divergence_billion": round((small_net - main_net) / 1e8, 2),
                "margin_balance_billion": round(rzye_curr / 1e8, 2),
                "margin_buy_billion": round(rzmre / 1e8, 2),
                "margin_buy_ratio_pct": round(margin_buy_ratio, 2),
                "advances": advances,
                "declines": declines,
                "limit_up_count": zt_count,
                "broken_board_count": limit_pool.get("broken_board_count", 0),
                "limit_down_count": limit_pool.get("limit_down_count", 0),
                "broken_rate_pct": round(broken_rate, 2),
                "max_consecutive_board": limit_pool.get("max_consecutive_board", 1),
                "bias60_pct": round(bias60, 2),
                "csi2000_pct": round(csi2000_pct, 2),
                "csi300_pct": round(csi300_pct, 2),
            }
        }

    def _get_status_interpretation(self, composite: float, rsi: float, bmei: float, nsi: float) -> Dict[str, str]:
        if composite >= 80.0 or bmei >= 85.0:
            return {
                "level": "extreme_euphoria",
                "tag": "极度狂热 / 见顶预警",
                "badge_class": "badge-danger",
                "summary": "宝妈入场与场外追涨情绪达历史极值，主力机构大单净流出派发，两融与投机杠杆偏离度过高，进入顶部防御区间。",
                "action": "防守减仓 · 警惕诱多接盘"
            }
        elif composite >= 65.0:
            return {
                "level": "overheated",
                "tag": "投机升温 / 赚钱效应扩散",
                "badge_class": "badge-warning",
                "summary": "全市场广度与赚钱效应活跃，散户跟风买入意愿充沛，短线题材股弹性放大，注意去弱留强。",
                "action": "顺势跟踪 · 严设止盈保护"
            }
        elif composite >= 45.0:
            return {
                "level": "neutral",
                "tag": "情绪平衡 / 常态博弈",
                "badge_class": "badge-primary",
                "summary": "多空力量相对均衡，主力机构与散户资金流动处于正常区间，板块轮动分化，重在结构性精选。",
                "action": "中性均衡 · 聚焦主线低吸"
            }
        elif composite >= 25.0:
            return {
                "level": "fear",
                "tag": "情绪低迷 / 磨底震荡",
                "badge_class": "badge-info",
                "summary": "成交量换手收缩，散户观望情绪占主导，市场赚钱效应偏弱，风险已逐步释放，等待流动性拐点。",
                "action": "耐心潜伏 · 跟踪企稳信号"
            }
        else:
            return {
                "level": "extreme_panic",
                "tag": "极度恐慌 / 冰点割肉",
                "badge_class": "badge-success",
                "summary": "市场恐慌割肉盘涌现，换手与散户买盘双降至冰点，往往对应中长期筹码出清与逆向价值布局窗口。",
                "action": "逆向思维 · 逢低分批布局"
            }

    def evaluate_sector_quant(self, sectors: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Score each sector's retail sentiment and multi-factor heat."""
        results = []
        for s in sectors:
            pct = s.get("pct", 0.0)
            turnover = s.get("turnover", 1.0)
            main_net = s.get("main_net", 0.0)
            retail_net = s.get("retail_net", 0.0)
            divergence = s.get("divergence", 0.0)

            # Sector Heat (0 - 100)
            pct_score = clamp(50.0 + pct * 10.0)
            to_score = clamp((turnover - 0.5) / 5.0 * 100.0)
            main_score = clamp(50.0 + (main_net / 1e8) * 5.0)

            sector_heat = round(0.40 * pct_score + 0.35 * to_score + 0.25 * main_score, 1)
            retail_score = clamp(50.0 + (retail_net / 1e8) * 8.0)

            # Sector State Rating
            if main_net > 2e8 and retail_net < 0:
                tag = "机构洗盘吸筹"
                tag_class = "tag-accumulate"
            elif main_net > 1e8 and retail_net > 0:
                tag = "主升共振做多"
                tag_class = "tag-bull"
            elif main_net < -2e8 and retail_net > 2e8:
                tag = "散户追高接盘"
                tag_class = "tag-trap"
            elif main_net < -1e8 and retail_net < 0:
                tag = "多空合力杀跌"
                tag_class = "tag-bear"
            else:
                tag = "中性整理蓄势"
                tag_class = "tag-neutral"

            results.append({
                **s,
                "sector_heat": sector_heat,
                "retail_sentiment": retail_score,
                "tag": tag,
                "tag_class": tag_class,
                "main_net_billion": round(main_net / 1e8, 2),
                "retail_net_billion": round(retail_net / 1e8, 2),
            })

        results.sort(key=lambda x: x["sector_heat"], reverse=True)
        return results

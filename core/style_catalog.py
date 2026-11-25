"""Concise style-bucket layer above the 30 leaf sectors.

The dashboard should answer one question first: where is money rotating?
Thirty leaf-sector cards are useful for drill-down, but they are too noisy for
a daily overview. These eight buckets keep the professional grouping
(large financials, STAR50, STAR semis, ChiNext50, optical/AI hardware,
robotics, microcap speculation, consumer/health) and aggregate the underlying
sector data.

Buckets are descriptive, not investment advice.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.quant_engine import clamp, tanh_score


STYLE_BUCKETS: List[Dict[str, Any]] = [
    {
        "id": "big_finance",
        "name": "大金融",
        "subtitle": "银行 · 保险 · 证券",
        "sector_ids": ["banking", "securities_insurance"],
    },
    {
        "id": "star50",
        "name": "科创50",
        "subtitle": "科创板硬科技宽基",
        "index_codes": ["000688"],
        "sector_ids": [],
    },
    {
        "id": "star_semi",
        "name": "科创半导体",
        "subtitle": "设备 · 材料 · 芯片设计",
        "sector_ids": ["semiconductor_equipment", "chip_design"],
    },
    {
        "id": "chinext50",
        "name": "创业板50",
        "subtitle": "创业板成长宽基",
        "index_codes": ["399673"],
        "sector_ids": [],
    },
    {
        "id": "ai_hardware",
        "name": "光模块 / 算力",
        "subtitle": "CPO · PCB · AI服务器",
        "sector_ids": ["cpo_optical", "pcb", "ai_compute", "ai_application"],
    },
    {
        "id": "advanced_mfg",
        "name": "机器人 / 高端制造",
        "subtitle": "机器人 · 军工 · 机械",
        "sector_ids": ["robotics", "aerospace_defense"],
    },
    {
        "id": "speculation",
        "name": "投机 / 炒小炒差",
        "subtitle": "微盘 · 涨停 · 炸板",
        "sector_ids": [],
        "special": "speculation",
    },
    {
        "id": "consumer_health",
        "name": "消费 / 医药",
        "subtitle": "白酒 · 食品 · 创新药 · 器械",
        "sector_ids": ["baijiu", "food_beverage", "innovative_drugs", "medical_devices"],
    },
]


def _tag_for(flow_quality: str, pct: Optional[float], net: Optional[float], amount: float) -> tuple:
    if pct is None:
        return "数据暂缺", "tag-neutral"
    if flow_quality in ("order_flow", "aggregate") and net is not None and amount > 0:
        intensity = net / amount * 100.0
        if intensity > 0.5 and pct > 0:
            return "资金流入", "tag-bull"
        if intensity > 0.5 and pct < 0:
            return "逆势承接", "tag-accumulate"
        if intensity < -0.5 and pct > 0:
            return "价涨量缩", "tag-trap"
        if intensity < -0.5 and pct < 0:
            return "资金流出", "tag-bear"
        return "多空平衡", "tag-neutral"
    if pct > 1.0:
        return "领涨", "tag-bull"
    if pct < -1.0:
        return "领跌", "tag-bear"
    return "震荡", "tag-neutral"


def build_style_summary(
    sectors: List[Dict[str, Any]],
    indices: Dict[str, Any],
    quant: Dict[str, Any],
    micro: Dict[str, Any],
) -> List[Dict[str, Any]]:
    by_id = {s.get("id"): s for s in sectors}
    out: List[Dict[str, Any]] = []

    for bucket in STYLE_BUCKETS:
        members = [
            by_id[sid]
            for sid in bucket.get("sector_ids", [])
            if sid in by_id and by_id[sid].get("available")
        ]
        index_pcts = []
        index_names = []
        for code in bucket.get("index_codes", []):
            idx = indices.get(code) or {}
            pct = idx.get("pct")
            if isinstance(pct, (int, float)):
                index_pcts.append(float(pct))
            if idx.get("name"):
                index_names.append(str(idx["name"]))

        if bucket.get("special") == "speculation":
            pct = micro.get("microcap_spread_pct")
            heat = quant.get("novice", 50.0)
            broken = micro.get("broken_rate_pct") or 0.0
            zt = micro.get("limit_up_count") or 0
            if heat >= 65:
                tag, tag_class = "投机升温", "tag-hot"
            elif broken >= 40:
                tag, tag_class = "炸板风险", "tag-trap"
            elif heat >= 45:
                tag, tag_class = "常态博弈", "tag-neutral"
            else:
                tag, tag_class = "投机降温", "tag-bear"
            out.append(
                {
                    "id": bucket["id"],
                    "name": bucket["name"],
                    "subtitle": bucket["subtitle"],
                    "available": True,
                    "pct": round(float(pct), 2) if isinstance(pct, (int, float)) else None,
                    "heat": round(clamp(float(heat)), 1),
                    "net_inflow_billion": None,
                    "flow_quality": "none",
                    "tag": tag,
                    "tag_class": tag_class,
                    "members": [],
                    "detail": f"微盘价差 {pct:+.2f}% · 涨停 {zt} 家 · 炸板率 {broken:.1f}%"
                    if isinstance(pct, (int, float))
                    else f"涨停 {zt} 家 · 炸板率 {broken:.1f}%",
                    "index_code": None,
                }
            )
            continue

        amount = sum(float(s.get("amount") or 0.0) for s in members)
        order_members = [s for s in members if s.get("flow_quality") == "order_flow"]
        aggregate_members = [s for s in members if s.get("flow_quality") == "aggregate"]
        if order_members:
            flow_quality = "order_flow"
            net = sum(float(s.get("main_net") or 0.0) for s in order_members)
        elif aggregate_members:
            flow_quality = "aggregate"
            net = sum(float(s.get("net_inflow") or 0.0) for s in aggregate_members)
        else:
            flow_quality = "none"
            net = None

        if members and amount > 0:
            pct = sum(float(s.get("pct") or 0.0) * float(s.get("amount") or 0.0) for s in members) / amount
            heat = sum(float(s.get("sector_heat") or 50.0) * float(s.get("amount") or 0.0) for s in members) / amount
        elif members:
            pcts = [float(s.get("pct") or 0.0) for s in members]
            heats = [float(s.get("sector_heat") or 50.0) for s in members]
            pct = sum(pcts) / len(pcts)
            heat = sum(heats) / len(heats)
        else:
            pct = None
            heat = None

        if index_pcts:
            ipct = sum(index_pcts) / len(index_pcts)
            iscore = tanh_score(ipct, 0.0, 2.0)
            pct = ipct if pct is None else 0.5 * pct + 0.5 * ipct
            heat = iscore if heat is None else 0.6 * heat + 0.4 * iscore

        flow_amount = (
            sum(float(s.get("amount") or 0.0) for s in order_members)
            if order_members
            else sum(float(s.get("amount") or 0.0) for s in aggregate_members)
        )
        tag, tag_class = _tag_for(flow_quality, pct, net, flow_amount or amount)
        members_text = " · ".join(
            [s.get("name", "") for s in members] + index_names
        ).strip(" · ")
        out.append(
            {
                "id": bucket["id"],
                "name": bucket["name"],
                "subtitle": bucket["subtitle"],
                "available": bool(members or index_pcts),
                "pct": round(float(pct), 2) if pct is not None else None,
                "heat": round(clamp(float(heat)), 1) if heat is not None else None,
                "net_inflow_billion": round(net / 1e8, 2) if net is not None else None,
                "flow_quality": flow_quality,
                "tag": tag,
                "tag_class": tag_class,
                "members": [s.get("name", "") for s in members] + index_names,
                "detail": members_text,
                "index_code": bucket.get("index_codes", [None])[0] if bucket.get("index_codes") else None,
            }
        )

    return out

"""Unit tests for the QUANT 2.1 A-share sentiment system.

All tests are offline. Provider responses are either pure functions or small
deterministic fixtures, so the suite can run inside GitHub Actions before the
network pipeline starts.
"""

from __future__ import annotations

import json
import math
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from core.calendar import (
    CST,
    coerce_date,
    get_latest_trading_day,
    get_previous_trading_day,
    get_recent_trading_days,
    is_trading_day,
    resolve_session,
)
from core.fetcher import (
    MarketFetcher,
    align_by_date,
    limit_rule,
    parse_tencent_quotes,
    summarize_universe,
)
from core.quant_engine import (
    QuantSentimentEngine,
    clamp,
    percentile_rank,
    safe_std,
    tanh_score,
    z_score_to_scale,
)
from core.sector_catalog import CATEGORIES, SECTOR_DEFINITIONS
import run_pipeline


def make_market(days: int = 70, start: float = 3000.0, daily: float = 0.002) -> list:
    rows = []
    close = start
    base = date(2026, 8, 1)
    for i in range(days):
        close = close * (1.0 + daily)
        rows.append(
            {
                "date": (base + timedelta(days=i)).isoformat(),
                "open": close * 0.998,
                "close": round(close, 2),
                "high": round(close * 1.01, 2),
                "low": round(close * 0.99, 2),
                "change_pct": round(daily * 100.0, 3),
                "volume": 1.0e9 + i * 1.0e6,
                "turnover_rate": 1.2 + 0.02 * i,
                "amount": 1.2e12 + i * 1.0e10,
            }
        )
    return rows


def make_margins(market: list, ratio: float = 0.085) -> list:
    rows = []
    for i, row in enumerate(market):
        rows.append(
            {
                "date": row["date"],
                "rzye": 2.0e12 + i * 2.0e9,
                "rzmre": row["amount"] * ratio,
                "rzche": row["amount"] * ratio * 0.95,
                "rqye": 3.0e10,
                "rzrqye": 2.03e12,
                "ltsz": 8.0e13,
                "rzyezb": 2.5,
            }
        )
    return rows


def make_overview(market: list, advances=3000, declines=2000) -> dict:
    last = market[-1]
    return {
        "date": last["date"],
        "total_turnover": last["amount"],
        "market_turnover_rate": last["turnover_rate"],
        "total_advances": advances,
        "total_declines": declines,
        "total_unchanged": 100,
        "advance_ratio": advances / (advances + declines) * 100.0,
        "indices": {"000001": {"close": last["close"], "pct": last["change_pct"]}},
        "universe": {
            "available": True,
            "advances": advances,
            "declines": declines,
            "unchanged": 100,
            "active_count": advances + declines + 100,
            "tail_heat_10": 7.0,
            "p90_turnover": 4.0,
            "microcap_spread": 1.0,
            "coverage": 0.99,
        },
        "breadth_source": "test",
        "csi2000_pct": 1.0,
        "csi300_pct": -0.2,
        "microcap_spread": 1.2,
    }


class TestCalendar(unittest.TestCase):
    def test_weekend_and_holiday(self):
        self.assertFalse(is_trading_day("2026-11-21"))
        self.assertFalse(is_trading_day("2026-11-22"))
        self.assertFalse(is_trading_day("2026-10-01"))
        self.assertTrue(is_trading_day("2026-11-24"))

    def test_recent_trading_days(self):
        days = get_recent_trading_days(5, end_date="2026-11-24")
        self.assertEqual(len(days), 5)
        for d in days:
            self.assertTrue(is_trading_day(d))

    def test_morning_session_points_to_previous_close(self):
        now = datetime(2026, 11, 25, 8, 30, tzinfo=CST)
        target, session = resolve_session(now, mode="morning")
        self.assertEqual(target, date(2026, 11, 24))
        self.assertEqual(session, "pre_market_briefing")

    def test_close_session_points_to_today(self):
        now = datetime(2026, 11, 25, 15, 30, tzinfo=CST)
        target, session = resolve_session(now, mode="close")
        self.assertEqual(target, date(2026, 11, 25))
        self.assertEqual(session, "close")

    def test_intraday_is_preview(self):
        now = datetime(2026, 11, 25, 13, 0, tzinfo=CST)
        target, session = resolve_session(now, mode="auto")
        self.assertEqual(target, date(2026, 11, 25))
        self.assertEqual(session, "intraday_preview")

    def test_delayed_morning_job_still_reports_previous_close(self):
        now = datetime(2026, 11, 25, 11, 0, tzinfo=CST)
        target, session = resolve_session(now, mode="morning")
        self.assertEqual(target, date(2026, 11, 24))
        self.assertEqual(session, "pre_market_briefing")

    def test_holiday_uses_latest(self):
        now = datetime(2026, 11, 21, 8, 30, tzinfo=CST)  # Saturday
        target, session = resolve_session(now, mode="morning")
        self.assertEqual(target, date(2026, 11, 20))
        self.assertEqual(session, "holiday_review")


class TestFetcherPure(unittest.TestCase):
    def test_parse_tencent_quote(self):
        text = 'v_s_sh000001="1~上证指数~000001~3512.66~-35.71~-1.01~249603981~35207018~~613792.85~ZS~";'
        parsed = parse_tencent_quotes(text)
        self.assertIn("000001", parsed)
        self.assertAlmostEqual(parsed["000001"]["close"], 3512.66)
        self.assertAlmostEqual(parsed["000001"]["pct"], -1.01)
        self.assertAlmostEqual(parsed["000001"]["amount"], 35207018 * 1e4)

    def test_limit_rules(self):
        self.assertEqual(limit_rule("600000", "浦发银行"), 10.0)
        self.assertEqual(limit_rule("300001", "特锐德"), 20.0)
        self.assertEqual(limit_rule("688001", "华兴源创"), 20.0)
        self.assertEqual(limit_rule("600001", "ST某某"), 5.0)

    def test_summarize_universe(self):
        rows = [
            {"code": "600001", "name": "A", "trade": 11.0, "settlement": 10.0, "high": 11.0, "changepercent": 10.0, "amount": 1e9, "turnoverratio": 12.0, "mktcap": 100.0, "nmc": 100.0},
            {"code": "600002", "name": "B", "trade": 10.5, "settlement": 10.0, "high": 10.6, "changepercent": 5.0, "amount": 2e9, "turnoverratio": 3.0, "mktcap": 200.0, "nmc": 200.0},
            {"code": "600003", "name": "C", "trade": 9.5, "settlement": 10.0, "high": 10.0, "changepercent": -5.0, "amount": 1e9, "turnoverratio": 2.0, "mktcap": 300.0, "nmc": 300.0},
            {"code": "600004", "name": "D", "trade": 9.0, "settlement": 10.0, "high": 9.1, "changepercent": -10.0, "amount": 1e9, "turnoverratio": 1.0, "mktcap": 400.0, "nmc": 400.0},
            {"code": "600005", "name": "E", "trade": 10.0, "settlement": 10.0, "high": 10.1, "changepercent": 0.0, "amount": 1e9, "turnoverratio": 0.5, "mktcap": 500.0, "nmc": 500.0},
        ]
        s = summarize_universe(rows)
        self.assertTrue(s["available"])
        self.assertEqual(s["advances"], 2)
        self.assertEqual(s["declines"], 2)
        self.assertEqual(s["unchanged"], 1)
        self.assertAlmostEqual(s["advance_ratio"], 50.0)
        self.assertAlmostEqual(s["total_amount"], 6e9)
        self.assertGreaterEqual(s["tail_heat_10"], 0.0)
        self.assertEqual(s["limit_up_est"], 1)
        self.assertEqual(s["limit_down_est"], 1)

    def test_align_by_date_descending_provider(self):
        rows = [{"date": "2026-11-24"}, {"date": "2026-11-23"}, {"date": "2026-07-14"}]
        self.assertEqual(align_by_date(rows, "2026-11-25")["date"], "2026-11-24")
        self.assertEqual(align_by_date(rows, "2026-11-22")["date"], "2026-07-14")

    def test_sector_matching_does_not_reuse_board(self):
        fetcher = MarketFetcher()
        boards = [
            {"name": "半导体设备", "pct": 1.0, "turnover": 2.0, "amount": 1e10, "main_net": 1e8, "retail_net": -1e7, "net_inflow": 1e8, "flow_quality": "order_flow", "leader_name": "", "leader_code": "", "source": "test"},
            {"name": "半导体", "pct": 0.5, "turnover": 1.5, "amount": 2e10, "main_net": 2e8, "retail_net": 1e7, "net_inflow": 2e8, "flow_quality": "order_flow", "leader_name": "", "leader_code": "", "source": "test"},
        ]
        defs = [
            {"id": "eq", "name": "设备", "category": "科技", "board_keywords": ["半导体设备"], "typical_companies": ["A"], "description": ""},
            {"id": "design", "name": "设计", "category": "科技", "board_keywords": ["半导体"], "typical_companies": ["B"], "description": ""},
        ]
        out = fetcher.match_sectors(boards, defs)
        self.assertTrue(out[0]["available"])
        self.assertTrue(out[1]["available"])
        self.assertNotEqual(out[0]["provider_board"], out[1]["provider_board"])


class TestQuantEngine(unittest.TestCase):
    def setUp(self):
        self.engine = QuantSentimentEngine()

    def test_math_helpers(self):
        self.assertEqual(clamp(150), 100.0)
        self.assertEqual(clamp(-10), 0.0)
        self.assertAlmostEqual(tanh_score(0, 0, 1), 50.0)
        self.assertGreater(tanh_score(1, 0, 1), 80.0)
        self.assertAlmostEqual(percentile_rank(30, [10, 20, 30, 40, 50]), 50.0)
        self.assertAlmostEqual(z_score_to_scale(0), 50.0)
        self.assertGreater(safe_std([1, 2, 3]), 0.0)

    def test_rofi_monotonic_and_bounded(self):
        base = 1.0e12
        pos = self.engine.factor_rofi({"small_net": 1e10, "main_net": -2e10}, base)
        neg = self.engine.factor_rofi({"small_net": -2e10, "main_net": 1e10}, base)
        self.assertTrue(pos["available"])
        self.assertGreater(pos["score"], neg["score"])
        self.assertGreaterEqual(neg["score"], 0.0)
        self.assertLessEqual(pos["score"], 100.0)

    def test_missing_flow_reweights_retail(self):
        market = make_market()
        margins = make_margins(market)
        overview = make_overview(market)
        limit = {"available": True, "limit_up_count": 50, "broken_board_count": 5, "broken_rate": 9.09, "max_consecutive_board": 4}
        full = self.engine.evaluate_market_sentiment(
            overview, {"small_net": 1e10, "main_net": -1e10, "divergence": 2e10}, margins[-1], limit,
            market, margins, {r["date"]: 0.5 for r in market}, market[-1]["date"],
        )
        missing = self.engine.evaluate_market_sentiment(
            overview, None, margins[-1], limit, market, margins, {r["date"]: 0.5 for r in market}, market[-1]["date"],
        )
        self.assertEqual(full["coverage"]["retail"], 1.0)
        self.assertAlmostEqual(missing["coverage"]["retail"], 0.6)
        self.assertLess(missing["confidence"], full["confidence"])
        self.assertFalse(missing["factors"]["retail"]["rofi"]["available"])

    def test_margin_ratio_uses_its_own_trade_date_amount(self):
        market = make_market()
        # The margin record is the previous day, with a different turnover.
        last = market[-1]
        prev = market[-2]
        margins = make_margins(market, ratio=0.10)
        margin = margins[-2]
        factor = self.engine.factor_margin(
            margin,
            total_amount=last["amount"],
            history=margins,
            amount_by_date={r["date"]: r["amount"] for r in market},
            target_date=last["date"],
        )
        self.assertTrue(factor["available"])
        self.assertAlmostEqual(factor["raw"]["margin_buy_ratio_pct"], 10.0, places=2)

    def test_extreme_panic_is_low(self):
        market = make_market(days=70, start=4200, daily=-0.004)
        margins = make_margins(market, ratio=0.05)
        last = market[-1]
        overview = make_overview(market, advances=300, declines=4800)
        flow = {"small_net": -3e10, "main_net": 1e10, "divergence": -4e10}
        limit = {"available": True, "limit_up_count": 8, "broken_board_count": 25, "broken_rate": 75.0, "max_consecutive_board": 1}
        res = self.engine.evaluate_market_sentiment(
            overview, flow, margins[-1], limit, market, margins,
            {r["date"]: -1.5 for r in market}, last["date"],
        )
        self.assertLess(res["composite"], 45.0)
        for key in ("composite", "baoma", "novice", "retail"):
            self.assertGreaterEqual(res[key], 0.0)
            self.assertLessEqual(res[key], 100.0)

    def test_euphoria_is_high(self):
        market = make_market(days=70, start=3000, daily=0.006)
        margins = make_margins(market, ratio=0.12)
        last = market[-1]
        overview = make_overview(market, advances=4200, declines=900)
        flow = {"small_net": 4e10, "main_net": -3e10, "divergence": 7e10}
        limit = {"available": True, "limit_up_count": 110, "broken_board_count": 4, "broken_rate": 3.5, "max_consecutive_board": 8}
        res = self.engine.evaluate_market_sentiment(
            overview, flow, margins[-1], limit, market, margins,
            {r["date"]: 2.0 for r in market}, last["date"],
        )
        self.assertGreater(res["composite"], 70.0)

    def test_sector_aggregate_and_order_flow_tags(self):
        sectors = [
            {"id": "a", "name": "A", "category": "科技", "description": "", "typical_companies": [], "available": True, "pct": 3.0, "turnover": 5.0, "amount": 1e10, "main_net": None, "retail_net": None, "net_inflow": 3e8, "flow_quality": "aggregate"},
            {"id": "b", "name": "B", "category": "科技", "description": "", "typical_companies": [], "available": True, "pct": -2.0, "turnover": 2.0, "amount": 1e10, "main_net": -3e8, "retail_net": 2e8, "net_inflow": -3e8, "flow_quality": "order_flow"},
        ]
        out = self.engine.evaluate_sector_quant(sectors)
        by_id = {s["id"]: s for s in out}
        self.assertEqual(by_id["a"]["tag_class"], "tag-bull")
        self.assertEqual(by_id["b"]["tag_class"], "tag-trap")
        self.assertGreaterEqual(by_id["a"]["sector_heat"], 0.0)
        self.assertLessEqual(by_id["a"]["sector_heat"], 100.0)


class TestPipeline(unittest.TestCase):
    def setUp(self):
        self.market = make_market(days=70)
        self.margins = make_margins(self.market)
        self.engine = QuantSentimentEngine()
        overview = make_overview(self.market)
        limit = {"available": True, "limit_up_count": 50, "broken_board_count": 8, "broken_rate": 13.79, "max_consecutive_board": 5}
        self.quant = self.engine.evaluate_market_sentiment(
            overview, {"small_net": 1e10, "main_net": -1e10, "divergence": 2e10}, self.margins[-1], limit,
            self.market, self.margins, {r["date"]: 0.5 for r in self.market}, self.market[-1]["date"],
        )

    def test_history_series_is_bounded_and_marked(self):
        history = run_pipeline.build_history_series(
            self.market, self.margins, [], {r["date"]: 0.5 for r in self.market},
            self.engine, self.quant, self.market[-1]["date"], "finalized", days=60,
        )
        self.assertEqual(len(history), 60)
        self.assertFalse(history[-1]["estimated"])
        self.assertEqual(history[-1]["source"], "actual")
        self.assertTrue(all(p["estimated"] for p in history[:-1]))
        for p in history:
            for key in ("composite", "baoma", "novice", "retail"):
                self.assertGreaterEqual(p[key], 0.0)
                self.assertLessEqual(p[key], 100.0)
        # amount is real yuan -> 100m yuan
        self.assertAlmostEqual(history[-1]["amount_billion"], self.market[-1]["amount"] / 1e8, places=1)

    def test_history_update_is_idempotent(self):
        history = run_pipeline.build_history_series(
            self.market, self.margins, [], {r["date"]: 0.5 for r in self.market},
            self.engine, self.quant, self.market[-1]["date"], "finalized", days=60,
        )
        before = len(history)
        updated = run_pipeline.update_history_point(history, self.market[-1]["date"], self.quant)
        self.assertEqual(len(updated), before)
        self.assertEqual(updated[-1]["date"], self.market[-1]["date"])

    def test_validation_rejects_bad_scores_and_duplicates(self):
        payload = {
            "market_date": self.market[-1]["date"],
            "data_status": "finalized",
            "scores": {"composite": 101, "baoma": 50, "novice": 50, "retail": 50},
            "confidence": 80,
            "coverage": {},
            "estimated": {},
            "signals": {},
            "raw": {},
            "weights_used": {},
            "factors": {},
            "status": {},
            "microstructure": {},
            "indices_overview": {},
            "breadth_source": "test",
            "sectors": [],
            "data_quality": {},
            "raw_inputs": {},
            "history_count": 0,
            "version": "2.1_quant",
            "schema_version": "2.1",
            "session_mode": "close",
            "session_title": "",
            "revision": "",
            "updated_at": "",
            "updated_at_cst": "",
        }
        with self.assertRaises(ValueError):
            run_pipeline.validate_payload(payload, [])
        payload["scores"]["composite"] = 50
        with self.assertRaises(ValueError):
            run_pipeline.validate_payload(payload, [{"date": self.market[-1]["date"]}, {"date": self.market[-1]["date"]}])

    def test_build_overview_uses_full_market_turnover(self):
        quotes = {"indices": {"000001": {"code": "000001", "close": 1, "pct": 0}}}
        universe = {"available": True, "breadth_valid": True, "advances": 3000, "declines": 2000, "unchanged": 100, "total_amount": 9e11, "active_count": 5100, "coverage": 0.99, "microcap_spread": 0.5, "tail_heat_10": 6.0, "p90_turnover": 3.0}
        market_hist = {
            "market": self.market[-2:],
            "sse": [{"date": self.market[-1]["date"], "close": 3500, "change_pct": 0.5, "amount": self.market[-1]["amount"] / 2, "turnover_rate": 1.2}],
            "szse": [{"date": self.market[-1]["date"], "close": 2000, "change_pct": 0.3, "amount": self.market[-1]["amount"] / 2, "turnover_rate": 1.5}],
            "csi2000": [{"date": self.market[-1]["date"], "close": 7000, "change_pct": 1.0, "amount": 0, "turnover_rate": 0}],
            "csi300": [{"date": self.market[-1]["date"], "close": 4000, "change_pct": -0.2, "amount": 0, "turnover_rate": 0}],
        }
        overview = run_pipeline.build_overview(
            coerce_date(self.market[-1]["date"]), "close", quotes, universe, market_hist, None
        )
        self.assertAlmostEqual(overview["total_turnover"], self.market[-1]["amount"])
        self.assertEqual(overview["total_advances"], 3000)
        self.assertEqual(overview["breadth_source"], "live_cross_section")

    def test_morning_recompute_uses_cache_and_fresh_margin(self):
        target = self.market[-1]["date"]
        cache = {
            "market_date": target,
            "data_status": "finalized",
            "scores": self.quant,
            "confidence": self.quant["confidence"],
            "sectors": [{"id": "x", "name": "X", "available": True, "sector_heat": 50.0}],
            "data_quality": {"sources": [{"name": "universe", "status": "ok"}], "missing_factors": []},
            "raw_inputs": {
                "overview": make_overview(self.market),
                "flow": {"small_net": 1e10, "main_net": -1e10, "divergence": 2e10},
                "limit_pool": {"available": True, "limit_up_count": 50, "broken_board_count": 8, "broken_rate": 13.79, "max_consecutive_board": 5},
            },
        }

        class StubFetcher:
            quality = {"margin": {"status": "ok", "source": "stub", "date": target}, "market_history": {"status": "ok", "source": "stub", "date": target}}

            def fetch_margin_data(self, page_size=90):
                return self.margins

            def fetch_market_history(self, limit=120, finalized_through=None):
                return {
                    "available": True,
                    "market": self.market,
                    "sse": [],
                    "szse": [],
                    "csi2000": [],
                    "csi300": [],
                    "spread": {r["date"]: 0.5 for r in self.market},
                }

        with tempfile.TemporaryDirectory() as td:
            hist_path = Path(td) / "history.json"
            payload_path = Path(td) / "latest.json"
            existing = run_pipeline.build_history_series(
                self.market, self.margins, [], {r["date"]: 0.5 for r in self.market},
                self.engine, self.quant, target, "finalized", days=60,
            )
            hist_path.write_text(json.dumps(existing), encoding="utf-8")
            with patch.object(run_pipeline, "HISTORY_FILE", hist_path), patch.object(
                run_pipeline, "LATEST_FILE", payload_path
            ):
                stub = StubFetcher()
                stub.margins = self.margins
                stub.market = self.market
                payload, history = run_pipeline.run_morning(
                    coerce_date(target), datetime(2026, 11, 25, 8, 30, tzinfo=CST), cache, stub, self.engine
                )
        self.assertEqual(payload["market_date"], target)
        self.assertEqual(payload["data_status"], "finalized")
        self.assertEqual(payload["session_mode"], "pre_market_briefing")
        self.assertEqual(history[-1]["date"], target)
        self.assertFalse(history[-1]["estimated"])

    def test_validate_accepts_good_payload(self):
        history = run_pipeline.build_history_series(
            self.market, self.margins, [], {r["date"]: 0.5 for r in self.market},
            self.engine, self.quant, self.market[-1]["date"], "finalized", days=60,
        )
        payload = {
            "market_date": self.market[-1]["date"],
            "data_status": "finalized",
            "scores": {k: self.quant[k] for k in ("composite", "baoma", "novice", "retail")},
        }
        run_pipeline.validate_payload(payload, history)


class TestSectorCatalog(unittest.TestCase):
    def test_catalog_shape(self):
        self.assertEqual(len(SECTOR_DEFINITIONS), 30)
        self.assertEqual(len(CATEGORIES), 8)
        for s in SECTOR_DEFINITIONS:
            self.assertIn(s["category"], CATEGORIES)
            self.assertTrue(s["typical_companies"])


if __name__ == "__main__":
    unittest.main()

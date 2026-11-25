"""Unit tests for A-Share Quant Sentiment Index System."""

import unittest
from datetime import date
from core.calendar import (
    is_trading_day,
    coerce_date,
    get_latest_trading_day,
    get_recent_trading_days,
)
from core.sector_catalog import SECTOR_DEFINITIONS, CATEGORIES
from core.quant_engine import (
    QuantSentimentEngine,
    clamp,
    percentile_rank,
    z_score_to_scale,
)

class TestQuantCalendar(unittest.TestCase):
    def test_weekend_not_trading_day(self):
        # 2026-11-21 is Saturday, 2026-11-22 is Sunday
        self.assertFalse(is_trading_day("2026-11-21"))
        self.assertFalse(is_trading_day("2026-11-22"))

    def test_weekday_trading_day(self):
        # 2026-11-24 is Tuesday
        self.assertTrue(is_trading_day("2026-11-24"))

    def test_official_holiday(self):
        # 2026-10-01 to 2026-10-07 is National Day
        self.assertFalse(is_trading_day("2026-10-01"))
        self.assertFalse(is_trading_day("2026-10-05"))

    def test_get_recent_trading_days(self):
        days = get_recent_trading_days(5, end_date="2026-11-24")
        self.assertEqual(len(days), 5)
        for d in days:
            self.assertTrue(is_trading_day(d))

class TestSectorCatalog(unittest.TestCase):
    def test_sector_count(self):
        self.assertEqual(len(SECTOR_DEFINITIONS), 30)
        self.assertEqual(len(CATEGORIES), 8)

    def test_sector_structure(self):
        for s in SECTOR_DEFINITIONS:
            self.assertIn("id", s)
            self.assertIn("name", s)
            self.assertIn("category", s)
            self.assertIn(s["category"], CATEGORIES)
            self.assertGreater(len(s["typical_companies"]), 0)

class TestQuantEngine(unittest.TestCase):
    def setUp(self):
        self.engine = QuantSentimentEngine()

    def test_math_utils(self):
        self.assertEqual(clamp(150, 0, 100), 100.0)
        self.assertEqual(clamp(-50, 0, 100), 0.0)
        self.assertEqual(clamp(50, 0, 100), 50.0)

        # Percentile rank
        history = [10.0, 20.0, 30.0, 40.0, 50.0]
        self.assertEqual(percentile_rank(30.0, history), 50.0)
        self.assertEqual(percentile_rank(60.0, history), 100.0)
        self.assertEqual(percentile_rank(5.0, history), 0.0)

    def test_evaluate_sentiment_bounded(self):
        overview = {
            "total_turnover": 6e11,
            "total_advances": 2800,
            "total_declines": 2400,
            "indices": {
                "399303": {"pct": 0.5},
                "000300": {"pct": -0.2},
            }
        }
        flow = {
            "main_net": -5e9,
            "small_net": 4e9,
            "divergence": 9e9,
        }
        margin = {
            "rzye": 2.14e12,
            "rzmre": 1.4e11,
        }
        limit_pool = {
            "limit_up_count": 55,
            "broken_board_count": 10,
            "broken_rate": 15.38,
            "max_consecutive_board": 4,
        }
        klines = [
            {"date": f"2026-11-{i:02d}", "close": 3500 + i * 2, "turnover_rate": 0.5}
            for i in range(1, 31)
        ]
        margins = [margin] * 10

        res = self.engine.evaluate_market_sentiment(
            overview=overview,
            flow=flow,
            margin=margin,
            limit_pool=limit_pool,
            historical_klines=klines,
            historical_margins=margins,
        )

        for key in ["composite", "baoma", "novice", "retail"]:
            score = res[key]
            self.assertGreaterEqual(score, 0.0)
            self.assertLessEqual(score, 100.0)

        self.assertIn("status", res)
        self.assertIn("tag", res["status"])
        self.assertIn("action", res["status"])

    def test_extreme_panic_state(self):
        # 4500 declines, negative flow, extreme broken board
        overview = {"total_turnover": 3e11, "total_advances": 300, "total_declines": 4800, "indices": {}}
        flow = {"main_net": -2e10, "small_net": -1e10, "divergence": 1e10}
        margin = {"rzye": 1.5e12, "rzmre": 3e10}
        limit_pool = {"limit_up_count": 8, "broken_board_count": 25, "broken_rate": 75.0, "max_consecutive_board": 1}
        klines = [{"date": "2026-11-01", "close": 2800, "turnover_rate": 0.2}] * 60
        margins = [margin] * 10

        res = self.engine.evaluate_market_sentiment(
            overview=overview,
            flow=flow,
            margin=margin,
            limit_pool=limit_pool,
            historical_klines=klines,
            historical_margins=margins,
        )
        self.assertLess(res["composite"], 40.0)

if __name__ == "__main__":
    unittest.main()

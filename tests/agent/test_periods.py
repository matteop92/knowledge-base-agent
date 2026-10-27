import unittest
from datetime import date

from app.agent.periods import read_limit, read_period

TODAY = date(2026, 10, 27)  # a Tuesday


class PeriodTests(unittest.TestCase):
    def test_month_alone_is_this_years_month(self):
        period = read_period("what did I read in October", TODAY)
        self.assertEqual((period.start, period.end), ("2026-10-01", "2026-10-31"))
        self.assertEqual(period.label, "from October 2026")

    def test_year_and_month_with_year(self):
        self.assertEqual((read_period("sources from 2025", TODAY).start, read_period("2025", TODAY).end),
                         ("2025-01-01", "2025-12-31"))
        self.assertEqual(read_period("notes from March 2024", TODAY).end, "2024-03-31")
        self.assertEqual(read_period("February 2028", TODAY).end, "2028-02-29")

    def test_last_week_is_the_previous_seven_days(self):
        period = read_period("what did I save last week", TODAY)
        self.assertEqual((period.start, period.end), ("2026-10-19", "2026-10-25"))
        self.assertEqual(period.field, "added")

    def test_this_and_last_month_and_year(self):
        self.assertEqual(read_period("this week", TODAY).start, "2026-10-26")
        self.assertEqual(read_period("last month", date(2026, 1, 5)).start, "2025-12-01")
        self.assertEqual(read_period("this month", TODAY).end, "2026-10-27")
        self.assertEqual(read_period("last year", TODAY).start, "2025-01-01")

    def test_no_period_stated(self):
        for question in ("articles I saved", "what may happen next", "my latest thinking on AI agents"):
            self.assertIsNone(read_period(question, TODAY), question)
        self.assertEqual(read_period("in May", TODAY).start, "2026-05-01")

    def test_own_date_or_added_date(self):
        self.assertEqual(read_period("articles from 2025", TODAY).field, "date")
        self.assertEqual(read_period("articles I uploaded in 2025", TODAY).field, "added")

    def test_period_contains_by_day(self):
        period = read_period("2025", TODAY)
        self.assertTrue(period.contains("2025-06-18"))
        self.assertFalse(period.contains("2026-01-01"))
        self.assertFalse(period.contains(None))

    def test_limits(self):
        self.assertEqual(read_limit("the three most recent notes"), 3)
        self.assertEqual(read_limit("show my 5 latest articles"), 5)
        self.assertEqual(read_limit("two oldest sources"), 2)
        self.assertIsNone(read_limit("the latest note"))
        self.assertIsNone(read_limit("0 latest"))


if __name__ == "__main__":
    unittest.main()

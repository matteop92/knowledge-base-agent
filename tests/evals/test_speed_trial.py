import unittest

from evals.speed_trial import model_seconds, percentile, summarize


def receipt(kind, elapsed, model, calls=2, cost=0.01):
    return {"type": kind, "elapsed_seconds": elapsed, "wall_seconds": elapsed + 1, "model_calls": calls,
            "cost_usd": cost, "termination": "completed",
            "model_usage": [{"model": "embed", "output_tokens": None, "seconds": 0.5},
                            {"model": "gen", "output_tokens": 10, "seconds": model}]}


class SpeedTrialTests(unittest.TestCase):
    def test_nearest_rank_percentile_is_an_observed_value(self):
        values = [float(value) for value in range(1, 21)]
        self.assertEqual(percentile(values, 0.5), 10.0)
        self.assertEqual(percentile(values, 0.95), 19.0)
        self.assertEqual(percentile([7.0], 0.95), 7.0)
        self.assertIsNone(percentile([], 0.5))

    def test_summary_splits_types_and_counts_only_generation_time(self):
        items = [receipt("single", 10, 8), receipt("single", 30, 20), receipt("property", 100, 90)]
        self.assertEqual(model_seconds(items[0]), 8)
        summary = summarize(items)
        self.assertEqual(summary["all"]["answers"], 3)
        self.assertEqual(summary["all"]["p50_elapsed"], 30)
        self.assertEqual(summary["single"]["p95_model"], 20)
        self.assertEqual(summary["property"]["p50_wall"], 101)


if __name__ == "__main__":
    unittest.main()

import unittest

from app.models import routes


class RoutesTest(unittest.TestCase):
    def test_private_routes_pin_one_zero_retention_provider(self):
        route = routes.provider_routes([routes.EMBEDDING_MODEL])[routes.EMBEDDING_MODEL]
        self.assertEqual(route["only"], ["perplexity/int8"])
        self.assertFalse(route["allow_fallbacks"])
        self.assertEqual((route["data_collection"], route["zdr"]), ("deny", True))
        self.assertEqual(routes.PRIVACY, {"data_collection": "deny", "zdr": True})

    def test_price_uses_the_approved_ceiling(self):
        self.assertAlmostEqual(routes.price_usd("openai/gpt-5-mini", 1_000_000, 1_000_000), 2.25)
        self.assertAlmostEqual(routes.price_usd(routes.EMBEDDING_MODEL, 2_000_000), 0.008)

    def test_evals_use_the_shared_routes(self):
        from evals import compare_chunking
        self.assertIs(compare_chunking.verify_routes, routes.verify_routes)


if __name__ == "__main__":
    unittest.main()

import unittest

from evals.hosted_smoke import verify_dsn

REF = "abcdefghijklmnopqrst"
GOOD = f"postgresql://knowledge_runtime_hosted.{REF}:pw@aws-0-eu-central-1.pooler.supabase.com:5432/postgres?sslmode=require"


class HostedSmokeTest(unittest.TestCase):
    def test_accepts_the_matching_project_login(self) -> None:
        verify_dsn(GOOD, REF)

    def test_rejects_a_different_project(self) -> None:
        with self.assertRaises(ValueError):
            verify_dsn(GOOD, "zzzzzzzzzzzzzzzzzzzz")

    def test_rejects_a_missing_project_ref(self) -> None:
        with self.assertRaises(ValueError):
            verify_dsn(GOOD, "")


if __name__ == "__main__":
    unittest.main()

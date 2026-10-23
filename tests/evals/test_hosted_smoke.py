import os
import unittest
from unittest.mock import patch

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
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(ValueError):
            verify_dsn(GOOD, "")

    def test_rejects_the_wrong_host_port_or_tls_mode(self) -> None:
        for bad in (GOOD.replace("pooler.supabase.com", "example.com"), GOOD.replace(":5432", ":6543"),
                    GOOD.replace("sslmode=require", "sslmode=disable")):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                verify_dsn(bad, REF)


if __name__ == "__main__":
    unittest.main()

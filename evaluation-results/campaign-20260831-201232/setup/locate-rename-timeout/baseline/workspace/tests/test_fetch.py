import unittest

from fetch.client import fetch


class FetchTests(unittest.TestCase):
    def test_returns_the_url_unchanged(self) -> None:
        self.assertEqual(fetch("https://example.test/a")["url"], "https://example.test/a")

    def test_reports_a_positive_timeout(self) -> None:
        self.assertGreater(fetch("https://example.test/a")["timeout"], 0)

    def test_the_response_shape_is_stable(self) -> None:
        self.assertEqual(set(fetch("https://example.test/a")), {"url", "timeout"})


if __name__ == "__main__":
    unittest.main()

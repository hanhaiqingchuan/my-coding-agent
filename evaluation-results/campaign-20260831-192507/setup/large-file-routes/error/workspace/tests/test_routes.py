import unittest

from router.table import ROUTES, path_for


class RouteTableTests(unittest.TestCase):
    def test_the_first_route_returns_its_own_path(self) -> None:
        self.assertEqual(path_for("route_00"), "/v1/routes/00")

    def test_the_last_route_returns_its_own_path(self) -> None:
        self.assertEqual(path_for("route_99"), "/v1/routes/99")

    def test_every_route_name_is_registered(self) -> None:
        self.assertEqual(len(ROUTES), 100)

    def test_unknown_route_names_are_rejected(self) -> None:
        with self.assertRaises(KeyError):
            path_for("route_999")


if __name__ == "__main__":
    unittest.main()

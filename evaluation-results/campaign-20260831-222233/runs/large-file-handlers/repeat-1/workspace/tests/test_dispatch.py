import unittest

from dispatch.handlers import HANDLERS, dispatch


class DispatchTests(unittest.TestCase):
    def test_first_handler_doubles_its_payload(self) -> None:
        self.assertEqual(
            dispatch("event_00", 3), {"event": "event_00", "status": "ok", "value": 6}
        )

    def test_last_handler_doubles_its_payload(self) -> None:
        self.assertEqual(
            dispatch("event_79", 5), {"event": "event_79", "status": "ok", "value": 10}
        )

    def test_unknown_event_is_rejected(self) -> None:
        with self.assertRaises(KeyError):
            dispatch("event_999", 1)

    def test_non_integer_payload_is_rejected(self) -> None:
        with self.assertRaises(TypeError):
            dispatch("event_01", "3")

    def test_every_event_name_has_a_handler(self) -> None:
        self.assertEqual(len(HANDLERS), 80)


if __name__ == "__main__":
    unittest.main()

import unittest

from pipeline.batcher import batches
from pipeline.settings import MAX_BATCH_SIZE


class BatchesTests(unittest.TestCase):
    def test_order_is_preserved(self) -> None:
        items = list(range(137))
        flattened = [item for batch in batches(items) for item in batch]
        self.assertEqual(flattened, items)

    def test_no_batch_exceeds_the_configured_size(self) -> None:
        for batch in batches(list(range(137))):
            self.assertLessEqual(len(batch), MAX_BATCH_SIZE)

    def test_no_batch_is_empty(self) -> None:
        for batch in batches(list(range(137))):
            self.assertTrue(batch)

    def test_empty_input_produces_no_batch(self) -> None:
        self.assertEqual(batches([]), [])


if __name__ == "__main__":
    unittest.main()

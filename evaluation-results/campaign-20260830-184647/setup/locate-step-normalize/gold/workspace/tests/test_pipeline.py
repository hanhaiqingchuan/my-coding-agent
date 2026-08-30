import unittest

from pipeline.runner import run_step
from pipeline.steps import normalize_step


class NormalizeStepTests(unittest.TestCase):
    def test_trims_and_lowercases(self) -> None:
        self.assertEqual(normalize_step("  Deploy  "), "deploy")

    def test_plain_names_pass_through_lowercased(self) -> None:
        self.assertEqual(normalize_step("Build"), "build")


class RunStepTests(unittest.TestCase):
    def test_runs_a_cleaned_step_name(self) -> None:
        self.assertEqual(run_step(" Build "), "ran build")

    def test_runs_a_plain_step_name(self) -> None:
        self.assertEqual(run_step("deploy"), "ran deploy")


if __name__ == "__main__":
    unittest.main()

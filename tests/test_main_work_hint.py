import runpy
import unittest
from pathlib import Path

ROUTER = Path(__file__).resolve().parents[1] / "plugins" / "skill-router" / "skills" / "skill-router-settings" / "router.py"


class MainWorkHint(unittest.TestCase):
    """Candidates come from the user's words; the agent searches again with the main work."""

    def setUp(self):
        self.hint = runpy.run_path(str(ROUTER))["FIND_HINT"]

    def test_every_hint_names_the_main_work_and_the_search(self):
        for after in (False, True, "read"):
            with self.subTest(after=after):
                text = self.hint(after=after)
                self.assertIn("最終的に求める作業", text)
                self.assertIn("index find", text)

    def test_shortlist_hint_applies_when_candidates_are_shown(self):
        # With candidates above, the hint must not say there are none (it would never apply).
        self.assertNotIn("候補は上にない", self.hint(after=True))
        self.assertNotIn("候補は上にない", self.hint(after="read"))
        self.assertIn("候補は上にない", self.hint())


if __name__ == "__main__":
    unittest.main()

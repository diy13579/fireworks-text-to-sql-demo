import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DashboardAssetTests(unittest.TestCase):
    def test_public_questions_exclude_gold_sql_and_answers(self) -> None:
        questions = json.loads(
            (ROOT / "docs" / "data" / "demo_questions.json").read_text(
                encoding="utf-8"
            )
        )

        self.assertEqual(len(questions), 10)
        self.assertTrue(all("gold_sql" not in question for question in questions))
        self.assertTrue(all("gold_answer" not in question for question in questions))
        self.assertTrue(all("expected_result" in question for question in questions))

    def test_dashboard_is_fireworks_only(self) -> None:
        html = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")

        self.assertIn("Fireworks", html)
        self.assertNotIn("Baseten", html)
        self.assertNotIn("baseten", html)

    def test_dashboard_does_not_persist_api_keys(self) -> None:
        javascript = (ROOT / "docs" / "app.js").read_text(encoding="utf-8")

        self.assertNotIn("localStorage.", javascript)
        self.assertNotIn("sessionStorage.", javascript)
        self.assertNotIn("document.cookie", javascript)
        self.assertIn("pagehide", javascript)

    def test_pages_workflow_uses_no_api_key_secret(self) -> None:
        workflow = (
            ROOT / ".github" / "workflows" / "pages.yml"
        ).read_text(encoding="utf-8")

        self.assertNotIn("FIREWORKS_API_KEY", workflow)
        self.assertIn("python scripts/build_demo_assets.py", workflow)
        self.assertIn("python -m unittest discover -s tests -v", workflow)
        self.assertIn("actions/deploy-pages@v4", workflow)


if __name__ == "__main__":
    unittest.main()

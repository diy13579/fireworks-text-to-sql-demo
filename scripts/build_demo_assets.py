"""Generate public browser-demo assets without exposing gold SQL."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.agent import build_schema_context
from src.policy import DataSourceProfile
from src.utils import get_schema, load_db


DOCS_DATA = ROOT / "docs" / "data"


def main() -> None:
    DOCS_DATA.mkdir(parents=True, exist_ok=True)
    source = json.loads(
        (ROOT / "data" / "dev_questions_with_answers.json").read_text(
            encoding="utf-8"
        )
    )
    questions = [
        {
            "id": case["id"],
            "question": case["question"],
            "tier": case["tier"],
            "expected_result": case["expected_result"],
        }
        for case in source
    ]
    (DOCS_DATA / "demo_questions.json").write_text(
        json.dumps(questions, indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )

    profile = DataSourceProfile.load(str(ROOT / "data" / "database_profile.json"))
    conn = load_db(str(ROOT / "data" / "financial.sqlite"))
    try:
        schema = get_schema(conn)
        policy = profile.authorization_policy(schema)
        context = build_schema_context(schema, profile, policy)
    finally:
        conn.close()
    (DOCS_DATA / "schema_context.txt").write_text(context + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

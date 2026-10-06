"""data/questions.json → quiz.db questions 테이블 (id 기준 upsert).

다시 실행해도 attempts, wrong_notes, ai_explanations 는 유지된다.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db import DB_PATH, connect, init_db, upsert_questions  # noqa: E402

JSON_PATH = ROOT / "data" / "questions.json"


def main() -> None:
    questions = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    with connect() as conn:
        init_db(conn)
        upsert_questions(conn, questions)
        total = conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
        attempts = conn.execute("SELECT COUNT(*) FROM attempts").fetchone()[0]
    print(f"로드: {len(questions)}문제 → {DB_PATH}")
    print(f"DB 문제 수: {total}, 풀이 기록: {attempts}건 (유지됨)")


if __name__ == "__main__":
    main()

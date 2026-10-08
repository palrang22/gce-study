"""data/questions_<exam>.json → quiz.db questions 테이블 (id 기준 upsert).

다시 실행해도 attempts, wrong_notes, ai_explanations 는 유지된다.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db import DB_PATH, connect, init_db, upsert_questions  # noqa: E402

JSON_PATHS = [ROOT / "data" / "questions_ace.json", ROOT / "data" / "questions_pca.json"]


def main() -> None:
    questions = []
    for path in JSON_PATHS:
        if not path.exists():
            print(f"건너뜀 (없음): {path}")
            continue
        loaded = json.loads(path.read_text(encoding="utf-8"))
        questions.extend(loaded)
        print(f"읽음: {len(loaded)}문제 ← {path}")

    with connect() as conn:
        init_db(conn)
        upsert_questions(conn, questions)
        total = conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
        attempts = conn.execute("SELECT COUNT(*) FROM attempts").fetchone()[0]
        by_exam = conn.execute(
            "SELECT exam, COUNT(*) AS n FROM questions GROUP BY exam ORDER BY exam"
        ).fetchall()
    print(f"로드: {len(questions)}문제 → {DB_PATH}")
    exam_summary = ", ".join(f"{r['exam']} {r['n']}개" for r in by_exam)
    print(f"DB 문제 수: {total} ({exam_summary}), 풀이 기록: {attempts}건 (유지됨)")


if __name__ == "__main__":
    main()

"""SQLite 스키마와 DB 접근."""
import json
import os
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# 테스트에서는 QUIZ_DB_PATH 로 임시 DB를 쓴다
DB_PATH = Path(os.environ.get("QUIZ_DB_PATH", ROOT / "data" / "quiz.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS questions (
    id          INTEGER PRIMARY KEY,
    number      INTEGER NOT NULL,
    exam        TEXT NOT NULL DEFAULT 'ACE',  -- "ACE" | "PCA"
    section     TEXT,
    question    TEXT NOT NULL,
    options     TEXT NOT NULL,              -- JSON {"A": "...", ...}
    answer      TEXT NOT NULL,              -- JSON ["B"]
    multi       INTEGER NOT NULL DEFAULT 0,
    explanation TEXT NOT NULL DEFAULT '',
    images      TEXT NOT NULL DEFAULT '[]'  -- JSON ["Q42.png"]
);
CREATE INDEX IF NOT EXISTS idx_questions_exam ON questions(exam);

-- 테스트 세션: 범위를 정해 푸는 한 회차. 이어 풀 수 있도록 위치를 저장한다.
CREATE TABLE IF NOT EXISTS test_sessions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    exam          TEXT NOT NULL DEFAULT 'ACE',
    label         TEXT NOT NULL,
    params        TEXT NOT NULL DEFAULT '{}',  -- JSON, 만들 때의 범위 조건 (표시용)
    question_ids  TEXT NOT NULL,               -- JSON [id, ...] 푸는 순서대로
    current_index INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at    TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_sessions_exam ON test_sessions(exam);

-- 풀 때마다 한 줄씩 누적 (덮어쓰지 않음). 세션을 지워도 기록은 남는다 (session_id 만 NULL).
CREATE TABLE IF NOT EXISTS attempts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id INTEGER NOT NULL REFERENCES questions(id),
    session_id  INTEGER REFERENCES test_sessions(id),
    selected    TEXT NOT NULL,              -- JSON ["A", "C"]
    is_correct  INTEGER NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_attempts_question ON attempts(question_id);

-- active=0 이어도 메모는 남는다
CREATE TABLE IF NOT EXISTS wrong_notes (
    question_id INTEGER PRIMARY KEY REFERENCES questions(id),
    active      INTEGER NOT NULL DEFAULT 1,
    memo        TEXT NOT NULL DEFAULT '',
    added_at    TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS ai_explanations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id INTEGER NOT NULL REFERENCES questions(id),
    selected    TEXT NOT NULL,              -- JSON, 정렬된 선택
    content     TEXT NOT NULL,              -- 마크다운 (최초 해설)
    model       TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_ai_question ON ai_explanations(question_id, selected);

-- 최초 해설(ai_explanations) 아래에서 이어가는 후속 질문/답변. 한 줄이 user 또는 model 메시지 하나.
CREATE TABLE IF NOT EXISTS ai_messages (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    explanation_id INTEGER NOT NULL REFERENCES ai_explanations(id),
    role           TEXT NOT NULL CHECK (role IN ('user', 'model')),
    content        TEXT NOT NULL,
    created_at     TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_ai_messages_explanation ON ai_messages(explanation_id);
"""


def connect(path: Path | None = None) -> sqlite3.Connection:
    path = Path(path or DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    # FastAPI 는 의존성(연결 생성)과 엔드포인트를 서로 다른 스레드에서 돌릴 수 있다.
    # 연결은 요청 하나 안에서만 순서대로 쓰므로 스레드 검사를 끈다.
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    # 이전 버전 DB: attempts 에 session_id 가 없으면 추가
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(attempts)")}
    if "session_id" not in columns:
        conn.execute("ALTER TABLE attempts ADD COLUMN session_id INTEGER REFERENCES test_sessions(id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_attempts_session ON attempts(session_id)")

    # 이전 버전 DB (ACE 단일 시험 시절): questions/test_sessions 에 exam 이 없으면 추가
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(questions)")}
    if "exam" not in columns:
        conn.execute("ALTER TABLE questions ADD COLUMN exam TEXT NOT NULL DEFAULT 'ACE'")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_questions_exam ON questions(exam)")
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(test_sessions)")}
    if "exam" not in columns:
        conn.execute("ALTER TABLE test_sessions ADD COLUMN exam TEXT NOT NULL DEFAULT 'ACE'")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_exam ON test_sessions(exam)")


def upsert_questions(conn: sqlite3.Connection, questions: list[dict]) -> None:
    """id 기준 upsert. 다른 테이블(풀이 기록 등)은 건드리지 않는다."""
    conn.executemany(
        """
        INSERT INTO questions (id, number, exam, section, question, options, answer, multi, explanation, images)
        VALUES (:id, :number, :exam, :section, :question, :options, :answer, :multi, :explanation, :images)
        ON CONFLICT(id) DO UPDATE SET
            number = excluded.number,
            exam = excluded.exam,
            section = excluded.section,
            question = excluded.question,
            options = excluded.options,
            answer = excluded.answer,
            multi = excluded.multi,
            explanation = excluded.explanation,
            images = excluded.images
        """,
        [
            {
                "id": q["id"],
                "number": q["number"],
                "exam": q.get("exam", "ACE"),
                "section": q.get("section"),
                "question": q["question"],
                "options": json.dumps(q["options"], ensure_ascii=False),
                "answer": json.dumps(q["answer"]),
                "multi": int(q["multi"]),
                "explanation": q.get("explanation") or "",
                "images": json.dumps(q.get("images", []), ensure_ascii=False),
            }
            for q in questions
        ],
    )


def question_from_row(row: sqlite3.Row) -> dict:
    q = dict(row)
    for key in ("options", "answer", "images"):
        q[key] = json.loads(q[key])
    q["multi"] = bool(q["multi"])
    return q

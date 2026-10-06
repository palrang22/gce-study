import copy

from app import db
from tests.conftest import SAMPLE


def test_upsert_keeps_history_and_updates_questions(db_path):
    with db.connect() as conn:
        conn.execute("INSERT INTO attempts (question_id, selected, is_correct) VALUES (1, '[\"A\"]', 0)")
        conn.execute("INSERT INTO wrong_notes (question_id, memo) VALUES (1, '헷갈림')")
        conn.execute("INSERT INTO ai_explanations (question_id, selected, content, model) VALUES (1, '[\"A\"]', 'x', 'm')")

    fixed = copy.deepcopy(SAMPLE)
    fixed[0]["question"] = "Q1 fixed"
    fixed[0]["answer"] = ["C"]
    with db.connect() as conn:
        db.upsert_questions(conn, fixed)  # 같은 JSON을 고쳐서 다시 로드
        db.upsert_questions(conn, fixed)  # 두 번 돌려도 중복 없음

        assert conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0] == 3
        q = db.question_from_row(conn.execute("SELECT * FROM questions WHERE id = 1").fetchone())
        assert q["question"] == "Q1 fixed" and q["answer"] == ["C"]
        assert conn.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == 1
        assert conn.execute("SELECT memo FROM wrong_notes WHERE question_id = 1").fetchone()[0] == "헷갈림"
        assert conn.execute("SELECT COUNT(*) FROM ai_explanations").fetchone()[0] == 1


def test_json_fields_roundtrip(db_path):
    with db.connect() as conn:
        q = db.question_from_row(conn.execute("SELECT * FROM questions WHERE id = 2").fetchone())
    assert q["options"]["E"] == "e"
    assert q["answer"] == ["B", "E"]
    assert q["multi"] is True
    assert q["images"] == ["Q2.png"]


def test_migration_adds_session_id_to_old_db(tmp_path):
    path = tmp_path / "old.db"
    conn = db.connect(path)
    conn.executescript("""
        CREATE TABLE questions (id INTEGER PRIMARY KEY, number INTEGER NOT NULL, section TEXT,
            question TEXT NOT NULL, options TEXT NOT NULL, answer TEXT NOT NULL,
            multi INTEGER NOT NULL DEFAULT 0, explanation TEXT NOT NULL DEFAULT '', images TEXT NOT NULL DEFAULT '[]');
        CREATE TABLE attempts (id INTEGER PRIMARY KEY AUTOINCREMENT, question_id INTEGER NOT NULL,
            selected TEXT NOT NULL, is_correct INTEGER NOT NULL, created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')));
        INSERT INTO questions VALUES (1, 1, NULL, 'q', '{}', '[]', 0, '', '[]');
        INSERT INTO attempts (question_id, selected, is_correct) VALUES (1, '["A"]', 0);
    """)
    db.init_db(conn)
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(attempts)")}
    assert "session_id" in columns
    assert conn.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == 1

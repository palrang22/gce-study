import pytest
from fastapi.testclient import TestClient

from app import db

SAMPLE = [
    {"id": 1, "number": 1, "section": None, "question": "Q1 text",
     "options": {"A": "a", "B": "b", "C": "c", "D": "d"}, "answer": ["B"], "multi": False,
     "explanation": "https://example.com/1", "images": [], "parse_warnings": []},
    {"id": 2, "number": 2, "section": None, "question": "Q2 text (Choose two.)",
     "options": {"A": "a", "B": "b", "C": "c", "D": "d", "E": "e"}, "answer": ["B", "E"], "multi": True,
     "explanation": "", "images": ["Q2.png"], "parse_warnings": []},
    {"id": 3, "number": 3, "section": None, "question": "Q3 text",
     "options": {"A": "a", "B": "b", "C": "c", "D": "d"}, "answer": ["A"], "multi": False,
     "explanation": "", "images": [], "parse_warnings": []},
]


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "quiz.db"
    monkeypatch.setattr(db, "DB_PATH", path)
    with db.connect() as conn:
        db.init_db(conn)
        db.upsert_questions(conn, SAMPLE)
    return path


@pytest.fixture
def client(db_path, monkeypatch):
    from app import ai
    from app.main import app
    # 테스트에서는 Gemini 를 실제로 호출하지 않는다
    monkeypatch.setattr(ai, "generate_explanation", lambda q, selected: ("**fake** 해설", "fake-model"))
    with TestClient(app) as c:
        yield c

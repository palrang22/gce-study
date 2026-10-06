"""FastAPI 라우트. 실행: uvicorn app.main:app --reload"""
import json
import random
import sqlite3
from collections.abc import Iterator
from contextlib import asynccontextmanager, closing
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import ai
from app.db import ROOT, connect, init_db, question_from_row

STATIC_DIR = Path(__file__).resolve().parent / "static"
IMAGES_DIR = ROOT / "questions"
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}


@asynccontextmanager
async def lifespan(_: FastAPI):
    with closing(connect()) as conn, conn:
        init_db(conn)
    yield


app = FastAPI(title="ACE Quiz", lifespan=lifespan)


def get_db() -> Iterator[sqlite3.Connection]:
    with closing(connect()) as conn:
        with conn:  # 정상 종료 시 commit, 예외 시 rollback
            yield conn


def get_question_row(conn: sqlite3.Connection, question_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM questions WHERE id = ?", (question_id,)).fetchone()
    if row is None:
        raise HTTPException(404, f"문제 {question_id} 없음")
    return row


def normalize_selected(selected: list[str], options: dict) -> list[str]:
    result = sorted(set(s.strip().upper() for s in selected))
    bad = [s for s in result if s not in options]
    if bad:
        raise HTTPException(400, f"보기에 없는 선택: {bad}")
    return result


# ---------- 범위 선택 ----------

NULL_SECTION = "null"  # section 파라미터에서 섹션 없는 문제를 가리키는 값


def section_filter(section: list[str]) -> tuple[str, list[str]]:
    """섹션 목록 → (WHERE 조각, 파라미터). questions 별칭은 q."""
    named = [s for s in section if s != NULL_SECTION]
    parts = []
    if named:
        parts.append(f"q.section IN ({','.join('?' * len(named))})")
    if NULL_SECTION in section:
        parts.append("q.section IS NULL")
    return f"({' OR '.join(parts)})", named


@app.get("/api/sections")
def list_sections(conn: sqlite3.Connection = Depends(get_db)):
    rows = conn.execute(
        "SELECT section, COUNT(*) AS count FROM questions GROUP BY section ORDER BY section"
    ).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/questions")
def list_question_ids(
    from_: int | None = Query(None, alias="from", description="시작 번호 (포함)"),
    to: int | None = Query(None, description="끝 번호 (포함)"),
    section: list[str] | None = Query(None, description=f"여러 개 가능. 섹션 없는 문제는 '{NULL_SECTION}'"),
    mode: str = Query("all", pattern="^(all|wrong|unsolved)$"),
    shuffle: bool = False,
    conn: sqlite3.Connection = Depends(get_db),
):
    where, params = [], []
    if from_ is not None:
        where.append("q.number >= ?")
        params.append(from_)
    if to is not None:
        where.append("q.number <= ?")
        params.append(to)
    if section:
        clause, section_params = section_filter(section)
        where.append(clause)
        params.extend(section_params)
    if mode == "wrong":
        where.append("q.id IN (SELECT question_id FROM wrong_notes WHERE active = 1)")
    elif mode == "unsolved":
        where.append("q.id NOT IN (SELECT question_id FROM attempts)")

    sql = "SELECT q.id FROM questions q"
    if where:
        sql += " WHERE " + " AND ".join(where)
    ids = [r["id"] for r in conn.execute(sql + " ORDER BY q.number", params)]
    if shuffle:
        random.shuffle(ids)
    return {"count": len(ids), "ids": ids}


# ---------- 문제 풀이 ----------

@app.get("/api/questions/{question_id}")
def get_question(question_id: int, conn: sqlite3.Connection = Depends(get_db)):
    q = question_from_row(get_question_row(conn, question_id))
    note = conn.execute(
        "SELECT active, memo FROM wrong_notes WHERE question_id = ?", (question_id,)
    ).fetchone()
    wrong = wrong_summary(conn, question_id)
    # 정답과 해설은 제출 전에 보내지 않는다. 복수정답 안내용으로 개수만 보낸다.
    return {
        "id": q["id"],
        "number": q["number"],
        "section": q["section"],
        "question": q["question"],
        "options": q["options"],
        "multi": q["multi"],
        "answer_count": len(q["answer"]),
        "images": q["images"],
        "in_wrong_notes": bool(note and note["active"]),
        "memo": note["memo"] if note else "",
        "wrong_count": wrong["wrong_count"],
        "last_wrong_at": wrong["last_wrong_at"],
    }


def wrong_summary(conn: sqlite3.Connection, question_id: int) -> dict:
    row = conn.execute(
        "SELECT COUNT(*) AS wrong_count, MAX(created_at) AS last_wrong_at "
        "FROM attempts WHERE question_id = ? AND is_correct = 0",
        (question_id,),
    ).fetchone()
    return dict(row)


class AnswerIn(BaseModel):
    selected: list[str]
    session_id: int | None = None


@app.post("/api/questions/{question_id}/answer")
def submit_answer(question_id: int, body: AnswerIn, conn: sqlite3.Connection = Depends(get_db)):
    q = question_from_row(get_question_row(conn, question_id))
    if not body.selected:
        raise HTTPException(400, "선택한 보기가 없음")
    selected = normalize_selected(body.selected, q["options"])
    is_correct = set(selected) == set(q["answer"])
    if body.session_id is not None:
        get_session_row(conn, body.session_id)
        touch_session(conn, body.session_id)

    cur = conn.execute(
        "INSERT INTO attempts (question_id, session_id, selected, is_correct) VALUES (?, ?, ?, ?)",
        (question_id, body.session_id, json.dumps(selected), int(is_correct)),
    )
    created_at = conn.execute(
        "SELECT created_at FROM attempts WHERE id = ?", (cur.lastrowid,)
    ).fetchone()[0]
    if not is_correct:
        # 틀리면 오답노트 자동 추가. 다시 맞혀도 자동 해제하지 않는다.
        conn.execute(
            """
            INSERT INTO wrong_notes (question_id, active) VALUES (?, 1)
            ON CONFLICT(question_id) DO UPDATE SET
                active = 1, updated_at = datetime('now', 'localtime')
            """,
            (question_id,),
        )
    note = conn.execute(
        "SELECT active FROM wrong_notes WHERE question_id = ?", (question_id,)
    ).fetchone()
    return {
        "is_correct": is_correct,
        "selected": selected,
        "answer": q["answer"],
        "explanation": q["explanation"],
        "in_wrong_notes": bool(note and note["active"]),
        "answered_at": created_at,
        **wrong_summary(conn, question_id),
    }


# ---------- 오답노트 ----------

WRONG_NOTE_SORTS = {
    "number": "q.number",
    "recent": "last_wrong_at IS NULL, last_wrong_at DESC, q.number",
    "count": "wrong_count DESC, last_wrong_at DESC, q.number",
}


@app.get("/api/wrong-notes")
def list_wrong_notes(
    section: list[str] | None = Query(None, description=f"섹션 없는 문제는 '{NULL_SECTION}'"),
    min_wrong: int = Query(0, ge=0, description="이 횟수 이상 틀린 문제만 (예: 2)"),
    sort: str = Query("number", pattern="^(number|recent|count)$"),
    conn: sqlite3.Connection = Depends(get_db),
):
    sql = """
        SELECT * FROM (
            SELECT q.id AS question_id, q.number, q.section, q.question,
                   w.memo, w.added_at, w.updated_at,
                   COUNT(a.id) AS wrong_count,
                   MIN(a.created_at) AS first_wrong_at,
                   MAX(a.created_at) AS last_wrong_at
            FROM wrong_notes w
            JOIN questions q ON q.id = w.question_id
            LEFT JOIN attempts a ON a.question_id = q.id AND a.is_correct = 0
            WHERE w.active = 1 {section}
            GROUP BY q.id
        ) q
        WHERE wrong_count >= ?
    """
    params: list = []
    section_sql = ""
    if section:
        clause, params = section_filter(section)
        section_sql = f"AND {clause}"
    params.append(min_wrong)
    rows = conn.execute(
        sql.format(section=section_sql) + f" ORDER BY {WRONG_NOTE_SORTS[sort]}", params
    ).fetchall()

    # 문제별 틀린 시각 목록 (최근 순)
    times: dict[int, list[str]] = {}
    ids = [r["question_id"] for r in rows]
    if ids:
        for a in conn.execute(
            f"SELECT question_id, created_at FROM attempts "
            f"WHERE is_correct = 0 AND question_id IN ({','.join('?' * len(ids))}) "
            f"ORDER BY created_at DESC, id DESC",
            ids,
        ):
            times.setdefault(a["question_id"], []).append(a["created_at"])
    return [
        {
            **dict(r),
            "question": r["question"][:120],  # 목록용 미리보기
            "wrong_times": times.get(r["question_id"], []),
        }
        for r in rows
    ]


class WrongNoteIn(BaseModel):
    active: bool | None = None
    memo: str | None = None


@app.put("/api/wrong-notes/{question_id}")
def update_wrong_note(
    question_id: int, body: WrongNoteIn, conn: sqlite3.Connection = Depends(get_db)
):
    get_question_row(conn, question_id)
    if body.active is None and body.memo is None:
        raise HTTPException(400, "active 또는 memo 중 하나는 필요")
    # 메모만 저장하는 경우 오답노트에는 넣지 않는다 (active=0 으로 생성)
    conn.execute(
        "INSERT INTO wrong_notes (question_id, active) VALUES (?, ?) ON CONFLICT(question_id) DO NOTHING",
        (question_id, int(bool(body.active))),
    )
    if body.active is not None:
        conn.execute(
            "UPDATE wrong_notes SET active = ?, updated_at = datetime('now', 'localtime') WHERE question_id = ?",
            (int(body.active), question_id),
        )
    if body.memo is not None:
        conn.execute(
            "UPDATE wrong_notes SET memo = ?, updated_at = datetime('now', 'localtime') WHERE question_id = ?",
            (body.memo, question_id),
        )
    row = conn.execute("SELECT * FROM wrong_notes WHERE question_id = ?", (question_id,)).fetchone()
    return {**dict(row), "active": bool(row["active"])}


# ---------- AI 해설 ----------

def explanation_out(row: sqlite3.Row, cached: bool) -> dict:
    return {**dict(row), "selected": json.loads(row["selected"]), "cached": cached}


@app.get("/api/questions/{question_id}/ai-explain")
def get_ai_explanation(
    question_id: int,
    selected: str | None = Query(None, description="예: A,C. 주면 이 선택으로 만든 해설만 찾는다"),
    conn: sqlite3.Connection = Depends(get_db),
):
    q = question_from_row(get_question_row(conn, question_id))
    sql, params = "SELECT * FROM ai_explanations WHERE question_id = ?", [question_id]
    if selected:
        sql += " AND selected = ?"
        params.append(json.dumps(normalize_selected(selected.split(","), q["options"])))
    row = conn.execute(sql + " ORDER BY id DESC LIMIT 1", params).fetchone()
    if row is None:
        raise HTTPException(404, "저장된 AI 해설 없음")
    return explanation_out(row, cached=True)


class AiExplainIn(BaseModel):
    selected: list[str] = []


@app.post("/api/questions/{question_id}/ai-explain")
def create_ai_explanation(
    question_id: int,
    body: AiExplainIn,
    force: bool = Query(False, description="true면 저장본이 있어도 재생성"),
    conn: sqlite3.Connection = Depends(get_db),
):
    q = question_from_row(get_question_row(conn, question_id))
    selected = normalize_selected(body.selected, q["options"])
    selected_json = json.dumps(selected)

    if not force:
        row = conn.execute(
            "SELECT * FROM ai_explanations WHERE question_id = ? AND selected = ? ORDER BY id DESC LIMIT 1",
            (question_id, selected_json),
        ).fetchone()
        if row is not None:
            return explanation_out(row, cached=True)

    try:
        content, model = ai.generate_explanation(q, selected)
    except Exception as e:  # 타임아웃, API 오류는 메시지로 프론트에 전달
        raise HTTPException(502, f"AI 해설 생성 실패: {e}") from e
    cur = conn.execute(
        "INSERT INTO ai_explanations (question_id, selected, content, model) VALUES (?, ?, ?, ?)",
        (question_id, selected_json, content, model),
    )
    row = conn.execute("SELECT * FROM ai_explanations WHERE id = ?", (cur.lastrowid,)).fetchone()
    return explanation_out(row, cached=False)


# ---------- 테스트 세션 ----------

def get_session_row(conn: sqlite3.Connection, session_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM test_sessions WHERE id = ?", (session_id,)).fetchone()
    if row is None:
        raise HTTPException(404, f"테스트 세션 {session_id} 없음")
    return row


def touch_session(conn: sqlite3.Connection, session_id: int) -> None:
    conn.execute(
        "UPDATE test_sessions SET updated_at = datetime('now', 'localtime') WHERE id = ?",
        (session_id,),
    )


def session_summary(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    """세션 정보 + 진행 현황. 같은 문제를 여러 번 냈으면 마지막 제출 기준."""
    ids = json.loads(row["question_ids"])
    stats = conn.execute(
        """
        SELECT COUNT(*) AS answered, COALESCE(SUM(is_correct), 0) AS correct
        FROM attempts a
        WHERE session_id = ? AND id = (
            SELECT MAX(id) FROM attempts b
            WHERE b.session_id = a.session_id AND b.question_id = a.question_id
        )
        """,
        (row["id"],),
    ).fetchone()
    return {
        "id": row["id"],
        "label": row["label"],
        "params": json.loads(row["params"]),
        "total": len(ids),
        "current_index": row["current_index"],
        "answered": stats["answered"],
        "correct": stats["correct"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


class SessionIn(BaseModel):
    label: str
    question_ids: list[int]
    params: dict = {}


@app.post("/api/sessions")
def create_session(body: SessionIn, conn: sqlite3.Connection = Depends(get_db)):
    if not body.question_ids:
        raise HTTPException(400, "문제가 없음")
    found = {
        r["id"]
        for r in conn.execute(
            f"SELECT id FROM questions WHERE id IN ({','.join('?' * len(body.question_ids))})",
            body.question_ids,
        )
    }
    missing = [i for i in body.question_ids if i not in found]
    if missing:
        raise HTTPException(400, f"없는 문제: {missing[:10]}")
    cur = conn.execute(
        "INSERT INTO test_sessions (label, params, question_ids) VALUES (?, ?, ?)",
        (body.label.strip() or "테스트", json.dumps(body.params, ensure_ascii=False), json.dumps(body.question_ids)),
    )
    return session_summary(conn, get_session_row(conn, cur.lastrowid))


@app.get("/api/sessions")
def list_sessions(conn: sqlite3.Connection = Depends(get_db)):
    rows = conn.execute("SELECT * FROM test_sessions ORDER BY updated_at DESC, id DESC").fetchall()
    return [session_summary(conn, r) for r in rows]


@app.get("/api/sessions/{session_id}")
def get_session(session_id: int, conn: sqlite3.Connection = Depends(get_db)):
    """세션 + 이미 제출한 문제의 결과 (다시 열었을 때 채점 결과를 보여주기 위해)."""
    row = get_session_row(conn, session_id)
    results = {}
    for a in conn.execute(
        """
        SELECT a.question_id, a.selected, a.is_correct, a.created_at, q.answer, q.explanation
        FROM attempts a JOIN questions q ON q.id = a.question_id
        WHERE a.session_id = ? ORDER BY a.id
        """,
        (session_id,),
    ):
        results[a["question_id"]] = {  # 같은 문제는 마지막 제출로 덮인다
            "selected": json.loads(a["selected"]),
            "is_correct": bool(a["is_correct"]),
            "answer": json.loads(a["answer"]),
            "explanation": a["explanation"],
            "answered_at": a["created_at"],
        }
    return {
        **session_summary(conn, row),
        "question_ids": json.loads(row["question_ids"]),
        "results": results,
    }


class SessionUpdate(BaseModel):
    current_index: int | None = None
    label: str | None = None


@app.patch("/api/sessions/{session_id}")
def update_session(session_id: int, body: SessionUpdate, conn: sqlite3.Connection = Depends(get_db)):
    row = get_session_row(conn, session_id)
    if body.current_index is not None:
        total = len(json.loads(row["question_ids"]))
        # total 은 "끝까지 풀었음" (결과 화면)
        index = max(0, min(body.current_index, total))
        conn.execute("UPDATE test_sessions SET current_index = ? WHERE id = ?", (index, session_id))
    if body.label is not None and body.label.strip():
        conn.execute("UPDATE test_sessions SET label = ? WHERE id = ?", (body.label.strip(), session_id))
    touch_session(conn, session_id)
    return session_summary(conn, get_session_row(conn, session_id))


@app.delete("/api/sessions/{session_id}")
def delete_session(session_id: int, conn: sqlite3.Connection = Depends(get_db)):
    """세션만 지운다. 풀이 기록(오답 횟수, 통계)은 남긴다."""
    get_session_row(conn, session_id)
    conn.execute("UPDATE attempts SET session_id = NULL WHERE session_id = ?", (session_id,))
    conn.execute("DELETE FROM test_sessions WHERE id = ?", (session_id,))
    return {"deleted": session_id}


# ---------- 통계 ----------

@app.get("/api/stats")
def get_stats(conn: sqlite3.Connection = Depends(get_db)):
    rows = conn.execute(
        """
        SELECT q.section,
               COUNT(DISTINCT q.id) AS total,
               COUNT(DISTINCT a.question_id) AS solved,
               COUNT(a.id) AS attempts,
               COALESCE(SUM(a.is_correct), 0) AS correct
        FROM questions q LEFT JOIN attempts a ON a.question_id = q.id
        GROUP BY q.section ORDER BY q.section
        """
    ).fetchall()

    def with_rate(d: dict) -> dict:
        d["accuracy"] = round(d["correct"] / d["attempts"], 3) if d["attempts"] else None
        return d

    sections = [with_rate(dict(r)) for r in rows]
    overall = {
        k: sum(s[k] for s in sections) for k in ("total", "solved", "attempts", "correct")
    }
    return {"overall": with_rate(overall), "sections": sections}


# ---------- 정적 파일 ----------

@app.get("/images/{name}")
def get_image(name: str):
    """questions/ 의 문제 그림. PDF 등 다른 파일은 내보내지 않는다."""
    path = (IMAGES_DIR / name).resolve()
    if path.parent != IMAGES_DIR.resolve() or path.suffix.lower() not in IMAGE_SUFFIXES or not path.is_file():
        raise HTTPException(404, "이미지 없음")
    return FileResponse(path)


# API 라우트보다 뒤에 둬야 /api/* 를 가리지 않는다
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")

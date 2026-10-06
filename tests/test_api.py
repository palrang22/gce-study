def test_question_detail_hides_answer(client):
    q = client.get("/api/questions/2").json()
    assert "answer" not in q and "explanation" not in q
    assert q["multi"] is True and q["answer_count"] == 2
    assert client.get("/api/questions/999").status_code == 404


def test_single_answer_scoring(client):
    r = client.post("/api/questions/1/answer", json={"selected": ["b"]}).json()
    assert r["is_correct"] is True and r["answer"] == ["B"]
    assert r["explanation"] == "https://example.com/1"
    assert r["in_wrong_notes"] is False


def test_multi_answer_needs_exact_set(client):
    assert client.post("/api/questions/2/answer", json={"selected": ["E", "B"]}).json()["is_correct"]
    assert not client.post("/api/questions/2/answer", json={"selected": ["B"]}).json()["is_correct"]
    assert not client.post("/api/questions/2/answer", json={"selected": ["B", "C", "E"]}).json()["is_correct"]


def test_invalid_selection(client):
    assert client.post("/api/questions/1/answer", json={"selected": ["Z"]}).status_code == 400
    assert client.post("/api/questions/1/answer", json={"selected": []}).status_code == 400


def test_wrong_answer_adds_note_and_stays(client):
    r = client.post("/api/questions/1/answer", json={"selected": ["A"]}).json()
    assert r["is_correct"] is False and r["in_wrong_notes"] is True
    # 다시 맞혀도 자동 해제하지 않음
    assert client.post("/api/questions/1/answer", json={"selected": ["B"]}).json()["in_wrong_notes"] is True

    notes = client.get("/api/wrong-notes").json()
    assert [n["question_id"] for n in notes] == [1]
    assert notes[0]["wrong_count"] == 1


def test_wrong_note_memo_and_toggle(client):
    # 메모만 저장하면 오답노트에는 안 들어감
    assert client.put("/api/wrong-notes/3", json={"memo": "메모"}).json()["active"] is False
    assert client.get("/api/questions/3").json()["memo"] == "메모"
    assert client.get("/api/wrong-notes").json() == []

    assert client.put("/api/wrong-notes/3", json={"active": True}).json()["memo"] == "메모"
    assert client.get("/api/questions/3").json()["in_wrong_notes"] is True
    client.put("/api/wrong-notes/3", json={"active": False})
    assert client.get("/api/wrong-notes").json() == []


def test_question_range_and_modes(client):
    assert client.get("/api/questions").json() == {"count": 3, "ids": [1, 2, 3]}
    assert client.get("/api/questions", params={"from": 2, "to": 3}).json()["ids"] == [2, 3]
    assert client.get("/api/questions", params={"section": "null"}).json()["count"] == 3
    assert client.get("/api/questions", params={"section": "Networking"}).json()["count"] == 0

    client.post("/api/questions/1/answer", json={"selected": ["A"]})  # 오답
    client.post("/api/questions/2/answer", json={"selected": ["B", "E"]})  # 정답
    assert client.get("/api/questions", params={"mode": "wrong"}).json()["ids"] == [1]
    assert client.get("/api/questions", params={"mode": "unsolved"}).json()["ids"] == [3]

    shuffled = client.get("/api/questions", params={"shuffle": True}).json()["ids"]
    assert sorted(shuffled) == [1, 2, 3]


def test_ai_explain_cache_and_force(client):
    assert client.get("/api/questions/1/ai-explain").status_code == 404

    first = client.post("/api/questions/1/ai-explain", json={"selected": ["A"]}).json()
    assert first["cached"] is False and first["selected"] == ["A"]
    again = client.post("/api/questions/1/ai-explain", json={"selected": ["A"]}).json()
    assert again["cached"] is True and again["id"] == first["id"]

    forced = client.post("/api/questions/1/ai-explain?force=true", json={"selected": ["A"]}).json()
    assert forced["cached"] is False and forced["id"] != first["id"]
    assert client.get("/api/questions/1/ai-explain").json()["id"] == forced["id"]

    other = client.post("/api/questions/1/ai-explain", json={"selected": ["C"]}).json()
    assert other["cached"] is False
    assert client.get("/api/questions/1/ai-explain", params={"selected": "A"}).json()["id"] == forced["id"]


def test_stats(client):
    client.post("/api/questions/1/answer", json={"selected": ["A"]})
    client.post("/api/questions/1/answer", json={"selected": ["B"]})
    s = client.get("/api/stats").json()["overall"]
    assert s == {"total": 3, "solved": 1, "attempts": 2, "correct": 1, "accuracy": 0.5}


def test_sections(client):
    assert client.get("/api/sections").json() == [{"section": None, "count": 3}]


def test_images_only_serves_pictures(client):
    assert client.get("/images/GCP-ACE.pdf").status_code == 404
    assert client.get("/images/..%2F.env").status_code == 404
    assert client.get("/").status_code == 200


def test_ai_explain_error_is_reported(client, monkeypatch):
    from app import ai

    def boom(q, selected):
        raise TimeoutError("deadline exceeded")

    monkeypatch.setattr(ai, "generate_explanation", boom)
    r = client.post("/api/questions/1/ai-explain", json={"selected": ["A"]})
    assert r.status_code == 502 and "deadline exceeded" in r.json()["detail"]
    assert client.get("/api/questions/1/ai-explain").status_code == 404  # 실패는 저장하지 않음


def test_prompt_contains_question_and_choice():
    from app.ai import build_prompt
    from tests.conftest import SAMPLE

    prompt = build_prompt(SAMPLE[0], ["A"])
    assert "Q1 text" in prompt and "A. a" in prompt
    assert "[정답] B" in prompt and "[내가 고른 답] A" in prompt
    assert "토론 링크만 있음: https://example.com/1" in prompt


def test_wrong_notes_count_times_filter_and_sort(client):
    for _ in range(3):
        client.post("/api/questions/1/answer", json={"selected": ["A"]})  # 3회 오답
    client.post("/api/questions/3/answer", json={"selected": ["B"]})      # 1회 오답
    client.put("/api/wrong-notes/2", json={"active": True})               # 직접 추가 (틀린 적 없음)

    notes = {n["question_id"]: n for n in client.get("/api/wrong-notes").json()}
    assert notes[1]["wrong_count"] == 3 and len(notes[1]["wrong_times"]) == 3
    assert notes[1]["last_wrong_at"] == notes[1]["wrong_times"][0]
    assert notes[2]["wrong_count"] == 0 and notes[2]["last_wrong_at"] is None

    ids = lambda **p: [n["question_id"] for n in client.get("/api/wrong-notes", params=p).json()]
    assert ids(min_wrong=2) == [1]
    assert ids(min_wrong=1) == [1, 3]
    assert ids(sort="count")[0] == 1
    assert ids(sort="recent")[-1] == 2  # 틀린 적 없는 문제는 맨 뒤

    q = client.get("/api/questions/1").json()
    assert q["wrong_count"] == 3 and q["last_wrong_at"]


def test_session_create_resume_and_results(client):
    s = client.post("/api/sessions", json={"label": "1~3번", "question_ids": [3, 1, 2], "params": {"from": 1, "to": 3}}).json()
    assert s["total"] == 3 and s["answered"] == 0 and s["current_index"] == 0

    client.post("/api/questions/3/answer", json={"selected": ["A"], "session_id": s["id"]})
    client.post("/api/questions/1/answer", json={"selected": ["A"], "session_id": s["id"]})
    client.post("/api/questions/1/answer", json={"selected": ["A"]})  # 세션 밖 풀이는 섞이지 않음
    client.patch(f"/api/sessions/{s['id']}", json={"current_index": 2})

    loaded = client.get(f"/api/sessions/{s['id']}").json()
    assert loaded["question_ids"] == [3, 1, 2] and loaded["current_index"] == 2
    assert loaded["answered"] == 2 and loaded["correct"] == 1
    assert loaded["results"]["1"]["is_correct"] is False and loaded["results"]["1"]["answer"] == ["B"]
    assert "2" not in loaded["results"]

    # 매번 새로 열 수 있다: 같은 범위라도 별도 세션
    s2 = client.post("/api/sessions", json={"label": "1~3번", "question_ids": [1, 2, 3]}).json()
    assert s2["id"] != s["id"] and s2["answered"] == 0
    assert [x["id"] for x in client.get("/api/sessions").json()] == [s2["id"], s["id"]]


def test_session_delete_keeps_history(client):
    s = client.post("/api/sessions", json={"label": "t", "question_ids": [1]}).json()
    client.post("/api/questions/1/answer", json={"selected": ["A"], "session_id": s["id"]})
    assert client.delete(f"/api/sessions/{s['id']}").status_code == 200
    assert client.get(f"/api/sessions/{s['id']}").status_code == 404
    assert client.get("/api/questions/1").json()["wrong_count"] == 1


def test_session_validation(client):
    assert client.post("/api/sessions", json={"label": "t", "question_ids": []}).status_code == 400
    assert client.post("/api/sessions", json={"label": "t", "question_ids": [999]}).status_code == 400
    assert client.post("/api/questions/1/answer", json={"selected": ["A"], "session_id": 999}).status_code == 404
    s = client.post("/api/sessions", json={"label": "t", "question_ids": [1, 2]}).json()
    assert client.patch(f"/api/sessions/{s['id']}", json={"current_index": 99}).json()["current_index"] == 2

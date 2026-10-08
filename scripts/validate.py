"""data/questions_<exam>.json 형식 검증. 문제가 있는 항목을 표로 출력한다."""
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JSON_PATHS = [ROOT / "data" / "questions_ace.json", ROOT / "data" / "questions_pca.json"]


def validate_one(exam: str, questions: list[dict]) -> list[tuple]:
    rows = []  # (번호, 항목, 내용)

    numbers = [q["number"] for q in questions]
    for n, count in Counter(numbers).items():
        if count > 1:
            rows.append((n, "중복 번호", f"{count}번 등장"))
    for n in sorted(set(range(1, max(numbers) + 1)) - set(numbers)):
        rows.append((n, "빠진 번호", "-"))

    for q in questions:
        n, options, answer = q["number"], q["options"], q["answer"]
        if not 4 <= len(options) <= 6:
            rows.append((n, "보기 수", f"{len(options)}개"))
        if not answer:
            rows.append((n, "정답 없음", "-"))
        bad = [a for a in answer if a not in options]
        if bad:
            rows.append((n, "보기에 없는 정답", ", ".join(bad)))
        if q["multi"] != (len(answer) > 1):
            rows.append((n, "multi 불일치", f"multi={q['multi']}, 정답 {len(answer)}개"))
        for w in q["parse_warnings"]:
            rows.append((n, "파서 경고", w))

    print(f"\n[{exam}] 총 {len(questions)}문제, 이상 항목 {len(rows)}건")
    if rows:
        print(f"{'번호':>5}  {'항목':<12}  내용")
        print("-" * 70)
        for n, kind, detail in sorted(rows):
            print(f"{'Q' + str(n):>5}  {kind:<12}  {detail}")
    return rows


def main() -> None:
    total_rows = 0
    for path in JSON_PATHS:
        if not path.exists():
            print(f"건너뜀 (없음): {path}")
            continue
        questions = json.loads(path.read_text(encoding="utf-8"))
        exam = questions[0]["exam"] if questions else path.stem
        total_rows += len(validate_one(exam, questions))
    print(f"\n전체 이상 항목: {total_rows}건")


if __name__ == "__main__":
    main()

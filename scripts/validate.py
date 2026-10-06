"""data/questions.json 형식 검증. 문제가 있는 항목을 표로 출력한다."""
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JSON_PATH = ROOT / "data" / "questions.json"


def main() -> None:
    questions = json.loads(JSON_PATH.read_text(encoding="utf-8"))
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

    print(f"총 {len(questions)}문제, 이상 항목 {len(rows)}건\n")
    if rows:
        print(f"{'번호':>5}  {'항목':<12}  내용")
        print("-" * 70)
        for n, kind, detail in sorted(rows):
            print(f"{'Q' + str(n):>5}  {kind:<12}  {detail}")


if __name__ == "__main__":
    main()

"""ACE 기출예상문제 PDF → data/questions.json 변환.

PDF 패턴 (Examtopics Q375 덤프):
    Q<번호>
    지문 (여러 줄)
    A. 보기 (여러 줄로 이어질 수 있음)
    ...
    Answer: B        # 복수정답은 "Answer: B, E"
    https://www.examtopics.com/discussions/...   # 해설 대신 토론 링크 (두 줄로 잘림)
"""
import json
import re
from pathlib import Path

import pdfplumber

ROOT = Path(__file__).resolve().parent.parent
PDF_PATH = ROOT / "questions" / "GCP-ACE.pdf"
# PDF만으로 채울 수 없는 내용 (그림 옮겨 적기, 빠진 정답). 이미지 파일도 questions/ 에 있다.
FIXES_PATH = ROOT / "questions" / "manual_fixes.json"
OUT_PATH = ROOT / "data" / "questions.json"

Q_RE = re.compile(r"^Q(\d+)$")
OPTION_RE = re.compile(r"^([A-F])\.\s*(.*)$")
ANSWER_RE = re.compile(r"^Answer:\s*(.*)$")
URL_RE = re.compile(r"^https?://")
MULTI_RE = re.compile(r"\((choose|select) (two|three)\.?\)", re.IGNORECASE)

# UTF-8 따옴표/대시가 다른 인코딩으로 깨진 문자열 복원 (긴 것부터 치환)
MOJIBAKE = [
    ('ג€"', "-"),   # 대시: n2ג€"highmem → n2-highmem, ג€"-preview → --preview
    ("ג€", '"'),    # 따옴표
    ('"¢', "•"),    # 글머리 기호
]

# 그림/표를 봐야 풀 수 있는 문제로 의심되는 표현
FIGURE_HINTS = re.compile(
    r"(shown below|specified below|schema is shown|following (DDL|output|command)|"
    r"status:|below:|exhibit)",
    re.IGNORECASE,
)


def fix_text(text: str) -> str:
    for bad, good in MOJIBAKE:
        text = text.replace(bad, good)
    return text


def join_lines(lines: list[str]) -> str:
    """PDF 줄바꿈을 공백으로 합친다. 글머리(•, *, 1.)로 시작하는 줄은 줄바꿈 유지."""
    out = ""
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if not out:
            out = line
        elif re.match(r"^(•|\*|\d+\.)\s", line):
            out += "\n" + line
        elif out.endswith("-") and not out.endswith(" -"):
            # "high-\nperformance" 처럼 하이픈에서 잘린 줄은 공백 없이 붙인다
            out += line
        else:
            out += " " + line
    return out


def extract_lines() -> tuple[list[tuple[int, str]], set[int]]:
    """(페이지 번호, 줄) 목록과 이미지가 있는 페이지 번호 집합."""
    lines, image_pages = [], set()
    with pdfplumber.open(PDF_PATH) as pdf:
        for page_no, page in enumerate(pdf.pages, start=1):
            if page.images:
                image_pages.add(page_no)
            for line in (page.extract_text() or "").splitlines():
                lines.append((page_no, fix_text(line)))
    return lines, image_pages


def split_blocks(lines: list[tuple[int, str]]) -> list[tuple[int, list[tuple[int, str]]]]:
    """Q<번호> 줄 기준으로 문제 블록을 자른다. 첫 문제 앞(머리말)은 버린다."""
    blocks, current = [], None
    for page_no, line in lines:
        m = Q_RE.match(line.strip())
        if m:
            current = (int(m.group(1)), [])
            blocks.append(current)
        elif current is not None:
            current[1].append((page_no, line))
    return blocks


def parse_block(number: int, body: list[tuple[int, str]], image_pages: set[int]) -> dict:
    warnings = []
    question_lines, options, answer_raw, url_lines = [], {}, None, []
    current_option = None
    pages = {p for p, _ in body}

    for _, line in body:
        stripped = line.strip()
        if answer_raw is not None:
            # 정답 뒤에는 토론 링크만 온다 (URL이 두 줄로 잘려 있음)
            if URL_RE.match(stripped) or url_lines:
                url_lines.append(stripped)
            elif stripped:
                warnings.append(f"정답 뒤에 알 수 없는 텍스트: {stripped[:40]}")
            continue
        m = ANSWER_RE.match(stripped)
        if m:
            answer_raw = m.group(1)
            continue
        m = OPTION_RE.match(stripped)
        if m and (current_option is not None or m.group(1) == "A"):
            current_option = m.group(1)
            if current_option in options:
                warnings.append(f"보기 {current_option} 중복")
            options[current_option] = [m.group(2)]
            continue
        if current_option is None:
            question_lines.append(stripped)
        else:
            options[current_option].append(stripped)

    question = join_lines(question_lines)
    options = {k: join_lines(v) for k, v in options.items()}
    answer = re.findall(r"[A-F]", answer_raw or "")
    multi = bool(MULTI_RE.search(question)) or len(answer) > 1
    explanation = "".join(url_lines)

    if answer_raw is None:
        warnings.append("Answer 줄이 없음")
    elif not answer:
        warnings.append("정답이 비어 있음 (PDF에 정답 표기 없음)")
    if any(a not in options for a in answer):
        warnings.append(f"보기에 없는 정답: {answer}")
    if len(options) < 4:
        warnings.append(f"보기 수가 {len(options)}개")
    if MULTI_RE.search(question) and len(answer) < 2:
        warnings.append("Choose two/three 문제인데 정답이 1개")
    if not explanation:
        warnings.append("토론 링크 없음")
    if FIGURE_HINTS.search(question) or any(v.endswith(":") for v in options.values()):
        hint = "그림/표 참조 문제로 보임 (PDF에 이미지로만 있음)"
        if pages & image_pages:
            hint += f", 이미지 페이지: {sorted(pages & image_pages)}"
        warnings.append(hint)

    return {
        "id": number,
        "number": number,
        "section": None,
        "question": question,
        "options": options,
        "answer": answer,
        "multi": multi,
        "explanation": explanation,
        "parse_warnings": warnings,
    }


def apply_fixes(q: dict, fix: dict) -> None:
    """manual_fixes.json 내용을 반영하고, 해결된 경고는 지운다."""
    images = []
    for fig in fix.get("figures", []):
        field = fig["field"]
        text = q["question"] if field == "question" else q["options"][field]
        if fig["after"] not in text:
            q["parse_warnings"].append(f"그림 삽입 위치를 못 찾음: {fig['after']}")
            continue
        before, rest = text.split(fig["after"], 1)
        text = f"{before}{fig['after']}\n\n{fig['text']}\n\n{rest.lstrip()}".strip()
        if field == "question":
            q["question"] = text
        else:
            q["options"][field] = text
        images.append(fig["image"])
    q["images"] = images

    if "answer" in fix:
        q["answer"] = fix["answer"]
        q["multi"] = len(q["answer"]) > 1

    resolved = []
    if images:
        resolved.append("그림/표 참조 문제")
    if "answer" in fix:
        resolved.append("정답이 비어 있음")
    if fix.get("allow_no_link"):
        resolved.append("토론 링크 없음")
    q["parse_warnings"] = [
        w for w in q["parse_warnings"] if not any(w.startswith(r) for r in resolved)
    ]


def main() -> None:
    lines, image_pages = extract_lines()
    questions = [parse_block(n, body, image_pages) for n, body in split_blocks(lines)]

    fixes = json.loads(FIXES_PATH.read_text(encoding="utf-8")) if FIXES_PATH.exists() else {}
    for q in questions:
        apply_fixes(q, fixes.get(str(q["number"]), {}))
    print(f"수동 보정 적용: {', '.join('Q' + n for n in fixes)}")
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(questions, ensure_ascii=False, indent=2), encoding="utf-8")

    warned = [q for q in questions if q["parse_warnings"]]
    print(f"총 문제 수: {len(questions)}")
    print(f"복수정답 문제: {sum(q['multi'] for q in questions)}")
    print(f"경고 있는 문제: {len(warned)}")
    for q in warned:
        print(f"  Q{q['number']}: {' / '.join(q['parse_warnings'])}")
    print(f"저장: {OUT_PATH}")


if __name__ == "__main__":
    main()

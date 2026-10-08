"""ACE / PCA 기출예상문제 PDF → data/questions_<exam>.json 변환.

PDF 패턴 (Examtopics 덤프):
    Q<번호>
    지문 (여러 줄)
    A. 보기 (여러 줄로 이어질 수 있음)
    ...
    Answer: B        # 복수정답은 "Answer: B, E"
    https://www.examtopics.com/discussions/...   # 해설 대신 토론 링크 (두 줄로 잘림)

여러 시험을 다루므로 id 충돌을 막기 위해 시험별로 id 오프셋을 둔다 (ACE: 0, PCA: 10000).
"""
import json
import re
import sys
from pathlib import Path

import pdfplumber

ROOT = Path(__file__).resolve().parent.parent
QUESTIONS_DIR = ROOT / "questions"
DATA_DIR = ROOT / "data"
PICS_DIR = DATA_DIR / "pics"

EXAMS = {
    "ACE": {"pdf": QUESTIONS_DIR / "GCP-ACE.pdf", "offset": 0, "out": DATA_DIR / "questions_ace.json"},
    "PCA": {"pdf": QUESTIONS_DIR / "GCP-PCA.pdf", "offset": 10000, "out": DATA_DIR / "questions_pca.json"},
}
# PDF만으로 채울 수 없는 내용 (그림 옮겨 적기, 빠진 정답). 이미지 파일도 questions/ 에 있다.
FIXES_PATH = QUESTIONS_DIR / "manual_fixes.json"

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
# pdftotext 계열에서 보이는 bidi 래퍼(RLE ... PDF). 안에 '-'가 있으면 "--"(gcloud 플래그), 없으면 겹인용부호.
MOJIBAKE_RE = re.compile("‫([^‬]*)‬")

# 그림/표를 봐야 풀 수 있는 문제로 의심되는 표현
FIGURE_HINTS = re.compile(
    r"(shown below|specified below|schema is shown|following (DDL|output|command)|"
    r"status:|below:|exhibit)",
    re.IGNORECASE,
)


def fix_text(text: str) -> str:
    for bad, good in MOJIBAKE:
        text = text.replace(bad, good)
    text = MOJIBAKE_RE.sub(lambda m: "--" if "-" in m.group(1) else '"', text)
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


def extract_lines(pdf_path: Path) -> list[str]:
    lines = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            for line in (page.extract_text() or "").splitlines():
                lines.append(fix_text(line))
    return lines


def split_blocks(lines: list[str]) -> list[tuple[int, list[str]]]:
    """Q<번호> 줄 기준으로 문제 블록을 자른다. 첫 문제 앞(머리말)은 버린다."""
    blocks, current = [], None
    for line in lines:
        m = Q_RE.match(line.strip())
        if m:
            current = (int(m.group(1)), [])
            blocks.append(current)
        elif current is not None:
            current[1].append(line)
    return blocks


def parse_block(number: int, body: list[str]) -> dict:
    warnings = []
    question_lines, options, answer_raw, url_lines = [], {}, None, []
    current_option = None

    for line in body:
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
        warnings.append("그림/표 참조 문제로 보임 (PDF에 이미지로만 있음)")

    return {
        "number": number,
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


# data/pics/<exam 소문자>/Q<번호>.png (여러 장이면 -1, -2 ...) 네이밍 규칙.
# manual_fixes.json 의 손으로 적은 "그림 삽입 위치"가 없어도, 사진만 이 규칙으로 넣으면
# 문제 풀이 화면의 "원본 그림 보기"에 바로 뜬다 (본문에 끼워 넣지 않고 별도 섹션으로 보여줌).
PIC_RE = re.compile(r"^Q(\d+)(?:-(\d+))?\.(png|jpe?g|gif|webp)$", re.IGNORECASE)


def attach_pics(questions: list[dict], exam: str) -> None:
    exam_dir = PICS_DIR / exam.lower()
    if not exam_dir.exists():
        return
    by_number: dict[int, list[tuple[int, str]]] = {}
    for path in exam_dir.iterdir():
        if not path.is_file():
            continue
        m = PIC_RE.match(path.name)
        if not m:
            print(f"[{exam}] 경고: {path.name} 이 사진 네이밍 규칙과 안 맞아서 건너뜀 (Q<번호>[-<순번>].png)")
            continue
        number = int(m.group(1))
        order = int(m.group(2)) if m.group(2) else 0
        by_number.setdefault(number, []).append((order, path.name))

    by_number_q = {q["number"]: q for q in questions}
    for number, files in by_number.items():
        q = by_number_q.get(number)
        if q is None:
            print(f"[{exam}] 경고: data/pics/{exam.lower()} 에 Q{number} 사진이 있는데 해당 문제가 없음")
            continue
        q["images"] = [f"{exam.lower()}/{name}" for _, name in sorted(files)]
        q["parse_warnings"] = [
            w for w in q["parse_warnings"] if not w.startswith("그림/표 참조 문제로 보임")
        ]


def parse_exam(exam: str) -> list[dict]:
    cfg = EXAMS[exam]
    lines = extract_lines(cfg["pdf"])
    questions = [parse_block(n, body) for n, body in split_blocks(lines)]

    all_fixes = json.loads(FIXES_PATH.read_text(encoding="utf-8")) if FIXES_PATH.exists() else {}
    fixes = all_fixes.get(exam, {})
    for q in questions:
        apply_fixes(q, fixes.get(str(q["number"]), {}))
    if fixes:
        print(f"[{exam}] 수동 보정 적용: {', '.join('Q' + n for n in fixes)}")
    attach_pics(questions, exam)

    out = []
    for q in questions:
        out.append({
            "id": cfg["offset"] + q["number"],
            "number": q["number"],
            "exam": exam,
            "section": None,
            "question": q["question"],
            "options": q["options"],
            "answer": q["answer"],
            "multi": q["multi"],
            "explanation": q["explanation"],
            "images": q.get("images", []),
            "parse_warnings": q["parse_warnings"],
        })

    cfg["out"].parent.mkdir(parents=True, exist_ok=True)
    cfg["out"].write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    warned = [q for q in out if q["parse_warnings"]]
    needs_image = [q for q in out if any(w.startswith("그림/표 참조 문제로 보임") for w in q["parse_warnings"])]
    print(f"[{exam}] 총 문제 수: {len(out)}")
    print(f"[{exam}] 복수정답 문제: {sum(q['multi'] for q in out)}")
    print(f"[{exam}] 경고 있는 문제: {len(warned)}")
    print(f"[{exam}] 사진/표 확인 필요: {', '.join(str(q['number']) for q in needs_image) or '없음'}")
    for q in warned:
        print(f"  Q{q['number']}: {' / '.join(q['parse_warnings'])}")
    print(f"[{exam}] 저장: {cfg['out']}")
    return out


def main() -> None:
    exams = sys.argv[1:] or list(EXAMS)
    for exam in exams:
        if exam not in EXAMS:
            print(f"알 수 없는 exam: {exam} (가능: {', '.join(EXAMS)})")
            sys.exit(1)
    for exam in exams:
        parse_exam(exam)


if __name__ == "__main__":
    main()

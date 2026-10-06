"""PDF 앞부분 텍스트를 data/sample.txt 로 뽑아 패턴을 확인한다."""
import sys
from pathlib import Path

import pdfplumber

ROOT = Path(__file__).resolve().parent.parent
PDF_PATH = ROOT / "questions" / "GCP-ACE.pdf"
OUT_PATH = ROOT / "data" / "sample.txt"


def main(pages: int = 5) -> None:
    with pdfplumber.open(PDF_PATH) as pdf:
        print(f"총 페이지: {len(pdf.pages)}")
        chunks = []
        for i, page in enumerate(pdf.pages[:pages], start=1):
            chunks.append(f"===== PAGE {i} =====\n{page.extract_text() or ''}")
    OUT_PATH.write_text("\n".join(chunks), encoding="utf-8")
    print(f"저장: {OUT_PATH}")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 5)

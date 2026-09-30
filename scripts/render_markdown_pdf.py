from __future__ import annotations

import argparse
from pathlib import Path


def main() -> int:
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.platypus import Paragraph, Preformatted, SimpleDocTemplate, Spacer
        from xml.sax.saxutils import escape
    except ImportError as exc:
        raise SystemExit("Missing dependency: reportlab") from exc

    parser = argparse.ArgumentParser(description="Render a small markdown subset to PDF.")
    parser.add_argument("input")
    parser.add_argument("output")
    args = parser.parse_args()

    text = Path(args.input).read_text(encoding="utf-8")
    lines = text.splitlines()

    styles = getSampleStyleSheet()
    code_style = ParagraphStyle(
        "CodeBlock",
        parent=styles["BodyText"],
        fontName="Courier",
        fontSize=7.5,
        leading=9,
        leftIndent=6,
        rightIndent=6,
        spaceBefore=4,
        spaceAfter=8,
    )
    bullet_style = ParagraphStyle(
        "BulletText",
        parent=styles["BodyText"],
        leftIndent=12,
        firstLineIndent=-8,
        spaceAfter=3,
    )

    title = next((line[2:].strip() for line in lines if line.startswith("# ")), "Workbook")
    story = []
    in_code = False
    code_lines: list[str] = []
    para_lines: list[str] = []

    def flush_paragraph() -> None:
        nonlocal para_lines
        if not para_lines:
            return
        text = " ".join(part.strip() for part in para_lines).strip()
        if text:
            story.append(Paragraph(escape(text), styles["BodyText"]))
            story.append(Spacer(1, 4))
        para_lines = []

    def flush_code() -> None:
        nonlocal code_lines
        if not code_lines:
            return
        story.append(Preformatted("\n".join(code_lines), code_style))
        story.append(Spacer(1, 4))
        code_lines = []

    for line in lines:
        stripped = line.rstrip("\n")
        if stripped.startswith("```"):
            flush_paragraph()
            if in_code:
                flush_code()
                in_code = False
            else:
                in_code = True
            continue

        if in_code:
            code_lines.append(stripped)
            continue

        if not stripped.strip():
            flush_paragraph()
            continue

        if stripped.startswith("# "):
            flush_paragraph()
            story.append(Paragraph(escape(stripped[2:].strip()), styles["Title"]))
            story.append(Spacer(1, 8))
            continue
        if stripped.startswith("## "):
            flush_paragraph()
            story.append(Paragraph(escape(stripped[3:].strip()), styles["Heading1"]))
            story.append(Spacer(1, 6))
            continue
        if stripped.startswith("### "):
            flush_paragraph()
            story.append(Paragraph(escape(stripped[4:].strip()), styles["Heading2"]))
            story.append(Spacer(1, 4))
            continue
        if stripped.startswith("- "):
            flush_paragraph()
            story.append(Paragraph(escape("• " + stripped[2:].strip()), bullet_style))
            continue

        para_lines.append(stripped)

    flush_paragraph()
    flush_code()

    doc = SimpleDocTemplate(
        args.output,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=title,
        author="Hermes Agent",
    )
    doc.build(story)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

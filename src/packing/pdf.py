"""PDF rendering for challenge writeups."""

from __future__ import annotations

import re
from pathlib import Path

from packing.errors import PackingError


def _render_pdf(markdown_path: Path, destination: Path, warnings: list[str]) -> None:
    source = markdown_path.read_text(encoding="utf-8")
    if not re.search(r"[\u3400-\u9fff]", source):
        warnings.append(f"{markdown_path}: writeup contains no CJK text")
    try:
        from reportlab.lib.enums import TA_LEFT
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont
        from reportlab.platypus import Paragraph, Preformatted, SimpleDocTemplate, Spacer
    except ImportError as exc:
        raise PackingError("PDF dependencies unavailable; run `uv sync`") from exc

    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    styles = getSampleStyleSheet()
    body_style = ParagraphStyle(
        "ChineseBody",
        parent=styles["BodyText"],
        fontName="STSong-Light",
        fontSize=10.5,
        leading=17,
        alignment=TA_LEFT,
        spaceAfter=7,
    )
    heading_styles = {
        level: ParagraphStyle(
            f"ChineseHeading{level}",
            parent=styles[f"Heading{min(level, 3)}"],
            fontName="STSong-Light",
            fontSize={1: 20, 2: 16, 3: 13}.get(level, 11),
            leading={1: 26, 2: 21, 3: 18}.get(level, 16),
            spaceBefore=10,
            spaceAfter=8,
        )
        for level in range(1, 7)
    }
    code_style = ParagraphStyle(
        "Code",
        parent=styles["Code"],
        fontName="Courier",
        fontSize=8.5,
        leading=11,
        leftIndent=6,
        rightIndent=6,
        spaceBefore=4,
        spaceAfter=8,
    )
    story = []
    paragraph: list[str] = []
    code: list[str] = []
    in_code = False

    def flush_paragraph() -> None:
        if paragraph:
            text = " ".join(line.strip() for line in paragraph)
            story.append(Paragraph(_escape_pdf_text(text), body_style))
            paragraph.clear()

    for line in source.splitlines():
        if line.startswith("```"):
            if in_code:
                story.append(Preformatted("\n".join(code), code_style))
                code.clear()
            else:
                flush_paragraph()
            in_code = not in_code
            continue
        if in_code:
            code.append(line)
            continue
        heading = re.match(r"^(#{1,6})\s+(.*)$", line)
        if heading:
            flush_paragraph()
            level = len(heading.group(1))
            story.append(Paragraph(_escape_pdf_text(heading.group(2)), heading_styles[level]))
        elif not line.strip():
            flush_paragraph()
            story.append(Spacer(1, 2 * mm))
        else:
            paragraph.append(line)
    flush_paragraph()
    if code:
        story.append(Preformatted("\n".join(code), code_style))

    destination.parent.mkdir(parents=True, exist_ok=True)
    document = SimpleDocTemplate(
        str(destination),
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=markdown_path.stem,
        author="Challenge Factory",
    )
    document.build(story)
    if not destination.read_bytes().startswith(b"%PDF"):
        raise PackingError(f"{markdown_path}: PDF renderer produced invalid output")


def _escape_pdf_text(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

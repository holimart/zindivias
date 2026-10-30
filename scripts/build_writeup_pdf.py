#!/usr/bin/env python3
"""Build a deterministic, form-ready PDF from the assembled writeup Markdown.

The PDF is a staged artifact only. This script never submits it or changes operator-owned
identity/form/submission fields.
"""
from __future__ import annotations

import argparse
import html
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MARKDOWN = ROOT / "docs/writeup/final-writeup.md"
DEFAULT_PDF = ROOT / "docs/writeup/final-writeup.pdf"
EXPECTED_REPORTLAB = "4.4.10"
MAX_BYTES = 10_000_000
MIN_PAGES = 5
MAX_PAGES = 80
URL_RE = re.compile(r"https?://[^\s<>\"'`]+")
MD_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)]+)\)")
IMAGE_RE = re.compile(r"^!\[([^\]]*)\]\(([^)]+)\)$")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
TRAILING_PUNCTUATION = ".,;:!?)]}"


def sha256_file(path: Path) -> str:
    import hashlib
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_pdf(path: Path) -> dict[str, int]:
    """Inspect uncompressed PDF object dictionaries without third-party parser dependencies."""
    data = path.read_bytes()
    return {
        "bytes": len(data),
        "page_count": len(re.findall(rb"/Type\s*/Page\b", data)),
        "link_annotations": len(re.findall(rb"/Subtype\s*/Link\b", data)),
        "embedded_images": len(re.findall(rb"/Subtype\s*/Image\b", data)),
    }


def _safe_external_href(url: str) -> str:
    return html.escape(url, quote=True)


def inline_markup(text: str) -> str:
    """Convert a small safe Markdown inline subset to ReportLab Paragraph markup."""
    saved: list[str] = []

    def hold(markup: str) -> str:
        saved.append(markup)
        return f"@@LINK{len(saved) - 1}@@"

    def md_link(match: re.Match[str]) -> str:
        label, url = match.groups()
        return hold(f'<link href="{_safe_external_href(url)}" color="#075985"><u>'
                    f'{html.escape(label)}</u></link>')

    text = MD_LINK_RE.sub(md_link, text)
    text = re.sub(r"`([^`]+)`", lambda m: hold(f"<font name=\"Courier\">{html.escape(m.group(1))}</font>"), text)

    def raw_link(match: re.Match[str]) -> str:
        raw = match.group(0)
        url = raw
        trailing = ""
        while url and url[-1] in TRAILING_PUNCTUATION:
            trailing = url[-1] + trailing
            url = url[:-1]
        if not url:
            return raw
        return hold(f'<link href="{_safe_external_href(url)}" color="#075985"><u>'
                    f'{html.escape(url)}</u></link>') + html.escape(trailing)

    text = URL_RE.sub(raw_link, text)
    text = html.escape(text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<i>\1</i>", text)
    for index, markup in enumerate(saved):
        text = text.replace(f"@@LINK{index}@@", markup)
    return text


def build_pdf(markdown_path: Path = DEFAULT_MARKDOWN,
              output_path: Path = DEFAULT_PDF) -> dict[str, Any]:
    """Render Markdown with cover, generated clickable TOC, links, page numbers and figures."""
    from reportlab import rl_config
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas as rl_canvas
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import (BaseDocTemplate, Frame, Image, KeepTogether,
                                    PageBreak, PageTemplate, Paragraph, Spacer, Table,
                                    TableStyle)
    from reportlab.platypus.tableofcontents import TableOfContents
    import reportlab
    from importlib.metadata import version

    expected_versions = {"reportlab": "4.4.10", "Pillow": "13.0.0",
                         "charset-normalizer": "3.5.2"}
    found_versions = {name: version(name) for name in expected_versions}
    if reportlab.Version != EXPECTED_REPORTLAB or found_versions != expected_versions:
        raise RuntimeError(f"locked PDF toolchain mismatch: expected {expected_versions}, "
                           f"found {found_versions}")
    unicode_font_path = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    if not unicode_font_path.is_file():
        raise RuntimeError(f"Unicode fallback font is unavailable: {unicode_font_path}")
    if "WriteupUnicode" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("WriteupUnicode", str(unicode_font_path)))
    if not markdown_path.is_file():
        raise FileNotFoundError(markdown_path)
    source = markdown_path.read_text(encoding="utf-8")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    rl_config.invariant = 1

    page_width, page_height = A4
    left = right = 18 * mm
    top = 18 * mm
    bottom = 18 * mm
    frame = Frame(left, bottom, page_width - left - right, page_height - top - bottom,
                  leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0, id="body")

    class DeterministicCanvas(rl_canvas.Canvas):
        def __init__(self, filename: str, **kwargs: Any):
            kwargs.pop("invariant", None)
            super().__init__(filename, invariant=1, **kwargs)

    class WriteupDocTemplate(BaseDocTemplate):
        def __init__(self, filename: str):
            super().__init__(filename, pagesize=A4, leftMargin=left, rightMargin=right,
                             topMargin=top, bottomMargin=bottom,
                             title="Bias Bounty Mapping Equity Challenge — methodology writeup",
                             author="[TEAM NAME — OPERATOR TO CONFIRM]",
                             subject="Submission IDs hvLtPVmr and eAaWXWxM",
                             creator="scripts/build_writeup_pdf.py")
            self._heading_no = 0
            self._outline_level = 0
            self.addPageTemplates(PageTemplate(id="writeup", frames=frame,
                                               onPage=self._draw_footer))

        def beforeDocument(self) -> None:
            self._heading_no = 0
            self._outline_level = 0

        @staticmethod
        def _draw_footer(canvas: Any, doc: Any) -> None:
            canvas.saveState()
            canvas.setFont("Helvetica", 8)
            canvas.setFillColor(colors.HexColor("#475569"))
            canvas.drawRightString(page_width - right, 9 * mm, f"Page {doc.page}")
            canvas.restoreState()

        def afterFlowable(self, flowable: Any) -> None:
            if not isinstance(flowable, Paragraph) or not flowable.style.name.startswith("H"):
                return
            try:
                requested = int(flowable.style.name[1:]) - 2
            except ValueError:
                return
            if requested < 0:
                return
            level = min(requested, self._outline_level + 1, 2)
            self._outline_level = level
            self._heading_no += 1
            key = f"writeup-heading-{self._heading_no:03d}"
            title = flowable.getPlainText()
            self.canv.bookmarkPage(key)
            self.canv.addOutlineEntry(title[:120], key, level=level, closed=False)
            self.notify("TOCEntry", (level, title, self.page, key))

    styles = getSampleStyleSheet()
    body = ParagraphStyle("WriteupBody", parent=styles["BodyText"], fontName="Helvetica",
                          fontSize=8.7, leading=11.2, alignment=TA_LEFT, spaceAfter=4,
                          wordWrap="CJK", splitLongWords=1)
    unicode_body = ParagraphStyle("WriteupUnicodeBody", parent=body,
                                  fontName="WriteupUnicode")
    compact = ParagraphStyle("WriteupCompact", parent=body, fontSize=7.0, leading=8.3,
                             spaceAfter=0, wordWrap="CJK", splitLongWords=1)
    code = ParagraphStyle("WriteupCode", parent=body, fontName="Courier", fontSize=6.8,
                          leading=8, leftIndent=7, rightIndent=7, backColor=colors.HexColor("#f1f5f9"),
                          wordWrap="CJK", splitLongWords=1)
    quote = ParagraphStyle("WriteupQuote", parent=body, leftIndent=12,
                           fontName="Helvetica-Oblique", textColor=colors.HexColor("#334155"))
    headings = {}
    for level in range(1, 7):
        headings[level] = ParagraphStyle(
            f"H{level}", parent=styles[f"Heading{min(level, 4)}"], fontName="Helvetica-Bold",
            fontSize=max(17 - 1.6 * level, 9.2), leading=max(20 - 1.7 * level, 11),
            spaceBefore=8 if level > 1 else 12, spaceAfter=4, keepWithNext=True,
            textColor=colors.HexColor("#102a43"))
    cover_title = ParagraphStyle("CoverTitle", parent=styles["Title"], fontName="Helvetica-Bold",
                                 fontSize=24, leading=29, alignment=TA_CENTER,
                                 textColor=colors.HexColor("#102a43"), spaceAfter=16)
    cover_subtitle = ParagraphStyle("CoverSubtitle", parent=body, fontSize=14, leading=18,
                                    alignment=TA_CENTER, textColor=colors.HexColor("#334155"))
    cover_text = ParagraphStyle("CoverText", parent=body, fontSize=10, leading=14,
                                alignment=TA_CENTER, textColor=colors.HexColor("#334155"))
    toc_title = ParagraphStyle("TOCTitle", parent=cover_title, fontSize=19, leading=23,
                               alignment=TA_LEFT, spaceAfter=12)
    toc_styles = [ParagraphStyle("TOCLevel0", fontName="Helvetica", fontSize=9.0,
                                 leading=12, leftIndent=0, firstLineIndent=0, spaceAfter=2),
                  ParagraphStyle("TOCLevel1", fontName="Helvetica", fontSize=8.3,
                                 leading=10.4, leftIndent=12, firstLineIndent=0, spaceAfter=1),
                  ParagraphStyle("TOCLevel2", fontName="Helvetica", fontSize=7.8,
                                 leading=9.6, leftIndent=24, firstLineIndent=0, spaceAfter=0)]

    toc = TableOfContents()
    toc.levelStyles = toc_styles
    toc.dotsMinLevel = 0
    story: list[Any] = [Spacer(1, 38 * mm),
                        Paragraph("Bias Bounty Mapping Equity Challenge", cover_title),
                        Paragraph("Discovery and methodology writeup", cover_subtitle),
                        Spacer(1, 12 * mm),
                        Paragraph("Team: [TEAM NAME — OPERATOR TO CONFIRM]", cover_text),
                        Spacer(1, 5 * mm),
                        Paragraph("Protocol A: V2_model_cgs — Zindi submission hvLtPVmr", cover_text),
                        Paragraph("Protocol B: B_from_A_cgs — Zindi submission eAaWXWxM", cover_text),
                        Spacer(1, 11 * mm),
                        Paragraph("Form upload: PDF, maximum 10 MB (Zindi discussion #35334, 2026-10-14).",
                                  cover_text),
                        PageBreak(), Paragraph("Contents", toc_title), toc, PageBreak()]

    def make_table(rows: list[list[str]]) -> Table:
        cell_rows = [[Paragraph(inline_markup(cell), compact) for cell in row] for row in rows]
        columns = max(map(len, cell_rows))
        width = (page_width - left - right) / columns
        table = Table(cell_rows, colWidths=[width] * columns, repeatRows=1, hAlign="LEFT")
        table.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#94a3b8")),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e2e8f0")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 3),
            ("RIGHTPADDING", (0, 0), (-1, -1), 3),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ]))
        return table

    lines = source.splitlines()
    index = 0
    skipped_contents = False
    skipped_document_title = False
    while index < len(lines):
        raw = lines[index]
        stripped = raw.strip()
        if not stripped:
            index += 1
            continue
        heading = HEADING_RE.match(stripped)
        if heading and len(heading.group(1)) == 1 and not skipped_document_title:
            skipped_document_title = True
            index += 1
            continue
        if heading and heading.group(2).strip().lower() == "contents" and not skipped_contents:
            skipped_contents = True
            index += 1
            while index < len(lines):
                if HEADING_RE.match(lines[index].strip()):
                    break
                index += 1
            continue
        if stripped.startswith("```"):
            index += 1
            code_lines = []
            while index < len(lines) and not lines[index].strip().startswith("```"):
                code_lines.append(html.escape(lines[index].rstrip()))
                index += 1
            index += 1
            story.extend([Paragraph("<br/>".join(code_lines) or "&nbsp;", code), Spacer(1, 4)])
            continue
        image_match = IMAGE_RE.match(stripped)
        if image_match:
            alt, target = image_match.groups()
            image_path = (markdown_path.parent / target).resolve()
            if not image_path.is_file():
                image_path = (ROOT / "docs/writeup" / target).resolve()
            if not image_path.is_file():
                raise FileNotFoundError(f"writeup image missing: {image_path} ({alt})")
            graphic = Image(str(image_path), hAlign="CENTER")
            scale = min((page_width - left - right) / graphic.imageWidth,
                        145 * mm / graphic.imageHeight, 1.0)
            graphic.drawWidth = graphic.imageWidth * scale
            graphic.drawHeight = graphic.imageHeight * scale
            story.extend([KeepTogether([graphic, Spacer(1, 5)])])
            index += 1
            continue
        if heading:
            level = len(heading.group(1))
            story.append(Paragraph(inline_markup(heading.group(2)), headings[level]))
            index += 1
            continue
        if stripped.startswith("|"):
            rows = []
            while index < len(lines) and lines[index].strip().startswith("|"):
                cells = [cell.strip() for cell in lines[index].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-{2,}:?", cell or "-") for cell in cells):
                    rows.append(cells)
                index += 1
            if rows:
                story.extend([make_table(rows), Spacer(1, 5)])
            continue
        if stripped in {"---", "***", "___"}:
            story.extend([Spacer(1, 5)])
            index += 1
            continue
        if stripped.startswith("> "):
            story.append(Paragraph(inline_markup(stripped[2:]), quote))
            index += 1
            continue
        bullet = re.match(r"^(\s*)([-*]|\d+[.)])\s+(.*)$", raw)
        if bullet:
            indent = min(len(bullet.group(1)) * 4, 24)
            style = ParagraphStyle(f"bullet-{index}", parent=body, leftIndent=indent + 10,
                                   firstLineIndent=-8)
            marker = "•" if bullet.group(2) in {"-", "*"} else f"{bullet.group(2)}"
            story.append(Paragraph(f"{marker} {inline_markup(bullet.group(3))}", style))
            index += 1
            continue
        try:
            stripped.encode("cp1252")
            paragraph_style = body
        except UnicodeEncodeError:
            paragraph_style = unicode_body
        story.append(Paragraph(inline_markup(stripped), paragraph_style))
        index += 1

    doc = WriteupDocTemplate(str(output_path))
    doc.multiBuild(story, maxPasses=8, canvasmaker=DeterministicCanvas)
    stats = inspect_pdf(output_path)
    if stats["bytes"] >= MAX_BYTES:
        raise ValueError(f"PDF exceeds 10 MB: {stats['bytes']} bytes")
    if not MIN_PAGES <= stats["page_count"] <= MAX_PAGES:
        raise ValueError(f"PDF page count outside [{MIN_PAGES}, {MAX_PAGES}]: {stats['page_count']}")
    if stats["link_annotations"] < 1:
        raise ValueError("PDF contains no clickable links")
    if stats["embedded_images"] < 1:
        raise ValueError("PDF contains no embedded figures")
    return {**stats, "sha256": sha256_file(output_path), "path": str(output_path),
            "reportlab_version": reportlab.Version,
            "toolchain_versions": found_versions}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--markdown", type=Path, default=DEFAULT_MARKDOWN)
    parser.add_argument("--pdf", type=Path, default=DEFAULT_PDF)
    parser.add_argument("--check-only", action="store_true",
                        help="inspect an existing PDF without rebuilding it")
    args = parser.parse_args()
    if args.check_only:
        stats = inspect_pdf(args.pdf)
        if not MIN_PAGES <= stats["page_count"] <= MAX_PAGES:
            raise SystemExit(f"PDF page count outside [{MIN_PAGES}, {MAX_PAGES}]")
        if stats["bytes"] >= MAX_BYTES or not stats["link_annotations"] or not stats["embedded_images"]:
            raise SystemExit("PDF size/link/image acceptance failed")
        stats.update({"sha256": sha256_file(args.pdf), "path": str(args.pdf)})
    else:
        stats = build_pdf(args.markdown, args.pdf)
    import json
    print(json.dumps(stats, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())

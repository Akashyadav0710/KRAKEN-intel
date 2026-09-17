"""
KRAKEN -- CASE FILE PDF
=======================
Registered FIR ka printable record. reportlab se banta hai, evidence images
seedhe document mein embed hoti hain.

fir_registry.register_fir() isko call karta hai. Agar reportlab missing ho to
wahan exception pakad liya jaata hai -- case bina PDF ke bhi register rehta hai.
"""

import os
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (
    Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

INK = colors.HexColor("#101418")
ACCENT = colors.HexColor("#00626e")
MUTED = colors.HexColor("#5b6b6d")
RULE = colors.HexColor("#c2ced0")
ALERT = colors.HexColor("#8c1d18")

PAGE_W, PAGE_H = A4
MARGIN = 18 * mm
CONTENT_W = PAGE_W - 2 * MARGIN


def _styles():
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "kTitle", parent=base["Title"], fontName="Helvetica-Bold",
            fontSize=16, leading=19, textColor=INK, alignment=TA_CENTER, spaceAfter=2),
        "sub": ParagraphStyle(
            "kSub", parent=base["Normal"], fontName="Helvetica", fontSize=8.5,
            leading=11, textColor=MUTED, alignment=TA_CENTER, spaceAfter=10),
        "h2": ParagraphStyle(
            "kH2", parent=base["Heading2"], fontName="Helvetica-Bold", fontSize=9.5,
            leading=12, textColor=ACCENT, spaceBefore=12, spaceAfter=5),
        "body": ParagraphStyle(
            "kBody", parent=base["Normal"], fontName="Helvetica", fontSize=9.5,
            leading=14, textColor=INK, spaceAfter=5),
        "mono": ParagraphStyle(
            "kMono", parent=base["Normal"], fontName="Courier", fontSize=8.5,
            leading=12, textColor=INK),
        "cell": ParagraphStyle(
            "kCell", parent=base["Normal"], fontName="Helvetica", fontSize=8.5,
            leading=11.5, textColor=INK),
        "alert": ParagraphStyle(
            "kAlert", parent=base["Normal"], fontName="Helvetica-Bold", fontSize=8.5,
            leading=12, textColor=ALERT),
        "cap": ParagraphStyle(
            "kCap", parent=base["Normal"], fontName="Helvetica-Oblique", fontSize=8,
            leading=10, textColor=MUTED, spaceBefore=3, spaceAfter=10),
    }


def _esc(value):
    """Paragraph XML-parse karta hai, isliye escape zaroori hai."""
    return (str(value if value is not None else "")
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _rule(space_before=0):
    table = Table([[""]], colWidths=[CONTENT_W], rowHeights=[0.6])
    table.setStyle(TableStyle([("LINEABOVE", (0, 0), (-1, -1), 0.6, RULE)]))
    if space_before:
        return [Spacer(1, space_before), table]
    return [table]


def _kv_table(rows):
    """Do-column label/value table."""
    data = [[Paragraph("<b>%s</b>" % _esc(k), _styles()["cell"]),
             Paragraph(_esc(v) or "&mdash;", _styles()["cell"])] for k, v in rows]
    table = Table(data, colWidths=[38 * mm, CONTENT_W - 38 * mm])
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("LINEBELOW", (0, 0), (-1, -2), 0.4, colors.HexColor("#e6ecec")),
    ]))
    return table


def _entity_table(extracted):
    groups = [
        ("Persons", extracted.get("persons")),
        ("Phone numbers", extracted.get("phones")),
        ("Bank accounts", extracted.get("accounts")),
        ("Locations", extracted.get("locations")),
        ("Vehicles", extracted.get("vehicles")),
        ("IPC sections", extracted.get("sections")),
        ("Dates cited", extracted.get("dates")),
        ("Organisations", extracted.get("organisations")),
    ]
    rows = [(label, ", ".join(values)) for label, values in groups if values]
    if not rows:
        return None
    return _kv_table(rows)


def _scaled_image(path, max_w, max_h):
    """Aspect ratio bachate hue image ko box ke andar fit karo."""
    try:
        reader = ImageReader(path)
        iw, ih = reader.getSize()
    except Exception:
        return None
    if not iw or not ih:
        return None
    scale = min(max_w / float(iw), max_h / float(ih), 1.0)
    return Image(path, width=iw * scale, height=ih * scale)


def _header_footer(canvas, doc, case_no):
    canvas.saveState()
    canvas.setFont("Helvetica-Bold", 7)
    canvas.setFillColor(ACCENT)
    canvas.drawString(MARGIN, PAGE_H - 12 * mm, "KRAKEN // CASE FILE")
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(MUTED)
    canvas.drawRightString(PAGE_W - MARGIN, PAGE_H - 12 * mm, case_no)
    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(0.6)
    canvas.line(MARGIN, PAGE_H - 14 * mm, PAGE_W - MARGIN, PAGE_H - 14 * mm)

    canvas.line(MARGIN, 14 * mm, PAGE_W - MARGIN, 14 * mm)
    canvas.setFont("Helvetica", 6.5)
    canvas.drawString(MARGIN, 10 * mm,
                      "Generated by KRAKEN intake console — not a substitute for the statutory FIR record.")
    canvas.drawRightString(PAGE_W - MARGIN, 10 * mm, "Page %d" % doc.page)
    canvas.restoreState()


def build_case_pdf(case, evidence_dir, out_dir):
    """Case dict -> PDF file. Wapas sirf filename deta hai."""
    os.makedirs(out_dir, exist_ok=True)
    st = _styles()
    case_no = case.get("case_no") or "KRK-UNKNOWN"
    filename = "%s.pdf" % case_no
    path = os.path.join(out_dir, filename)

    story = []

    # ---- heading ----
    story.append(Paragraph("FIRST INFORMATION REPORT", st["title"]))
    story.append(Paragraph(
        "KRAKEN INTELLIGENCE PLATFORM &middot; AUTO-GENERATED CASE RECORD", st["sub"]))

    registered = case.get("registered_at") or ""
    try:
        registered = datetime.fromisoformat(registered).strftime("%d %b %Y, %H:%M")
    except (ValueError, TypeError):
        pass

    story.append(_kv_table([
        ("Case number", case_no),
        ("FIR number", case.get("fir_number") or "Not stated in report"),
        ("Classification", case.get("title")),
        ("Registered", registered),
        ("Registering officer", case.get("officer")),
        ("Intake mode", (case.get("mode") or "passage").upper()),
    ]))

    # ---- form fields (agar form mode tha) ----
    form = case.get("form") or {}
    filled = [(k.replace("_", " ").title(), v) for k, v in form.items()
              if v and k != "narrative"]
    if filled:
        story.append(Paragraph("STRUCTURED PARTICULARS", st["h2"]))
        story += _rule()
        story.append(Spacer(1, 5))
        story.append(_kv_table(filled))

    # ---- narrative ----
    story.append(Paragraph("STATEMENT OF THE INFORMANT", st["h2"]))
    story += _rule()
    story.append(Spacer(1, 5))
    for block in (case.get("narrative") or "").split("\n"):
        if block.strip():
            story.append(Paragraph(_esc(block), st["body"]))
        else:
            story.append(Spacer(1, 5))

    # ---- machine extraction ----
    table = _entity_table(case.get("extracted") or {})
    if table is not None:
        story.append(Paragraph("ENTITIES EXTRACTED (NER ENGINE)", st["h2"]))
        story += _rule()
        story.append(Spacer(1, 5))
        story.append(table)

    ownership = (case.get("extracted") or {}).get("ownership") or []
    relationships = (case.get("extracted") or {}).get("relationships") or []
    if ownership or relationships:
        story.append(Paragraph("LINKS INFERRED", st["h2"]))
        story += _rule()
        story.append(Spacer(1, 5))
        for link in relationships:
            story.append(Paragraph(
                "&bull; %s &mdash; <i>%s</i> &mdash; %s"
                % (_esc(link.get("person_a")), _esc(link.get("type")), _esc(link.get("person_b"))),
                st["cell"]))
        for link in ownership:
            label = "registered SIM" if link.get("type") == "OWNS_PHONE" else "bank account"
            story.append(Paragraph(
                "&bull; %s &mdash; <i>%s</i> &mdash; %s"
                % (_esc(link.get("person")), label, _esc(link.get("value"))),
                st["cell"]))

    # ---- cross-match / clues ----
    matches = case.get("matches") or []
    story.append(Paragraph("CROSS-MATCH AGAINST CASE DATABASE", st["h2"]))
    story += _rule()
    story.append(Spacer(1, 5))
    if not matches:
        story.append(Paragraph(
            "No subject in this report matches an existing record. "
            "All entities were filed as new.", st["cell"]))
    else:
        for match in matches:
            prior = ", ".join(p["case_no"] for p in match.get("prior_cases") or [])
            where = []
            if match.get("in_graph"):
                where.append("already in the live intelligence graph")
            if prior:
                where.append("previously named in %s" % prior)
            story.append(Paragraph(
                "&bull; <b>%s</b> (%s) &mdash; %s."
                % (_esc(match.get("value")), _esc(match.get("kind")),
                   _esc("; ".join(where) or "matched")),
                st["alert"]))

    # ---- evidence ----
    evidence = case.get("evidence") or []
    if evidence:
        story.append(PageBreak())
        story.append(Paragraph("ANNEXURE &mdash; EVIDENCE", st["h2"]))
        story += _rule()
        story.append(Spacer(1, 8))
        for index, item in enumerate(evidence, start=1):
            img_path = os.path.join(evidence_dir, item.get("file") or "")
            if not os.path.exists(img_path):
                continue
            picture = _scaled_image(img_path, CONTENT_W, 150 * mm)
            if picture is None:
                continue
            story.append(picture)
            story.append(Paragraph(
                "Exhibit %d &mdash; %s" % (index, _esc(item.get("caption"))), st["cap"]))

    # ---- signature block ----
    story.append(Spacer(1, 18))
    story += _rule()
    story.append(Spacer(1, 4))
    story.append(Paragraph(
        "Entities above were extracted automatically from the informant's statement "
        "and merged into the live graph at the time of registration. "
        "Verify every machine-inferred link before acting on it.", st["cap"]))

    doc = SimpleDocTemplate(
        path, pagesize=A4,
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=20 * mm, bottomMargin=20 * mm,
        title="%s — FIR" % case_no, author="KRAKEN")

    draw = lambda canvas, d: _header_footer(canvas, d, case_no)
    doc.build(story, onFirstPage=draw, onLaterPages=draw)
    return filename

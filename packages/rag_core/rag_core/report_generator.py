"""Enterprise PDF report generation (reportlab Platypus).

reportlab is pure Python with no system dependencies — WeasyPrint would give
better HTML fidelity but needs Cairo/Pango (~80 MB), unnecessary on the Droplet.
See ``docs/ARCHITECTURE.md``.

Two reports:
    * single-document audit (cover, summary, clauses, optional cross-reference)
    * portfolio summary (cover, overview + bar chart, renewal alerts, inventory)

Both return raw PDF ``bytes``; the API router streams them. Nothing touches the
filesystem.
"""

from __future__ import annotations

import io
from datetime import UTC, date, datetime
from typing import Any

from reportlab.graphics.shapes import Drawing, Rect, String
from reportlab.lib import colors
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import (
    Flowable,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from rag_core.schemas import ContractAuditSchema, CriticalClause, RiskBand
from rag_core.schemas_xref import CrossReferenceAuditSchema, DeviationType

BRAND_COLOR = HexColor("#1B2A4A")
ACCENT_COLOR = HexColor("#E8F0FE")
RISK_RED = HexColor("#DC2626")
RISK_AMBER = HexColor("#D97706")
RISK_GREEN = HexColor("#16A34A")

_RED_TINT = HexColor("#FDE8E8")
_AMBER_TINT = HexColor("#FEF3E2")
_GREY_TINT = HexColor("#EEF1F5")

_ANNOTATION_GREY = HexColor("#6B7A99")
_ANNOTATION_BLUE = HexColor("#4F8CFF")
#: Annotation-type → accent colour (left border), shared with the web UI.
_ANNOTATION_COLORS = {
    "accepted_risk": RISK_GREEN,
    "escalate_to_legal": RISK_RED,
    "disputed": RISK_AMBER,
    "requires_negotiation": RISK_AMBER,
    "false_positive": _ANNOTATION_GREY,
    "custom": _ANNOTATION_BLUE,
}

_PAGE_W, _PAGE_H = A4


def _risk_color(score: int) -> HexColor:
    """Map a 1-10 risk score to its band colour."""
    band = RiskBand.from_score(score)
    if band is RiskBand.LOW:
        return RISK_GREEN
    if band is RiskBand.MEDIUM:
        return RISK_AMBER
    return RISK_RED


def _fmt_date(value: Any) -> str:
    """Format an ISO date/`date` as e.g. ``15 Jan 2026``; '—' when missing."""
    if not value:
        return "—"
    parsed = value if isinstance(value, date) else None
    if parsed is None:
        try:
            parsed = date.fromisoformat(str(value)[:10])
        except ValueError:
            return str(value)
    return parsed.strftime("%d %b %Y")


class _NumberedCanvas(Canvas):  # type: ignore[misc] # reportlab ships no stubs
    """Canvas that stamps 'Page X of Y' + CONFIDENTIAL on every page but the cover."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._saved_states: list[dict[str, Any]] = []

    def showPage(self) -> None:  # noqa: N802 - reportlab API name
        self._saved_states.append(dict(self.__dict__))
        self._startPage()

    def save(self) -> None:
        total = len(self._saved_states)
        for index, state in enumerate(self._saved_states, start=1):
            self.__dict__.update(state)
            if index > 1:  # skip the cover page
                self._draw_footer(index, total)
            super().showPage()
        super().save()

    def _draw_footer(self, page: int, total: int) -> None:
        self.setStrokeColor(colors.grey)
        self.setLineWidth(0.5)
        self.line(20 * mm, 14 * mm, _PAGE_W - 20 * mm, 14 * mm)
        self.setFont("Helvetica", 8)
        self.setFillColor(colors.grey)
        self.drawCentredString(_PAGE_W / 2, 9 * mm, "CONFIDENTIAL")
        self.drawRightString(_PAGE_W - 20 * mm, 9 * mm, f"Page {page} of {total}")


class _RiskBars(Flowable):  # type: ignore[misc] # reportlab ships no stubs
    """A small horizontal bar chart of the low/medium/high risk distribution."""

    def __init__(self, distribution: dict[str, int], width: float = 150 * mm) -> None:
        super().__init__()
        self._dist = distribution
        self.width = width
        self.height = 38 * mm

    def draw(self) -> None:  # noqa: D102 - reportlab API
        drawing = Drawing(self.width, self.height)
        rows = [
            ("Low", self._dist.get("low", 0), RISK_GREEN),
            ("Medium", self._dist.get("medium", 0), RISK_AMBER),
            ("High", self._dist.get("high", 0), RISK_RED),
        ]
        peak = max((count for _, count, _ in rows), default=0) or 1
        bar_area = self.width - 45 * mm
        for i, (label, count, color) in enumerate(rows):
            y = self.height - (i + 1) * 11 * mm
            drawing.add(String(0, y + 2, label, fontSize=9, fillColor=colors.black))
            length = bar_area * (count / peak)
            drawing.add(
                Rect(28 * mm, y, max(length, 0.5), 7 * mm, fillColor=color, strokeColor=None)
            )
            drawing.add(
                String(28 * mm + length + 2 * mm, y + 2, str(count), fontSize=9)
            )
        drawing.drawOn(self.canv, 0, 0)


class ReportGenerator:
    """Builds branded single-document and portfolio audit PDFs."""

    def __init__(self) -> None:
        """Initialise shared paragraph styles."""
        base = getSampleStyleSheet()
        self._body = base["BodyText"]
        self._h1 = ParagraphStyle(
            "SectionHeader",
            parent=base["Heading1"],
            textColor=BRAND_COLOR,
            fontSize=16,
            spaceAfter=8,
        )
        self._h2 = ParagraphStyle(
            "SubHeader", parent=base["Heading2"], textColor=BRAND_COLOR, fontSize=12
        )
        self._small_grey = ParagraphStyle(
            "SmallGrey", parent=base["BodyText"], fontSize=8, textColor=colors.grey
        )
        self._cell = ParagraphStyle("Cell", parent=base["BodyText"], fontSize=8)

    # --- Single document -----------------------------------------------------

    def generate_single_document_report(
        self,
        audit: ContractAuditSchema,
        document_id: str,
        crossref: CrossReferenceAuditSchema | None,
        annotations: list[dict[str, Any]],
        tenant_id: str,
    ) -> bytes:
        """Generate a single-contract audit PDF.

        Args:
            audit: The structured audit.
            document_id: The audited document id.
            crossref: Optional cross-reference result to append.
            annotations: Human annotations (document / clause / deviation level).
            tenant_id: Owning tenant (shown for traceability).

        Returns:
            Raw PDF bytes.
        """
        doc_notes, by_chunk, by_deviation = _split_annotations(annotations)
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            topMargin=22 * mm,
            bottomMargin=22 * mm,
            leftMargin=20 * mm,
            rightMargin=20 * mm,
            title="Contract Audit Report",
        )
        story: list[Flowable] = [PageBreak()]  # page 1 is the canvas-drawn cover
        story += self._executive_summary(audit, doc_notes)
        story += self._critical_clauses(audit.critical_clauses, by_chunk)
        if crossref is not None:
            story += self._crossref_section(crossref, by_deviation)

        def cover(canvas: Canvas, _doc: SimpleDocTemplate) -> None:
            self._draw_cover(
                canvas,
                title="CONTRACT AUDIT REPORT",
                subtitle=audit.vendor_name,
                lines=[
                    f"Contract type: {audit.contract_type}",
                    f"Audit date: {_fmt_date(datetime.now(UTC).date())}",
                    f"Document: {document_id}",
                ],
                risk_score=audit.risk_score,
            )

        doc.build(story, onFirstPage=cover, canvasmaker=_NumberedCanvas)
        return buffer.getvalue()

    def _executive_summary(
        self, audit: ContractAuditSchema, doc_notes: list[dict[str, Any]]
    ) -> list[Flowable]:
        """Build the executive-summary flowables (+ document reviewer notes)."""
        risk_bg = _risk_color(audit.risk_score)
        info = [
            ["Vendor", audit.vendor_name],
            ["Contract Type", audit.contract_type],
            ["Auto-Renewal", "Yes" if audit.auto_renewal else "No"],
            ["Notice Period", f"{audit.notice_period_days} days"],
            ["Contract End Date", _fmt_date(audit.contract_end_date)],
            ["Risk Score", f"{audit.risk_score} / 10"],
        ]
        table = Table(info, colWidths=[45 * mm, 110 * mm])
        table.setStyle(
            TableStyle(
                [
                    ("FONTSIZE", (0, 0), (-1, -1), 10),
                    ("TEXTCOLOR", (0, 0), (0, -1), BRAND_COLOR),
                    ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                    # Risk score value cell coloured by tier.
                    ("BACKGROUND", (1, 5), (1, 5), risk_bg),
                    ("TEXTCOLOR", (1, 5), (1, 5), colors.white),
                ]
            )
        )
        out: list[Flowable] = [
            Paragraph("EXECUTIVE SUMMARY", self._h1),
            table,
            Spacer(1, 8 * mm),
        ]
        if doc_notes:
            out.append(Paragraph("REVIEWER NOTES", self._h2))
            for note in doc_notes:
                out.append(self._annotation_box(note))
                out.append(Spacer(1, 3 * mm))
            out.append(Spacer(1, 3 * mm))
        out.append(Paragraph(audit.risk_rationale, self._body))
        out.append(PageBreak())
        return out

    def _critical_clauses(
        self,
        clauses: list[CriticalClause],
        by_chunk: dict[str, list[dict[str, Any]]],
    ) -> list[Flowable]:
        """Build the critical-clauses flowables (+ inline clause annotations)."""
        out: list[Flowable] = [Paragraph("CRITICAL CLAUSES", self._h1)]
        if not clauses:
            out.append(Paragraph("No critical clauses identified.", self._body))
            return out
        for clause in clauses:
            page = clause.page_number if clause.page_number is not None else "?"
            provenance = f"Page {page}  ·  Chunk {clause.source_chunk_id}"
            box = Table(
                [
                    [Paragraph(provenance, self._small_grey)],
                    [Paragraph(clause.text, self._body)],
                ],
                colWidths=[165 * mm],
            )
            box.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, -1), ACCENT_COLOR),
                        ("BOX", (0, 0), (-1, -1), 0.5, colors.lightgrey),
                        ("LEFTPADDING", (0, 0), (-1, -1), 8),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                        ("TOPPADDING", (0, 0), (-1, -1), 6),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                    ]
                )
            )
            out.append(box)
            for note in by_chunk.get(clause.source_chunk_id, []):
                out.append(self._annotation_box(note))
            out.append(Spacer(1, 4 * mm))
        return out

    def _crossref_section(
        self,
        crossref: CrossReferenceAuditSchema,
        by_deviation: dict[str, list[dict[str, Any]]],
    ) -> list[Flowable]:
        """Build the cross-reference findings flowables (+ deviation notes)."""
        out: list[Flowable] = [
            PageBreak(),
            Paragraph("CROSS-REFERENCE AUDIT", self._h1),
            Paragraph(f"Against standard: {crossref.standard_version}", self._h2),
            Spacer(1, 3 * mm),
            Paragraph(
                f"Overall cross-reference risk: "
                f"<b>{crossref.overall_risk_score}/10</b>",
                self._body,
            ),
            Spacer(1, 3 * mm),
            Paragraph(crossref.executive_summary, self._body),
            Spacer(1, 5 * mm),
        ]

        header = ["Clause Type", "Deviation", "Severity", "Explanation"]
        data: list[list[Any]] = [header]
        style: list[tuple[Any, ...]] = [
            ("BACKGROUND", (0, 0), (-1, 0), BRAND_COLOR),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
        ]
        for dev in crossref.deviations:
            data.append(
                [
                    Paragraph(dev.clause_type, self._cell),
                    Paragraph(dev.deviation_type.value, self._cell),
                    str(dev.severity),
                    Paragraph(dev.explanation, self._cell),
                ]
            )
            row = len(data) - 1
            style.append(("BACKGROUND", (0, row), (-1, row), _tint_for(dev.deviation_type)))
            if dev.subject_text and dev.standard_text:
                data.append(
                    [
                        Paragraph(f"<b>CONTRACT</b><br/>{dev.subject_text[:300]}", self._cell),
                        "",
                        Paragraph(f"<b>STANDARD</b><br/>{dev.standard_text[:300]}", self._cell),
                        "",
                    ]
                )
                cmp_row = len(data) - 1
                style.append(("SPAN", (0, cmp_row), (1, cmp_row)))
                style.append(("SPAN", (2, cmp_row), (3, cmp_row)))
                style.append(("BACKGROUND", (0, cmp_row), (-1, cmp_row), colors.whitesmoke))
            for note in by_deviation.get(dev.deviation_id or "", []):
                label = note["annotation_type"].replace("_", " ").upper()
                data.append(
                    [
                        Paragraph(
                            f"NOTE [{label}]: {note['note']} "
                            f"<i>— {note['actor']}</i>",
                            self._cell,
                        ),
                        "",
                        "",
                        "",
                    ]
                )
                note_row = len(data) - 1
                style.append(("SPAN", (0, note_row), (-1, note_row)))
                style.append(
                    ("LINEBEFORE", (0, note_row), (0, note_row), 3,
                     _annotation_color(note["annotation_type"]))
                )
                style.append(
                    ("BACKGROUND", (0, note_row), (-1, note_row), colors.white)
                )

        table = Table(data, colWidths=[32 * mm, 24 * mm, 16 * mm, 93 * mm], repeatRows=1)
        table.setStyle(TableStyle(style))
        out.append(table)
        return out

    def _annotation_box(self, annotation: dict[str, Any]) -> Table:
        """Render one annotation as a shaded, colour-bordered box."""
        label = annotation["annotation_type"].replace("_", " ").upper()
        recorded = (
            f"Recorded by {annotation['actor']} on "
            f"{_fmt_date(annotation.get('created_at'))}"
        )
        box = Table(
            [
                [Paragraph(f"<b>{label}</b>", self._cell)],
                [Paragraph(annotation["note"], self._body)],
                [Paragraph(recorded, self._small_grey)],
            ],
            colWidths=[160 * mm],
        )
        box.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), ACCENT_COLOR),
                    ("LINEBEFORE", (0, 0), (0, -1), 3,
                     _annotation_color(annotation["annotation_type"])),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        return box

    # --- Portfolio -----------------------------------------------------------

    def generate_portfolio_report(
        self,
        audits: list[dict[str, Any]],
        summary: dict[str, Any],
        tenant_id: str,
        generated_at: datetime,
    ) -> bytes:
        """Generate a portfolio-wide PDF report.

        Args:
            audits: All audit-result rows for the tenant.
            summary: Dashboard summary dict.
            tenant_id: Owning tenant.
            generated_at: Report timestamp.

        Returns:
            Raw PDF bytes.
        """
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            topMargin=22 * mm,
            bottomMargin=22 * mm,
            leftMargin=20 * mm,
            rightMargin=20 * mm,
            title="Contract Portfolio Report",
        )
        story: list[Flowable] = [PageBreak()]
        story += self._portfolio_overview(summary)
        story += self._renewal_summary(audits)
        story += self._inventory(audits)

        def cover(canvas: Canvas, _doc: SimpleDocTemplate) -> None:
            self._draw_cover(
                canvas,
                title="CONTRACT PORTFOLIO REPORT",
                subtitle="Full Audit Summary",
                lines=[
                    f"Generated: {_fmt_date(generated_at.date())}",
                    f"Total contracts: {summary.get('total_contracts', len(audits))}",
                    f"Tenant: {tenant_id}",
                ],
                risk_score=None,
            )

        doc.build(story, onFirstPage=cover, canvasmaker=_NumberedCanvas)
        return buffer.getvalue()

    def _portfolio_overview(self, summary: dict[str, Any]) -> list[Flowable]:
        """Build the portfolio overview (stat grid + bar chart)."""
        dist = summary.get("risk_distribution", {})
        grid = [
            ["Total Contracts", str(summary.get("total_contracts", 0))],
            ["Avg Risk Score", str(summary.get("avg_risk_score", 0))],
            ["High Risk Contracts", str(dist.get("high", 0))],
            ["With Auto-Renewal", str(summary.get("contracts_with_autorenewal", 0))],
            ["Expiring Within 60 Days", str(summary.get("contracts_expiring_soon", 0))],
        ]
        table = Table(grid, colWidths=[80 * mm, 75 * mm])
        table.setStyle(
            TableStyle(
                [
                    ("FONTSIZE", (0, 0), (-1, -1), 10),
                    ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                    ("TEXTCOLOR", (0, 0), (0, -1), BRAND_COLOR),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ]
            )
        )
        return [
            Paragraph("PORTFOLIO OVERVIEW", self._h1),
            table,
            Spacer(1, 8 * mm),
            Paragraph("Risk distribution", self._h2),
            _RiskBars(dist),
            PageBreak(),
        ]

    def _renewal_summary(self, audits: list[dict[str, Any]]) -> list[Flowable]:
        """Build the renewal/SLA alert summary (30/60/90 day windows)."""
        out: list[Flowable] = [Paragraph("RENEWAL & SLA ALERTS", self._h1)]
        today = date.today()
        windows = [("Next 30 Days", 0, 30), ("31-60 Days", 30, 60), ("61-90 Days", 60, 90)]
        for label, low, high in windows:
            out.append(Paragraph(label, self._h2))
            rows = [
                a
                for a in audits
                if a.get("auto_renewal")
                and (d := _safe_date(a.get("contract_end_date"))) is not None
                and low < (d - today).days <= high
            ]
            if not rows:
                out.append(Paragraph("No contracts in this window.", self._small_grey))
            else:
                out.append(self._alert_table(rows))
            out.append(Spacer(1, 5 * mm))
        out.append(PageBreak())
        return out

    def _alert_table(self, rows: list[dict[str, Any]]) -> Table:
        """Build a renewal-alert table flowable."""
        data: list[list[Any]] = [
            ["Vendor", "End Date", "Notice", "Auto-Renew", "Risk"]
        ]
        for r in rows:
            data.append(
                [
                    Paragraph(r["vendor_name"], self._cell),
                    _fmt_date(r.get("contract_end_date")),
                    f"{r.get('notice_period_days', '—')}",
                    "Yes" if r.get("auto_renewal") else "No",
                    str(r["risk_score"]),
                ]
            )
        table = Table(data, colWidths=[60 * mm, 30 * mm, 25 * mm, 25 * mm, 20 * mm], repeatRows=1)
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), BRAND_COLOR),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
                ]
            )
        )
        return table

    def _inventory(self, audits: list[dict[str, Any]]) -> list[Flowable]:
        """Build the full contract inventory table (risk-sorted)."""
        ordered = sorted(audits, key=lambda a: a["risk_score"], reverse=True)
        data: list[list[Any]] = [
            ["#", "Vendor", "Type", "Risk", "Auto-Renew", "End Date", "Status"]
        ]
        style: list[tuple[Any, ...]] = [
            ("BACKGROUND", (0, 0), (-1, 0), BRAND_COLOR),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]
        for i, a in enumerate(ordered, start=1):
            row = len(data)
            data.append(
                [
                    str(i),
                    Paragraph(a["vendor_name"], self._cell),
                    Paragraph(a["contract_type"], self._cell),
                    str(a["risk_score"]),
                    "Yes" if a.get("auto_renewal") else "No",
                    _fmt_date(a.get("contract_end_date")),
                    a.get("status", "audited"),
                ]
            )
            style.append(("BACKGROUND", (3, row), (3, row), _risk_color(a["risk_score"])))
            style.append(("TEXTCOLOR", (3, row), (3, row), colors.white))
            if i % 2 == 0:
                style.append(("BACKGROUND", (0, row), (2, row), ACCENT_COLOR))
                style.append(("BACKGROUND", (4, row), (-1, row), ACCENT_COLOR))
        table = Table(
            data,
            colWidths=[10 * mm, 50 * mm, 28 * mm, 15 * mm, 25 * mm, 27 * mm, 20 * mm],
            repeatRows=1,
        )
        table.setStyle(TableStyle(style))
        return [Paragraph("CONTRACT INVENTORY", self._h1), table]

    # --- Shared cover --------------------------------------------------------

    def _draw_cover(
        self,
        canvas: Canvas,
        *,
        title: str,
        subtitle: str,
        lines: list[str],
        risk_score: int | None,
    ) -> None:
        """Draw the full-page navy cover (called from ``onFirstPage``)."""
        canvas.setFillColor(BRAND_COLOR)
        canvas.rect(0, 0, _PAGE_W, _PAGE_H, fill=1, stroke=0)
        canvas.setFillColor(colors.white)
        canvas.setFont("Helvetica-Bold", 26)
        canvas.drawCentredString(_PAGE_W / 2, _PAGE_H - 90 * mm, title)
        canvas.setFont("Helvetica", 16)
        canvas.drawCentredString(_PAGE_W / 2, _PAGE_H - 105 * mm, subtitle)

        if risk_score is not None:
            cx, cy, radius = _PAGE_W / 2, _PAGE_H - 150 * mm, 18 * mm
            canvas.setFillColor(_risk_color(risk_score))
            canvas.circle(cx, cy, radius, fill=1, stroke=0)
            canvas.setFillColor(colors.white)
            canvas.setFont("Helvetica-Bold", 22)
            canvas.drawCentredString(cx, cy - 8, str(risk_score))

        canvas.setFillColor(HexColor("#AAB4C8"))
        canvas.setFont("Helvetica", 11)
        y = _PAGE_H - 185 * mm
        for line in lines:
            canvas.drawCentredString(_PAGE_W / 2, y, line)
            y -= 7 * mm

        canvas.setFont("Helvetica", 9)
        canvas.drawCentredString(
            _PAGE_W / 2,
            22 * mm,
            "CONFIDENTIAL — Generated by Secure Contract Intelligence",
        )


def _annotation_color(annotation_type: str) -> HexColor:
    """Accent colour for an annotation type (left border)."""
    return _ANNOTATION_COLORS.get(annotation_type, _ANNOTATION_GREY)


def _split_annotations(
    annotations: list[dict[str, Any]],
) -> tuple[
    list[dict[str, Any]],
    dict[str, list[dict[str, Any]]],
    dict[str, list[dict[str, Any]]],
]:
    """Partition annotations into document / by-chunk / by-deviation groups.

    Args:
        annotations: Raw annotation rows.

    Returns:
        ``(document_notes, by_chunk_id, by_deviation_id)``.
    """
    doc_notes: list[dict[str, Any]] = []
    by_chunk: dict[str, list[dict[str, Any]]] = {}
    by_deviation: dict[str, list[dict[str, Any]]] = {}
    for ann in annotations:
        target = ann.get("target_type")
        reference = ann.get("target_reference")
        if target == "document":
            doc_notes.append(ann)
        elif target == "clause" and reference:
            by_chunk.setdefault(reference, []).append(ann)
        elif target == "deviation" and reference:
            by_deviation.setdefault(reference, []).append(ann)
    return doc_notes, by_chunk, by_deviation


def _tint_for(deviation_type: DeviationType) -> HexColor:
    """Row tint for a deviation type."""
    if deviation_type in (DeviationType.MISSING, DeviationType.CONTRADICTORY):
        return _RED_TINT
    if deviation_type is DeviationType.WEAKENED:
        return _AMBER_TINT
    return _GREY_TINT


def _safe_date(value: Any) -> date | None:
    """Parse an ISO date string to ``date``; ``None`` on failure/missing."""
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None

import sys
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

# Ensure backend root is in sys.path when script is executed directly
backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.ipir.package import IPIRPackage  # noqa: E402


def generate_pdf_spec(json_spec_path: Path, output_pdf_path: Path) -> None:
    """Generates a synthetic human-readable PDF rate specification using ReportLab."""
    with open(json_spec_path, encoding="utf-8") as f:
        pkg = IPIRPackage.model_validate_json(f.read())

    output_pdf_path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(str(output_pdf_path), pagesize=letter)
    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "DocTitle",
        parent=styles["Title"],
        fontSize=18,
        leading=22,
        textColor=colors.HexColor("#1A365D"),
    )

    h2_style = ParagraphStyle(
        "DocH2",
        parent=styles["Heading2"],
        fontSize=14,
        leading=18,
        textColor=colors.HexColor("#2B6CB0"),
        spaceBefore=12,
        spaceAfter=6,
    )

    body_style = styles["Normal"]

    story = []

    # Header / Notice
    story.append(Paragraph("RateGuard AI — Synthetic Pricing Specification", title_style))
    story.append(Spacer(1, 10))

    notice_text = (
        "<b>NOTICE: Synthetic Hackathon Demonstration — Not a Real Regulatory Filing</b><br/>"
        "This document represents a synthetic Arizona Homeowners HO3 rate plan for automated "
        "pricing assurance validation."
    )
    story.append(Paragraph(notice_text, body_style))
    story.append(Spacer(1, 15))

    # Metadata Table
    story.append(Paragraph("1. Filing Metadata", h2_style))
    meta_data = [
        ["Package ID", pkg.id],
        ["Product / State", f"{pkg.product.id} ({pkg.product.jurisdiction.state_or_province})"],
        ["Effective Start Date", str(pkg.effective_period.start)],
        ["Base Rate", "$650.00"],
        ["Minimum Premium Floor", "$575.00"],
        ["Statutory Policy Fee", "$25.00"],
    ]
    t_meta = Table(meta_data, colWidths=[150, 300])
    t_meta.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#EDF2F7")),
            ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor("#2D3748")),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E0")),
            ("PADDING", (0, 0), (-1, -1), 5),
        ])
    )
    story.append(t_meta)
    story.append(Spacer(1, 15))

    # Rate Tables Summary
    story.append(Paragraph("2. Rate Table Schedules", h2_style))
    tbl_data = [["Table ID", "Table Name", "Dimensions", "Rows"]]
    for tbl in pkg.tables:
        dims = ", ".join(d.input_ref for d in tbl.dimensions)
        tbl_data.append([tbl.id, tbl.name, dims, str(len(tbl.rows))])

    t_tbl = Table(tbl_data, colWidths=[140, 160, 100, 50])
    t_tbl.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2B6CB0")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E0")),
            ("PADDING", (0, 0), (-1, -1), 5),
        ])
    )
    story.append(t_tbl)
    story.append(Spacer(1, 15))

    # Modifiers & Fees
    story.append(Paragraph("3. Modifiers & Statutory Fees", h2_style))
    mod_data = [["ID", "Name", "Type", "Value"]]
    for mod in pkg.modifiers:
        mod_data.append([mod.id, mod.name, mod.modifier_type.value, str(mod.value)])
    for fee in pkg.fees:
        mod_data.append([fee.id, fee.name, "FIXED_FEE", f"${fee.amount}"])

    t_mod = Table(mod_data, colWidths=[120, 180, 80, 70])
    t_mod.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2B6CB0")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E0")),
            ("PADDING", (0, 0), (-1, -1), 5),
        ])
    )
    story.append(t_mod)

    doc.build(story)
    print(f"Successfully generated synthetic PDF rate spec at: {output_pdf_path}")


def main() -> None:
    root_dir = Path(__file__).resolve().parent.parent.parent
    json_path = root_dir / "data" / "actuarial" / "AZ_HO3_2026_09_rate_spec.json"
    pdf_path = root_dir / "data" / "filings" / "AZ_HO3_2026_09_synthetic_rate_spec.pdf"

    generate_pdf_spec(json_path, pdf_path)


if __name__ == "__main__":
    main()


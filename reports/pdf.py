import re
from datetime import datetime
from flask import Response
from fpdf import FPDF, FontFace
from fpdf.enums import XPos, YPos

VIOLET = (124, 58, 237)
PINK = (219, 39, 119)
INK = (46, 16, 101)
MUTED = (107, 98, 144)
SWAPS = {"₹": "Rs. ", "✓ ": "", "≈ ": "~ ", "–": "-", "—": "-",
         "‘": "'", "’": "'", "“": '"', "”": '"'}


def clean(value):
    """The built-in PDF fonts only know Latin-1, so swap or drop other symbols (emoji)."""
    text = str(value if value is not None else "-")
    for old, new in SWAPS.items():
        text = text.replace(old, new)
    text = text.encode("latin-1", "ignore").decode("latin-1")
    return re.sub(r" {2,}", " ", text).strip()


def money(amount):
    return f"Rs. {amount:,}"


class Report(FPDF):
    """An A4 report with the Matchmaker header and page numbers on every page."""

    def __init__(self, title):
        super().__init__()
        self.report_title = title
        self.set_auto_page_break(auto=True, margin=18)
        self.add_page()

    def header(self):
        self.set_fill_color(*VIOLET)
        self.rect(0, 0, self.w, 22, style="F")
        self.set_fill_color(*PINK)
        self.rect(0, 22, self.w, 1.5, style="F")
        self.set_text_color(255, 255, 255)
        self.set_xy(10, 6)
        self.set_font("Helvetica", "B", 14)
        self.cell(0, 6, "SPONSORSHIP MATCHMAKER")
        self.set_xy(10, 13)
        self.set_font("Helvetica", "", 9)
        self.cell(0, 5, f"{self.report_title} - generated {datetime.now():%d %b %Y, %H:%M}")
        self.set_y(30)

    def footer(self):
        self.set_y(-12)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(*MUTED)
        self.cell(0, 6, f"Page {self.page_no()}/{{nb}}", align="C")

    def title_block(self, title, subtitle):
        self.set_font("Helvetica", "B", 20)
        self.set_text_color(*INK)
        self.multi_cell(0, 9, clean(title), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_font("Helvetica", "", 11)
        self.set_text_color(*MUTED)
        self.multi_cell(0, 6, clean(subtitle), new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    def heading(self, text):
        self.ln(4)
        self.set_font("Helvetica", "B", 11)
        self.set_text_color(*VIOLET)
        self.cell(0, 7, text.upper(), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_draw_color(*VIOLET)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.ln(2)

    def paragraph(self, text):
        self.set_font("Helvetica", "", 10)
        self.set_text_color(*INK)
        self.multi_cell(0, 5.5, clean(text), new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    def data_table(self, headers, rows, widths, empty_note):
        """A striped table, or a short note when there are no rows."""
        if not rows:
            self.paragraph(empty_note)
            return
        self.set_font("Helvetica", "", 9)
        self.set_text_color(*INK)
        heading_style = FontFace(emphasis="BOLD", color=(255, 255, 255), fill_color=VIOLET)
        with self.table(col_widths=widths, line_height=6, text_align="LEFT",
                        headings_style=heading_style, cell_fill_color=(245, 240, 255),
                        cell_fill_mode="ROWS") as table:
            for row in [headers] + rows:
                cells = table.row()
                for value in row:
                    cells.cell(clean(value))


def send_pdf(pdf, filename):
    """Send the PDF so the browser opens it in a new tab (it can be saved from there)."""
    return Response(bytes(pdf.output()), mimetype="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{filename}"'})
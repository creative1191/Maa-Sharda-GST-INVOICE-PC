"""
gst_invoice_engine.py
---------------------
B2B GST-compliant TAX INVOICE generator (PDF + HTML) for
Maa Sharda Enterprises — bills a BUSINESS CLIENT for the courier /
logistics service provided (shipping charge + GST).

STRICT RULES IMPLEMENTED:
  * Branding      — "Maa Sharda Enterprises" as main header (top-left).
                    Delhivery ONLY as a small icon next to the AWB number
                    with text "Routing Partner: Delhivery" (never header).
  * Supplier      — hardcoded Maa Sharda Enterprises details.
  * Billed To     — dynamic client (name / address / GSTIN).
  * Removed       — NO product name, NO item description, NO product
                    value, NO COD amount, NO quantity anywhere.
  * Service       — "Courier & Logistics Services", SAC 9968, AWB,
                    origin/destination pincode, weight.
  * GST math      — Base (taxable) + GST @18%:
                    same state  -> CGST 9% + SGST 9% (two rows)
                    diff state  -> IGST 18% (one row)
                    Total = Base + GST, plus Amount in Words.
"""

import datetime
import html as _html
import os
from decimal import Decimal, ROUND_HALF_UP

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import (
    HRFlowable,
    Image,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from embedded_assets import LOGO_B64, get_logo_stream
from pdf_parser import clean_date_string

# --------------------------------------------------------------------------- #
#  SUPPLIER DETAILS — HARDCODED (RULE 2)
#  ⚠️ TODO: apna real GSTIN yahan daalein (15 characters, e.g. 23ABCDE1234F1Z5)
# --------------------------------------------------------------------------- #

SUPPLIER_NAME = "Maa Sharda Enterprises"
SUPPLIER_ADDRESS1 = "Near Rest House, Panna Satna Road"
SUPPLIER_ADDRESS2 = "Nagod, Distt. Satna, Madhya Pradesh - 485446"
SUPPLIER_GSTIN = "23BCPPD5853C1ZT"
SUPPLIER_STATE = "Madhya Pradesh"
SUPPLIER_STATE_CODE = "23"

SERVICE_DESCRIPTION = "Courier & Logistics Services"
SAC_CODE = "9968"
ROUTING_PARTNER = "Delhivery"

GST_RATE_FULL = Decimal("0.18")
GST_RATE_HALF = Decimal("0.09")

# --------------------------------------------------------------------------- #
#  Color palette (same family as the receipt engine)
# --------------------------------------------------------------------------- #

C_BODY = colors.HexColor("#1e293b")
C_HEADER = colors.HexColor("#0f172a")
C_MUTED = colors.HexColor("#64748b")
C_SEMI_MUTED = colors.HexColor("#475569")
C_BORDER = colors.HexColor("#cbd5e1")
C_BORDER_LIGHT = colors.HexColor("#e2e8f0")
C_BG_LIGHT = colors.HexColor("#f8fafc")
C_RED = colors.HexColor("#d9534f")

# --------------------------------------------------------------------------- #
#  Paragraph styles
# --------------------------------------------------------------------------- #

s_firm = ParagraphStyle("firm", fontName="Helvetica-Bold", fontSize=20, leading=24,
                        textColor=C_HEADER)
s_firm_sub = ParagraphStyle("firmSub", fontName="Helvetica", fontSize=9, leading=12.5,
                            textColor=C_SEMI_MUTED)
s_title = ParagraphStyle("title", fontName="Helvetica-Bold", fontSize=15, leading=19,
                         textColor=C_HEADER, alignment=2)
s_meta = ParagraphStyle("meta", fontName="Helvetica", fontSize=10, leading=14.5,
                        textColor=C_BODY, alignment=2)
s_sec = ParagraphStyle("sec", fontName="Helvetica-Bold", fontSize=10.5, leading=14,
                       textColor=C_HEADER, spaceAfter=4)
s_body = ParagraphStyle("body", fontName="Helvetica", fontSize=10, leading=14,
                        textColor=C_BODY)
s_body_b = ParagraphStyle("bodyB", parent=s_body, fontName="Helvetica-Bold")
s_small = ParagraphStyle("small", fontName="Helvetica", fontSize=9, leading=12.5,
                         textColor=C_BODY)
s_small_m = ParagraphStyle("smallM", parent=s_small, textColor=C_MUTED)
s_val = ParagraphStyle("val", fontName="Helvetica", fontSize=10.5, leading=14,
                       textColor=C_BODY, alignment=2)
s_val_b = ParagraphStyle("valB", parent=s_val, fontName="Helvetica-Bold")
s_right = ParagraphStyle("right", fontName="Helvetica", fontSize=10, leading=14,
                         textColor=C_BODY, alignment=2)
s_lab_b = ParagraphStyle("labB", fontName="Helvetica-Bold", fontSize=10.5, leading=14,
                         textColor=C_BODY)
s_lab_t = ParagraphStyle("labT", parent=s_lab_b, textColor=C_HEADER, fontSize=11.5)
s_words = ParagraphStyle("words", fontName="Helvetica-Oblique", fontSize=9.5, leading=13,
                         textColor=C_SEMI_MUTED)
s_decl = ParagraphStyle("decl", fontName="Helvetica", fontSize=8.5, leading=12,
                        textColor=C_MUTED)
s_sign = ParagraphStyle("sign", fontName="Helvetica-Bold", fontSize=10, leading=14,
                        textColor=C_HEADER, alignment=2)
s_footer = ParagraphStyle("footer", fontName="Helvetica", fontSize=8, leading=11,
                          alignment=1, textColor=C_MUTED)

# --------------------------------------------------------------------------- #
#  GST state codes (GSTIN ke pehle 2 digits)
# --------------------------------------------------------------------------- #

STATE_CODES = {
    "01": "Jammu and Kashmir", "02": "Himachal Pradesh", "03": "Punjab",
    "04": "Chandigarh", "05": "Uttarakhand", "06": "Haryana", "07": "Delhi",
    "08": "Rajasthan", "09": "Uttar Pradesh", "10": "Bihar", "11": "Sikkim",
    "12": "Arunachal Pradesh", "13": "Nagaland", "14": "Manipur",
    "15": "Mizoram", "16": "Tripura", "17": "Meghalaya", "18": "Assam",
    "19": "West Bengal", "20": "Jharkhand", "21": "Odisha",
    "22": "Chhattisgarh", "23": "Madhya Pradesh", "24": "Gujarat",
    "26": "Dadra and Nagar Haveli and Daman and Diu", "27": "Maharashtra",
    "29": "Karnataka", "30": "Goa", "31": "Lakshadweep", "32": "Kerala",
    "33": "Tamil Nadu", "34": "Puducherry",
    "35": "Andaman and Nicobar Islands", "36": "Telangana",
    "37": "Andhra Pradesh", "38": "Ladakh",
}

CODE_BY_STATE = {v.lower(): k for k, v in STATE_CODES.items()}

GSTIN_RE = __import__("re").compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")


# --------------------------------------------------------------------------- #
#  Helpers
# --------------------------------------------------------------------------- #


def money(x) -> Decimal:
    """Round to 2 decimals, half-up (GST-safe)."""
    return Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def is_valid_gstin(gstin: str) -> bool:
    return bool(gstin) and bool(GSTIN_RE.match(gstin.strip().upper()))


def state_code_of_client(client_gstin: str = "", client_state: str = "") -> str:
    """Client state code: GSTIN prefix first, else state-name lookup."""
    gstin = (client_gstin or "").strip().upper()
    if len(gstin) >= 2 and gstin[:2].isdigit() and gstin[:2] in STATE_CODES:
        return gstin[:2]
    code = CODE_BY_STATE.get((client_state or "").strip().lower())
    if code:
        return code
    # partial name match (e.g. "Madhya Pradesh" / "MadhyaPradesh")
    slim = (client_state or "").replace(" ", "").lower()
    for name, c in CODE_BY_STATE.items():
        if slim and (slim in name.replace(" ", "").lower()):
            return c
    return ""


def calc_gst_reverse(total_inclusive, client_gstin: str = "", client_state: str = "") -> dict:
    """
    REVERSE GST (inclusive → exclusive):
      Input amount = FINAL TOTAL (GST-inclusive).
      Base (taxable) = Total / 1.18
      GST            = Total − Base
      Intra-state: CGST = round(GST/2), SGST = GST − CGST  (sum stays exact)
      Inter-state: IGST = GST
    Guarantee: base + cgst + sgst + igst == total_inclusive (2dp exact).
    """
    total = money(total_inclusive)
    base = money(total / Decimal("1.18"))
    gst = total - base
    client_code = state_code_of_client(client_gstin, client_state)
    same_state = bool(client_code) and client_code == SUPPLIER_STATE_CODE
    if same_state:
        cgst = money(gst / 2)
        sgst = gst - cgst          # absorb rounding → exact sum
        igst = Decimal("0.00")
    else:
        cgst = sgst = Decimal("0.00")
        igst = gst
    return {
        "base": base,
        "cgst": cgst,
        "sgst": sgst,
        "igst": igst,
        "total": total,
        "same_state": same_state,
        "client_state_code": client_code,
        "client_state_name": STATE_CODES.get(client_code, client_state or "—"),
        "amount_mode": "inclusive",
    }


def calc_gst(base_charge, client_gstin: str = "", client_state: str = "") -> dict:
    """
    Returns GST breakdown:
      same state -> {'type': 'intra', 'cgst': X, 'sgst': X, 'igst': 0}
      diff state -> {'type': 'inter', 'cgst': 0, 'sgst': 0, 'igst': Y}
    """
    base = money(base_charge)
    client_code = state_code_of_client(client_gstin, client_state)
    same_state = bool(client_code) and client_code == SUPPLIER_STATE_CODE
    if same_state:
        cgst = money(base * GST_RATE_HALF)
        sgst = money(base * GST_RATE_HALF)
        igst = Decimal("0.00")
    else:
        cgst = sgst = Decimal("0.00")
        igst = money(base * GST_RATE_FULL)
    total = base + cgst + sgst + igst
    return {
        "base": base,
        "cgst": cgst,
        "sgst": sgst,
        "igst": igst,
        "total": money(total),
        "same_state": same_state,
        "client_state_code": client_code,
        "client_state_name": STATE_CODES.get(client_code, client_state or "—"),
    }


_ONES = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight",
         "Nine", "Ten", "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen",
         "Sixteen", "Seventeen", "Eighteen", "Nineteen"]
_TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy",
         "Eighty", "Ninety"]


def _two(n: int) -> str:
    if n < 20:
        return _ONES[n]
    return (_TENS[n // 10] + ("-" + _ONES[n % 10] if n % 10 else "")).strip("-")


def _three(n: int) -> str:
    h, rest = divmod(n, 100)
    out = []
    if h:
        out.append(f"{_ONES[h]} Hundred")
    if rest:
        out.append(_two(rest))
    return " ".join(out)


def indian_words(n: int) -> str:
    """Indian system: crore / lakh / thousand."""
    if n == 0:
        return "Zero"
    parts = []
    crore, n = divmod(n, 10_000_000)
    lakh, n = divmod(n, 100_000)
    thousand, n = divmod(n, 1000)
    if crore:
        parts.append(_three(crore) + " Crore")
    if lakh:
        parts.append(_two(lakh) + " Lakh")
    if thousand:
        parts.append(_two(thousand) + " Thousand")
    if n:
        parts.append(_three(n) if n >= 100 else _two(n))
    return " ".join(parts)


def amount_in_words(amount: Decimal) -> str:
    rupees = int(amount)
    paise = int((amount * 100) % 100)
    words = f"{indian_words(rupees)} Rupees"
    if paise:
        words += f" and {indian_words(paise)} Paise"
    return words + " Only"


def fiscal_year(iso_date: str) -> str:
    d = datetime.date.fromisoformat(iso_date)
    y = d.year
    if d.month >= 4:  # FY starts April
        return f"{y}-{str(y + 1)[2:]}"
    return f"{y - 1}-{str(y)[2:]}"


def next_invoice_num(invoice_date: str, counter_file: str, prefix: str = "MSE") -> str:
    """
    Sequential invoice number: MSE/2026-27/0001 (FY-aware, April–March).
    Counter file stores 'FY count' lines; auto-increments.
    """
    fy = fiscal_year(invoice_date)
    counts = {}
    if os.path.exists(counter_file):
        for line in open(counter_file, encoding="utf-8"):
            if ":" in line:
                k, v = line.split(":", 1)
                counts[k.strip()] = int(v.strip() or 0)
    counts[fy] = counts.get(fy, 0) + 1
    os.makedirs(os.path.dirname(os.path.abspath(counter_file)), exist_ok=True)
    with open(counter_file, "w", encoding="utf-8") as fh:
        for k, v in counts.items():
            fh.write(f"{k}: {v}\n")
    return f"{prefix}/{fy}/{counts[fy]:04d}"


def inr(x) -> str:
    return f"₹{money(x):,.2f}" if False else f"INR {money(x):,.2f}"


# --------------------------------------------------------------------------- #
#  Data normalization
# --------------------------------------------------------------------------- #


def normalize_gst_data(data: dict) -> dict:
    d = dict(data)
    amount_mode = "inclusive" if str(d.get("amount_mode", "exclusive")).lower() == "inclusive" else "exclusive"
    if amount_mode == "inclusive":
        # Input amount = FINAL TOTAL (GST-inclusive) → reverse-calculate base
        gst = calc_gst_reverse(
            d.get("base_charge", d.get("total_amount", 0.0)),
            d.get("client_gstin", ""),
            d.get("client_state", ""),
        )
    else:
        gst = calc_gst(
            d.get("base_charge", 0.0),
            d.get("client_gstin", ""),
            d.get("client_state", ""),
        )
    gst["amount_mode"] = amount_mode
    return {
        "invoice_num": d.get("invoice_num", ""),
        "invoice_date": clean_date_string(d.get("invoice_date")
                                          or str(datetime.date.today())),
        "client_name": d.get("client_name", ""),
        "client_address1": d.get("client_address1", ""),
        "client_address2": d.get("client_address2", ""),
        "client_gstin": (d.get("client_gstin") or "").strip().upper(),
        "client_state": d.get("client_state", ""),
        "awb": d.get("awb", ""),
        "consignee_name": d.get("consignee_name", ""),
        "client_order_id": d.get("client_order_id", ""),
        "origin_pincode": d.get("origin_pincode", ""),
        "destination_pincode": d.get("destination_pincode", ""),
        "weight": d.get("weight", ""),
        "service": SERVICE_DESCRIPTION,
        "sac": SAC_CODE,
        "routing_partner": ROUTING_PARTNER,
        **gst,
        "amount_in_words": amount_in_words(gst["total"]),
    }


# --------------------------------------------------------------------------- #
#  PDF STORY
# --------------------------------------------------------------------------- #


def _delhivery_icon():
    try:
        return Image(get_logo_stream(), width=52, height=9)
    except Exception:
        return Paragraph("", s_small)


def build_gst_story(d: dict) -> list:
    story = []

    # ① HEADER — firm branding top-left, doc ID top-right (NO Delhivery here)
    left = [
        Paragraph(SUPPLIER_NAME, s_firm),
        Spacer(1, 3),
        Paragraph(SUPPLIER_ADDRESS1, s_firm_sub),
        Paragraph(SUPPLIER_ADDRESS2, s_firm_sub),
        Paragraph(f"GSTIN: {SUPPLIER_GSTIN} &nbsp;|&nbsp; State: {SUPPLIER_STATE} "
                  f"({SUPPLIER_STATE_CODE})", s_firm_sub),
    ]
    right = [
        Paragraph("TAX INVOICE", s_title),
        Spacer(1, 6),
        Paragraph(f"Invoice No: <b>{d['invoice_num']}</b>", s_meta),
        Paragraph(f"Date: {d['invoice_date']}", s_meta),
    ]
    head = Table([[left, right]], colWidths=[360, 180])
    head.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(head)
    story.append(Spacer(1, 12))
    story.append(HRFlowable(width="100%", thickness=1.5, color=C_BORDER))
    story.append(Spacer(1, 16))

    # ② BILLED TO (CLIENT) — supplier details upar HEADER me hain (FROM block removed)
    client_cell = [
        Paragraph("BILLED TO (CLIENT)", s_sec),
        Spacer(1, 4),
        Paragraph(d["client_name"] or "-", s_body_b),
        Paragraph(d["client_address1"] or "", s_small),
        Paragraph(d["client_address2"] or "", s_small),
        Paragraph(f"GSTIN: {d['client_gstin'] or '—'}", s_small),
        Paragraph(f"State: {d['client_state_name']}"
                  + (f" ({d['client_state_code']})" if d["client_state_code"] else ""),
                  s_small),
    ]
    parties = Table([[client_cell]], colWidths=[540])
    parties.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (0, 0), 12),
        ("RIGHTPADDING", (1, 0), (1, 0), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(parties)
    story.append(Spacer(1, 16))

    # ③ SERVICE & SHIPMENT DETAILS — AWB + small Delhivery icon HERE (only here)
    awb_right = Table(
        [[_delhivery_icon(), Paragraph(f"Routing Partner: {d['routing_partner']}",
                                       s_small_m)]],
        colWidths=[60, 120],
    )
    awb_right.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (0, 0), "RIGHT"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (0, 0), 6),
        ("RIGHTPADDING", (1, 0), (1, 0), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    awb_left = Paragraph(
        f"AWB No / Tracking ID: <b>{d['awb']}</b>", s_body)

    recon_row = [
        Paragraph(
            f"Consignee / Delivered To: <b>{d['consignee_name'] or '—'}</b>",
            s_body),
        Paragraph(
            f"Client Order ID: <b>{d['client_order_id'] or '—'}</b>", s_body),
    ]


    specs = Table(
        [[
            Paragraph("Origin Pincode", s_sec),
            Paragraph("Destination Pincode", s_sec),
            Paragraph("Weight", s_sec),
        ], [
            Paragraph(str(d["origin_pincode"] or "—"), s_body_b),
            Paragraph(str(d["destination_pincode"] or "—"), s_body_b),
            Paragraph(str(d["weight"] or "—"), s_body_b),
        ]],
        colWidths=[166, 166, 168],
    )
    specs.setStyle(TableStyle([
        ("LINEABOVE", (0, 1), (-1, 1), 0.5, C_BORDER_LIGHT),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))

    svc_rows = [
        [Paragraph("SERVICE &amp; SHIPMENT DETAILS", s_sec), ""],
        [awb_left, awb_right],
        recon_row,
        [Paragraph(f"Service Description: <b>{d['service']}</b>", s_body),
         Paragraph(f"SAC Code: <b>{d['sac']}</b>", s_right)],
        [specs, ""],
    ]
    svc = Table(svc_rows, colWidths=[310, 200])
    svc.setStyle(TableStyle([
        ("SPAN", (0, 0), (1, 0)),
        ("SPAN", (0, 4), (1, 4)),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (1, 1), (1, 1), "RIGHT"),
        ("ALIGN", (1, 3), (1, 3), "RIGHT"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 4), (-1, 4), 2),
    ]))
    svc_box = Table([[svc]], colWidths=[540])
    svc_box.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), C_BG_LIGHT),
        ("BOX", (0, 0), (-1, -1), 0.5, C_BORDER_LIGHT),
        ("LEFTPADDING", (0, 0), (-1, -1), 15),
        ("RIGHTPADDING", (0, 0), (-1, -1), 15),
        ("TOPPADDING", (0, 0), (-1, -1), 12),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
    ]))
    story.append(svc_box)
    story.append(Spacer(1, 20))

    # ④ FINANCIAL TABLE — strict breakdown (Base + GST = Total + words)
    fin_rows = [
        [Paragraph("Base Shipping Charge (Taxable Value)", s_lab_b),
         Paragraph(inr(d["base"]), s_val_b)],
    ]
    if d["same_state"]:
        fin_rows.append([Paragraph("Add: CGST @ 9%", s_body),
                         Paragraph(inr(d["cgst"]), s_val)])
        fin_rows.append([Paragraph("Add: SGST @ 9%", s_body),
                         Paragraph(inr(d["sgst"]), s_val)])
    else:
        fin_rows.append([Paragraph("Add: IGST @ 18%", s_body),
                         Paragraph(inr(d["igst"]), s_val)])
    n_tax = len(fin_rows)  # last tax row index
    fin_rows.append([Paragraph("Total Invoice Amount", s_lab_t),
                     Paragraph(inr(d["total"]), ParagraphStyle(
                         "totV", parent=s_val_b, fontSize=11.5,
                         textColor=C_HEADER))])
    fin_rows.append([Paragraph(
        f"Amount in Words: <b>{d['amount_in_words']}</b>", s_words), ""])
    fin = Table(fin_rows, colWidths=[360, 180])
    style = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("BOX", (0, 0), (-1, -2), 0.5, C_BORDER),
        ("LINEBELOW", (0, n_tax - 2), (-1, n_tax - 2), 0.25, C_BORDER_LIGHT),
        ("SPAN", (0, -1), (1, -1)),
        ("BACKGROUND", (0, -2), (-1, -2), C_BG_LIGHT),
        ("LINEABOVE", (0, -2), (-1, -2), 1, C_HEADER),
    ]
    fin.setStyle(TableStyle(style))
    story.append(fin)
    story.append(Spacer(1, 30))

    # ⑤ DECLARATION + SIGNATORY
    decl = Paragraph(
        "Declaration: We declare that this invoice shows the actual price of the "
        "service described and that all particulars are true and correct. "
        "This is a computer-generated invoice and does not require a physical "
        "signature.", s_decl)
    sign = [
        Spacer(1, 26),
        Paragraph(f"For {SUPPLIER_NAME}", s_sign),
        Spacer(1, 24),
        Paragraph("Authorised Signatory", ParagraphStyle(
            "as", parent=s_sign, fontName="Helvetica", fontSize=9,
            textColor=C_MUTED)),
    ]
    bottom = Table([[decl, sign]], colWidths=[330, 210])
    bottom.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "BOTTOM"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(bottom)
    story.append(Spacer(1, 16))

    # ⑥ FOOTER
    story.append(HRFlowable(width="100%", thickness=0.5, color=C_BORDER))
    story.append(Spacer(1, 8))
    story.append(Paragraph(
        f"This is a computer-generated tax invoice for courier &amp; logistics "
        f"services. | Routing Partner: {ROUTING_PARTNER}", s_footer))
    return story


def generate_gst_pdf(data: dict, output_path: str) -> None:
    d = normalize_gst_data(data)
    doc = SimpleDocTemplate(
        output_path, pagesize=letter,
        rightMargin=36, leftMargin=36, topMargin=36, bottomMargin=36,
        title=f"Tax Invoice {d['invoice_num']}",
        author=SUPPLIER_NAME,
    )
    doc.build(build_gst_story(d))


# --------------------------------------------------------------------------- #
#  HTML VERSION (standalone, inline styles, print-ready)
# --------------------------------------------------------------------------- #


def generate_gst_html(data: dict, output_path: str) -> None:
    d = normalize_gst_data(data)
    e = _html.escape

    if d["same_state"]:
        tax_rows = (
            f'<tr><td>Add: CGST @ 9%</td><td class="v">{e(inr(d["cgst"]))}</td></tr>'
            f'<tr><td>Add: SGST @ 9%</td><td class="v">{e(inr(d["sgst"]))}</td></tr>'
        )
    else:
        tax_rows = (f'<tr><td>Add: IGST @ 18%</td>'
                    f'<td class="v">{e(inr(d["igst"]))}</td></tr>')

    html_doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Tax Invoice {e(d['invoice_num'])}</title>
<style>
  @page {{ size: letter; margin: 0.5in; }}
  * {{ box-sizing: border-box; }}
  body {{ font-family: Helvetica, Arial, sans-serif; background:#f1f5f9;
         color:#1e293b; margin:0; padding:24px; }}
  .toolbar {{ max-width:8.5in; margin:0 auto 14px auto; text-align:right; }}
  .toolbar button {{ background:#0f172a; color:#fff; border:none; padding:8px 18px;
        border-radius:5px; font-weight:bold; cursor:pointer; font-size:13px; }}
  .card {{ max-width:8.5in; margin:0 auto; background:#fff; padding:36pt;
        border:1px solid #cbd5e1; box-shadow:0 4px 15px rgba(0,0,0,.05); }}
  .hdr {{ display:flex; justify-content:space-between; }}
  .firm h1 {{ margin:0; font-size:20pt; color:#0f172a; }}
  .firm .ad {{ font-size:9pt; color:#475569; margin:1pt 0; }}
  .doc {{ text-align:right; }}
  .doc .ti {{ font-size:15pt; font-weight:bold; color:#0f172a; }}
  .doc div {{ font-size:10pt; }}
  hr.rule {{ border:none; border-top:1.5pt solid #cbd5e1; margin:12pt 0 16pt 0; }}
  .parties {{ display:flex; gap:12pt; }}
  .parties > div {{ flex:1; }}
  .sh {{ font-size:10.5pt; font-weight:bold; color:#0f172a; margin:0 0 4pt 0; }}
  .sm {{ font-size:9pt; margin:1pt 0; }}
  .b {{ font-weight:bold; }}
  .box {{ background:#f8fafc; border:0.5pt solid #e2e8f0; padding:12pt 15pt;
          margin-top:16pt; }}
  .awb-row {{ display:flex; justify-content:space-between; align-items:center; }}
  .awb-row .rp {{ font-size:9pt; color:#64748b; }}
  .awb-row .rp img {{ width:52px; height:9px; vertical-align:middle; margin-right:6px; }}
  .svc2 {{ display:flex; justify-content:space-between; margin-top:6pt;
           font-size:10pt; }}
  .specs {{ display:flex; margin-top:10pt; border-top:0.5pt solid #e2e8f0;
            padding-top:8pt; }}
  .specs > div {{ flex:1; }}
  .specs .sh {{ font-size:10.5pt; }}
  table.fin {{ width:100%; border-collapse:collapse; margin-top:20pt;
               font-size:10.5pt; }}
  table.fin td {{ border:0.5pt solid #cbd5e1; padding:8pt 12pt; }}
  table.fin td.v {{ text-align:right; font-weight:bold; }}
  table.fin tr.total td {{ background:#f8fafc; border-top:1pt solid #0f172a;
           font-weight:bold; color:#0f172a; font-size:11.5pt; }}
  table.fin tr.words td {{ font-style:italic; color:#475569; font-size:9.5pt; }}
  .bot {{ display:flex; justify-content:space-between; margin-top:30pt;
          align-items:flex-end; }}
  .decl {{ font-size:8.5pt; color:#64748b; max-width:330pt; }}
  .sign {{ text-align:right; font-weight:bold; font-size:10pt; }}
  .sign .as {{ font-weight:normal; font-size:9pt; color:#64748b; margin-top:26pt; }}
  .foot {{ border-top:0.5pt solid #cbd5e1; margin-top:16pt; padding-top:8pt;
           text-align:center; font-size:8pt; color:#64748b; }}
  @media print {{ body {{ background:#fff; padding:0; }}
    .toolbar {{ display:none; }}
    .card {{ border:none; box-shadow:none; max-width:none; padding:0; }} }}
</style>
</head>
<body>
<div class="toolbar"><button onclick="window.print()">🖨️ Print / Save as PDF</button></div>
<div class="card">
  <div class="hdr">
    <div class="firm">
      <h1>{e(SUPPLIER_NAME)}</h1>
      <div class="ad">{e(SUPPLIER_ADDRESS1)}</div>
      <div class="ad">{e(SUPPLIER_ADDRESS2)}</div>
      <div class="ad">GSTIN: {e(SUPPLIER_GSTIN)} | State: {e(SUPPLIER_STATE)} ({e(SUPPLIER_STATE_CODE)})</div>
    </div>
    <div class="doc">
      <div class="ti">TAX INVOICE</div>
      <div>Invoice No: <b>{e(d['invoice_num'])}</b></div>
      <div>Date: {e(d['invoice_date'])}</div>
    </div>
  </div>
  <hr class="rule">
  <div class="parties">
    <div>
      <p class="sh">BILLED TO (CLIENT)</p>
      <p class="sm b">{e(d['client_name'] or '-')}</p>
      <p class="sm">{e(d['client_address1'] or '')}</p>
      <p class="sm">{e(d['client_address2'] or '')}</p>
      <p class="sm">GSTIN: {e(d['client_gstin'] or '—')}</p>
      <p class="sm">State: {e(d['client_state_name'])}</p>
    </div>
  </div>
  <div class="box">
    <p class="sh">SERVICE &amp; SHIPMENT DETAILS</p>
    <div class="awb-row">
      <div>AWB No / Tracking ID: <b>{e(d['awb'])}</b></div>
      <div class="rp"><img alt="Delhivery"
        src="data:image/png;base64,{LOGO_B64}">Routing Partner: {e(d['routing_partner'])}</div>
    </div>
    <div class="svc2">
      <div>Consignee / Delivered To: <b>{e(d['consignee_name'] or '—')}</b></div>
      <div>Client Order ID: <b>{e(d['client_order_id'] or '—')}</b></div>
    </div>
    <div class="svc2">
      <div>Service Description: <b>{e(d['service'])}</b></div>
      <div>SAC Code: <b>{e(d['sac'])}</b></div>
    </div>
    <div class="specs">
      <div><p class="sh">Origin Pincode</p><p class="sm b" style="font-size:10pt;">{e(str(d['origin_pincode'] or '—'))}</p></div>
      <div><p class="sh">Destination Pincode</p><p class="sm b" style="font-size:10pt;">{e(str(d['destination_pincode'] or '—'))}</p></div>
    </div>
  </div>
  <table class="fin">
    <tr><td>Base Shipping Charge (Taxable Value)</td><td class="v">{e(inr(d['base']))}</td></tr>
    {tax_rows}
    <tr class="total"><td>Total Invoice Amount</td><td class="v">{e(inr(d['total']))}</td></tr>
    <tr class="words"><td colspan="2">Amount in Words: <b>{e(d['amount_in_words'])}</b></td></tr>
  </table>
  <div class="bot">
    <div class="decl">Declaration: We declare that this invoice shows the actual
    price of the service described and that all particulars are true and correct.
    This is a computer-generated invoice and does not require a physical signature.</div>
    <div class="sign">For {e(SUPPLIER_NAME)}<div class="as">Authorised Signatory</div></div>
  </div>
  <div class="foot">This is a computer-generated tax invoice for courier &amp;
  logistics services. | Routing Partner: {e(ROUTING_PARTNER)}</div>
</div>
</body>
</html>
"""
    with open(output_path, "w", encoding="utf-8") as fh:
        fh.write(html_doc)


def generate_gst_invoice(data: dict, output_pdf: str, output_html: str = None) -> dict:
    """Generate B2B GST tax invoice (PDF always, HTML optional).
    Returns the normalized data dict (incl. computed GST breakdown)."""
    d = normalize_gst_data(data)
    generate_gst_pdf(data, output_pdf)
    if output_html:
        generate_gst_html(data, output_html)
    return d


# --------------------------------------------------------------------------- #
#  Archival: PDF -> "<Client>/<YYYY-MM Month>/" folder, generated/ cleanup
#  (User policy: Shree Mahadev ke folder me poori month ki invoices,
#   sirf PDF format me; generated/ sirf temporary rahta hai)
# --------------------------------------------------------------------------- #

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def archive_invoice(output_pdf: str, output_html: str = None, invoice_date: str = "",
                    client_name: str = "", archive_root: str = None) -> str:
    """
    PDF ko client ke month-folder me move karo:
        <root>/<Client Name>/<YYYY-MM MonthName>/invoice.pdf
    HTML (agar bana) delete ho jaata hai — sirf PDF store hota hai.
    Returns: archived PDF ka full path.
    """
    import calendar
    import re as _re
    import shutil

    date = clean_date_string(invoice_date) or str(datetime.date.today())
    try:
        d = datetime.date.fromisoformat(date)
    except ValueError:
        d = datetime.date.today()

    folder = _re.sub(r"[^A-Za-z0-9 ]+", "", (client_name or "").strip()).strip() or "Invoices"
    root = archive_root or os.path.join(PROJECT_ROOT, folder)
    month_dir = os.path.join(root, f"{d.year}-{d.month:02d} {calendar.month_name[d.month]}")
    os.makedirs(month_dir, exist_ok=True)

    dest = os.path.join(month_dir, os.path.basename(output_pdf))
    shutil.move(output_pdf, dest)
    if output_html and os.path.exists(output_html):
        os.remove(output_html)
    return dest

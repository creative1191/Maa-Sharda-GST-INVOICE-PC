"""
app.py — GST Invoice Generator (STANDALONE SINGLE FILE — v2.0)
=======================================================
Maa Sharda Enterprises | Streamlit Cloud ke liye — koi `src/` folder Nahi chahiye!

Sirf 2 files GitHub par daalo:
    1. app.py            (ye file — poora code andar hai)
    2. requirements.txt

Run: streamlit run app.py
"""

# ======================= src/pdf_parser.py =======================
"""
pdf_parser.py
-------------
Delhivery courier invoice PDF parser.

Reads a Delhivery-generated Tax Invoice PDF (via pdfplumber), extracts
the customer / shipment data, and returns one dict per page
(multi-page PDFs = multiple invoices, one per page).

Key helpers (CRITICAL RULES implemented here):
  * clean_date_string()   -> "2026-7-21 9:34:10"  =>  "2026-07-21"  (NO TIME)
  * deduplicate_line()    -> "Kajal Singh Kajal Singh" => "Kajal Singh"
                             (Delhivery prints SHIPPING + BILLING address
                              side-by-side, so every line is duplicated)
  * "Sales Number" is extracted raw; the receipt displays it as "Unique Number"
"""

import re

import pdfplumber

# --------------------------------------------------------------------------- #
#  Text-cleaning helpers
# --------------------------------------------------------------------------- #


def clean_date_string(raw: str) -> str:
    """
    Strip the time from a Delhivery date string and zero-pad it.
        "2026-7-21 9:34:10"  -> "2026-07-21"
        "2026-07-21"         -> "2026-07-21"
    RULE 1/2: the receipt must NEVER contain a time component.
    """
    if not raw:
        return ""
    m = re.match(r"\s*(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", str(raw).strip())
    if m:
        y, mo, d = (int(g) for g in m.groups())
        return f"{y:04d}-{mo:02d}-{d:02d}"
    # Fallback: first whitespace-separated token (the date part only)
    token = str(raw).strip().split()[0] if str(raw).strip() else ""
    return token


def deduplicate_line(line: str) -> str:
    """
    Delhivery PDFs duplicate address lines because SHIPPING ADDRESS and
    BILLING ADDRESS are printed side-by-side on the same visual line.
    "Pankaj Singh Baghel Pankaj Singh Baghel" -> "Pankaj Singh Baghel"

    Algorithm:
      1. Word-level: if first i words == next i words, keep first half.
      2. Character-level fallback: split at a space near the midpoint and
         compare both halves.
    """
    line = (line or "").strip()
    if not line:
        return line

    words = line.split()
    for i in range(1, len(words) // 2 + 1):
        if words[:i] == words[i : 2 * i]:
            return " ".join(words[:i])

    mid = len(line) // 2
    for offset in range(25):
        for idx in ({mid + offset, mid - offset}):
            if idx > 0 and idx < len(line) and line[idx] == " ":
                left = line[:idx].strip()
                right = line[idx:].strip()
                if left == right:
                    return left
                return line  # a genuine space at midpoint, but halves differ
    return line


# --------------------------------------------------------------------------- #
#  Single-page extraction
# --------------------------------------------------------------------------- #

RE_INVOICE = re.compile(r"Invoice\s*No\s*[.:#-]*\s*(\S+)", re.IGNORECASE)
RE_DATE = re.compile(r"Date\s*[.:#-]*\s*(.+)", re.IGNORECASE)
RE_SALES = re.compile(r"Sales\s*Number\s*[.:#-]*\s*(\S+)", re.IGNORECASE)
RE_AWB = re.compile(r"AWB\s*Number\s*[.:#-]*\s*(\S+)", re.IGNORECASE)
RE_ITEM_ROW = re.compile(
    r"([A-Za-z][A-Za-z\s/.&-]*?)\s+(\S+)\s+(\d+)\s+INR\s*([\d,.]+)", re.IGNORECASE
)


def _empty_record() -> dict:
    return {
        "invoice_num": "",
        "date": "",
        "name": "",
        "address1": "",
        "address2": "",
        "sales_number": "",
        "awb_number": "",
        "item_desc": "",
        "sku_code": "",
        "qty": 1,
        "rate": 0.0,
        "payment_type": "Prepaid",
        "page": 1,
    }


def parse_page_text(text: str, page_no: int = 1) -> dict:
    """Parse the extracted text of ONE Delhivery invoice page into a dict."""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    rec = _empty_record()
    rec["page"] = page_no

    billing_idx = None
    item_header_idx = None

    for i, line in enumerate(lines):
        # --- key: value fields ------------------------------------------------
        m = RE_INVOICE.search(line)
        if m and not rec["invoice_num"]:
            rec["invoice_num"] = m.group(1)

        m = RE_DATE.search(line)
        if m and not rec["date"] and "sale" not in line.lower():
            rec["date"] = clean_date_string(m.group(1))

        m = RE_SALES.search(line)
        if m and not rec["sales_number"]:
            rec["sales_number"] = m.group(1)

        m = RE_AWB.search(line)
        if m and not rec["awb_number"]:
            rec["awb_number"] = m.group(1)

        # --- anchors ----------------------------------------------------------
        up = line.upper()
        if "BILLING ADDRESS" in up or "SHIPPING ADDRESS" in up:
            billing_idx = i

        low = line.lower()
        if item_header_idx is None and "item description" in low and "qty" in low:
            item_header_idx = i

        if line.strip() in ("Prepaid", "COD", "Paid", "Pre-Paid", " prepaid"):
            rec["payment_type"] = line.strip().title()

    # --- consignee name + address (lines after BILLING ADDRESS anchor) --------
    if billing_idx is not None:
        addr_lines = lines[billing_idx + 1 : billing_idx + 4]
        addr_lines = [deduplicate_line(a) for a in addr_lines]
        # Skip lines that are actually section headers (e.g. ORDER DETAILS)
        cleaned = [
            a for a in addr_lines
            if a.upper() not in ("ORDER DETAILS",) and not a.upper().startswith("ITEM DESCRIPTION")
        ]
        if len(cleaned) >= 1:
            rec["name"] = cleaned[0]
        if len(cleaned) >= 2:
            rec["address1"] = cleaned[1]
        if len(cleaned) >= 3:
            rec["address2"] = cleaned[2]

    # --- item row (first data row after the ITEM table header) ---------------
    if item_header_idx is not None:
        for line in lines[item_header_idx + 1 : item_header_idx + 4]:
            m = RE_ITEM_ROW.search(line)
            if m:
                rec["item_desc"] = m.group(1).strip()
                rec["sku_code"] = m.group(2).strip()
                rec["qty"] = int(m.group(3))
                rec["rate"] = float(m.group(4).replace(",", ""))
                break

    return rec


# --------------------------------------------------------------------------- #
#  Public API
# --------------------------------------------------------------------------- #


def parse_delhivery_pdf(pdf_path: str) -> list:
    """
    Parse a Delhivery invoice PDF.

    Returns a LIST of dicts — one per page (each page is a separate
    invoice). Pages that contain no invoice data are skipped.

    Dict keys:
        invoice_num, date, name, address1, address2,
        sales_number, awb_number, item_desc, sku_code,
        qty, rate, payment_type, page
    """
    records = []
    with pdfplumber.open(pdf_path) as pdf:
        for page_no, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            rec = parse_page_text(text, page_no)
            # A real invoice page must carry at least an AWB or invoice no.
            if rec["awb_number"] or rec["invoice_num"]:
                records.append(rec)
    return records


# ======================= src/embedded_assets.py =======================
"""
embedded_assets.py
------------------
Delhivery logo + PREPAID stamp embedded directly as base64 so the
compiled EXE is fully self-contained (no external image files needed).
Decoded at runtime into BytesIO streams for reportlab / HTML data URIs.
"""
import base64
import io


def _stream(b64_string: str) -> "io.BytesIO":
    """Decode a base64 string into a binary stream."""
    return io.BytesIO(base64.b64decode(b64_string))


def get_logo_stream() -> "io.BytesIO":
    return _stream(LOGO_B64)


def get_stamp_stream() -> "io.BytesIO":
    return _stream(STAMP_B64)


LOGO_B64 = (
    "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"
)


STAMP_B64 = (
    "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"
)


# ======================= src/gst_invoice_engine.py =======================
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

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))


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


# ======================= src/doc_extractor.py =======================
"""
doc_extractor.py
----------------
OCR / document extraction for invoice auto-fill.

Accepts an IMAGE (png/jpg/jpeg/webp/bmp/tiff) or PDF (shipping label,
receipt, Delhivery invoice) and extracts:
    * AWB / Tracking Number
    * Consignee details (customer name + address lines)
    * Destination pincode (bonus, for the invoice's shipment specs)

Pipeline:
    PDF   → pdfplumber text → try Delhivery-invoice parser first, then
            generic label regexes
    IMAGE → Tesseract OCR (pytesseract, falls back to tesseract CLI)

All results are BEST-EFFORT heuristics — the CLI always prints what was
extracted so the user can override with explicit flags.
"""

import os
import re
import shutil
import subprocess
import sys


# --------------------------------------------------------------------------- #
#  AWB patterns (label-aware first, then generic long digit runs)
# --------------------------------------------------------------------------- #

AWB_LABELED = re.compile(
    r"(?:AWB|Tracking|Track|Way\s*Bill|Consignment|Air\s*Way\s*Bill)"
    r"[^0-9]{0,25}([0-9][0-9 ,]{9,24})",
    re.IGNORECASE,
)
AWB_DELHIVERY = re.compile(r"\b(3[0-9]{13})\b")     # 14-digit, starts with 3
AWB_GENERIC = re.compile(r"\b([0-9]{12,14})\b")

NAME_LABEL = re.compile(
    r"^\s*(?:consignee|deliver\s*to|delivery\s*to|ship\s*to|shipped\s*to|"
    r"sold\s*to|buyer|customer|recipient)\s*(?:name)?\s*[:\-]?\s*(.*)$",
    re.IGNORECASE,
)
NAME_INLINE = re.compile(
    r"(?:consignee|deliver\s*to|ship\s*to|sold\s*to|buyer|customer|recipient)"
    r"\s*(?:name)?\s*[:\-]\s*(.+)",
    re.IGNORECASE,
)

STOP_WORDS = (
    "awb", "tracking", "order", "invoice", "gst", "gstin", "sku", "qty",
    "quantity", "item", "product", "cod", "amount", "weight", "dimensions",
    "pincode", "origin", "destination", "payment", "prepaid", "powered",
    "delhivery", "shipping address", "billing address", "seller", "pickup",
    "phone", "mobile", "tel", "email", "date", "tax", "invoice no",
)

PINCODE_RE = re.compile(r"\b([1-9][0-9]{5})\b")
PHONE_RE = re.compile(r"^\s*(?:ph(?:one)?|mob(?:ile)?|tel)?[\s:.-]*[0-9+\-]{10,}\s*$",
                      re.IGNORECASE)


def _clean_line(line: str) -> str:
    return deduplicate_line(re.sub(r"\s{2,}", " ", (line or "").strip()))


def _clean_name(name: str) -> str:
    """Strip trailing OCR noise (!.,;:|) from a person/firm name."""
    return re.sub(r"[^A-Za-z0-9 .,&'/()-]+$", "", (name or "").strip()).strip()


def _is_stop_line(line: str) -> bool:
    low = line.lower()
    return any(low.startswith(w) or w in low for w in STOP_WORDS)


def _find_awb(text: str) -> str:
    # 1) label-aware (digits may contain OCR-inserted spaces/commas)
    m = AWB_LABELED.search(text)
    if m:
        digits = re.sub(r"\D", "", m.group(1))
        if 10 <= len(digits) <= 14:
            return digits
    # 2) compact text: join "digit space digit" splits, then generic runs
    compact = re.sub(r"(?<=\d)[ ,]+(?=\d)", "", text)
    for rx in (AWB_DELHIVERY, AWB_GENERIC):
        m = rx.search(compact)
        if m:
            return m.group(1)
    return ""


def _find_consignee(lines: list) -> tuple:
    """Return (name, address1, address2, pincode) using label heuristics."""
    for i, line in enumerate(lines):
        m = NAME_LABEL.match(line)
        if m:
            name = _clean_name(m.group(1))
            rest = lines[i + 1:]
            if not name:  # label on its own line → name is next line
                cand = [l for l in rest if l.strip()]
                name = _clean_line(cand[0]) if cand else ""
                rest = rest[1:]
            addr = []
            for raw in rest:
                l = _clean_line(raw)
                if not l or PHONE_RE.match(l) or _is_stop_line(l):
                    break
                addr.append(l)
                if len(addr) == 2:
                    break
            pin = ""
            for a in addr:
                pm = PINCODE_RE.search(a)
                if pm:
                    pin = pm.group(1)
            return (_clean_name(name), addr[0] if addr else "",
                    addr[1] if len(addr) > 1 else "", pin)
    return "", "", "", ""


def parse_shipping_text(text: str) -> dict:
    """Extract AWB + consignee heuristics from raw text (PDF text or OCR)."""
    lines = [_clean_line(l) for l in (text or "").splitlines()]
    lines = [l for l in lines if l]
    flat = "\n".join(lines)

    awb = _find_awb(flat)
    name, addr1, addr2, pin = _find_consignee(lines)

    if not name:  # fallback: inline pattern anywhere in text
        m = NAME_INLINE.search(flat)
        if m:
            name = _clean_name(m.group(1))

    if not pin:  # last-resort: last 6-digit pincode-looking number
        pins = PINCODE_RE.findall(flat)
        if pins:
            pin = pins[-1]

    return {
        "awb": awb,
        "consignee_name": name,
        "consignee_address1": addr1,
        "consignee_address2": addr2,
        "destination_pincode": pin,
    }


# --------------------------------------------------------------------------- #
#  PDF input
# --------------------------------------------------------------------------- #


def _extract_pdf(path: str) -> tuple:
    """Returns (text, method). Tries Delhivery-invoice parser first."""
    import pdfplumber

    text = ""
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            text += (page.extract_text() or "") + "\n"

    # Delhivery invoice? (structured, proven parser available)
    try:

        records = parse_delhivery_pdf(path)
        if records:
            r = records[0]
            pm = PINCODE_RE.search(r.get("address2", "") or "")
            return text, {
                "awb": r.get("awb_number", ""),
                "consignee_name": r.get("name", ""),
                "consignee_address1": r.get("address1", ""),
                "consignee_address2": r.get("address2", ""),
                "destination_pincode": pm.group(1) if pm else "",
                # Sales Number → Client Order ID (auto, reconciliation key)
                "client_order_id": r.get("sales_number", ""),
                "_source": "delhivery-invoice-parser",
            }
    except Exception:
        pass

    return text, None


# --------------------------------------------------------------------------- #
#  Image OCR
# --------------------------------------------------------------------------- #


def _ocr_image(path: str) -> str:
    """Tesseract OCR (pytesseract) with label-optimized preprocessing:
    upscale small images, grayscale, autocontrast, binarize — digits
    (AWB/pincodes) ka misread drastically kam hota hai."""
    try:
        import pytesseract
        from PIL import Image, ImageOps

        img = Image.open(path)
        w, h = img.size
        if max(w, h) < 3000:                     # small label → upscale 3x
            img = img.resize((w * 3, h * 3), Image.LANCZOS)
        img = img.convert("L")                   # grayscale
        img = ImageOps.autocontrast(img)
        img = img.point(lambda p: 255 if p > 140 else 0)   # binarize
        return pytesseract.image_to_string(img, config="--psm 6")
    except Exception:
        pass
    if shutil.which("tesseract"):
        try:
            out = subprocess.run(
                ["tesseract", path, "stdout", "--psm", "6"],
                capture_output=True, text=True, timeout=120, check=True,
            )
            return out.stdout
        except Exception:
            pass
    raise RuntimeError(
        "OCR available nahi hai — 'tesseract' install karein "
        "(Ubuntu: apt install tesseract-ocr | Windows: UB-Mannheim build) "
        "ya text-wali PDF use karein."
    )


# --------------------------------------------------------------------------- #
#  Public API
# --------------------------------------------------------------------------- #


def extract_from_document(path: str) -> dict:
    """
    Auto-extract AWB + consignee details from an image/PDF.

    Returns dict:
        awb, consignee_name, consignee_address1, consignee_address2,
        destination_pincode, _source ('pdf-text' | 'ocr' | parser name)
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"File nahi mila: {path}")

    ext = os.path.splitext(path)[1].lower().lstrip(".")
    if ext == "pdf":
        text, pre = _extract_pdf(path)
        if pre:
            return pre
        if not text.strip():
            raise RuntimeError(
                "PDF me text nahi mila (scanned PDF lagta hai). "
                "Image/exported PDF bhejein ya OCR ke liye image use karein."
            )
        result = parse_shipping_text(text)
        result["_source"] = "pdf-text"
        return result

    if ext in ("png", "jpg", "jpeg", "webp", "bmp", "tif", "tiff"):
        text = _ocr_image(path)
        result = parse_shipping_text(text)
        result["_source"] = "ocr"
        return result

    raise ValueError(f"Unsupported file type: .{ext} (PDF ya image bhejein)")


# ======================= Streamlit UI (app) =======================
"""
app.py — Streamlit Web UI for the B2B GST Tax Invoice Generator
----------------------------------------------------------------
Maa Sharda Enterprises | Workflow:
    1. Delhivery invoice PDF upload karo  → AWB, Consignee, Order ID,
       Destination Pincode AUTO-EXTRACT ho jaate hain
    2. Sirf Weight + Shipping Amount daalo
    3. Generate → PDF download + auto-archive (<Client>/<Month>/ folder)

Run:
    streamlit run streamlit_app/app.py
"""

import datetime
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = HERE

import streamlit as st  # noqa: E402
# Fixed client ke built-in defaults (profile file na ho to bhi auto-fill)
DEFAULT_CLIENT = {
    "client_name": "SHREE MAHADEV",
    "client_address1": "00, VAN VIBHAG KE BAGAL ME",
    "client_address2": "SABJI MANDI KE SAMNE, NAGOD, SATNA, MADHYA PRADESH, 485446",
    "client_gstin": "23ADFPB7291L2ZG",
    "client_state": "Madhya Pradesh",
    "origin_pincode": "485446",
}

CLIENT_PROFILE = os.path.join(ROOT, "clients", "default.json")
GENERATED_DIR = os.path.join(ROOT, "generated")
COUNTER_FILE = os.path.join(GENERATED_DIR, "gst_invoice_counter.txt")

st.set_page_config(page_title="GST Invoice v2.0 — Maa Sharda Enterprises",
                   page_icon="🧾", layout="centered")


# --------------------------------------------------------------------------- #
#  Helpers
# --------------------------------------------------------------------------- #

def load_profile() -> dict:
    try:
        with open(CLIENT_PROFILE, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def save_profile(profile: dict) -> None:
    os.makedirs(os.path.dirname(CLIENT_PROFILE), exist_ok=True)
    with open(CLIENT_PROFILE, "w", encoding="utf-8") as fh:
        json.dump(profile, fh, indent=2, ensure_ascii=False)


def build_payload(client, awb, amount, amount_mode, ship, invoice_num, date_iso):
    """UI inputs → engine data-dict (pure function, testable)."""
    return {
        "invoice_num": invoice_num,
        "invoice_date": clean_date_string(date_iso),
        "client_name": client["name"].strip(),
        "client_address1": client["address1"].strip(),
        "client_address2": client["address2"].strip(),
        "client_gstin": client["gstin"].strip().upper(),
        "client_state": client.get("state", "").strip(),
        "amount_mode": amount_mode,               # 'inclusive' | 'exclusive'
        "base_charge": float(amount),
        "awb": (awb or "").strip(),
        "consignee_name": ship.get("consignee", "").strip(),
        "client_order_id": ship.get("order_id", "").strip(),
        "origin_pincode": str(ship.get("origin_pincode", "485446")).strip(),
        "destination_pincode": str(ship.get("destination_pincode", "")).strip(),
        "weight": ship.get("weight", "").strip(),
    }


def safe_name(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", (text or "invoice").strip()) or "invoice"


def tmp_upload_path(uploaded) -> str:
    base = "/tmp" if os.name != "nt" else os.environ.get("TEMP", ".")
    return os.path.join(base, uploaded.name)


# --------------------------------------------------------------------------- #
#  Sidebar: FIXED client (Billed To)
# --------------------------------------------------------------------------- #

profile = load_profile()

with st.sidebar:
    st.header("🏢 Billed To (Fixed Client)")
    client = {
        "name": st.text_input("Client Firm Name", value=profile.get("client_name", DEFAULT_CLIENT["client_name"]),
                              key="c_name"),
        "address1": st.text_input("Address Line 1", value=profile.get("client_address1", DEFAULT_CLIENT["client_address1"]),
                                  key="c_a1"),
        "address2": st.text_input("Address Line 2", value=profile.get("client_address2", DEFAULT_CLIENT["client_address2"]),
                                  key="c_a2"),
        "gstin": st.text_input("Client GSTIN", value=profile.get("client_gstin", DEFAULT_CLIENT["client_gstin"]),
                               placeholder="23ADFPB7291L2ZG", key="c_gst"),
        "state": st.text_input("State (optional — GSTIN se auto)",
                               value=profile.get("client_state", DEFAULT_CLIENT["client_state"]), key="c_st"),
    }
    if st.button("💾 Save as Default Client", use_container_width=True):
        save_profile({k: v for k, v in client.items()})
        st.success("Client profile saved ✓ (clients/default.json — GitHub pe nahi jata)")

# --------------------------------------------------------------------------- #
#  Step 1: Invoice upload → AUTO-EXTRACT
# --------------------------------------------------------------------------- #

st.title("🧾 GST Tax Invoice Generator")
st.markdown("🟢 **v2.0** (single-file, 2026-09-05) — ye line dikh rahi hai to **sahi nayi file live hai** ✅")
st.caption(f"{SUPPLIER_NAME} · {SUPPLIER_ADDRESS1}, {SUPPLIER_ADDRESS2} · "
           f"GSTIN {SUPPLIER_GSTIN} ({SUPPLIER_STATE} {SUPPLIER_STATE_CODE})")

st.subheader("1️⃣ Delhivery Invoice PDF upload karo")
st.caption("AWB, Consignee, Order ID, Destination Pincode — sab **automatic** nikal jayega. "
           "(Image label bhi chalega — OCR)")

up = None  # asli uploader neeche callback ke saath hai

# --------------------------------------------------------------------------- #
#  Step 1: Invoice upload → AUTO-EXTRACT (on_change callback — Streamlit-safe)
# --------------------------------------------------------------------------- #

def _on_upload() -> None:
    """Callback: rerun se PEHLE chalta hai — widget state set karna allowed hai."""
    f = st.session_state.uploader
    if f is None:
        return
    path = tmp_upload_path(f)
    with open(path, "wb") as fh:
        fh.write(f.getbuffer())
    try:
        data = extract_from_document(path)
        st.session_state["extracted"] = data
        if data.get("awb"):
            st.session_state.awb = data["awb"]
        if data.get("consignee_name"):
            st.session_state.consignee = data["consignee_name"]
        if data.get("client_order_id"):
            st.session_state.oid = data["client_order_id"]
        if data.get("destination_pincode"):
            st.session_state.dest_pin = data["destination_pincode"]
    except Exception as exc:  # noqa: BLE001
        st.session_state["extracted"] = {}
        st.session_state["extract_error"] = str(exc)


up = st.file_uploader("📄 Invoice PDF / Label image",
                      type=["pdf", "png", "jpg", "jpeg", "webp", "bmp", "tif", "tiff"],
                      key="uploader", on_change=_on_upload)

ex = st.session_state.get("extracted", {})
if st.session_state.get("extract_error"):
    st.error(f"Extraction fail: {st.session_state['extract_error']} — "
             "neeche fields manually bharo.")
elif ex:
    st.success(f"✅ Extract ho gaya! [{ex.get('_source', '?')}]")
    st.info("📋 **Extracted values** (neeche auto-fill hain — galat lage to badal lo):\n\n"
            f"- **AWB:** `{ex.get('awb', '—')}`\n"
            f"- **Consignee:** {ex.get('consignee_name', '—')}\n"
            f"- **Client Order ID (Sales Number):** `{ex.get('client_order_id', '—')}`\n"
            f"- **Destination Pincode:** `{ex.get('destination_pincode', '—')}`")

# ---- extracted/manual fields (auto-filled, editable) ----
col1, col2 = st.columns(2)
awb = col1.text_input("AWB / Tracking Number *", key="awb")
consignee = col2.text_input("Consignee / Delivered To", key="consignee")

colA, colB = st.columns(2)
order_id = colA.text_input("Client Order ID", key="oid")
dest_pin = colB.text_input("Destination Pincode", key="dest_pin")

# --------------------------------------------------------------------------- #
#  Step 2: Sirf WEIGHT + AMOUNT (user input)
# --------------------------------------------------------------------------- #

st.subheader("2️⃣ Weight aur Shipping Amount daalo")

w1, w2, w3 = st.columns(3)
weight = w1.text_input("Weight *", placeholder="350 gm / 2.94 kg", key="wt")
amount = w2.number_input("Shipping Amount (₹) *", min_value=1.0,
                         max_value=10_000_000.0, value=82.0, step=1.0, key="amt")
amount_mode_ui = w3.radio("Amount kya hai?",
                          ["Final Total (GST samet)", "Base Charge (GST alag)"],
                          key="mode")
amount_mode = "inclusive" if "Final" in amount_mode_ui else "exclusive"
st.caption("ℹ️ Final Total = reverse GST (base = total ÷ 1.18) | "
           "Base Charge = GST upar se add (18%)")

with st.expander("⚙️ Advanced (optional)"):
    o_col1, o_col2 = st.columns(2)
    inv_date = o_col1.date_input("Invoice Date", datetime.date.today(), key="dt")
    origin_pin = o_col2.text_input("Origin Pincode",
                                   value=profile.get("origin_pincode", DEFAULT_CLIENT["origin_pincode"]),
                                   key="opin")
    manual_num = st.checkbox("Invoice Number khud doon? (warna auto: MSE/FY/0001)",
                             key="mn")
    invoice_num = ""
    if manual_num:
        invoice_num = st.text_input("Invoice Number", key="inv_no")

st.divider()

# --------------------------------------------------------------------------- #
#  Generate
# --------------------------------------------------------------------------- #

if st.button("⚡ Generate Invoice", type="primary", use_container_width=True):
    errors = []
    if not client["name"].strip():
        errors.append("Sidebar me Client Firm Name daalo (ek baar — Save kar dena).")
    if not awb.strip():
        errors.append("AWB / Tracking Number daalo.")
    if not weight.strip():
        errors.append("Weight daalo.")
    if errors:
        for e in errors:
            st.error(e)
    else:
        if client["gstin"] and not is_valid_gstin(client["gstin"]):
            st.warning("⚠️ Client GSTIN ka format theek nahi lag raha — phir bhi ban raha hai.")
        elif not client["gstin"]:
            st.warning("⚠️ Client GSTIN khali hai — B2B invoice me hona chahiye.")
        try:
            if not invoice_num:
                invoice_num = next_invoice_num(str(inv_date), COUNTER_FILE)
            payload = build_payload(
                client, awb, amount, amount_mode,
                {"consignee": consignee, "order_id": order_id, "weight": weight,
                 "origin_pincode": origin_pin, "destination_pincode": dest_pin},
                invoice_num, str(inv_date),
            )
            os.makedirs(GENERATED_DIR, exist_ok=True)
            out_pdf = os.path.join(GENERATED_DIR,
                                   f"GST_invoice_{safe_name(consignee)}_{safe_name(awb)}.pdf")
            out_html = out_pdf.replace(".pdf", ".html")
            result = generate_gst_invoice(payload, out_pdf, out_html)

            with open(out_pdf, "rb") as fh:
                st.session_state["pdf_bytes"] = fh.read()
            st.session_state["pdf_name"] = os.path.basename(out_pdf)
            # Auto-archive: <Client>/<YYYY-MM Month>/ me PDF, generated/ cleanup
            st.session_state["archived"] = archive_invoice(
                out_pdf, out_html, str(inv_date), client["name"])
            st.session_state["result"] = {
                "invoice_num": result["invoice_num"],
                "base": float(result["base"]),
                "cgst": float(result["cgst"]),
                "sgst": float(result["sgst"]),
                "igst": float(result["igst"]),
                "total": float(result["total"]),
                "same_state": result["same_state"],
                "words": amount_in_words(result["total"]),
            }
        except Exception as exc:
            st.error(f"❌ Generate fail: {exc}")

# ---- result + download (rerun-proof) ----
if "pdf_bytes" in st.session_state:
    r = st.session_state["result"]
    st.success(f"✅ Invoice {r['invoice_num']} ready!")
    c1, c2, c3 = st.columns(3)
    c1.metric("Base (Taxable)", f"₹{r['base']:,.2f}")
    if r["same_state"]:
        c2.metric("CGST + SGST (9%+9%)", f"₹{r['cgst']:,.2f} + ₹{r['sgst']:,.2f}")
    else:
        c2.metric("IGST (18%)", f"₹{r['igst']:,.2f}")
    c3.metric("Total", f"₹{r['total']:,.2f}")
    st.info(f"**Amount in Words:** {r['words']}")
    st.download_button("⬇️ Download Invoice PDF", data=st.session_state["pdf_bytes"],
                       file_name=st.session_state["pdf_name"],
                       mime="application/pdf", use_container_width=True)
    st.caption(f"Archive: {st.session_state.get('archived', '')}")

st.divider()
st.caption("Maa Sharda Enterprises · Courier & Logistics Services · SAC 9968 | "
           "Total = Base + GST (product/COD kabhi include nahi)")

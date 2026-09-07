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

        if line.strip().lower() in ("prepaid", "cod", "paid", "pre-paid", "pre paid"):
            val = line.strip().lower()
            rec["payment_type"] = "COD" if val == "cod" else "Prepaid"

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


if __name__ == "__main__":
    import sys
    import json

    if len(sys.argv) < 2:
        print("Usage: python pdf_parser.py <delhivery_invoice.pdf>")
        sys.exit(1)
    for r in parse_delhivery_pdf(sys.argv[1]):
        print(json.dumps(r, indent=2, ensure_ascii=False))

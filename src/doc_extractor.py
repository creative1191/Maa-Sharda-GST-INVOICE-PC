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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pdf_parser import deduplicate_line  # noqa: E402

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

PAYMENT_LABEL = re.compile(
    r"payment\s*(?:method|mode|type)?\s*[:\-]?\s*(pre\s*-?\s*paid|cod)",
    re.IGNORECASE)
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


def _find_payment(text: str) -> str:
    """Payment method detect: 'Prepaid' ya 'COD' (sirf method — amount kabhi nahi)."""
    m = PAYMENT_LABEL.search(text)
    if m:
        val = m.group(1).lower().replace(" ", "").replace("-", "")
        return "COD" if val == "cod" else "Prepaid"
    if re.search(r"\bCOD\b", text):
        return "COD"
    if re.search(r"pre\s*-?\s*paid", text, re.IGNORECASE):
        return "Prepaid"
    return ""


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
        "payment_type": _find_payment(flat),
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
        from pdf_parser import parse_delhivery_pdf

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
                "payment_type": r.get("payment_type", ""),
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


if __name__ == "__main__":
    import json

    if len(sys.argv) < 2:
        print("Usage: python doc_extractor.py <label.png | invoice.pdf>")
        sys.exit(1)
    data = extract_from_document(sys.argv[1])
    data.pop("raw_text", None)
    print(json.dumps(data, indent=2, ensure_ascii=False))

"""
generate_gst_invoice.py
-----------------------
B2B GST Tax Invoice CLI (Maa Sharda Enterprises → Client).

Examples:

  # Inter-state (IGST 18%) — client Haryana:
  python src/generate_gst_invoice.py ^
    --client_name "Sharma Trading Co." ^
    --client_address1 "42, Civil Lines" ^
    --client_address2 "Gurugram, Haryana - 122001" ^
    --client_gstin "06ABCDE1234F1Z5" ^
    --base_charge 1000 ^
    --awb "34084710004266" --origin_pincode 485446 ^
    --destination_pincode 122001 --weight "0.5 kg"

  # Intra-state (CGST 9% + SGST 9%) — client Madhya Pradesh:
  python src/generate_gst_invoice.py --client_name "Bhopal Retail Pvt Ltd" ^
    --client_address1 "Plot 7, Industrial Area" ^
    --client_address2 "Bhopal, Madhya Pradesh - 462001" ^
    --client_gstin "23AACCU9603R1ZM" ^
    --base_charge 500 --awb "34084710004620" ^
    --origin_pincode 485446 --destination_pincode 462001 --weight "0.5 kg"

Invoice number auto-generates sequentially (MSE/2026-27/0001…) unless
--invoice_num is given.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gst_invoice_engine import (  # noqa: E402
    SUPPLIER_GSTIN,
    amount_in_words,
    archive_invoice,
    generate_gst_invoice,
    is_valid_gstin,
    next_invoice_num,
)


def _slug(text: str) -> str:
    """Filename-safe customer name (GST_invoice_<NAME>_<AWB>.pdf ke liye)."""
    import re as _re
    return _re.sub(r"[^A-Za-z0-9]+", "_", (text or "").strip()).strip("_")[:30] or "output"


def main(argv=None):
    ap = argparse.ArgumentParser(description="B2B GST Tax Invoice Generator")
    ap.add_argument("--client_name", default="",
                    help="Client firm ka naam (khali → client profile se auto)")
    ap.add_argument("--client_profile",
                    default=os.path.join(os.path.dirname(os.path.dirname(
                        os.path.abspath(__file__))), "clients", "default.json"),
                    help="FIXED client ki saved details (JSON) — ek baar bharni hai, "
                         "fir har invoice me auto use hoti hai")
    ap.add_argument("--client_address1", default="")
    ap.add_argument("--client_address2", default="")
    ap.add_argument("--client_gstin", default="", help="Client GSTIN (15 chars)")
    ap.add_argument("--client_state", default="",
                    help="Optional — auto-detected from GSTIN otherwise")
    ap.add_argument("--base_charge", type=float, required=True,
                    help="Base shipping charge (taxable value) in ₹ — "
                         "ya --total_inclusive ke saath = final total (GST samet)")
    ap.add_argument("--total_inclusive", action="store_true",
                    help="Input amount ko FINAL TOTAL (GST-inclusive) maano — "
                         "base = total/1.18 reverse-calculate hoga")
    ap.add_argument("--doc", default="",
                    help="Shipping label / receipt / Delhivery PDF (image ya PDF) — "
                         "AWB + Consignee auto-extract hoga (OCR)")
    ap.add_argument("--awb", default="", help="AWB / Tracking ID")
    ap.add_argument("--consignee", default="",
                    help="Consignee / Delivered To (end-customer name)")
    ap.add_argument("--client_order_id", default="",
                    help="Client's internal order number (reconciliation ke liye)")
    ap.add_argument("--origin_pincode", default="")
    ap.add_argument("--destination_pincode", default="")
    ap.add_argument("--weight", default="")
    ap.add_argument("--payment", default="", choices=["", "Prepaid", "COD"],
                    help="Payment method (PDF/OCR se auto — override ke liye)")
    ap.add_argument("--invoice_num", default="",
                    help="Manual invoice number (auto-sequential if empty)")
    ap.add_argument("--date", default="", help="Invoice date YYYY-MM-DD (default today)")
    ap.add_argument("--counter_file",
                    default=os.path.join("generated", "gst_invoice_counter.txt"))
    ap.add_argument("--out", default="")
    ap.add_argument("--out_html", default="")
    args = ap.parse_args(argv)

    # ---- FIXED CLIENT PROFILE (ek baar bhari, har invoice me auto) ----
    # Precedence: explicit flags > profile JSON > empty
    profile = {}
    if os.path.exists(args.client_profile):
        try:
            with open(args.client_profile, encoding="utf-8") as fh:
                profile = json.load(fh)
            if profile.get("client_name"):
                print(f"📋 Client profile loaded: {args.client_profile} "
                      f"→ {profile['client_name']}")
        except Exception as exc:  # noqa: BLE001
            print(f"⚠️  Client profile load nahi hui ({exc}) — flags use honge.")
    client_name = args.client_name or profile.get("client_name", "")
    client_address1 = args.client_address1 or profile.get("client_address1", "")
    client_address2 = args.client_address2 or profile.get("client_address2", "")
    client_gstin = args.client_gstin or profile.get("client_gstin", "")
    client_state = args.client_state or profile.get("client_state", "")
    origin_pin = str(args.origin_pincode or profile.get("origin_pincode", ""))
    if not client_name:
        ap.error("Client name chahiye — --client_name do ya "
                 f"'{args.client_profile}' me details bharein")

    # ---- warnings (invoice legally valid ho, ye zaroori hai) ----
    if not is_valid_gstin(SUPPLIER_GSTIN):
        print(f"⚠️  WARNING: Supplier GSTIN '{SUPPLIER_GSTIN}' valid format nahi hai "
              f"(15 chars, e.g. 23ABCDE1234F1Z5).\n   "
              f"src/gst_invoice_engine.py ke top par SUPPLIER_GSTIN update karein!")
    if not client_gstin:
        print("⚠️  WARNING: Client GSTIN khali hai — B2B invoice me client GSTIN hona chahiye.")
    elif not is_valid_gstin(client_gstin):
        print(f"⚠️  WARNING: Client GSTIN '{client_gstin}' format invalid lag raha hai.")

    # ---- document extraction (auto-fill AWB + consignee) ----
    extracted = {}
    if args.doc:
        from doc_extractor import extract_from_document

        try:
            extracted = extract_from_document(args.doc)
            print(f"🔎 Extracted from {args.doc} [{extracted.get('_source', '?')}]:")
            for k in ("awb", "consignee_name", "consignee_address1",
                      "consignee_address2", "destination_pincode"):
                if extracted.get(k):
                    print(f"   {k:20s}= {extracted[k]}")
        except Exception as exc:  # noqa: BLE001
            print(f"⚠️  Extraction failed: {exc}\n   Flags wale values use honge.")

    # explicit flags > extracted values
    awb = args.awb or extracted.get("awb", "")
    consignee = args.consignee or extracted.get("consignee_name", "")
    # Client Order ID: flag > Sales Number (Delhivery PDF se auto)
    order_id = args.client_order_id or extracted.get("client_order_id", "")
    dest_pin = str(args.destination_pincode or extracted.get("destination_pincode", ""))
    payment = args.payment or extracted.get("payment_type", "")

    invoice_num = args.invoice_num
    if not invoice_num:
        import datetime
        date = args.date or str(datetime.date.today())
        invoice_num = next_invoice_num(date, args.counter_file)

    out_pdf = args.out or os.path.join(
        "generated",
        f"GST_invoice_{_slug(consignee)}_{awb or 'output'}.pdf")
    out_html = args.out_html or out_pdf.rsplit(".", 1)[0] + ".html"

    data = {
        "invoice_num": invoice_num,
        "invoice_date": args.date,
        "client_name": client_name,
        "client_address1": client_address1,
        "client_address2": client_address2,
        "client_gstin": client_gstin,
        "client_state": client_state,
        "base_charge": args.base_charge,
        "amount_mode": "inclusive" if args.total_inclusive else "exclusive",
        "awb": awb,
        "consignee_name": consignee,
        "client_order_id": order_id,
        "origin_pincode": origin_pin,
        "destination_pincode": dest_pin,
        "weight": args.weight,
        "payment_method": payment,
    }
    result = generate_gst_invoice(data, out_pdf, out_html)

    tax_info = ("CGST 9% + SGST 9%" if result["same_state"] else "IGST 18%")
    mode_info = ("INCLUSIVE input (reverse calc: base = total/1.18)"
                 if result.get("amount_mode") == "inclusive" else "exclusive base")
    print(f"\n✅ GST Invoice generated  [{tax_info} | {mode_info}]")
    print(f"   Invoice No : {result['invoice_num']}")
    print(f"   Base       : {result['base']}")
    print(f"   CGST       : {result['cgst']}   SGST: {result['sgst']}   IGST: {result['igst']}")
    print(f"   TOTAL      : {result['total']}")
    print(f"   Words      : {amount_in_words(result['total'])}")
    # Archive: PDF -> Shree Mahadev/<month>/ folder, generated/ cleanup
    archived_pdf = archive_invoice(out_pdf, out_html, result["invoice_date"], client_name)
    print(f"   PDF (saved): {archived_pdf}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

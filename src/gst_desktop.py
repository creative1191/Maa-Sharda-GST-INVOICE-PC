"""
gst_desktop.py
--------------
GST Invoice Generator — Premium Business Dashboard UI (CustomTkinter).

Design: dark navy sidebar + light workspace + indigo accent,
card-based steps, rounded inputs, big indigo CTA.

Pages: Invoice (main) · History · Templates · Settings · Backup · Help

Bug-fix vs old app: nayi file select karte hi saare fields FRESH data se
overwrite hote hain (purana data atakta nahi) — app band karne ki zaroorat nahi.

Payment method (Prepaid/COD) PDF/OCR se auto-scan hota hai.

EXE Build (Windows):
    pyinstaller --onefile --windowed --collect-all customtkinter ^
        --name GST_Invoice_Generator src/gst_desktop.py
"""

import datetime
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import customtkinter as ctk
from tkinter import filedialog, messagebox

from gst_invoice_engine import (
    SUPPLIER_GSTIN,
    SUPPLIER_NAME,
    amount_in_words,
    archive_invoice,
    generate_gst_invoice,
    is_valid_gstin,
    next_invoice_num,
)
from doc_extractor import extract_from_document

FROZEN = getattr(sys, "frozen", False)
BASE_DIR = os.path.dirname(sys.executable) if FROZEN else os.path.dirname(HERE)

DEFAULT_CLIENT = {
    "client_name": "SHREE MAHADEV",
    "client_address1": "00, VAN VIBHAG KE BAGAL ME",
    "client_address2": "SABJI MANDI KE SAMNE, NAGOD, SATNA, MADHYA PRADESH, 485446",
    "client_gstin": "23ADFPB7291L2ZG",
    "client_state": "Madhya Pradesh",
    "origin_pincode": "485446",
}

# ---- palette: navy sidebar + light workspace + indigo accent ---------------- #
NAVY = "#171b2e"        # sidebar
NAVY_CARD = "#1f2440"   # sidebar hover / active base
INDIGO = "#6366f1"      # primary accent / CTA
INDIGO_HOV = "#4f46e5"
INDIGO_SOFT = "#eef0fe"  # light indigo tint (active nav text bg etc.)
CONTENT = "#f4f5fa"     # workspace background
CARD = "#ffffff"
TEXT = "#1e2438"
MUTED = "#6b7280"
FIELD = "#f8f9fc"
BORDER = "#e6e9f2"
CONSOLE_BG = "#101322"
CONSOLE_FG = "#c7d0e4"
GOOD = "#0f766e"

MODE_FINAL = "Final Total (GST samet)"
MODE_BASE = "Base Charge (GST alag)"

PAGES = [
    ("invoice", "Invoice", "🧾"),
    ("history", "History", "🕘"),
    ("templates", "Templates", "📐"),
    ("settings", "Settings", "⚙️"),
    ("backup", "Backup", "💾"),
    ("help", "Help", "❓"),
]


class GSTApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("light")
        self.title("GST Invoice Generator — Maa Sharda Enterprises")
        self.geometry("1180x800")
        self.minsize(1100, 740)
        self.configure(fg_color=CONTENT)

        self.extracted = {}
        self._pending_logs = []
        self._active_btn = None

        self._build_sidebar()
        self._build_content()
        self.show_page("invoice")

        self.load_profile()
        self._flush_logs()
        self.log("Ready! Pehle Delhivery invoice PDF select karo.")
        self.log(f"Data folder: {BASE_DIR}")

    # ================================================================ UI kit
    def log(self, msg):
        if not hasattr(self, "console"):
            self._pending_logs.append(msg)
            return
        self.console.configure(state="normal")
        self.console.insert("end", msg + "\n")
        self.console.see("end")
        self.console.configure(state="disabled")

    def _flush_logs(self):
        for m in self._pending_logs:
            self.log(m)
        self._pending_logs = []

    def card(self, master, title=None, subtitle=None):
        card = ctk.CTkFrame(master, fg_color=CARD, corner_radius=16,
                            border_width=1, border_color=BORDER)
        card.pack(fill="x", padx=28, pady=(12, 0))
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=20, pady=16)
        if title:
            ctk.CTkLabel(inner, text=title, font=ctk.CTkFont(size=14, weight="bold"),
                         text_color=TEXT, anchor="w").pack(fill="x")
        if subtitle:
            ctk.CTkLabel(inner, text=subtitle, font=ctk.CTkFont(size=12),
                         text_color=MUTED, anchor="w").pack(fill="x", pady=(0, 4))
        return inner

    def field(self, master, label, placeholder="", row=0, col=0):
        wrap = ctk.CTkFrame(master, fg_color="transparent")
        wrap.grid(row=row, column=col, sticky="nsew", padx=(0, 14), pady=(6, 2))
        master.columnconfigure(col, weight=1)
        ctk.CTkLabel(wrap, text=label, font=ctk.CTkFont(size=11, weight="bold"),
                     text_color=MUTED, anchor="w").pack(fill="x")
        e = ctk.CTkEntry(wrap, height=36, corner_radius=10, fg_color=FIELD,
                         border_color=BORDER, border_width=1,
                         placeholder_text=placeholder, font=ctk.CTkFont(size=13))
        e.pack(fill="x")
        return e

    # ============================================================== sidebar
    def _build_sidebar(self):
        sb = ctk.CTkFrame(self, width=232, corner_radius=0, fg_color=NAVY)
        sb.pack(side="left", fill="y")
        sb.pack_propagate(False)

        # brand
        brand = ctk.CTkFrame(sb, fg_color="transparent")
        brand.pack(fill="x", padx=20, pady=(24, 18))
        ctk.CTkLabel(brand, text="MSE", font=ctk.CTkFont(size=22, weight="bold"),
                     text_color="#ffffff").pack(anchor="w")
        ctk.CTkLabel(brand, text="GST Invoice Studio",
                     font=ctk.CTkFont(size=12), text_color="#8b93b8").pack(anchor="w")

        ctk.CTkFrame(sb, fg_color="#2a3052", height=1).pack(fill="x", padx=18, pady=(0, 12))

        self.nav_buttons = {}
        for key, label, icon in PAGES:
            btn = ctk.CTkButton(
                sb, text=f"{icon}   {label}", anchor="w", height=42,
                corner_radius=10, fg_color="transparent",
                hover_color=NAVY_CARD, text_color="#aab3d4",
                font=ctk.CTkFont(size=14, weight="bold"),
                command=lambda k=key: self.show_page(k))
            btn.pack(fill="x", padx=14, pady=3)
            self.nav_buttons[key] = btn

        # sidebar footer
        foot = ctk.CTkFrame(sb, fg_color="transparent")
        foot.pack(side="bottom", fill="x", padx=18, pady=(0, 18))
        ctk.CTkLabel(foot, text=SUPPLIER_NAME, font=ctk.CTkFont(size=11, weight="bold"),
                     text_color="#aab3d4", anchor="w").pack(fill="x")
        ctk.CTkLabel(foot, text=f"GSTIN {SUPPLIER_GSTIN}",
                     font=ctk.CTkFont(size=11), text_color="#69708f", anchor="w").pack(fill="x")
        ctk.CTkLabel(foot, text="SAC 9968 · v3.0",
                     font=ctk.CTkFont(size=11), text_color="#69708f", anchor="w").pack(fill="x")

    def show_page(self, key):
        for k, btn in self.nav_buttons.items():
            if k == key:
                btn.configure(fg_color=INDIGO, text_color="#ffffff",
                              hover_color=INDIGO_HOV)
            else:
                btn.configure(fg_color="transparent", text_color="#aab3d4",
                              hover_color=NAVY_CARD)
        for k, page in getattr(self, "pages", {}).items():
            page.pack_forget()
        page = self.pages[key]
        page.pack(fill="both", expand=True)
        if key == "history":
            self.refresh_history()

    # ============================================================= content
    def _build_content(self):
        self.pages = {}

        wrap = ctk.CTkFrame(self, fg_color="transparent")
        wrap.pack(side="right", fill="both", expand=True)
        self.content_host = wrap

        for key, _label, _icon in PAGES:
            page = ctk.CTkFrame(wrap, fg_color="transparent")
            self.pages[key] = page

        self._page_invoice(self.pages["invoice"])
        self._page_history(self.pages["history"])
        self._page_templates(self.pages["templates"])
        self._page_settings(self.pages["settings"])
        self._page_backup(self.pages["backup"])
        self._page_help(self.pages["help"])

    def _page_head(self, page, title, subtitle):
        head = ctk.CTkFrame(page, fg_color="transparent")
        head.pack(fill="x", padx=28, pady=(24, 2))
        ctk.CTkLabel(head, text=title, font=ctk.CTkFont(size=23, weight="bold"),
                     text_color=TEXT, anchor="w").pack(fill="x")
        ctk.CTkLabel(head, text=subtitle, font=ctk.CTkFont(size=13),
                     text_color=MUTED, anchor="w").pack(fill="x")

    # ---------------------------------------------------------- Invoice page
    def _page_invoice(self, page):
        self._page_head(page, "New Invoice",
                        "PDF select karo → details auto-fill → Weight + Amount → Generate")

        s1 = self.card(page, "1 · Shipment",
                       "Delhivery invoice PDF / label — AWB, Consignee, Order ID, Pincode, Payment auto")
        row = ctk.CTkFrame(s1, fg_color="transparent")
        row.pack(fill="x", pady=(4, 6))
        ctk.CTkButton(row, text="📁  Choose PDF / Image", width=190, height=38,
                      corner_radius=10, fg_color=INDIGO, hover_color=INDIGO_HOV,
                      font=ctk.CTkFont(size=13, weight="bold"),
                      command=self.on_select_doc).pack(side="left")
        ctk.CTkButton(row, text="🔄  Clear", width=90, height=38, corner_radius=10,
                      fg_color="#eef0fe", hover_color="#e2e5fd", text_color=INDIGO,
                      font=ctk.CTkFont(size=13, weight="bold"),
                      command=self.clear_shipment).pack(side="left", padx=10)
        self.lbl_file = ctk.CTkLabel(row, text="koi file select nahi hui",
                                     font=ctk.CTkFont(size=12), text_color=MUTED)
        self.lbl_file.pack(side="left", padx=6)

        g = ctk.CTkFrame(s1, fg_color="transparent")
        g.pack(fill="x")
        self.e_awb = self.field(g, "AWB / Tracking No *", "34084710004266", 0, 0)
        self.e_consignee = self.field(g, "Consignee / Delivered To", "customer name", 0, 1)
        self.e_orderid = self.field(g, "Client Order ID (auto)", "Sales Number", 1, 0)
        self.e_dest = self.field(g, "Destination Pincode", "481880", 1, 1)

        prow = ctk.CTkFrame(s1, fg_color="transparent")
        prow.pack(fill="x")
        pw = ctk.CTkFrame(prow, fg_color="transparent")
        pw.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        prow.columnconfigure(0, weight=1)
        ctk.CTkLabel(pw, text="Payment Method * (auto-scan)", font=ctk.CTkFont(size=11, weight="bold"),
                     text_color=MUTED, anchor="w").pack(fill="x")
        self.e_payment = ctk.CTkOptionMenu(pw, height=36, corner_radius=10,
                                           fg_color=FIELD, button_color=INDIGO,
                                           button_hover_color=INDIGO_HOV,
                                           text_color=TEXT, dropdown_fg_color=CARD,
                                           values=["Prepaid", "COD"],
                                           font=ctk.CTkFont(size=13))
        self.e_payment.set("Prepaid")
        self.e_payment.pack(fill="x")

        s2 = self.card(page, "2 · Weight & Amount", "Bas ye 2 cheezein daalo")
        r2 = ctk.CTkFrame(s2, fg_color="transparent")
        r2.pack(fill="x", pady=(4, 2))
        self.e_weight = self.field(r2, "Weight *", "350 gm / 2.94 kg", 0, 0)
        self.e_amount = self.field(r2, "Shipping Amount (₹) *", "82", 0, 1)
        mrow = ctk.CTkFrame(s2, fg_color="transparent")
        mrow.pack(fill="x", pady=(8, 0))
        ctk.CTkLabel(mrow, text="Amount kya hai?", font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=MUTED).pack(side="left", padx=(0, 10))
        self.mode = ctk.StringVar(value=MODE_FINAL)
        ctk.CTkSegmentedButton(mrow, values=[MODE_FINAL, MODE_BASE],
                               variable=self.mode, selected_color=INDIGO,
                               selected_hover_color=INDIGO_HOV,
                               font=ctk.CTkFont(size=12), height=32,
                               command=self._mode_hint).pack(side="left")
        self.lbl_hint = ctk.CTkLabel(s2, text="", font=ctk.CTkFont(size=11),
                                     text_color=MUTED, anchor="w")
        self.lbl_hint.pack(fill="x", pady=(6, 0))
        self._mode_hint()

        self.btn_gen = ctk.CTkButton(page, text="⚡   Generate Invoice", height=54,
                                     corner_radius=14, fg_color=INDIGO,
                                     hover_color=INDIGO_HOV,
                                     font=ctk.CTkFont(size=17, weight="bold"),
                                     command=self.on_generate)
        self.btn_gen.pack(fill="x", padx=28, pady=(18, 4))

        cc = ctk.CTkFrame(page, fg_color=CARD, corner_radius=16, border_width=1,
                          border_color=BORDER)
        cc.pack(fill="both", expand=True, padx=28, pady=(12, 20))
        ctk.CTkLabel(cc, text="ACTIVITY", font=ctk.CTkFont(size=11, weight="bold"),
                     text_color=MUTED, anchor="w").pack(fill="x", padx=16, pady=(12, 0))
        self.console = ctk.CTkTextbox(cc, font=ctk.CTkFont(family="Consolas", size=12),
                                      fg_color=CONSOLE_BG, text_color=CONSOLE_FG,
                                      corner_radius=12, state="disabled")
        self.console.pack(fill="both", expand=True, padx=16, pady=(4, 14))

    def _mode_hint(self, _value=None):
        if self.mode.get() == MODE_FINAL:
            self.lbl_hint.configure(text="Final Total = reverse GST  "
                                    "(Base = Total ÷ 1.18 · CGST+SGST ya IGST)")
        else:
            self.lbl_hint.configure(text="Base Charge = GST upar se add hoga (18%)")

    # ---------------------------------------------------------- History page
    def _page_history(self, page):
        self._page_head(page, "History", "Saari bani invoices — month-wise archive")
        bar = ctk.CTkFrame(page, fg_color="transparent")
        bar.pack(fill="x", padx=28, pady=(10, 0))
        ctk.CTkButton(bar, text="🔄 Refresh", width=110, height=32, corner_radius=9,
                      fg_color=INDIGO, hover_color=INDIGO_HOV,
                      font=ctk.CTkFont(size=12, weight="bold"),
                      command=self.refresh_history).pack(side="right")
        self.hist_box = ctk.CTkScrollableFrame(page, fg_color=CARD,
                                               corner_radius=16)
        self.hist_box.pack(fill="both", expand=True, padx=28, pady=(12, 20))

    def refresh_history(self):
        for w in self.hist_box.winfo_children():
            w.destroy()
        root = os.path.join(BASE_DIR, "Shree Mahadev")
        found = 0
        if os.path.isdir(root):
            months = sorted(os.listdir(root), reverse=True)
            for month in months:
                mdir = os.path.join(root, month)
                if not os.path.isdir(mdir):
                    continue
                ctk.CTkLabel(self.hist_box, text=f"📅 {month}",
                             font=ctk.CTkFont(size=13, weight="bold"),
                             text_color=INDIGO, anchor="w").pack(fill="x", padx=14,
                                                                 pady=(12, 4))
                for f in sorted(os.listdir(mdir)):
                    if not f.lower().endswith(".pdf"):
                        continue
                    found += 1
                    row = ctk.CTkFrame(self.hist_box, fg_color=FIELD,
                                       corner_radius=10)
                    row.pack(fill="x", padx=14, pady=4)
                    ctk.CTkLabel(row, text="📄 " + f, font=ctk.CTkFont(size=12),
                                 text_color=TEXT, anchor="w").pack(side="left",
                                                                   padx=12, pady=8)
                    ctk.CTkButton(row, text="Open", width=70, height=28,
                                  corner_radius=8, fg_color=INDIGO,
                                  hover_color=INDIGO_HOV,
                                  font=ctk.CTkFont(size=11, weight="bold"),
                                  command=lambda p=os.path.join(mdir, f): self._open(p)
                                  ).pack(side="right", padx=10, pady=6)
        if not found:
            ctk.CTkLabel(self.hist_box, text="Abhi koi invoice nahi bani.\n"
                         "Invoice page par jaake pehli invoice banao!",
                         font=ctk.CTkFont(size=13), text_color=MUTED,
                         justify="left").pack(padx=18, pady=24, anchor="w")
        else:
            self.log(f"🕘 History: {found} invoices mili.")

    # -------------------------------------------------------- Templates page
    def _page_templates(self, page):
        self._page_head(page, "Templates", "Invoice template — fixed GST format")
        t = self.card(page, "Active Template — B2B GST Tax Invoice",
                      "Ye format har invoice me fix hai (compliance ke liye)")
        for line in [
            "•  Header: Maa Sharda Enterprises + GSTIN (supplier details)",
            "•  Billed To: aapka fixed client (Settings me badlo)",
            "•  Service & Shipment box: AWB · Consignee · Order ID · "
            "Service (SAC 9968) · Origin/Destination · Weight · Payment Method",
            "•  Financial table: Base + CGST 9%/SGST 9% (MP) ya IGST 18% = Total",
            "•  Amount in Words + Declaration + Signature block",
            "•  Kabhi nahi: product naam/value, COD amount, quantity",
        ]:
            ctk.CTkLabel(t, text=line, font=ctk.CTkFont(size=13), text_color=TEXT,
                         anchor="w", justify="left").pack(fill="x", pady=3)

    # --------------------------------------------------------- Settings page
    def _page_settings(self, page):
        self._page_head(page, "Settings", "Billed To client (ek baar bharo — sab jagah auto)")
        c = self.card(page, "Fixed Client Details")
        g = ctk.CTkFrame(c, fg_color="transparent")
        g.pack(fill="x")
        self.e_cname = self.field(g, "Firm Name", row=0, col=0)
        self.e_cgstin = self.field(g, "GSTIN", row=0, col=1)
        self.e_caddr1 = self.field(g, "Address Line 1", row=1, col=0)
        self.e_caddr2 = self.field(g, "Address Line 2", row=1, col=1)
        ctk.CTkButton(c, text="💾  Save Client", width=150, height=38, corner_radius=10,
                      fg_color=INDIGO, hover_color=INDIGO_HOV,
                      font=ctk.CTkFont(size=13, weight="bold"),
                      command=self.save_profile).pack(anchor="w", pady=(10, 0))
        sup = self.card(page, "Supplier (locked — GST registered)",
                        "Ye details invoice header me hamesha fix rehti hain")
        for line in [f"•  {SUPPLIER_NAME}",
                     "•  Near Rest House, Panna Satna Road, Nagod, Distt. Satna, MP - 485446",
                     f"•  GSTIN: {SUPPLIER_GSTIN} · State: Madhya Pradesh (23)"]:
            ctk.CTkLabel(sup, text=line, font=ctk.CTkFont(size=13), text_color=TEXT,
                         anchor="w").pack(fill="x", pady=3)

    # ----------------------------------------------------------- Backup page
    def _page_backup(self, page):
        self._page_head(page, "Backup", "Invoices ka archive folder kholo / backup lo")
        b = self.card(page, "Archive Folder",
                      "Saari invoices yahan month-wise save hoti hain")
        ctk.CTkButton(b, text="📂  Open Archive Folder", height=40, corner_radius=10,
                      fg_color=INDIGO, hover_color=INDIGO_HOV,
                      font=ctk.CTkFont(size=13, weight="bold"),
                      command=lambda: self._open(self._archive_root())).pack(anchor="w")
        ctk.CTkLabel(b, text=f"Location: {self._archive_root()}",
                     font=ctk.CTkFont(size=12), text_color=MUTED, anchor="w"
                     ).pack(fill="x", pady=(8, 0))
        ctk.CTkLabel(b, text="Tip: is folder ko copy karke pendrive/cloud me backup "
                     "rakho. Counter file (generated/) delete mat karna — invoice "
                     "number sequence bigad jayega.",
                     font=ctk.CTkFont(size=12), text_color=MUTED, anchor="w",
                     justify="left", wraplength=600).pack(fill="x", pady=(4, 0))

    # ------------------------------------------------------------- Help page
    def _page_help(self, page):
        self._page_head(page, "Help", "Kaise use karein")
        h = self.card(page, "5 Steps")
        for i, line in enumerate([
            "1.  Settings me apna client check karo (ek baar — Save dabana mat bhoolo)",
            "2.  Invoice page → 'Choose PDF / Image' → Delhivery invoice select karo",
            "3.  Details auto-fill ho jayengi (AWB, Consignee, Order ID, Pincode, Payment)",
            "4.  Weight + Shipping Amount daalo (Final Total ya Base Charge chuno)",
            "5.  Generate dabao → PDF banke archive me save + khul jayegi",
        ], 1):
            ctk.CTkLabel(h, text=line, font=ctk.CTkFont(size=13), text_color=TEXT,
                         anchor="w", justify="left").pack(fill="x", pady=4)
        f = self.card(page, "Pro Tips")
        for line in [
            "•  Nayi file select karo to saare fields KHUD update hote hain — app "
            "band karne ki zaroorat nahi (v3 fix)",
            "•  'Clear' button se fields khali kar sakte ho",
            "•  History page me saari purani invoices milti hain",
            "•  Amount 'Final Total' mode me = GST-inclusive (base = total ÷ 1.18)",
        ]:
            ctk.CTkLabel(f, text=line, font=ctk.CTkFont(size=13), text_color=TEXT,
                         anchor="w", justify="left").pack(fill="x", pady=3)

    # ============================================================== helpers
    def _archive_root(self):
        return os.path.join(BASE_DIR, "Shree Mahadev")

    def profile_path(self):
        return os.path.join(BASE_DIR, "clients", "default.json")

    def load_profile(self):
        try:
            with open(self.profile_path(), encoding="utf-8") as fh:
                p = json.load(fh)
            self.log(f"📋 Client profile: {p.get('client_name', '—')}")
        except Exception:
            p = DEFAULT_CLIENT
            self.log("ℹ️ Built-in client defaults (SHREE MAHADEV).")
        self.e_cname.insert(0, p.get("client_name", ""))
        self.e_caddr1.insert(0, p.get("client_address1", ""))
        self.e_caddr2.insert(0, p.get("client_address2", ""))
        self.e_cgstin.insert(0, p.get("client_gstin", ""))

    def save_profile(self):
        p = {"client_name": self.e_cname.get().strip(),
             "client_address1": self.e_caddr1.get().strip(),
             "client_address2": self.e_caddr2.get().strip(),
             "client_gstin": self.e_cgstin.get().strip().upper(),
             "client_state": DEFAULT_CLIENT["client_state"],
             "origin_pincode": DEFAULT_CLIENT["origin_pincode"]}
        os.makedirs(os.path.dirname(self.profile_path()), exist_ok=True)
        with open(self.profile_path(), "w", encoding="utf-8") as fh:
            json.dump(p, fh, indent=2, ensure_ascii=False)
        self.log(f"💾 Client saved: {p['client_name']}")
        messagebox.showinfo("Saved", "Client profile save ho gayi ✓")

    # ------------------------------------------------------ shipment actions
    def _apply_extraction(self, data: dict):
        """NAYI file ka data — hamesha OVERWRITE (yahi v3 ka bug-fix hai)."""
        self.e_awb.delete(0, "end")
        self.e_consignee.delete(0, "end")
        self.e_orderid.delete(0, "end")
        self.e_dest.delete(0, "end")
        if data.get("awb"):
            self.e_awb.insert(0, str(data["awb"]))
        if data.get("consignee_name"):
            self.e_consignee.insert(0, str(data["consignee_name"]))
        if data.get("client_order_id"):
            self.e_orderid.insert(0, str(data["client_order_id"]))
        if data.get("destination_pincode"):
            self.e_dest.insert(0, str(data["destination_pincode"]))
        if data.get("payment_type") in ("Prepaid", "COD"):
            self.e_payment.set(data["payment_type"])

    def clear_shipment(self):
        for e in (self.e_awb, self.e_consignee, self.e_orderid, self.e_dest,
                  self.e_weight):
            e.delete(0, "end")
        self.e_payment.set("Prepaid")
        self.lbl_file.configure(text="koi file select nahi hui", text_color=MUTED)
        self.log("🔄 Fields clear ho gaye.")

    def on_select_doc(self):
        path = filedialog.askopenfilename(
            title="Delhivery Invoice PDF / Label select karo",
            filetypes=[("Invoice files", "*.pdf *.png *.jpg *.jpeg"),
                       ("All files", "*.*")])
        if not path:
            return
        self.lbl_file.configure(text=os.path.basename(path), text_color=TEXT)
        self.log(f"📄 File: {os.path.basename(path)}")
        self.log("🔎 Extract kar raha hoon…")
        try:
            data = extract_from_document(path)
        except Exception as exc:  # noqa: BLE001
            self.log(f"❌ Extraction fail: {exc}")
            self.log("   Fields manually bharo neeche.")
            return
        self.extracted = data
        self._apply_extraction(data)      # <- BUG FIX: hamesha fresh data
        self.log(f"   AWB       : {data.get('awb', '—')}")
        self.log(f"   Consignee : {data.get('consignee_name', '—')}")
        self.log(f"   Order ID  : {data.get('client_order_id', '—')}")
        self.log(f"   Dest. Pin : {data.get('destination_pincode', '—')}")
        self.log(f"   Payment   : {data.get('payment_type', '—')}")
        self.log("✅ Done! Ab Weight + Amount daalo.")

    # ---------------------------------------------------------- generate
    def on_generate(self):
        cname = self.e_cname.get().strip()
        awb = self.e_awb.get().strip()
        weight = self.e_weight.get().strip()
        errors = []
        if not cname:
            errors.append("• Settings me Client Firm Name daalo.")
        if not awb:
            errors.append("• AWB / Tracking Number daalo.")
        if not weight:
            errors.append("• Weight daalo.")
        try:
            amount = float(self.e_amount.get().strip().replace(",", ""))
            if amount <= 0:
                raise ValueError
        except ValueError:
            errors.append("• Amount sahi daalo (jaise 82 ya 250.50).")
        if errors:
            messagebox.showerror("Incomplete", "\n".join(errors))
            return

        gstin = self.e_cgstin.get().strip().upper()
        if gstin and not is_valid_gstin(gstin):
            self.log("⚠️ Client GSTIN ka format ajeeb lag raha hai — phir bhi ban rahi hai.")
        elif not gstin:
            self.log("⚠️ Client GSTIN khali hai — B2B me hona chahiye.")

        default_name = f"GST_invoice_{self._safe(self.e_consignee.get())}_{self._safe(awb)}.pdf"
        out_pdf = filedialog.asksaveasfilename(
            title="Invoice kahan save karni hai?", defaultextension=".pdf",
            initialfile=default_name, initialdir=BASE_DIR,
            filetypes=[("PDF file", "*.pdf")])
        if not out_pdf:
            return

        today = str(datetime.date.today())
        self.btn_gen.configure(state="disabled", text="⏳  Generating…")
        self.update_idletasks()
        try:
            self.log("⚡ Generating…")
            counter = os.path.join(BASE_DIR, "generated", "gst_invoice_counter.txt")
            invoice_num = next_invoice_num(today, counter)
            payload = {
                "invoice_num": invoice_num,
                "invoice_date": today,
                "client_name": cname,
                "client_address1": self.e_caddr1.get().strip(),
                "client_address2": self.e_caddr2.get().strip(),
                "client_gstin": gstin,
                "client_state": DEFAULT_CLIENT["client_state"],
                "amount_mode": "inclusive" if self.mode.get() == MODE_FINAL else "exclusive",
                "base_charge": amount,
                "awb": awb,
                "consignee_name": self.e_consignee.get().strip(),
                "client_order_id": self.e_orderid.get().strip(),
                "origin_pincode": DEFAULT_CLIENT["origin_pincode"],
                "destination_pincode": self.e_dest.get().strip(),
                "weight": weight,
                "payment_method": self.e_payment.get(),
            }
            result = generate_gst_invoice(payload, out_pdf)
            archived = archive_invoice(out_pdf, None, today, cname,
                                       archive_root=BASE_DIR)
            self.log(f"✅ Invoice {result['invoice_num']} ban gayi!")
            self.log(f"   Base : {result['base']}  CGST: {result['cgst']}  "
                     f"SGST: {result['sgst']}  IGST: {result['igst']}")
            self.log(f"   TOTAL: {result['total']}  ({amount_in_words(result['total'])})")
            self.log(f"   Payment: {self.e_payment.get()}")
            self.log(f"📁 Archive: {archived}")
            if messagebox.askyesno("Success 🎉",
                                   f"Invoice {result['invoice_num']} ready!\n"
                                   f"Total: {result['total']}\n\n"
                                   f"PDF kholti hai?"):
                self._open(archived)
        except Exception as exc:  # noqa: BLE001
            self.log(f"❌ ERROR: {exc}")
            messagebox.showerror("Generate Error", str(exc))
        finally:
            self.btn_gen.configure(state="normal", text="⚡   Generate Invoice")

    @staticmethod
    def _safe(t):
        return re.sub(r"[^A-Za-z0-9]+", "_", (t or "").strip())[:30] or "output"

    @staticmethod
    def _open(path):
        try:
            if sys.platform.startswith("win"):
                os.startfile(path)  # noqa: S606
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception:  # noqa: BLE001
            pass


def main():
    GSTApp().mainloop()


if __name__ == "__main__":
    main()

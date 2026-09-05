"""
gst_desktop.py
--------------
GST Invoice Generator — Windows Desktop App (Tkinter GUI).

Workflow (chat wala hi):
  Step 1: Delhivery invoice PDF (ya label image) select karo
          → AWB, Consignee, Client Order ID, Destination Pincode AUTO-EXTRACT
  Step 2: Weight + Shipping Amount daalo (Final Total / Base Charge)
  Generate → PDF save + auto-archive (<exe folder>/Shree Mahadev/<Month>/)

EXE Build (Windows):
    pyinstaller --onefile --windowed --name GST_Invoice_Generator src/gst_desktop.py
    (ya seedha build_exe.bat double-click karo)
"""

import datetime
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

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
from pdf_parser import clean_date_string

# EXE (frozen) me: exe ke folder ka use karo — warna project root
FROZEN = getattr(sys, "frozen", False)
BASE_DIR = os.path.dirname(sys.executable) if FROZEN else os.path.dirname(HERE)

# Fixed client ke built-in defaults (profile file na ho to bhi pre-filled)
DEFAULT_CLIENT = {
    "client_name": "SHREE MAHADEV",
    "client_address1": "00, VAN VIBHAG KE BAGAL ME",
    "client_address2": "SABJI MANDI KE SAMNE, NAGOD, SATNA, MADHYA PRADESH, 485446",
    "client_gstin": "23ADFPB7291L2ZG",
    "client_state": "Madhya Pradesh",
    "origin_pincode": "485446",
}

APP_BG = "#f0f4f8"
CARD_BG = "#ffffff"
BTN_DARK = "#0f172a"
BTN_RED = "#d9534f"
TEXT_DARK = "#0f172a"
TEXT_MUTED = "#64748b"


class GSTInvoiceApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("🧾 GST Invoice Generator — Maa Sharda Enterprises")
        self.configure(bg=APP_BG)
        self.geometry("720x780")
        self.minsize(660, 700)

        self.extracted = {}
        self.make_widgets()
        self.log("Ready! Pehle Delhivery invoice PDF select karo.")
        self.log(f"Data folder: {BASE_DIR}")

    # -------------------------------------------------------------- UI kit --
    def _card(self, title):
        card = tk.Frame(self, bg=CARD_BG, highlightthickness=1,
                        highlightbackground="#e2e8f0")
        card.pack(fill="x", padx=14, pady=7)
        inner = tk.Frame(card, bg=CARD_BG)
        inner.pack(fill="x", padx=14, pady=10)
        tk.Label(inner, text=title, bg=CARD_BG, fg=TEXT_DARK, anchor="w",
                 font=("Segoe UI", 10, "bold")).pack(fill="x")
        return inner

    def log(self, msg):
        self.txt.configure(state="normal")
        self.txt.insert("end", msg + "\n")
        self.txt.see("end")
        self.txt.configure(state="disabled")

    # ------------------------------------------------------------------ UI --
    def make_widgets(self):
        top = tk.Frame(self, bg=APP_BG)
        top.pack(fill="x", padx=14, pady=(12, 2))
        tk.Label(top, text="🧾 GST Invoice Generator", bg=APP_BG, fg=TEXT_DARK,
                 font=("Segoe UI", 16, "bold")).pack(anchor="w")
        tk.Label(top, text=f"{SUPPLIER_NAME} · GSTIN {SUPPLIER_GSTIN} · SAC 9968",
                 bg=APP_BG, fg=TEXT_MUTED, font=("Segoe UI", 9)).pack(anchor="w")

        # ---- Client (Billed To) — profile se auto ----
        c = self._card("🏢 BILLED TO (Fixed Client) — clients/default.json se auto")
        self.e_cname = self._entry(c, "Firm Name", 0, 0)
        self.e_caddr1 = self._entry(c, "Address Line 1", 0, 1)
        self.e_caddr2 = self._entry(c, "Address Line 2", 1, 0)
        self.e_cgstin = self._entry(c, "GSTIN", 1, 1)
        self.load_profile()
        tk.Button(c, text="💾 Client Save karo", command=self.save_profile,
                  bg=BTN_DARK, fg="#fff", activebackground="#334155",
                  activeforeground="#fff", relief="flat", cursor="hand2",
                  font=("Segoe UI", 9, "bold"), padx=10, pady=4).pack(anchor="w",
                                                                      pady=(6, 0))

        # ---- Step 1: invoice PDF ----
        s1 = self._card("Step 1: 📁 Delhivery Invoice PDF / Label Image")
        row = tk.Frame(s1, bg=CARD_BG)
        row.pack(fill="x", pady=(6, 2))
        tk.Button(row, text="📁 File Select karo", command=self.on_select_doc,
                  bg=BTN_DARK, fg="#fff", activebackground="#334155",
                  activeforeground="#fff", relief="flat", cursor="hand2",
                  font=("Segoe UI", 10, "bold"), padx=12, pady=5).pack(side="left")
        self.lbl_file = tk.Label(row, text="koi file select nahi hui", bg=CARD_BG,
                                 fg=TEXT_MUTED, font=("Segoe UI", 9))
        self.lbl_file.pack(side="left", padx=8)

        g = tk.Frame(s1, bg=CARD_BG)
        g.pack(fill="x", pady=(6, 0))
        self.e_awb = self._entry(g, "AWB / Tracking No *", 0, 0)
        self.e_consignee = self._entry(g, "Consignee / Delivered To", 0, 1)
        self.e_orderid = self._entry(g, "Client Order ID (Sales No. se auto)", 1, 0)
        self.e_dest = self._entry(g, "Destination Pincode", 1, 1)

        # ---- Step 2: weight + amount ----
        s2 = self._card("Step 2: ⚖️ Weight aur 💰 Shipping Amount")
        r2 = tk.Frame(s2, bg=CARD_BG)
        r2.pack(fill="x", pady=(6, 0))
        self.e_weight = self._lab_entry(r2, "Weight * (jaise 350 gm)")
        self.e_amount = self._lab_entry(r2, "Amount ₹ * (jaise 82)")
        self.e_weight.insert(0, "")
        self.e_amount.insert(0, "82")
        r3 = tk.Frame(s2, bg=CARD_BG)
        r3.pack(fill="x", pady=(6, 0))
        tk.Label(r3, text="Amount kya hai?", bg=CARD_BG, fg=TEXT_MUTED,
                 font=("Segoe UI", 9)).pack(side="left")
        self.mode = tk.StringVar(value="inclusive")
        tk.Radiobutton(r3, text="Final Total (GST samet)", variable=self.mode,
                       value="inclusive", bg=CARD_BG).pack(side="left", padx=6)
        tk.Radiobutton(r3, text="Base Charge (GST alag)", variable=self.mode,
                       value="exclusive", bg=CARD_BG).pack(side="left", padx=6)
        self.lbl_mode_hint = tk.Label(
            s2, text="Final Total = reverse GST (base = total ÷ 1.18)", bg=CARD_BG,
            fg=TEXT_MUTED, font=("Segoe UI", 8), anchor="w")
        self.lbl_mode_hint.pack(fill="x")

        # ---- Generate ----
        gen = tk.Button(self, text="⚡ Generate Invoice", command=self.on_generate,
                        bg=BTN_RED, fg="#fff", activebackground="#c9302c",
                        activeforeground="#fff", relief="flat", cursor="hand2",
                        font=("Segoe UI", 11, "bold"), padx=18, pady=8)
        gen.pack(fill="x", padx=14, pady=(6, 0))

        # ---- Console ----
        cc = tk.Frame(self, bg=CARD_BG, highlightthickness=1,
                      highlightbackground="#e2e8f0")
        cc.pack(fill="both", expand=True, padx=14, pady=(8, 12))
        tk.Label(cc, text="Console", bg=CARD_BG, fg=TEXT_MUTED, anchor="w",
                 font=("Segoe UI", 8, "bold")).pack(fill="x", padx=10, pady=(8, 0))
        self.txt = tk.Text(cc, height=9, bg="#0f172a", fg="#e2e8f0", relief="flat",
                           font=("Consolas", 9), state="disabled", wrap="word",
                           padx=10, pady=8)
        self.txt.pack(fill="both", expand=True, padx=10, pady=(4, 10))

    def _entry(self, parent, label, r, col):
        f = tk.Frame(parent, bg=CARD_BG)
        f.grid(row=r, column=col, sticky="we", padx=(0, 14), pady=3)
        parent.columnconfigure(col, weight=1)
        tk.Label(f, text=label, bg=CARD_BG, fg=TEXT_MUTED,
                 font=("Segoe UI", 8, "bold")).pack(anchor="w")
        e = tk.Entry(f, font=("Segoe UI", 10), bg="#f8fafc", relief="solid", bd=1)
        e.pack(fill="x")
        return e

    def _lab_entry(self, parent, label):
        f = tk.Frame(parent, bg=CARD_BG)
        f.pack(side="left", expand=True, fill="x", padx=(0, 12))
        tk.Label(f, text=label, bg=CARD_BG, fg=TEXT_MUTED,
                 font=("Segoe UI", 8, "bold")).pack(anchor="w")
        e = tk.Entry(f, font=("Segoe UI", 11, "bold"), bg="#f8fafc", relief="solid",
                     bd=1, justify="center")
        e.pack(fill="x")
        return e

    # ------------------------------------------------------------- profile --
    def profile_path(self):
        return os.path.join(BASE_DIR, "clients", "default.json")

    def load_profile(self):
        try:
            with open(self.profile_path(), encoding="utf-8") as fh:
                p = json.load(fh)
            self.log(f"📋 Client profile: {p.get('client_name', '—')}")
        except Exception:
            p = DEFAULT_CLIENT  # profile nahi mili → built-in defaults
            self.log("⚠️ Profile file nahi mili — built-in client defaults use ho rahe hain.")
        self.e_cname.insert(0, p.get("client_name", ""))
        self.e_caddr1.insert(0, p.get("client_address1", ""))
        self.e_caddr2.insert(0, p.get("client_address2", ""))
        self.e_cgstin.insert(0, p.get("client_gstin", ""))

    def save_profile(self):
        p = {"client_name": self.e_cname.get().strip(),
             "client_address1": self.e_caddr1.get().strip(),
             "client_address2": self.e_caddr2.get().strip(),
             "client_gstin": self.e_cgstin.get().strip().upper(),
             "client_state": "", "origin_pincode": "485446"}
        os.makedirs(os.path.dirname(self.profile_path()), exist_ok=True)
        with open(self.profile_path(), "w", encoding="utf-8") as fh:
            json.dump(p, fh, indent=2, ensure_ascii=False)
        self.log(f"💾 Client saved: {p['client_name']}")
        messagebox.showinfo("Saved", "Client profile save ho gayi ✓")

    # ------------------------------------------------------------ Step 1 ----
    def on_select_doc(self):
        path = filedialog.askopenfilename(
            title="Delhivery Invoice PDF / Label select karo",
            filetypes=[("Invoice files", "*.pdf *.png *.jpg *.jpeg"),
                       ("All files", "*.*")])
        if not path:
            return
        self.lbl_file.configure(text=os.path.basename(path), fg=TEXT_DARK)
        self.log(f"📄 File: {os.path.basename(path)}")
        self.log("🔎 Extract kar raha hoon…")
        try:
            data = extract_from_document(path)
        except Exception as exc:  # noqa: BLE001
            self.log(f"❌ Extraction fail: {exc}")
            self.log("   Fields manually bharo neeche.")
            return
        self.extracted = data
        # auto-fill (khali fields me hi — user ka likha overwrite na karo)
        for entry, key in [(self.e_awb, "awb"), (self.e_consignee, "consignee_name"),
                           (self.e_orderid, "client_order_id"),
                           (self.e_dest, "destination_pincode")]:
            if data.get(key) and not entry.get().strip():
                entry.delete(0, "end")
                entry.insert(0, str(data[key]))
        self.log(f"   AWB        : {data.get('awb', '—')}")
        self.log(f"   Consignee  : {data.get('consignee_name', '—')}")
        self.log(f"   Order ID   : {data.get('client_order_id', '—')}")
        self.log(f"   Dest. Pin  : {data.get('destination_pincode', '—')}")
        self.log("✅ Extract complete! Ab Weight + Amount daalo.")

    # ------------------------------------------------------------ Generate --
    def on_generate(self):
        cname = self.e_cname.get().strip()
        awb = self.e_awb.get().strip()
        weight = self.e_weight.get().strip()
        amount_raw = self.e_amount.get().strip()
        errors = []
        if not cname:
            errors.append("Client Firm Name daalo (upar).")
        if not awb:
            errors.append("AWB / Tracking Number daalo.")
        if not weight:
            errors.append("Weight daalo.")
        try:
            amount = float(amount_raw)
            if amount <= 0:
                raise ValueError
        except ValueError:
            errors.append("Amount sahi number daalo (jaise 82 ya 250.50).")
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
            title="Invoice kahan save karni hai?",
            defaultextension=".pdf", initialfile=default_name,
            initialdir=BASE_DIR,
            filetypes=[("PDF file", "*.pdf")])
        if not out_pdf:
            return

        today = str(datetime.date.today())
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
                "client_state": "",
                "amount_mode": self.mode.get(),
                "base_charge": amount,
                "awb": awb,
                "consignee_name": self.e_consignee.get().strip(),
                "client_order_id": self.e_orderid.get().strip(),
                "origin_pincode": "485446",
                "destination_pincode": self.e_dest.get().strip(),
                "weight": weight,
            }
            result = generate_gst_invoice(payload, out_pdf)
            archived = archive_invoice(out_pdf, None, today, cname,
                                       archive_root=BASE_DIR)
            self.log(f"✅ Invoice {result['invoice_num']} ban gayi!")
            self.log(f"   Base  : {result['base']}  |  "
                     f"CGST: {result['cgst']}  SGST: {result['sgst']}  "
                     f"IGST: {result['igst']}")
            self.log(f"   TOTAL : {result['total']}  ({amount_in_words(result['total'])})")
            self.log(f"📁 Archive: {archived}")
            if messagebox.askyesno("Success 🎉",
                                   f"Invoice {result['invoice_num']} ready!\n"
                                   f"Total: {result['total']}\n\n"
                                   f"Archive: {archived}\n\nPDF kholti hai?"):
                self._open(archived)
        except Exception as exc:  # noqa: BLE001
            self.log(f"❌ ERROR: {exc}")
            messagebox.showerror("Generate Error", str(exc))

    @staticmethod
    def _safe(t):
        import re
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
    GSTInvoiceApp().mainloop()


if __name__ == "__main__":
    main()

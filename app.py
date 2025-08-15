from flask import Flask, request, jsonify, render_template
from werkzeug.utils import secure_filename
import os, uuid, re
import pdfplumber

APP_PORT = 5000
UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

app = Flask(__name__)
app.config["UPLOAD_FOLDER"] = UPLOAD_DIR

# Store the latest uploaded invoice
LATEST_INVOICE = None


def extract_invoice_fields(pdf_path: str, original_name: str) -> dict:
    """
    Parse a PDF invoice and extract key fields.
    """
    vendor = ""
    vendor_address = ""
    vendor_email = ""
    invoice_no = ""
    issue_date = ""
    due_date = ""
    total = 0.0
    currency = "USD"

    with pdfplumber.open(pdf_path) as pdf:
        text = "\n".join((page.extract_text() or "") for page in pdf.pages)

    # Extract vendor
    m = re.search(r"(?i)Vendor\s*:\s*(.+)", text)
    if m:
        vendor = m.group(1).strip()

    # Vendor address
    m = re.search(r"(?i)Vendor Address\s*:\s*(.+)", text)
    if m:
        vendor_address = m.group(1).strip()
    else:
        m = re.search(r"(?i)^Address\s*:\s*(.+)", text, flags=re.MULTILINE)
        if m:
            vendor_address = m.group(1).strip()

    # Vendor email
    m = re.search(r"(?i)(Vendor Email|Email)\s*:\s*([^\s]+@[^\s]+)", text)
    if m:
        vendor_email = m.group(2).strip()

    # Invoice number
    m = re.search(r"(?i)Invoice Number\s*:\s*([A-Z0-9\-_]+)", text)
    if m:
        invoice_no = m.group(1).strip()

    # Dates
    m = re.search(r"(?i)Issue Date\s*:\s*(\d{4}-\d{2}-\d{2})", text)
    if m:
        issue_date = m.group(1).strip()

    m = re.search(r"(?i)Due Date\s*:\s*(\d{4}-\d{2}-\d{2})", text)
    if m:
        due_date = m.group(1).strip()

    # Total amount
    m = re.search(r"(?mi)^\s*Total\s*[:\-]?\s*\$?\s*([\d,]+(?:\.\d{2})?)\b", text)
    if m:
        try:
            total = float(m.group(1).replace(",", ""))
        except ValueError:
            total = 0.0

    return {
        "id": str(uuid.uuid4()),
        "file_name": original_name,
        "storage_path": pdf_path,
        "vendor": vendor,
        "vendor_address": vendor_address,
        "vendor_email": vendor_email,
        "invoice_no": invoice_no,
        "issue_date": issue_date,
        "due_date": due_date,
        "currency": currency,
        "total": total,
        "status": "open",
    }


def format_invoice_summary(inv: dict) -> str:
    """
    Return a nicely formatted invoice summary string.
    """
    return (
        "INVOICE\n"
        f"Vendor: {inv.get('vendor','')}\n"
        f"Address: {inv.get('vendor_address','')}\n"
        f"Email: {inv.get('vendor_email','')}\n"
        f"Invoice Number: {inv.get('invoice_no','')}\n"
        f"Issue Date: {inv.get('issue_date','')}\n"
        f"Due Date: {inv.get('due_date','')}\n"
        f"Total: ${float(inv.get('total',0.0)):.2f} {inv.get('currency','')}"
    )


@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")


@app.route("/upload", methods=["POST"])
def upload():
    """
    Handle invoice PDF upload and parse it immediately.
    """
    global LATEST_INVOICE

    if "file" not in request.files:
        return jsonify({"ok": False, "error": "No file part"}), 400

    f = request.files["file"]
    if f.filename == "":
        return jsonify({"ok": False, "error": "No selected file"}), 400

    fname = secure_filename(f.filename)
    saved_name = f"{uuid.uuid4()}__{fname}"
    saved_path = os.path.join(app.config["UPLOAD_FOLDER"], saved_name)
    f.save(saved_path)

    # Parse new invoice every time (no reuse of old data)
    LATEST_INVOICE = extract_invoice_fields(saved_path, fname)

    msg = (
        f"Invoice '{LATEST_INVOICE['file_name']}' uploaded successfully! "
        f"Vendor: {LATEST_INVOICE['vendor'] or 'N/A'}, "
        f"Total: {LATEST_INVOICE['total']} {LATEST_INVOICE['currency']}, "
        f"Due: {LATEST_INVOICE['due_date'] or 'N/A'}."
    )

    # Only return in JSON for frontend upload log
    return jsonify({"ok": True, "invoice": LATEST_INVOICE, "message": msg})


@app.route("/ask", methods=["POST"])
def ask():
    """
    Handle chat questions.
    """
    global LATEST_INVOICE
    data = request.get_json(silent=True) or {}
    q = (data.get("question") or "").strip().lower()

    if not q:
        return jsonify({"answer": "Missing question."}), 400

    # Greet only
    if q in ("hi", "hello", "hey"):
        return jsonify({"answer": "Hi! I can summarize invoices, show what’s due, and more. How can I help?"})

    if "invoice summary" in q:
        if not LATEST_INVOICE:
            return jsonify({"answer": "No invoices available."})
        return jsonify({"answer": format_invoice_summary(LATEST_INVOICE)})

    if "top vendor" in q:
        if not LATEST_INVOICE:
            return jsonify({"answer": "No invoices uploaded yet."})
        return jsonify({"answer": f"Top vendor by spend is {LATEST_INVOICE['vendor'] or 'N/A'} with a total of ${LATEST_INVOICE['total']:.2f}."})

    if "total spend" in q:
        if not LATEST_INVOICE:
            return jsonify({"answer": "Total spend is $0.00"})
        return jsonify({"answer": f"Total spend is ${LATEST_INVOICE['total']:.2f}"})

    # Default response
    return jsonify({"answer": "I can help with invoice queries. Try: 'invoice summary', 'top vendor', or 'total spend'."})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=APP_PORT, debug=True, use_reloader=False)

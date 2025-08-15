from flask import Flask, request, jsonify, render_template
from werkzeug.utils import secure_filename
import os, uuid, datetime, requests, json, random

# -----------------------
# Config
# -----------------------
APP_PORT = 5000
UPLOAD_DIR = "uploads"
OLLAMA_URL = "http://localhost:11434/api/generate"   # Ollama server
MODEL_NAME = "mistral:latest"

os.makedirs(UPLOAD_DIR, exist_ok=True)
app = Flask(__name__)
app.config["UPLOAD_FOLDER"] = UPLOAD_DIR

# In-memory invoice store
INVOICES = []

# -----------------------
# LLM helper
# -----------------------
def call_llm(prompt: str) -> str:
    payload = {"model": MODEL_NAME, "prompt": prompt}
    try:
        resp = requests.post(OLLAMA_URL, json=payload, stream=True, timeout=10)
        resp.raise_for_status()
    except requests.RequestException as e:
        print("LLM request failed:", e)
        return ""  # fallback to local logic

    out = []
    for line in resp.iter_lines():
        if not line:
            continue
        try:
            data = json.loads(line.decode("utf-8"))
            out.append(data.get("response", ""))
        except Exception:
            pass
    return "".join(out).strip()


# -----------------------
# Utilities
# -----------------------
def total_spent():
    return sum(float(inv["total"]) for inv in INVOICES)

def invoice_count():
    return len(INVOICES)

def list_due_within(days: int = 7):
    today = datetime.date.today()
    cutoff = today + datetime.timedelta(days=days)
    due = []
    for inv in INVOICES:
        try:
            d = datetime.date.fromisoformat(inv["due_date"])
            if d <= cutoff:
                due.append(inv)
        except Exception:
            continue
    return due

def compute_top_vendor():
    if not INVOICES:
        return None, 0.0
    tally = {}
    for inv in INVOICES:
        key = (inv["vendor"], inv["invoice_no"])
        tally[inv["vendor"]] = tally.get(inv["vendor"], 0.0) + float(inv["total"])
    vendor = max(tally, key=tally.get)
    return vendor, tally[vendor]

# Generate a fake invoice with randomized total to simulate different files
def fake_extract_invoice(saved_path: str, original_name: str) -> dict:
    today = datetime.date.today()
    due = today + datetime.timedelta(days=random.randint(3, 14))  # random due date
    total_amount = round(random.uniform(100, 1000), 2)  # random total
    subtotal = round(total_amount * 0.9, 2)
    tax = round(total_amount - subtotal, 2)

    existing = [inv for inv in INVOICES if inv["file_name"] == original_name]
    idx = len(INVOICES) + 1
    invoice_no = f"INV-{idx:03d}" if not existing else f"INV-{len(existing)+1:03d}"

    return {
        "id": str(uuid.uuid4()),
        "file_name": original_name,
        "storage_path": saved_path,
        "vendor": f"Vendor {random.randint(1,5)}",  # random vendor
        "invoice_no": invoice_no,
        "issue_date": today.isoformat(),
        "due_date": due.isoformat(),
        "currency": "USD",
        "line_items": [
            {"desc": "Product A", "qty": random.randint(1,5), "unit_price": round(total_amount/2,2), "amount": round(total_amount/2,2)},
            {"desc": "Product B", "qty": random.randint(1,3), "unit_price": round(total_amount/2,2), "amount": round(total_amount/2,2)},
        ],
        "subtotal": subtotal,
        "tax": tax,
        "total": total_amount,
        "status": "open",
    }


# -----------------------
# Polite chatbot + local fallback
# -----------------------
def polite_chat_response(user_msg: str, invoices: list[dict]) -> str:
    user_lower = user_msg.lower().strip()

    if user_lower in ["hi", "hello", "hey"]:
        return "Hi! I can help you check invoices, show due payments, or summarize invoice info."

    if any(kw in user_lower for kw in ["what else", "what can you do", "help", "capabilities", "features"]):
        return ("I can help you:\n"
                "- Show invoices due this week\n"
                "- Summarize invoices\n"
                "- Tell you the top vendor by spend\n"
                "- Answer basic finance questions like total spend, number of invoices, etc.")

    # Basic finance info
    if "total spend" in user_lower or "total amount" in user_lower:
        return f"The total spend across all invoices is ${total_spent():.2f}."
    if "number of invoices" in user_lower or "how many invoices" in user_lower:
        return f"There are {invoice_count()} invoices uploaded so far."

    # Top vendor
    if "top vendor" in user_lower:
        vendor, total = compute_top_vendor()
        if vendor:
            return f"Top vendor by spend is {vendor} with a total of ${total:.2f}."
        else:
            return "No invoices uploaded yet."

    # Invoice summary / due
    if ("due" in user_lower and "week" in user_lower) or "invoice summary" in user_lower:
        due = list_due_within(7)
        if due:
            seen = set()
            lines = []
            for i in due:
                key = (i["vendor"], i["invoice_no"])
                if key not in seen:
                    seen.add(key)
                    lines.append(f"- {i['vendor']}: {i['total']} {i['currency']} due {i['due_date']} ({i['invoice_no']})")
            return "Here’s a summary of invoices due this week:\n" + "\n".join(lines)
        else:
            return "No invoices are due in the next 7 days."

    # fallback to LLM
    llm_resp = call_llm(user_msg)
    if llm_resp:
        return llm_resp
    else:
        return f"I'm not connected to the LLM right now. Based on uploaded invoices:\n- Total invoices: {invoice_count()}\n- Total spend: ${total_spent():.2f}"


# -----------------------
# Routes
# -----------------------
@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"ok": True})

@app.route("/upload", methods=["POST"])
def upload():
    if "file" not in request.files:
        return jsonify({"ok": False, "error": "No file part"}), 400
    f = request.files["file"]
    if f.filename == "":
        return jsonify({"ok": False, "error": "No selected file"}), 400

    fname = secure_filename(f.filename)
    file_id = str(uuid.uuid4())
    saved_name = f"{file_id}__{fname}"
    saved_path = os.path.join(app.config["UPLOAD_FOLDER"], saved_name)
    f.save(saved_path)

    inv = fake_extract_invoice(saved_path, fname)
    INVOICES.append(inv)

    llm_msg = f"Invoice '{inv['file_name']}' uploaded successfully! Vendor: {inv['vendor']}, Total: {inv['total']} {inv['currency']}, Due: {inv['due_date']}."
    return jsonify({"ok": True, "invoice": inv, "message": llm_msg})

@app.route("/ask", methods=["POST"])
def ask():
    data = request.get_json(silent=True) or {}
    q = data.get("question", "").strip()
    if not q:
        return jsonify({"error": "Missing 'question'"}), 400

    answer = polite_chat_response(q, INVOICES)
    return jsonify({"answer": answer})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=APP_PORT, debug=True)

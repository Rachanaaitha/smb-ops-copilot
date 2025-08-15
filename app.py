from flask import Flask, request, jsonify, render_template
from werkzeug.utils import secure_filename
import os, uuid, datetime, requests, json

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
        return "I couldn’t reach the local LLM right now. But I’m still here to help with invoices and summaries."

    out = []
    for line in resp.iter_lines():
        if not line:
            continue
        try:
            data = json.loads(line.decode("utf-8"))
            out.append(data.get("response", ""))
        except Exception:
            pass
    return "".join(out).strip() or "I didn’t get a response this time—try again?"

def polite_chat_response(user_msg: str, invoices: list[dict]) -> str:
    user_lower = user_msg.lower().strip()
    # handle simple greetings
    greetings = ["hi", "hello", "hey"]
    if user_lower in greetings:
        return "Hi there! I can help you check invoices, show due payments, or summarize invoice info."
    
    # handle help/what can you do
    help_kw = ["what else", "what can you do", "help", "capabilities", "features"]
    if any(kw in user_lower for kw in help_kw):
        return ("I can help you:\n"
                "- Show invoices due this week\n"
                "- Summarize invoices\n"
                "- Tell you the top vendor by spend\n"
                "- Answer simple finance questions")

    if invoices:
        ctx = []
        for inv in invoices[-5:]:
            ctx.append(f"{inv['vendor']} | {inv['invoice_no']} | total {inv['total']} {inv['currency']} | due {inv['due_date']}")
        invoice_context = "\n".join(ctx)
    else:
        invoice_context = "No invoices uploaded yet."

    prompt = f"""
You are SMB Ops Copilot, a helpful finance assistant.
Be concise, friendly, and specific. Use the given invoice context when helpful.

User message:
\"\"\"{user_msg}\"\"\" 

Invoice context (latest up to 5):
{invoice_context}

Respond in 2–5 lines.
"""
    return call_llm(prompt)

# -----------------------
# Utilities
# -----------------------
def fake_extract_invoice(saved_path: str, original_name: str) -> dict:
    today = datetime.date.today()
    due = today + datetime.timedelta(days=7)
    # Check if this file was already uploaded
    existing = [inv for inv in INVOICES if inv["file_name"] == original_name]
    idx = len(INVOICES) + 1
    invoice_no = f"INV-{idx:03d}" if not existing else f"INV-{len(existing)+1:03d}"
    return {
        "id": str(uuid.uuid4()),
        "file_name": original_name,
        "storage_path": saved_path,
        "vendor": "Demo Vendor",
        "invoice_no": invoice_no,
        "issue_date": today.isoformat(),
        "due_date": due.isoformat(),
        "currency": "USD",
        "line_items": [
            {"desc": "Product A", "qty": 2, "unit_price": 50, "amount": 100},
            {"desc": "Product B", "qty": 1, "unit_price": 75, "amount": 75},
        ],
        "subtotal": 175.0,
        "tax": 17.5,
        "total": 192.5,
        "status": "open",
    }


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
        tally[inv["vendor"]] = tally.get(inv["vendor"], 0.0) + float(inv["total"])
    vendor = max(tally, key=tally.get)
    return vendor, tally[vendor]

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

    low = q.lower()

    if "top vendor" in low:
        vendor, total = compute_top_vendor()
        answer = f"Top vendor by spend is {vendor} with a total of ${total:.2f}." if vendor else "No invoices uploaded yet."
        return jsonify({"answer": answer})

    if ("due" in low and "week" in low) or "invoice summary" in low:
        due = list_due_within(7)
        if due:
            lines = [f"- {i['vendor']}: {i['total']} {i['currency']} due {i['due_date']} ({i['invoice_no']})" for i in due]
            answer = "Here’s a summary of invoices due this week:\n" + "\n".join(lines)
        else:
            answer = "No invoices are due in the next 7 days."
        return jsonify({"answer": answer})

    # fallback to LLM and polite response handling
    answer = polite_chat_response(q, INVOICES)
    return jsonify({"answer": answer})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=APP_PORT, debug=True)

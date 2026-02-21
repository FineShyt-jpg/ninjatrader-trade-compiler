"""
NinjaTrader Trade History Compiler — Flask Web App
"""

import os
import uuid
import json
import threading
import queue
import pandas as pd
from flask import Flask, request, jsonify, send_file, render_template

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024  # 500 MB max upload

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR  = os.path.join(BASE_DIR, "uploads")
OUTPUT_DIR  = os.path.join(BASE_DIR, "output")

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

# In-memory session store  { session_id: { "files": [...], "account": str } }
sessions: dict[str, dict] = {}
# Progress queues          { job_id: queue.Queue }
progress_queues: dict[str, queue.Queue] = {}


# ---------------------------------------------------------------------------
# NinjaTrader file parsing helpers
# ---------------------------------------------------------------------------

def detect_header_row(filepath: str) -> int:
    HEADER_KEYWORDS = {
        "instrument", "market position", "quantity", "entry price",
        "exit price", "profit", "commission", "mae", "mfe",
        "entry time", "exit time", "trade", "entry name", "exit name",
        "cum. net profit", "run-up", "drawdown", "account", "contract",
        "entry", "exit", "p&l", "pnl",
    }
    ext = os.path.splitext(filepath)[1].lower()
    try:
        if ext in (".xlsx", ".xls"):
            raw = pd.read_excel(filepath, header=None, nrows=30,
                                engine="openpyxl" if ext == ".xlsx" else "xlrd")
        else:
            raw = pd.read_csv(filepath, header=None, nrows=30,
                              encoding="utf-8", on_bad_lines="skip")
    except Exception:
        return 0

    for i, row in raw.iterrows():
        row_lower = {str(v).strip().lower() for v in row if pd.notna(v)}
        if row_lower & HEADER_KEYWORDS:
            return int(i)
    return 0


def read_file(filepath: str) -> pd.DataFrame | None:
    ext = os.path.splitext(filepath)[1].lower()
    header_row = detect_header_row(filepath)
    try:
        if ext in (".xlsx", ".xls"):
            engine = "openpyxl" if ext == ".xlsx" else "xlrd"
            df = pd.read_excel(filepath, header=header_row, engine=engine)
        elif ext == ".csv":
            df = pd.read_csv(filepath, header=header_row,
                             encoding="utf-8", on_bad_lines="skip")
        else:
            return None

        df.dropna(how="all", inplace=True)
        df.dropna(axis=1, how="all", inplace=True)
        df.columns = [str(c).strip() for c in df.columns]
        return df if not df.empty else None
    except Exception as e:
        print(f"  [WARN] Could not read {filepath}: {e}")
        return None


def compile_files(file_paths: list[str], account_name: str,
                  job_id: str) -> tuple[str, int, int]:
    q = progress_queues.get(job_id)

    def push(msg: dict):
        if q:
            q.put(msg)

    frames = []
    total = len(file_paths)

    for idx, fp in enumerate(file_paths, 1):
        push({"type": "progress", "current": idx, "total": total,
              "file": os.path.basename(fp)})
        df = read_file(fp)
        if df is not None:
            frames.append(df)

    if not frames:
        push({"type": "error", "message": "No readable data found in the selected files."})
        return None, 0, 0

    combined = pd.concat(frames, ignore_index=True)

    data_cols = list(combined.columns)
    before = len(combined)
    combined = combined.drop_duplicates(subset=data_cols)
    dupes_removed = before - len(combined)

    # Sort by first datetime-like column
    for col in combined.columns:
        if any(k in col.lower() for k in ("time", "date", "entry", "exit")):
            try:
                combined[col] = pd.to_datetime(combined[col], infer_datetime_format=True)
                combined.sort_values(col, inplace=True)
                combined.reset_index(drop=True, inplace=True)
                break
            except Exception:
                continue

    safe_name = "".join(
        c if c.isalnum() or c in " _-" else "_" for c in account_name
    ).strip()
    out_path = os.path.join(OUTPUT_DIR, f"{safe_name}_compiled.csv")
    combined.to_csv(out_path, index=False)

    push({"type": "done", "rows": len(combined), "dupes": dupes_removed,
          "filename": os.path.basename(out_path)})
    return out_path, len(combined), dupes_removed


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/session/new", methods=["POST"])
def new_session():
    sid = str(uuid.uuid4())
    sessions[sid] = {"files": [], "account": "Account_1"}
    return jsonify({"session_id": sid})


@app.route("/session/<sid>/upload", methods=["POST"])
def upload(sid):
    if sid not in sessions:
        return jsonify({"error": "Invalid session"}), 400

    uploaded = request.files.getlist("files")
    added = []
    for f in uploaded:
        if not f.filename:
            continue
        ext = os.path.splitext(f.filename)[1].lower()
        if ext not in (".csv", ".xlsx", ".xls"):
            continue
        file_id = str(uuid.uuid4())
        dest = os.path.join(UPLOAD_DIR, f"{file_id}{ext}")
        f.save(dest)
        entry = {"id": file_id, "name": f.filename, "path": dest,
                 "size": os.path.getsize(dest)}
        sessions[sid]["files"].append(entry)
        added.append({"id": file_id, "name": f.filename, "size": entry["size"]})

    return jsonify({"added": added, "total": len(sessions[sid]["files"])})


@app.route("/session/<sid>/files", methods=["GET"])
def list_files(sid):
    if sid not in sessions:
        return jsonify({"error": "Invalid session"}), 400
    files = [{"id": f["id"], "name": f["name"], "size": f["size"]}
             for f in sessions[sid]["files"]]
    return jsonify({"files": files, "account": sessions[sid]["account"]})


@app.route("/session/<sid>/remove", methods=["POST"])
def remove_file(sid):
    if sid not in sessions:
        return jsonify({"error": "Invalid session"}), 400
    data = request.get_json()
    file_id = data.get("id")
    before = len(sessions[sid]["files"])
    sessions[sid]["files"] = [
        f for f in sessions[sid]["files"] if f["id"] != file_id
    ]
    return jsonify({"total": len(sessions[sid]["files"]),
                    "removed": before - len(sessions[sid]["files"])})


@app.route("/session/<sid>/clear", methods=["POST"])
def clear_files(sid):
    if sid not in sessions:
        return jsonify({"error": "Invalid session"}), 400
    # Clean up temp files
    for f in sessions[sid]["files"]:
        try:
            os.remove(f["path"])
        except Exception:
            pass
    sessions[sid]["files"] = []
    return jsonify({"total": 0})


@app.route("/session/<sid>/account", methods=["POST"])
def set_account(sid):
    if sid not in sessions:
        return jsonify({"error": "Invalid session"}), 400
    data = request.get_json()
    sessions[sid]["account"] = data.get("account", "Account_1")
    return jsonify({"ok": True})


@app.route("/session/<sid>/compile", methods=["POST"])
def compile_route(sid):
    if sid not in sessions:
        return jsonify({"error": "Invalid session"}), 400
    sess = sessions[sid]
    if not sess["files"]:
        return jsonify({"error": "No files staged"}), 400

    job_id = str(uuid.uuid4())
    progress_queues[job_id] = queue.Queue()

    def run():
        compile_files(
            [f["path"] for f in sess["files"]],
            sess["account"],
            job_id
        )

    threading.Thread(target=run, daemon=True).start()
    return jsonify({"job_id": job_id})


@app.route("/job/<job_id>/progress")
def job_progress(job_id):
    """Server-Sent Events stream for compile progress."""
    q = progress_queues.get(job_id)
    if not q:
        return jsonify({"error": "Unknown job"}), 404

    def stream():
        while True:
            try:
                msg = q.get(timeout=60)
                yield f"data: {json.dumps(msg)}\n\n"
                if msg.get("type") in ("done", "error"):
                    break
            except queue.Empty:
                yield "data: {\"type\":\"ping\"}\n\n"

    return app.response_class(stream(), mimetype="text/event-stream")


@app.route("/download/<filename>")
def download(filename):
    path = os.path.join(OUTPUT_DIR, filename)
    if not os.path.isfile(path):
        return jsonify({"error": "File not found"}), 404
    return send_file(path, as_attachment=True)


if __name__ == "__main__":
    print("\n  NinjaTrader Compiler running at http://localhost:5000\n")
    app.run(debug=False, host="0.0.0.0", port=5000)

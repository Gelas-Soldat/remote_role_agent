from __future__ import annotations

import os
import threading
import webbrowser
from pathlib import Path
from typing import Any, Dict

from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, render_template, request, send_file, url_for

from job_agent import (
    OUTPUT_DIR,
    get_dashboard_stats,
    get_job,
    get_recent_rejected,
    init_db,
    query_jobs,
    run_ai_review,
    run_sources,
    update_job_status,
)

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
init_db()

app = Flask(__name__)
run_lock = threading.Lock()
run_state: Dict[str, Any] = {
    "running": False,
    "last_result": None,
    "last_error": None,
}


def background_run(force: bool = False, source: str | None = None) -> None:
    global run_state
    with run_lock:
        run_state["running"] = True
        run_state["last_error"] = None
    try:
        sources = [source] if source else None
        result = run_sources(force=force, only_sources=sources)
        with run_lock:
            run_state["last_result"] = result
    except Exception as exc:
        with run_lock:
            run_state["last_error"] = str(exc)
    finally:
        with run_lock:
            run_state["running"] = False


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/stats")
def api_stats():
    data = get_dashboard_stats()
    with run_lock:
        data["run_state"] = dict(run_state)
    return jsonify(data)


@app.route("/api/jobs")
def api_jobs():
    filters = {
        "q": request.args.get("q", ""),
        "fit": request.args.get("fit", ""),
        "source": request.args.get("source", ""),
        "status": request.args.get("status", ""),
        "sort": request.args.get("sort", "score"),
        "include_hidden": request.args.get("include_hidden") == "1",
        "limit": request.args.get("limit", "60"),
    }
    return jsonify({"jobs": query_jobs(filters)})


@app.route("/api/job/<job_key>")
def api_job(job_key: str):
    job = get_job(job_key)
    if not job:
        return jsonify({"ok": False, "error": "job not found"}), 404
    return jsonify({"ok": True, "job": job})


@app.route("/api/job/<job_key>/status", methods=["POST"])
def api_update_status(job_key: str):
    data = request.get_json(force=True, silent=True) or {}
    status = str(data.get("status", "")).strip()
    ok = update_job_status(job_key, status)
    return jsonify({"ok": ok})


@app.route("/api/job/<job_key>/ai", methods=["POST"])
def api_ai(job_key: str):
    return jsonify(run_ai_review(job_key))


@app.route("/api/rejected")
def api_rejected():
    limit = int(request.args.get("limit", "100"))
    return jsonify({"rejected": get_recent_rejected(limit=limit)})


@app.route("/api/run", methods=["POST"])
def api_run():
    data = request.get_json(force=True, silent=True) or {}
    force = bool(data.get("force", False))
    source = data.get("source") or None
    with run_lock:
        if run_state["running"]:
            return jsonify({"ok": False, "error": "search already running"}), 409
        run_state["running"] = True
    thread = threading.Thread(target=background_run, kwargs={"force": force, "source": source}, daemon=True)
    thread.start()
    return jsonify({"ok": True, "running": True})


@app.route("/api/run_state")
def api_run_state():
    with run_lock:
        return jsonify(dict(run_state))


@app.route("/exports/latest")
def export_latest():
    path = OUTPUT_DIR / "jobs_latest.csv"
    if not path.exists():
        return redirect(url_for("index"))
    return send_file(path, as_attachment=True, download_name="jobs_latest.csv")


@app.route("/exports/shortlist")
def export_shortlist():
    path = OUTPUT_DIR / "jobs_shortlist.csv"
    if not path.exists():
        return redirect(url_for("index"))
    return send_file(path, as_attachment=True, download_name="jobs_shortlist.csv")


@app.route("/exports/rejected")
def export_rejected():
    path = OUTPUT_DIR / "jobs_rejected_location_audit.csv"
    if not path.exists():
        return redirect(url_for("index"))
    return send_file(path, as_attachment=True, download_name="jobs_rejected_location_audit.csv")


def main() -> None:
    host = os.getenv("FLASK_HOST", "127.0.0.1")
    port = int(os.getenv("FLASK_PORT", "5000"))
    open_browser = os.getenv("OPEN_BROWSER", "1") == "1"
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(f"http://{host}:{port}")).start()
    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    main()

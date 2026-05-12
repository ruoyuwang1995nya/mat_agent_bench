#!/usr/bin/env python3
"""Inspect ~/.matbench/sessions.db — list sessions and their evaluation results."""

import json
import sqlite3
import sys
from pathlib import Path

DB = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.home() / ".matbench" / "sessions.db"

if not DB.exists():
    print(f"Database not found: {DB}")
    sys.exit(1)

conn = sqlite3.connect(str(DB))

# ── Sessions ──────────────────────────────────────────────────────────────────
sessions = conn.execute(
    "SELECT session_id, token, created_at FROM sessions ORDER BY created_at"
).fetchall()

print(f"=== Sessions ({len(sessions)}) ===")
for sid, token, created_at in sessions:
    print(f"  {sid}  token={token[:12]}...  created={created_at}")

# ── Results ───────────────────────────────────────────────────────────────────
results = conn.execute(
    "SELECT key, token, session_id, record_json FROM results ORDER BY session_id"
).fetchall()

print(f"\n=== Results ({len(results)}) ===")
by_session: dict[str, list[dict]] = {}
for key, token, session_id, record_json in results:
    rec = json.loads(record_json)
    by_session.setdefault(session_id, []).append(rec)

for session_id, recs in by_session.items():
    def _is_passed(r: dict) -> bool:
        if "passed" in r:
            return bool(r["passed"])
        if "passed_count" in r and "total_count" in r:
            return r["total_count"] > 0 and r["passed_count"] == r["total_count"]
        return r.get("overall_weighted_score", 0) >= 1.0

    passed = sum(1 for r in recs if _is_passed(r))
    print(f"\n  Session: {session_id}  ({passed}/{len(recs)} passed)")
    for r in recs:
        status = "PASS" if _is_passed(r) else "FAIL"
        score = r.get("overall_weighted_score", r.get("weighted_score", r.get("score", "?")))
        model = r.get("model_name", "?")
        #print(r)  # print full record for debugging
        print(f"    [{status}] {r['question_id']:40s}  score={score}  model={model}")

conn.close()

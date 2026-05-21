#!/usr/bin/env python3
"""Re-calculate overall_weighted_score for old records using the correct formula:

    Final = S_correct × S_ground × S_efficiency

Updates:
  - ~/.matbench/sessions.db  (or path given as first argument)
  - Any raw_runs.jsonl files given as additional arguments

Usage:
    python scripts/migrate_rescore.py                        # update default DB only
    python scripts/migrate_rescore.py ~/.matbench/sessions.db output_*/raw_runs.jsonl
    python scripts/migrate_rescore.py --dry-run              # preview changes, no writes
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import sys
from pathlib import Path


def _recompute(rec: dict) -> float:
    c_score = rec.get('correctness_weighted_score', 0.0)
    c_total = rec.get('correctness_total', 0)
    g_total = rec.get('grounding_total', 0)
    g_veto  = rec.get('grounding_veto', False)
    e_score = rec.get('efficiency_weighted_score', 0.0)
    e_total = rec.get('efficiency_total', 0)

    s_correct    = c_score if c_total > 0 else 0.0
    s_ground     = 1.0 if g_total == 0 else (0.0 if g_veto else 1.0)
    s_efficiency = min(1.0, e_score) if e_total > 0 else 1.0
    return s_correct * s_ground * s_efficiency


def _migrate_db(db_path: Path, dry_run: bool) -> tuple[int, int]:
    """Returns (total_rows, changed_rows)."""
    conn = sqlite3.connect(str(db_path))
    rows = conn.execute("SELECT key, record_json FROM results").fetchall()
    updates: list[tuple[str, str]] = []

    for key, record_json in rows:
        rec = json.loads(record_json)
        old = rec.get('overall_weighted_score', 0.0)
        new = _recompute(rec)
        if abs(old - new) > 1e-9:
            rec['overall_weighted_score'] = new
            updates.append((json.dumps(rec, ensure_ascii=False, default=str), key))
            print(f"  DB  {key}  {old:.4f} → {new:.4f}")

    if not dry_run and updates:
        conn.executemany("UPDATE results SET record_json = ? WHERE key = ?", updates)
        conn.commit()

    conn.close()
    return len(rows), len(updates)


def _migrate_jsonl(jsonl_path: Path, dry_run: bool) -> tuple[int, int]:
    """Returns (total_rows, changed_rows)."""
    lines = jsonl_path.read_text(encoding='utf-8').splitlines()
    new_lines: list[str] = []
    changed = 0

    for line in lines:
        line = line.strip()
        if not line:
            new_lines.append(line)
            continue
        rec = json.loads(line)
        old = rec.get('overall_weighted_score', 0.0)
        new = _recompute(rec)
        if abs(old - new) > 1e-9:
            rec['overall_weighted_score'] = new
            changed += 1
            qid = rec.get('question_id', '?')
            print(f"  JSONL {jsonl_path.name}  {qid}  {old:.4f} → {new:.4f}")
        new_lines.append(json.dumps(rec, ensure_ascii=False, default=str))

    if not dry_run and changed:
        backup = jsonl_path.with_suffix('.jsonl.bak')
        shutil.copy2(jsonl_path, backup)
        jsonl_path.write_text('\n'.join(new_lines) + '\n', encoding='utf-8')
        print(f"  (backup saved to {backup})")

    return len(lines), changed


def main() -> None:
    args = sys.argv[1:]
    dry_run = '--dry-run' in args
    args = [a for a in args if a != '--dry-run']

    db_path: Path | None = None
    jsonl_paths: list[Path] = []

    for a in args:
        p = Path(a)
        if p.suffix == '.jsonl':
            jsonl_paths.append(p)
        else:
            db_path = p

    if db_path is None:
        db_path = Path.home() / '.matbench' / 'sessions.db'

    if dry_run:
        print('[dry-run] no files will be modified\n')

    total_changed = 0

    if db_path.exists():
        print(f'Migrating DB: {db_path}')
        total, changed = _migrate_db(db_path, dry_run)
        print(f'  {changed}/{total} records updated\n')
        total_changed += changed
    else:
        print(f'DB not found, skipping: {db_path}')

    for jp in jsonl_paths:
        if not jp.exists():
            print(f'File not found, skipping: {jp}')
            continue
        print(f'Migrating JSONL: {jp}')
        total, changed = _migrate_jsonl(jp, dry_run)
        print(f'  {changed}/{total} records updated\n')
        total_changed += changed

    if total_changed == 0:
        print('All records already have correct scores — nothing to do.')
    elif dry_run:
        print(f'[dry-run] {total_changed} records would be updated.')
    else:
        print(f'Done. {total_changed} records updated.')


if __name__ == '__main__':
    main()

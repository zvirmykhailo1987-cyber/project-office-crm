#!/usr/bin/env python3
"""Numbers for the README / CV, computed from the code and (optionally) a database — never typed by hand.

    python scripts/metrics.py                       # code-only numbers
    python scripts/metrics.py --db data/ofis.sqlite # + data numbers, read-only, + acceptance run

Output is JSON so it can be pasted into docs or checked in CI.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sqlite3
import subprocess  # nosec B404 — runs pytest/acceptance of this repo only
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def pytest_count() -> int:
    out = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"],  # noqa: S603  # nosec B603
                         cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
    m = re.search(r"(\d+) tests? collected", out)
    return int(m.group(1)) if m else -1


def routes() -> dict[str, int]:
    sys.path.insert(0, str(ROOT))
    from fastapi.routing import APIRoute

    from app.main import app

    def flat(rs):
        for r in rs:
            inner = getattr(r, "original_router", None)
            yield from (flat(inner.routes) if inner is not None else [r])
    api = [r for r in flat(app.routes) if isinstance(r, APIRoute)]
    return {"routes": len(api), "get_screens": sum(1 for r in api if "GET" in r.methods),
            "mutating": sum(1 for r in api if r.methods & {"POST", "PUT", "PATCH", "DELETE"})}


def acceptance_static() -> dict[str, int] | None:
    f = ROOT / "tests" / "acceptance.py"
    if not f.exists():
        return None
    tree = ast.parse(f.read_text(encoding="utf-8"))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "id", "") in ("check", "warn")]
    in_loop = 0
    for loop in (n for n in ast.walk(tree) if isinstance(n, ast.For)):
        in_loop += sum(1 for n in ast.walk(loop) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "check")
    return {"criteria": sum(1 for c in calls if c.func.id == "check"),
            "warnings": sum(1 for c in calls if c.func.id == "warn"),
            "criteria_inside_loops": in_loop}


def acceptance_run(db: Path) -> dict[str, int] | None:
    if not (ROOT / "tests" / "acceptance.py").exists():
        return None
    env = {"DB_PATH": str(db), "DATA_DIR": str(db.parent), "OFIS_ENV": "demo", "PYTHONIOENCODING": "utf-8",
           "SYSTEMROOT": "C:\\Windows", "PATH": ""}
    out = subprocess.run([sys.executable, "-m", "tests.acceptance"], cwd=ROOT, capture_output=True,  # noqa: S603  # nosec B603
                         text=True, encoding="utf-8", errors="replace", env=env).stdout
    m = re.search(r"(\d+)\D+(\d+)\D+(\d+)\s*$", out.strip().splitlines()[-1]) if out.strip() else None
    return {"passed": int(m.group(1)), "failed": int(m.group(2)), "warnings": int(m.group(3))} if m else None


def db_counts(db: Path) -> dict[str, int]:
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    have = {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
    out = {}
    for label, sql in {
        "counterparties": "select count(*) from counterparty",
        "people": "select count(*) from person",
        "sites": "select count(*) from site",
        "deals": "select count(*) from deal",
        "programme_documents": "select count(*) from programfile",
        "application_documents": "select count(*) from document",
        "application_documents_ai_drafts": "select count(*) from document where ai_chernetka = 1",
        "opportunities": "select count(*) from opportunity",
        "applications": "select count(*) from application",
    }.items():
        table = re.search(r"from (\w+)", sql).group(1)
        if table in have:
            try:
                out[label] = con.execute(sql).fetchone()[0]
            except sqlite3.OperationalError:
                pass
    con.close()
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, help="SQLite database to count (opened read-only)")
    a = ap.parse_args()
    cat = ROOT / "app" / "locale" / "en.json"
    res: dict[str, object] = {
        "tests_collected": pytest_count(),
        **routes(),
        "templates": len(list((ROOT / ("templates_v2" if (ROOT / "templates_v2").exists() else "templates")).rglob("*.html"))),
        "i18n_catalogue_entries": len(json.loads(cat.read_text(encoding="utf-8"))) if cat.exists() else 0,
        "acceptance_static": acceptance_static(),
    }
    if a.db:
        res["data"] = db_counts(a.db)
        res["acceptance_run"] = acceptance_run(a.db)
    print(json.dumps(res, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

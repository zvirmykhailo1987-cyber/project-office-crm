#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mykhailo Zvir

"""Break the code on purpose and check that the tests notice. Writes docs/mutation-results.json.

Coverage says a line ran. This says a test fails when the line is wrong. Each mutation removes one
guarantee from a temporary copy of the project, and the whole suite runs against that copy. A mutation
nothing catches is reported as "survived", not hidden; the fix for a survivor is a new test, never a
deleted mutation. The repository itself is never modified.

    python scripts/mutation_check.py            # all mutations, ~1 hour on a laptop
    python scripts/mutation_check.py --check    # only verify that every mutation still applies
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess  # nosec B404 — runs this repository's own test suite on a temporary copy
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYTHON = sys.executable
SKIP = {".venv", "data", ".git", "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache", "docs"}

# (what the bug would mean, file, exact original text, replacement)
MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "A stage is entered although its gate is closed",
        "app/web/voronky.py",
        '    if not res.allowed:\n        return redirect(f"/voronky/{deal_id}?gate=1")',
        '    if False:\n        return redirect(f"/voronky/{deal_id}?gate=1")',
    ),
    (
        "An unsigned final document opens the gate",
        "app/services/gates.py",
        "return {d.kind for d in docs if d.kind and d.is_final and d.signed_at}",
        "return {d.kind for d in docs if d.kind and d.is_final}",
    ),
    (
        "A draft (not final) document opens the gate",
        "app/services/gates.py",
        "return {d.kind for d in docs if d.kind and d.is_final and d.signed_at}",
        "return {d.kind for d in docs if d.kind and d.signed_at}",
    ),
    (
        "The won stage is entered without its own contract",
        "app/services/gates.py",
        "    if target.is_won:\n        missing +=",
        "    if False:\n        missing +=",
    ),
    (
        "A detour through a terminal stage bypasses the gates",
        "app/services/gates.py",
        "z_terminalu = current.is_terminal and not current.is_won and not target.is_terminal",
        "z_terminalu = False",
    ),
    (
        "The automatic stage rule moves a deal the gate would stop",
        "app/services/gates.py",
        "    if not check(s, deal, nxt).allowed:\n        return None",
        "    if False:\n        return None",
    ),
    (
        "Finalising a document needs no confirmation (no HTTP 428)",
        "app/web/dokumenty.py",
        'audit.require_confirm("document.finalize", confirm == "yes")',
        'audit.require_confirm("document.finalize", True)',
    ),
    (
        "A stage gate is changed without confirmation (no HTTP 428)",
        "app/web/napryamy.py",
        '        audit.require_confirm("stage.gates", confirmed=bool(confirm))',
        "        pass",
    ),
    (
        "The author finalises their own document",
        "app/web/dokumenty.py",
        "    if _avtor(d, user, s):\n        raise HTTPException(403",
        "    if False:\n        raise HTTPException(403",
    ),
    (
        "A final document goes back to draft",
        "app/web/dokumenty.py",
        '    if d.is_final:\n        return redirect(f"/dokumenty/{doc_id}?err=finalnyi")',
        '    if False:\n        return redirect(f"/dokumenty/{doc_id}?err=finalnyi")',
    ),
    (
        "Someone without the approver role marks a document agreed",
        "app/web/dokumenty.py",
        '        if not user.can(CAP_APPROVE):\n            return redirect(f"/dokumenty/{doc_id}?err=lyshe-zatverdzhuvach")',
        '        if False:\n            return redirect(f"/dokumenty/{doc_id}?err=lyshe-zatverdzhuvach")',
    ),
    (
        "An author approves their own work through a colleague's submission",
        "app/web/collab.py",
        "    if a.submitted_by == user.name or user.name in _avtory_sutnosti(s, a.entity, a.entity_id):",
        "    if a.submitted_by == user.name:",
    ),
    (
        '"Needs work" is accepted without a comment',
        "app/web/collab.py",
        '    if status == "na_doopratsyuvannya" and not note.strip():',
        "    if False:",
    ),
    (
        "A role without the capability passes the route guard",
        "app/web/deps.py",
        "        if not user.can(cap):\n            raise HTTPException(403",
        "        if False:\n            raise HTTPException(403",
    ),
    (
        "A deactivated user keeps access",
        "app/web/deps.py",
        "    if not user or not user.active:",
        "    if not user:",
    ),
    (
        "A CSRF token that does not match the cookie is accepted",
        "app/authkit.py",
        "if not cookie or not sent or not hmac.compare_digest(cookie, sent) or not _same_origin(request):",
        "if not cookie or not sent or not _same_origin(request):",
    ),
    (
        "A cross-origin form post with a valid token is accepted",
        "app/authkit.py",
        "if not cookie or not sent or not hmac.compare_digest(cookie, sent) or not _same_origin(request):",
        "if not cookie or not sent or not hmac.compare_digest(cookie, sent):",
    ),
    (
        "A revoked session keeps working",
        "app/authkit.py",
        "    if not sess or sess.revoked_at is not None:",
        "    if not sess:",
    ),
    (
        "An idle session never times out",
        "app/authkit.py",
        "if now >= sess.expires_at or now - sess.last_seen_at > timedelta(seconds=cfg.idle_timeout):",
        "if now >= sess.expires_at:",
    ),
    (
        "Repeated failed logins never lock",
        "app/authkit.py",
        "            if len(q) >= self._limit(k):",
        "            if False:",
    ),
    (
        "Production starts with a development secret",
        "app/config.py",
        '    if SECRET.startswith("dev-") or len(SECRET) < 32:',
        "    if False:",
    ),
    (
        "A private chat archive is readable by id from outside",
        "app/web/chat.py",
        "    if room is not None and not room.vydyma(user.name):",
        "    if False:",
    ),
    (
        "The team feed names a personal chat archive",
        "app/web/chat.py",
        'detail=(f"особистий архів · нових {z.novyh}" if osobystyi else z.yak_tekst())[:200])',
        "detail=z.yak_tekst()[:200])",
    ),
    (
        "A stored path outside the document roots is served",
        "app/web/dokumenty.py",
        "        if kand.exists() and kand.resolve().parent in koreni:\n            path = kand\n    if fmt",
        "        if kand.exists():\n            path = kand\n    if fmt",
    ),
    (
        "An executable is accepted as a document upload",
        "app/web/dokumenty.py",
        "    if Path(imya).suffix.lower() not in DOZVOLENI_ROZSHYRENNYA:",
        "    if False:",
    ),
    (
        "A zip bomb is inflated past the limit",
        "app/services/wa_parser.py",
        "            if rozmir > mezha:",
        "            if False:",
    ),
    (
        "The import merges namesakes from different regions",
        "app/importers/common.py",
        "            if row_obl and cur_obl and row_obl.lower() != cur_obl.lower():\n                return None",
        "            if False:\n                return None",
    ),
    (
        "Erased criteria are stored as a score of zero instead of no data",
        "app/web/obyekty.py",
        "        if res and not scoring.bez_danykh(res):",
        "        if res:",
    ),
    (
        "A letter marks an unapproved document as sent",
        "app/web/dokumenty.py",
        'if d and not d.is_final and d.workflow_status in ("uzhodzheno", "do_vidpravlennya"):',
        "if d and not d.is_final:",
    ),
]


def copy_project(target: Path) -> None:
    shutil.copytree(ROOT, target, ignore=lambda _d, names: [n for n in names if n in SKIP], dirs_exist_ok=True)


def run_tests(cwd: Path, data_dir: Path) -> tuple[bool, str]:
    env = {**os.environ, "OFIS_ENV": "dev", "UI_DEFAULT_LANG": "uk", "DATA_DIR": str(data_dir),
           "PYTHONIOENCODING": "utf-8"}
    done = subprocess.run(  # nosec B603 — fixed argv, no shell
        [PYTHON, "-m", "pytest", "-x", "-q", "--no-header", "-p", "no:cacheprovider"],
        cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    found = re.findall(r"^FAILED (\S+)", done.stdout, re.M) or re.findall(r"^ERROR (\S+)", done.stdout, re.M)
    return done.returncode == 0, found[0] if found else ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="only verify that every mutation applies")
    args = ap.parse_args()

    stale = [name for name, rel, old, _ in MUTATIONS if old not in (ROOT / rel).read_text(encoding="utf-8")]
    if stale:
        print("Mutation no longer applies, fix the script:", *stale, sep="\n  ")
        return 2
    if args.check:
        print(f"all {len(MUTATIONS)} mutations apply")
        return 0

    results = []
    with tempfile.TemporaryDirectory() as tmp:
        work, data = Path(tmp) / "copy", Path(tmp) / "data"
        copy_project(work)
        data.mkdir()
        passed, failed = run_tests(work, data)
        if not passed:
            print("The unmodified copy does not pass, so nothing here could be trusted:", failed)
            return 2
        for name, relative, old, new in MUTATIONS:
            path = work / relative
            original = path.read_text(encoding="utf-8")
            path.write_text(original.replace(old, new, 1), encoding="utf-8", newline="\n")
            passed, caught_by = run_tests(work, data)
            path.write_text(original, encoding="utf-8", newline="\n")
            results.append({"bug": name, "file": relative, "caught": not passed,
                            "caught_by": caught_by if not passed else None})
            print("caught  " if not passed else "SURVIVED", name, caught_by, flush=True)
    summary = {
        "measured": dt.date.today().isoformat(),
        "bugs_injected": len(results),
        "caught": sum(row["caught"] for row in results),
        "results": results,
    }
    (ROOT / "docs" / "mutation-results.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print(summary["caught"], "of", summary["bugs_injected"])
    return 0 if summary["caught"] == summary["bugs_injected"] else 1


if __name__ == "__main__":
    sys.exit(main())

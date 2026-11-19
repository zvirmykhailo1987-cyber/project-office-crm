#!/usr/bin/env python3
"""PII leak scanner: proves the public repo and the demo database contain no real people data.

Two modes:

* **Reference mode** (run by the maintainer, never in CI) — reads the private
  production databases strictly read-only (``sqlite file:…?mode=ro``) and looks
  for any of their e-mails, phone numbers, tax codes (EDRPOU/IPN), full names of
  people and organisations (also transliterated to Latin), message fragments
  (6-word shingles) and file hashes inside the repository files and the demo DB::

      python scripts/pii_scan.py --real /path/prod.sqlite --real-files /path/uploads \
             --demo-db data/demo.sqlite --repo .

* **Pattern mode** (CI, no private data available) — flags e-mails outside
  reserved ``*.example`` domains, Ukrainian mobile numbers outside the never
  assigned ``+380 00`` range and 8/10-digit tax codes outside the synthetic
  ``99…`` range in the demo database.

Exit code 1 when anything is found. Matched values are printed masked.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sqlite3
import sys
from pathlib import Path

TEXT_EXT = {".py", ".html", ".js", ".css", ".md", ".txt", ".json", ".toml", ".yml", ".yaml", ".ini",
            ".cfg", ".sh", ".svg", ".csv", ".sql", ".env", ".example", ""}
SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache",
             ".ruff_cache", "data", "htmlcov"}
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
DIGITS = re.compile(r"(?<![\w.])\+?\d[\d\s()-]{7,16}\d(?![\w.])")
WORD = re.compile(r"[\w'ʼ’-]+", re.U)
TRANSLIT = dict(zip("абвгґдезиіїйклмнопрстуфхцчшщьюяєжʼ'’",
                    ["a", "b", "v", "h", "g", "d", "e", "z", "y", "i", "i", "i", "k", "l", "m", "n", "o", "p",
                     "r", "s", "t", "u", "f", "kh", "ts", "ch", "sh", "shch", "", "iu", "ia", "ie", "zh",
                     "", "", ""], strict=True))
SAFE_EMAIL_DOMAINS = (".example", "example.com", "example.org", "example.net", "localhost", ".test",
                      "noreply.anthropic.com", "users.noreply.github.com")
# Words that identify nobody: legal forms and placeholders that real records
# sometimes carry in a name field ("немає даних", "контактна особа"). A name or
# shingle made ONLY of these is not personal data.
GENERIC = set(["товариство", "з", "обмеженою", "відповідальністю", "сільськогосподарське", "приватне", "підприємство", "фермерське", "господарство", "комунальне", "некомерційне", "державне", "акціонерне", "кооператив", "виробничий", "обслуговуючий", "аграрне", "агрофірма", "немає", "даних", "контактна", "особа", "полігон", "тпв", "керівник", "директор", "орган", "органів", "органи", "місцевого", "місцеве", "самоврядування", "orhan", "orhaniv", "orhany", "mistsevoho", "mistseve", "samovriaduvannia", "tovarystvo", "z", "obmezhenoiu", "vidpovidalnistiu", "silskohospodarske", "pryvatne", "pidpryiemstvo", "fermerske", "hospodarstvo", "komunalne", "nekomertsiine", "derzhavne", "aktsionerne", "kooperatyv", "nemaie", "danykh", "kontaktna", "osoba", "polihon", "tpv", "kerivnyk", "dyrektor"])
CONFIG_TABLES = {"stage", "pipeline", "program", "setting", "doctemplate", "datasource", "stagerule",
                 "scorecomponent", "score", "auditentry", "importrun", "importconflict", "syncmeta",
                 "auth_session", "ingestlog"}
NON_PROSE_COLS = {"uid", "token_hash", "sha256", "url", "url_hash", "stored_path", "stored_name", "path",
                  "path_or_endpoint", "source_path", "filename", "source", "source_ref", "kved", "all_kved",
                  "activity_text", "breakdown_json", "gejt_json", "rezultat_json"}
LEGAL = {"тов", "пп", "фг", "сфг", "пат", "прат", "ат", "фоп", "дп", "кп", "llc", "ltd", "tov"}


def translit(s: str) -> str:
    return "".join(TRANSLIT.get(ch, ch) for ch in s.lower())


def words(s: str) -> list[str]:
    return [w.lower().strip("-'ʼ’") for w in WORD.findall(s or "") if w.strip("-'ʼ’")]


def digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def phone_key(d: str) -> str | None:
    return d[-9:] if 9 <= len(d) <= 13 and len(set(d[-9:])) > 2 else None


class Reference:
    """Everything sensitive extracted from the private databases (kept in memory only)."""

    def __init__(self) -> None:
        self.emails: set[str] = set()
        self.phones: set[str] = set()
        self.codes: set[str] = set()
        self.names: set[tuple[str, ...]] = set()
        self.shingles: set[tuple[str, ...]] = set()
        self.hashes: set[str] = set()

    def add_db(self, path: Path) -> None:
        con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        tables = [r[0] for r in con.execute("select name from sqlite_master where type='table'")
                  if not r[0].startswith(("sqlite_", "cp_fts_"))]
        for t in tables:
            cols = [r[1] for r in con.execute(f'pragma table_info("{t}")')]
            for row in con.execute(f'select * from "{t}"'):  # noqa: S608 — names from sqlite_master  # nosec B608
                for col, val in zip(cols, row, strict=True):
                    if isinstance(val, (int, float)) and col.lower() in ("edrpou", "ipn", "code"):
                        val = str(val)
                    if not isinstance(val, str) or not val.strip():
                        continue
                    self._value(t.lower(), col.lower(), val)
        con.close()

    def _value(self, table: str, col: str, val: str) -> None:
        for e in EMAIL.findall(val):
            if not e.lower().endswith(SAFE_EMAIL_DOMAINS):
                self.emails.add(e.lower())
        if "phone" in col or "telefon" in col or col == "tel":
            for chunk in re.split(r"[,;/]", val):
                k = phone_key(digits(chunk))
                if k:
                    self.phones.add(k)
        for m in DIGITS.findall(val):
            d = digits(m)
            if d.startswith(("380", "0")) and len(d) in (10, 12):
                k = phone_key(d)
                if k:
                    self.phones.add(k)
        if "edrpou" in col or col in ("ipn", "kod", "code_edrpou") or col.endswith("_edrpou"):
            for d in re.findall(r"\d{8,10}", val):
                if len(set(d)) > 2:
                    self.codes.add(d)
        if col in ("name", "author", "actor", "assignee", "owner", "pidpysant", "kerivnyk", "beneficiaries",
                   "anchor_name", "klient", "legal_name", "uploaded_by", "submitted_by", "approver",
                   "from_whom", "user", "sent_by", "recipient", "podano_kto", "pohodzheno_kym", "avtor",
                   "done_by", "confirmed_by"):
            for part in re.split(r"[,;\n]", val):
                ws = [w for w in words(part) if w not in LEGAL]
                if 2 <= len(ws) <= 6 and sum(len(w) for w in ws) >= 8 and not set(ws) <= GENERIC:
                    self.names.add(tuple(ws))
                    self.names.add(tuple(translit(w) for w in ws))
        # Message shingles only from columns people write into. Config tables
        # (stages, settings, templates, score labels) were seeded from this very
        # code, so matching them would only prove the code equals itself.
        ws = words(val)
        if len(ws) >= 6 and table not in CONFIG_TABLES and col not in NON_PROSE_COLS:
            for i in range(len(ws) - 5):
                sh = tuple(ws[i:i + 6])
                if sum(len(w) for w in sh) >= 24 and not set(sh) <= GENERIC:
                    self.shingles.add(sh)

    def add_files(self, root: Path) -> None:
        for f in root.rglob("*"):
            if f.is_file() and f.stat().st_size <= 50 * 1024 * 1024:
                self.hashes.add(hashlib.sha256(f.read_bytes()).hexdigest())


class Findings:
    def __init__(self) -> None:
        self.items: list[tuple[str, str, str]] = []

    def add(self, kind: str, where: str, value: str) -> None:
        masked = value if len(value) <= 4 else value[:2] + "…" + value[-2:]
        self.items.append((kind, where, masked))


def scan_text(text: str, where: str, ref: Reference | None, out: Findings) -> None:
    for e in EMAIL.findall(text):
        el = e.lower()
        if ref is not None:
            if el in ref.emails:
                out.add("email", where, el)
        elif not el.endswith(SAFE_EMAIL_DOMAINS) and not el.endswith((".png", ".svg", ".js", ".css")):
            out.add("email-pattern", where, el)
    for m in DIGITS.findall(text):
        d = digits(m)
        if ref is not None:
            k = phone_key(d)
            if k and k in ref.phones and d.startswith(("380", "0")):
                out.add("phone", where, d)
        elif d.startswith(("380", "0")) and len(d) in (10, 12) and not d.lstrip("+").startswith(("38000", "000", "00")):
            out.add("phone-pattern", where, d)
    if ref is not None:
        for d in re.findall(r"(?<!\d)\d{8,10}(?!\d)", text):
            if d in ref.codes:
                out.add("tax-code", where, d)
        ws = words(text)
        lat = [translit(w) for w in ws]
        for seq in (ws, lat):
            for n in range(2, 7):
                for i in range(len(seq) - n + 1):
                    if tuple(seq[i:i + n]) in ref.names:
                        out.add("name", where, " ".join(seq[i:i + n]))
        for i in range(len(ws) - 5):
            if tuple(ws[i:i + 6]) in ref.shingles:
                out.add("message", where, " ".join(ws[i:i + 6]))


def scan_repo(repo: Path, ref: Reference | None, out: Findings) -> int:
    n = 0
    for f in repo.rglob("*"):
        if not f.is_file() or set(f.relative_to(repo).parts[:-1]) & SKIP_DIRS:
            continue
        rel = f.relative_to(repo).as_posix()
        if rel == "scripts/pii_scan.py":
            continue
        n += 1
        if ref is not None and ref.hashes:
            if hashlib.sha256(f.read_bytes()).hexdigest() in ref.hashes:
                out.add("file", rel, f.name)
        if f.suffix.lower() not in TEXT_EXT:
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for i, line in enumerate(text.splitlines(), 1):
            scan_text(line, f"{rel}:{i}", ref, out)
        if ref is not None:                       # names/messages wrapped across lines
            tmp = Findings()
            scan_text(" ".join(text.split()), rel, ref, tmp)
            out.items.extend(x for x in tmp.items if x[0] in ("message", "name"))
    return n


def scan_db(path: Path, ref: Reference | None, out: Findings) -> int:
    con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    n = 0
    for (t,) in con.execute("select name from sqlite_master where type='table'"):
        if t.startswith(("sqlite_", "cp_fts_")):
            continue
        cols = [r[1] for r in con.execute(f'pragma table_info("{t}")')]
        for row in con.execute(f'select * from "{t}"'):  # noqa: S608 — names from sqlite_master  # nosec B608
            for col, val in zip(cols, row, strict=True):
                if isinstance(val, str) and val:
                    n += 1
                    scan_text(val, f"db:{t}.{col}", ref, out)
                    if ref is None and col.lower() == "edrpou" and val and not val.startswith("99"):
                        out.add("tax-code-pattern", f"db:{t}.{col}", val)
    con.close()
    return n


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--repo", default=".", type=Path)
    ap.add_argument("--demo-db", action="append", default=[], type=Path)
    ap.add_argument("--real", action="append", default=[], type=Path, help="private DB (read-only)")
    ap.add_argument("--real-files", action="append", default=[], type=Path, help="private upload dirs")
    a = ap.parse_args(argv)

    ref = None
    if a.real or a.real_files:
        ref = Reference()
        for p in a.real:
            ref.add_db(p)
        for p in a.real_files:
            ref.add_files(p)
        print(f"reference: {len(ref.emails)} e-mails, {len(ref.phones)} phones, {len(ref.codes)} tax codes, "
              f"{len(ref.names)} names, {len(ref.shingles)} message shingles, {len(ref.hashes)} files")
    out = Findings()
    files = scan_repo(a.repo.resolve(), ref, out)
    values = sum(scan_db(p, ref, out) for p in a.demo_db)
    uniq = sorted(set(out.items))
    print(f"scanned: {files} repo files, {values} demo-DB values; mode: {'reference' if ref else 'pattern'}")
    for kind, where, val in uniq[:60]:
        print(f"  {kind:16} {where}  {val}")
    print(f"PII matches: {len(uniq)}")
    return 1 if uniq else 0


if __name__ == "__main__":
    sys.exit(main())

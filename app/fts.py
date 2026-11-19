"""Повнотекстовий пошук довідника: FTS5-таблиця cp_fts, синхронізація з коду.

Перенесено з kontakty_crm/app/fts.py. Тверда межа (грабля): це ЗВИЧАЙНА fts5-таблиця,
не external-content — команда `INSERT INTO cp_fts(cp_fts) VALUES('rebuild')` НЕ працює.
Після прямих SQL-правок треба перезаливати рядки через rebuild_all().
"""
import re

from sqlalchemy import text
from sqlmodel import Session, select

from .models import Counterparty, Person

FTS_CREATE = """
CREATE VIRTUAL TABLE IF NOT EXISTS cp_fts USING fts5(
  cp_id UNINDEXED,
  name, legal_name, edrpou, city, oblast, hromada, address, kved_text, tags, notes, persons,
  tokenize='unicode61 remove_diacritics 2'
)
"""

_INSERT = text(
    "INSERT INTO cp_fts (cp_id, name, legal_name, edrpou, city, oblast, hromada, address, "
    "kved_text, tags, notes, persons) VALUES (:cp_id, :name, :legal_name, :edrpou, "
    ":city, :oblast, :hromada, :address, :kved_text, :tags, :notes, :persons)"
)


def ensure_fts(s: Session) -> None:
    s.connection().exec_driver_sql(FTS_CREATE)


def _row(cp: Counterparty, persons: list[Person]) -> dict:
    return {
        "cp_id": cp.id,
        "name": cp.name or "",
        "legal_name": cp.legal_name or "",
        "edrpou": cp.edrpou or "",
        "city": cp.city or "",
        "oblast": cp.oblast or "",
        "hromada": cp.hromada or "",
        "address": cp.address or "",
        "kved_text": cp.kved or "",
        "tags": (cp.tags or "").replace(",", " ") + " " + (cp.roles or "").replace(",", " "),
        "notes": cp.notes or "",
        "persons": " ".join(f"{p.name} {p.role} {p.email} {p.phone}" for p in persons),
    }


def fts_delete(s: Session, cp_id: int) -> None:
    s.connection().execute(text("DELETE FROM cp_fts WHERE cp_id = :i"), {"i": cp_id})


def fts_upsert(s: Session, cp: Counterparty, persons: list[Person] | None = None) -> None:
    if persons is None:
        persons = list(s.exec(select(Person).where(Person.counterparty_id == cp.id)))
    fts_delete(s, cp.id)
    s.connection().execute(_INSERT, _row(cp, persons))


def rebuild_all(s: Session) -> int:
    """Повний перезалив індексу (після імпорту або для ремонту)."""
    ensure_fts(s)
    s.connection().execute(text("DELETE FROM cp_fts"))
    by_cp: dict[int, list[Person]] = {}
    for p in s.exec(select(Person)):
        by_cp.setdefault(p.counterparty_id, []).append(p)
    n = 0
    for cp in s.exec(select(Counterparty)):
        s.connection().execute(_INSERT, _row(cp, by_cp.get(cp.id, [])))
        n += 1
    return n


def _match_expr(q: str) -> str:
    """Токени в лапках + '*' (префіксний пошук; лапки нейтралізують '-', '.' тощо)."""
    parts = []
    for t in re.split(r"\s+", (q or "").strip()):
        if not t:
            continue
        parts.append('"' + t.replace('"', '""') + '"*')
    return " ".join(parts)


def search_ids(s: Session, q: str, limit: int = 1000) -> list[int]:
    """id контрагентів у порядку релевантності; [] якщо запит порожній."""
    expr = _match_expr(q)
    if not expr:
        return []
    rows = s.connection().execute(
        text("SELECT cp_id FROM cp_fts WHERE cp_fts MATCH :e ORDER BY rank LIMIT :l"),
        {"e": expr, "l": limit},
    ).fetchall()
    return [r[0] for r in rows]

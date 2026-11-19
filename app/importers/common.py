# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mykhailo Zvir

"""Ядро ідемпотентного імпорту (ТЗ, Додаток В §2).

Перенесено з kontakty_crm/importers/common.py. Три правила, які не можна послаблювати:
1. Ключ ідентичності — нормалізований ЄДРПОУ; якщо його немає — ієрархічний ключ назви.
2. Кілька кандидатів на ключ → вставляємо НОВИЙ запис (маркер _AMBIGUOUS), не зливаємо навмання.
3. Merge fill-empty: порожнє заповнюємо, куроване НЕ затираємо; розбіжності → у конфлікти.
"""
from dataclasses import dataclass, field

from sqlmodel import Session, select

from ..models import Counterparty, ImportConflict, Person
from ..normalize import name_key, norm_edrpou, norm_phone, norm_place

_AMBIGUOUS = object()

# поля, розбіжність по яких іде у звіт конфліктів, а не в базу
CONFLICT_FIELDS = ("legal_name", "address", "phone", "email")

# поля, що зливаються як множини (CSV)
SET_FIELDS = ("tags", "source", "roles")


@dataclass
class Stats:
    read: int = 0
    new: int = 0
    merged: int = 0
    unchanged: int = 0
    conflicts: list[dict] = field(default_factory=list)
    # Джерело-специфічні числа (нові обʼєкти, кластери, пропущені рядки).
    # НЕ входить в as_dict: `ImportRun` має фіксовані колонки, а змішувати
    # лічильники контрагентів і обʼєктів в одному числі — брехати у звіті.
    extra: dict = field(default_factory=dict)

    def dodaty(self, klyuch: str, n: int = 1) -> None:
        self.extra[klyuch] = self.extra.get(klyuch, 0) + n

    def as_dict(self) -> dict:
        return {"rows_read": self.read, "rows_new": self.new, "rows_merged": self.merged,
                "rows_unchanged": self.unchanged, "rows_conflict": len(self.conflicts)}


def _merge_csv(existing: str, incoming: str) -> str:
    have = [x for x in (existing or "").split(",") if x]
    for x in (incoming or "").split(","):
        if x and x not in have:
            have.append(x)
    return ",".join(have)


class Index:
    """In-memory індекси довідника — дедуп без N запитів до БД."""

    def __init__(self, s: Session):
        self.s = s
        self.by_uid: dict[str, Counterparty] = {}
        self.by_edrpou: dict[str, Counterparty] = {}
        self.by_name: dict[str, object] = {}
        for cp in s.exec(select(Counterparty)):
            self.add(cp)

    @staticmethod
    def _get(obj, key: str) -> str:
        """Однаково читає і dict-рядок джерела, і модель Counterparty."""
        v = obj.get(key) if isinstance(obj, dict) else getattr(obj, key, None)
        return (v or "") if isinstance(v, str) else ("" if v is None else str(v))

    def _name_keys(self, cp_or_row) -> list[str]:
        name = self._get(cp_or_row, "name")
        oblast = self._get(cp_or_row, "oblast")
        city = self._get(cp_or_row, "city")
        base = name_key(name)
        return [
            f"3:{base}|{oblast.lower()}|{norm_place(city)}",
            f"2:{base}|{oblast.lower()}",
            f"1:{base}",
        ]

    def add(self, cp: Counterparty) -> None:
        if cp.uid:
            self.by_uid[cp.uid] = cp
        if cp.edrpou:
            self.by_edrpou[cp.edrpou] = cp
        for k in self._name_keys(cp):
            cur = self.by_name.get(k)
            if cur is None:
                self.by_name[k] = cp
            elif cur is not _AMBIGUOUS and cur.id != cp.id:
                self.by_name[k] = _AMBIGUOUS

    def find(self, row: dict, src_uid: str = "") -> Counterparty | None:
        """Наявний контрагент або None.

        Порядок ключів: детермінований uid рядка джерела → ЄДРПОУ → ієрархічний ключ назви.
        `src_uid` потрібен саме для ідемпотентності: без ЄДРПОУ й з неоднозначною назвою
        рядок інакше вставлявся б наново при кожному прогоні.
        """
        if src_uid and src_uid in self.by_uid:
            return self.by_uid[src_uid]
        e = norm_edrpou(row.get("edrpou"))
        if e:
            return self.by_edrpou.get(e)

        row_city, row_obl = self._get(row, "city"), self._get(row, "oblast")
        for k in self._name_keys(row):
            cur = self.by_name.get(k)
            if cur is _AMBIGUOUS:
                return None          # неоднозначно → новий запис, розбере людина
            if cur is None:
                continue
            # Guard від злиття тезок: типові назви «Маяк»/«Нива» в різних місцях —
            # це РІЗНІ підприємства. Відкат на менш специфічний ключ не має права
            # склеїти записи, які прямо суперечать один одному.
            cur_city, cur_obl = self._get(cur, "city"), self._get(cur, "oblast")
            if row_obl and cur_obl and row_obl.lower() != cur_obl.lower():
                return None
            if row_city and cur_city and norm_place(row_city) != norm_place(cur_city):
                return None
            return cur
        return None


def upsert(s: Session, idx: Index, row: dict, source: str, st: Stats,
           src_uid: str = "") -> Counterparty:
    """Ідемпотентне злиття одного рядка. Повертає контрагента (новий або наявний).

    `src_uid` — детермінований ключ рядка джерела (напр. 'kontakty_sqlite:1234').
    Саме він робить повторний прогін безпечним.
    """
    st.read += 1
    row = dict(row)
    row["edrpou"] = norm_edrpou(row.get("edrpou"))
    for f in ("phone", "phone2"):
        if row.get(f):
            row[f] = norm_phone(row[f])
    row["source"] = _merge_csv(row.get("source", ""), source)

    cp = idx.find(row, src_uid)
    if cp is None:
        cp = Counterparty(**{k: v for k, v in row.items()
                             if hasattr(Counterparty, k) and v not in (None, "")},
                          created_by="import")
        if src_uid:
            cp.uid = src_uid
        if not cp.name:
            cp.name = row.get("name") or "—"
        s.add(cp)
        s.flush()          # потрібен id для індексів і FTS
        idx.add(cp)
        st.new += 1
        return cp

    changed = False
    for k, v in row.items():
        if v in (None, "") or not hasattr(cp, k) or k in ("id", "uid", "created_at"):
            continue
        cur = getattr(cp, k)
        if k in SET_FIELDS:
            merged = _merge_csv(cur or "", str(v))
            if merged != (cur or ""):
                setattr(cp, k, merged)
                changed = True
        elif cur in (None, ""):
            setattr(cp, k, v)          # fill-empty
            changed = True
        elif k in CONFLICT_FIELDS and str(cur) != str(v):
            st.conflicts.append({"entity_id": cp.id, "field": k,
                                 "existing_value": str(cur), "incoming_value": str(v)})
        # інакше: заповнене куроване значення не чіпаємо

    if changed:
        s.add(cp)
        st.merged += 1
    else:
        st.unchanged += 1
    return cp


def upsert_person(s: Session, cp: Counterparty, p: dict,
                  existing: list[Person] | None = None) -> Person | None:
    """Особа дедуплікується в межах контрагента за (ім'я, телефон, email)."""
    name = (p.get("name") or "").strip()
    phone = norm_phone(p.get("phone", ""))
    email = (p.get("email") or "").strip()
    if not (name or phone or email):
        return None
    if existing is None:
        existing = list(s.exec(select(Person).where(Person.counterparty_id == cp.id)))
    for e in existing:
        if (e.name or "").strip().lower() == name.lower() and name:
            return e
        if phone and e.phone == phone:
            return e
        if email and (e.email or "").lower() == email.lower():
            return e
    row = Person(counterparty_id=cp.id, name=name, role=p.get("role", ""),
                 phone=phone, email=email, is_primary=bool(p.get("is_primary")),
                 source=p.get("source", ""), created_by="import")
    s.add(row)
    existing.append(row)
    return row


def save_conflicts(s: Session, run_id: int, st: Stats) -> None:
    for c in st.conflicts:
        s.add(ImportConflict(import_run_id=run_id, entity="counterparty", **c))

"""Контрагент і контактна особа — ядро довідника контактів (ТЗ §4b, Додаток А §1.2–1.3).

Схема сумісна з kontakty_crm.Organization, щоб міграція йшла без перетворень,
плюс поле `roles` (роль сторони у схемі угоди) — потрібне для напряму «Елеватори».
"""

from sqlmodel import Field

from .base import BaseRow

# категорія: slug → людська назва (успадковано з kontakty_crm)
CATEGORIES = {
    "pidpryyemstvo": "Підприємство",
    "zamovnyk_zakupivel": "Замовник (ProZorro)",
    "postachalnyk": "Постачальник (ProZorro)",
    "vyrobnyk_obladnannya": "Виробник обладнання",
    "kontragent_ugod": "Контрагент угод",
    "hromada": "Громада",
    "knp": "КНП",
    "go": "Громадська організація",
}

# роль сторони у схемі (ТЗ, Додаток А §1.2) — CSV у полі `roles`
PARTY_ROLES = {
    "investor": "Інвестор",
    "gromada": "Громада",
    "fermer": "Фермер",
    "kredytor": "Кредитор",
    "pidryadnyk": "Підрядник",
    "donor": "Донор",
    "bank": "Банк",
    "postachalnyk_syrovyny": "Постачальник сировини",
    "oda": "ОДА / облрада",
    "yuryst": "Юрист / консультант",
}

DATA_STATUSES = {
    "": "—",
    "verified": "Перевірено",
    "needs_check": "Потребує перевірки",
}


class Counterparty(BaseRow, table=True):
    name: str = Field(index=True)
    legal_name: str = ""
    edrpou: str = Field(default="", index=True)   # нормалізований: цифри, lpad до 8
    country: str = Field(default="UA", index=True)
    oblast: str = Field(default="", index=True)
    raion: str = ""
    city: str = ""
    address: str = ""
    hromada: str = Field(default="", index=True)
    lat: float | None = None
    lon: float | None = None
    phone: str = ""      # нормалізований +380…
    phone2: str = ""
    email: str = ""
    website: str = ""
    card_url: str = ""
    kved: str = ""
    category: str = Field(default="pidpryyemstvo", index=True)
    roles: str = Field(default="", index=True)    # CSV з PARTY_ROLES
    tags: str = ""                                # CSV: segment:…,tier:…,cluster:…
    source: str = Field(default="", index=True)   # CSV джерел у порядку появи
    source_ref: str = ""
    notes: str = ""
    data_status: str = ""                         # "" | verified | needs_check

    # ── реєстрові дані юрособи (оновлення 05.08.2026) ────────────────────────
    all_kved: str = ""                            # усі КВЕД через «;», не лише основний
    statut_kapital: float | None = None
    vat_code: str = ""
    date_reg: str = ""                            # рядком: у джерелі бувають неповні дати
    org_status: str = ""                          # зареєстровано / припинено / банкрутство
    # Три стани: True — підтверджено активність, False — підтверджено відсутність,
    # None — НЕ ПЕРЕВІРЯЛИ. Порожнє ≠ «неактивний».
    financially_active: bool | None = None
    beneficiaries: str = ""
    transport_size: str = ""
    real_estate_count: int | None = None
    activity_text: str = ""                       # довгий опис КВЕД; ніколи не в API карти

    @property
    def completeness(self) -> int:
        """Повнота картки 0–100 — індикатор для довідника (ТЗ §4b)."""
        checks = [
            bool(self.edrpou), bool(self.phone or self.phone2), bool(self.email),
            bool(self.address), bool(self.city), bool(self.oblast),
            bool(self.kved), bool(self.lat and self.lon),
        ]
        return round(100 * sum(checks) / len(checks))


class Person(BaseRow, table=True):
    counterparty_id: int = Field(foreign_key="counterparty.id", index=True)
    name: str
    role: str = ""
    phone: str = ""
    email: str = ""
    is_primary: bool = False
    source: str = ""
    notes: str = ""

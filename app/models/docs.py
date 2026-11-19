# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mykhailo Zvir

"""Шаблони документів, вихідні запити й налаштування офісу (ТЗ §4a, Додаток А §1.9–1.10)."""
from datetime import date, datetime

from sqlmodel import Field, SQLModel

from .base import BaseRow, utcnow

# Потік узгодження документа (ТЗ §2, Додаток А §1.13).
# Правка Богдана НЕ затирає оригінал Марка — створює нову версію.
WORKFLOW = {
    "chernetka": "Чернетка",
    "na_pravtsi": "На правці",
    "uzhodzheno": "Узгоджено",
    "do_vidpravlennya": "До відправлення",
    "nadislano": "Надіслано",
    "arkhiv": "Архів",
}
WORKFLOW_ORDER = list(WORKFLOW)

# Види документів, які мають ВЛАСНЕ значення для процесу офісу. Поле `kind` лишається
# вільним рядком (там живуть коди листів COM-06, INV-05 і будь-що інше) — цей словник
# лише дає підказку у формах і назву для людини.
# «Презентація» заведена окремо: попередні презентації —
# це його основний продукт на старті проєкту, і саме їх затверджує Богдан.
DOC_KINDS = {
    "prezentatsiya": "Презентація",
    "TZ": "Технічне завдання",
    "KP": "Комерційна пропозиція",
    "dogovir": "Договір",
    "MOU": "Меморандум",
    "analiz": "Аналітична записка",
}

# Вид документа, без якого новий проєкт вважається «ще не опрацьованим»
KIND_PREZENTATSIYA = "prezentatsiya"

CHANNELS = {
    "email": "Email",
    "poshta": "Пошта",
    "osobysto": "Особисто",
    "messenger": "Месенджер",
}

REPLY_STATUSES = {
    "chekaiemo": "Чекаємо",
    "otrymano": "Отримано",
    "prostrocheno": "Прострочено",
    "bez_vidpovidi": "Без відповіді",
}


class DocTemplate(BaseRow, table=True):
    """26 листів з `elevator_robocha/output/letters/` + шаблони біогазу."""
    code: str = Field(default="", index=True)          # COM-06, INV-05, KP…
    title: str = ""
    group: str = Field(default="", index=True)         # INV | COM | FARM | AUX | BIO
    program_code: str = Field(default="", index=True)  # biogaz | elevator
    stage_code: str = ""                               # прив'язка за funnel.md
    lang: str = "uk"
    body_md: str = ""
    placeholders: str = ""                             # CSV знайдених у тілі
    output_formats: str = "docx,pdf"
    source_path: str = ""

    @property
    def placeholder_list(self) -> list[str]:
        return [p for p in (self.placeholders or "").split("||") if p]


class Outreach(BaseRow, table=True):
    """Вихідний запит або лист. Робоче місце Галини (модуль M14)."""
    document_id: int | None = Field(default=None, foreign_key="document.id", index=True)
    document_version: int = 1
    counterparty_id: int | None = Field(default=None, foreign_key="counterparty.id",
                                           index=True)
    person_id: int | None = Field(default=None, foreign_key="person.id")
    site_id: int | None = Field(default=None, foreign_key="site.id")
    deal_id: int | None = Field(default=None, foreign_key="deal.id", index=True)
    recipient: str = ""                    # кому — текстом, як у листі
    subject: str = ""
    channel: str = Field(default="email", index=True)
    sent_at: datetime | None = None
    sent_by: str = ""
    reply_due_date: date | None = Field(default=None, index=True)
    reply_status: str = Field(default="chekaiemo", index=True)
    reply_at: date | None = None
    reply_note: str = ""
    parent_outreach_id: int | None = Field(default=None, foreign_key="outreach.id")

    @property
    def is_overdue(self) -> bool:
        return bool(self.reply_status == "chekaiemo" and self.reply_due_date
                    and self.reply_due_date < date.today())

    @property
    def days_waiting(self) -> int:
        if not self.sent_at:
            return 0
        return (utcnow() - self.sent_at).days


class Setting(SQLModel, table=True):
    """Налаштування офісу: назва, контакти, підпис — підставляються в шаблони."""
    key: str = Field(primary_key=True)
    value: str = ""
    label: str = ""
    updated_at: datetime = Field(default_factory=utcnow)


DEFAULT_SETTINGS = [
    ("office.name", "Проєктний офіс", "Назва офісу (укр.)"),
    ("office.name_en", "Project Office", "Назва офісу (англ.)"),
    ("office.contacts", "", "Контакти офісу (телефон, email)"),
    ("office.signature", "", "Підпис під листами"),
    ("outreach.reply_days", "14", "Скільки днів чекаємо відповідь за замовчуванням"),
]

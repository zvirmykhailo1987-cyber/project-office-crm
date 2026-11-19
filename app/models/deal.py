# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mykhailo Zvir

"""Угода (проєктний трек), активність і документ (ТЗ, Додаток А §1.4, §1.6, §1.8)."""
from datetime import date, datetime

from sqlmodel import Field

from .base import BaseRow, utcnow

# типи активностей — успадковано з biogaz_robocha/crm/models.py:25
ACTIVITY_TYPES = ["Дзвінок", "WhatsApp", "Email", "Зустріч", "Нотатка"]

DEAL_STATUSES = {
    "active": "В роботі",
    "won": "Виграно",
    "lost": "Втрачено",
    "paused": "Відкладено",
}


class Deal(BaseRow, table=True):
    program_id: int = Field(foreign_key="program.id", index=True)
    pipeline_id: int = Field(foreign_key="pipeline.id", index=True)
    stage_id: int = Field(foreign_key="stage.id", index=True)
    title: str = ""
    counterparty_id: int | None = Field(default=None, foreign_key="counterparty.id",
                                           index=True)
    owner: str = Field(default="", index=True)
    value: float | None = None
    currency: str = "UAH"
    probability: int = 0
    # обов'язкове за домашнім правилом: угода без наступної дії підсвічується
    next_action_date: date | None = Field(default=None, index=True)
    next_action_note: str = ""
    last_contacted_at: datetime | None = None
    stage_entered_at: datetime = Field(default_factory=utcnow)
    status: str = Field(default="active", index=True)
    lost_reason: str = ""
    tags: str = ""
    notes: str = ""

    @property
    def days_on_stage(self) -> int:
        return (utcnow() - self.stage_entered_at).days

    @property
    def is_stale(self) -> bool:
        """Без активності понад 21 день на нетермінальній стадії (ТЗ, Додаток Б §1)."""
        if self.status != "active":
            return False
        base = self.last_contacted_at or self.stage_entered_at
        return (utcnow() - base).days > 21

    @property
    def action_overdue(self) -> bool:
        return bool(self.next_action_date and self.next_action_date < date.today())


class Activity(BaseRow, table=True):
    """Слід контакту. Дата, автор і прив'язка проставляються системою."""
    type: str = Field(default="Дзвінок", index=True)
    when: datetime = Field(default_factory=utcnow, index=True)
    author: str = ""
    deal_id: int | None = Field(default=None, foreign_key="deal.id", index=True)
    counterparty_id: int | None = Field(default=None, foreign_key="counterparty.id",
                                           index=True)
    person_id: int | None = Field(default=None, foreign_key="person.id")
    outcome: str = ""
    note: str = ""
    is_ai_draft: bool = False       # чернетка AI — не впливає на воронку до підтвердження


class Document(BaseRow, table=True):
    """Мінімальний документ — потрібен уже в Ф2, бо на ньому тримаються гейти.

    Повний модуль (шаблони, генерація, узгодження) — Ф4.
    `doc_group_uid` + `version` вирішують проблему `_v2/_v3/_final`.
    """
    title: str = ""
    kind: str = Field(default="", index=True)      # код: KP, MOU, COM-06, INV-05…
    doc_group_uid: str = Field(default="", index=True)
    version: int = 1
    filename: str = ""
    stored_path: str = ""
    size: int = 0
    deal_id: int | None = Field(default=None, foreign_key="deal.id", index=True)
    counterparty_id: int | None = Field(default=None, foreign_key="counterparty.id")
    stage_id: int | None = Field(default=None, foreign_key="stage.id")
    is_final: bool = False                          # Tier3
    signed_at: date | None = None
    signed_by_side: str = ""
    workflow_status: str = Field(default="chernetka", index=True)
    uploaded_by: str = ""

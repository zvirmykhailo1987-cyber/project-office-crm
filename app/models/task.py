"""Завдання (ТЗ, Додаток А §1.7).

Завдання не лише вводяться руками: їх породжують правила — прострочена відповідь
на лист, наступна дія по угоді, перехід стадії. Джерело завжди видно в полі `source`.
"""
from datetime import date, datetime

from sqlmodel import Field

from .base import BaseRow

STATUSES = {
    "open": "Відкрите",
    "in_progress": "В роботі",
    "done": "Зроблено",
    "cancelled": "Скасовано",
}

PRIORITIES = {"high": "Високий", "normal": "Звичайний", "low": "Низький"}

SOURCES = {
    "manual": "створено вручну",
    "vid_koleghy": "доручення колеги",
    "vid_komentarya": "виросло з рекомендації",
    "outreach_due": "контроль відповіді на лист",
    "stage_rule": "правило стадії",
    "ai_draft": "AI-чернетка",
}


class Task(BaseRow, table=True):
    title: str = ""
    note: str = ""
    assignee: str = Field(default="", index=True)
    due_date: date | None = Field(default=None, index=True)
    priority: str = Field(default="normal", index=True)
    status: str = Field(default="open", index=True)
    source: str = Field(default="manual", index=True)

    deal_id: int | None = Field(default=None, foreign_key="deal.id", index=True)
    site_id: int | None = Field(default=None, foreign_key="site.id")
    counterparty_id: int | None = Field(default=None, foreign_key="counterparty.id")
    document_id: int | None = Field(default=None, foreign_key="document.id")
    outreach_id: int | None = Field(default=None, foreign_key="outreach.id", index=True)

    comment_id: int | None = Field(default=None, foreign_key="comment.id")
    # Хаб як робоча сутність (25.08.2026): завдання можна привʼязати до
    # кластера так само, як до угоди/обʼєкта — «наступна дія» хабу живе тут.
    cluster_id: int | None = Field(default=None, foreign_key="cluster.id", index=True)
    accepted_at: datetime | None = None      # виконавець узяв у роботу
    declined_reason: str = ""                   # відмова лише з причиною
    done_at: datetime | None = None
    done_by: str = ""

    @property
    def is_open(self) -> bool:
        return self.status in ("open", "in_progress")

    @property
    def from_colleague(self) -> bool:
        """Доручення від іншої людини — його показуємо помітніше за власне."""
        return bool(self.created_by and self.created_by != self.assignee
                    and self.created_by != "правило")

    @property
    def is_overdue(self) -> bool:
        return bool(self.is_open and self.due_date and self.due_date < date.today())

    @property
    def is_today(self) -> bool:
        return bool(self.is_open and self.due_date == date.today())

    @property
    def days_left(self) -> int | None:
        return (self.due_date - date.today()).days if self.due_date else None

# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mykhailo Zvir

"""Сервіс баз даних (ТЗ §4c): реєстр джерел + журнал імпортів + конфлікти злиття."""
from datetime import datetime

from sqlmodel import Field, SQLModel

from .base import BaseRow, utcnow


class DataSource(BaseRow, table=True):
    """Реєстр джерел даних: що підключено, коли востаннє імпортувалось, що дало."""
    code: str = Field(index=True)
    title: str = ""
    kind: str = "csv"                 # csv | xlsx | json | sqlite | api
    path_or_endpoint: str = ""
    what_it_gives: str = ""
    owner: str = ""
    refresh_period_days: int = 0      # 0 = разове джерело
    last_import_at: datetime | None = None
    last_import_run_id: int | None = None
    rows_last: int = 0
    rows_total_contributed: int = 0
    active: bool = True

    @property
    def is_stale(self) -> bool:
        if not self.refresh_period_days or not self.last_import_at:
            return False
        return (utcnow() - self.last_import_at).days > self.refresh_period_days


class ImportRun(SQLModel, table=True):
    """Один прогін імпорту. Ідемпотентність перевіряється по цих числах."""
    id: int | None = Field(default=None, primary_key=True)
    source_code: str = Field(default="", index=True)
    file_path: str = ""
    started_at: datetime = Field(default_factory=utcnow, index=True)
    finished_at: datetime | None = None
    rows_read: int = 0
    rows_new: int = 0
    rows_merged: int = 0
    rows_unchanged: int = 0
    rows_conflict: int = 0
    run_by: str = ""
    error: str = ""

    @property
    def is_idempotent(self) -> bool:
        """Повторний прогін має дати «нових 0, злито 0» (ТЗ, Ф1)."""
        return self.rows_new == 0 and self.rows_merged == 0


class ImportConflict(SQLModel, table=True):
    """Розбіжність, яку не можна вирішити автоматично — вирішує людина."""
    id: int | None = Field(default=None, primary_key=True)
    import_run_id: int = Field(default=0, index=True)
    entity: str = "counterparty"
    entity_id: int | None = None
    field: str = ""
    existing_value: str = ""
    incoming_value: str = ""
    resolution: str = Field(default="pending", index=True)  # pending | kept | replaced
    resolved_by: str = ""
    resolved_at: datetime | None = None

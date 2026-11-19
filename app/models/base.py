# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mykhailo Zvir

"""Спільна база рядка: id + uid + позначки часу (ТЗ, Додаток А)."""
import uuid
from datetime import datetime, UTC

from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    # Наївний UTC, як і раніше (БД зберігає наївні значення); без deprecated utcnow().
    return datetime.now(UTC).replace(tzinfo=None)


class BaseRow(SQLModel):
    id: int | None = Field(default=None, primary_key=True)
    uid: str = Field(default_factory=lambda: uuid.uuid4().hex, index=True)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    created_by: str = ""


class SyncMeta(SQLModel, table=True):
    """Курсори фонових процесів (напр. збагачення ProZorro)."""
    key: str = Field(primary_key=True)
    value: str = ""
    updated_at: datetime = Field(default_factory=utcnow)

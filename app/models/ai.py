"""Журнал прогонів AI (ТЗ §6, п.5).

Кожен прогін фіксується: що подали, що вийшло, чи пройшло валідацію схеми,
чи довелось падати на детермінований екстрактор, хто підтвердив результат.
Без цього журналу неможливо відповісти «звідки в картці це значення».
"""
from datetime import datetime

from sqlmodel import Field

from .base import BaseRow

KINDS = {
    "rahunok": "Рахунок / КП",
    "anketa": "Анкета скринінгу C1–C9",
    "kontakty": "Список контактів",
}

# Звідки взявся результат — показується користувачу поруч із чернеткою
ENGINES = {
    "llm": "мовна модель",
    "deterministic": "детермінований екстрактор",
    "none": "не вдалося",
}


class AiRun(BaseRow, table=True):
    kind: str = Field(default="", index=True)
    engine: str = Field(default="deterministic", index=True)
    model: str = ""
    source_file: str = ""
    source_chars: int = 0
    output_json: str = ""
    schema_valid: bool = False
    fallback_used: bool = False
    arithmetic_ok: bool | None = None     # None = арифметики не було
    arithmetic_note: str = ""
    fields_filled: int = 0
    fields_total: int = 0
    duration_ms: int = 0
    error: str = ""
    confirmed_by: str = ""
    confirmed_at: datetime | None = None
    applied_to: str = ""                     # куди застосовано після підтвердження

    @property
    def fill_rate(self) -> int:
        return round(100 * self.fields_filled / self.fields_total) if self.fields_total else 0

    @property
    def engine_label(self) -> str:
        return ENGINES.get(self.engine, self.engine)

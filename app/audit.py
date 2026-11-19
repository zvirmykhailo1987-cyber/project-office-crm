"""Risk-tier модель дій (ТЗ §9).

Tier0 читання (не логуємо) · Tier1 зворотна зміна (лог) ·
Tier2 масова/помітна (лог + сповіщення) · Tier3 незворотна (hard-confirm → HTTP 428).
"""
from fastapi import HTTPException
from sqlmodel import Session

from .models import AuditEntry

TIER_READ = 0
TIER_CHANGE = 1
TIER_BULK = 2
TIER_IRREVERSIBLE = 3

TIER_LABELS = {
    TIER_READ: "читання",
    TIER_CHANGE: "зміна",
    TIER_BULK: "масова дія",
    TIER_IRREVERSIBLE: "незворотна дія",
}

# Дії Tier3 — вимагають окремого підтвердження (ТЗ §9)
TIER3_ACTIONS = {
    "document.finalize": "позначити документ фінальним",
    "document.generate_financial": "згенерувати документ з фінансовими цифрами",
    "site.set_guarantee": "змінити суму фінансового зобов'язання громади",
    "score.change_weights": "змінити ваги моделі скорингу",
    "entity.delete": "видалити запис",
    "backup.download": "вивантажити бекап",
    # Гейт стадії перекриває шлях угодам, які ВЖЕ стоять далі по воронці:
    # вчора вони пройшли, сьогодні їхній рух заблоковано новою вимогою.
    "stage.gates": "змінити обовʼязкові документи стадії",
    "stage.delete": "прибрати стадію воронки",
}


def log(s: Session, actor: str, action: str, *, entity: str = "",
        entity_id: int | None = None, tier: int = TIER_CHANGE,
        confirmed: bool = False, detail: str = "") -> AuditEntry:
    e = AuditEntry(actor=actor, action=action, entity=entity, entity_id=entity_id,
                   tier=tier, confirmed=confirmed, detail=detail)
    s.add(e)
    return e


def require_confirm(action: str, confirmed: bool) -> None:
    """Tier3 без підтвердження → 428 Precondition Required з поясненням наслідків."""
    if confirmed:
        return
    what = TIER3_ACTIONS.get(action, action)
    raise HTTPException(
        status_code=428,
        detail=f"Незворотна дія: {what}. Потрібне окреме підтвердження.",
    )

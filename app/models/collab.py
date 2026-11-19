"""Шар співпраці: затвердження · коментарі й рекомендації · сповіщення (Ф8).

Процес офісу дослівно (уточнення Марка 04.08.2026):
  Марко — попередній аналіз, пошук, стратегії, технічна розробка;
  Богдан — ЗАТВЕРДЖУЄ або каже, що треба поміняти;
  кожен — може дати рекомендацію або коментар;
  усі — оперативно бачать, що робиться, і оперативно реагують.

Прив'язка до сутностей — поліморфна `(entity, entity_id)`. Це вже усталений патерн
проєкту (`AuditEntry`, `ImportConflict`), тому не заводимо 20 таблиць звʼязків.
Ідентичність людини — рядок `user.name`, як усюди в проєкті (не FK).
"""
from datetime import datetime

from sqlmodel import Field

from .base import BaseRow, utcnow

# ── сутності, до яких можна чіпляти співпрацю ────────────────────────────────
# ключ → (людська назва, шаблон URL картки)
ENTITIES = {
    "deal": ("Угода", "/voronky/{id}"),
    "site": ("Обʼєкт", "/obyekty/{id}"),
    "cluster": ("Хаб", "/haby/{id}"),
    "counterparty": ("Контрагент", "/dovidnyk/{id}"),
    "document": ("Документ", "/dokumenty/{id}"),
    "airun": ("AI-витяг", "/ai/{id}"),
    "task": ("Завдання", "/zavdannya#t{id}"),
    "chat_message": ("Повідомлення в чаті", "/chat#m{id}"),
}


def entity_url(entity: str, entity_id: int | None) -> str:
    """Посилання на картку сутності. Немає шаблону або id → порожньо, не вигадуємо."""
    meta = ENTITIES.get(entity)
    if not meta or not entity_id:
        return ""
    return meta[1].format(id=entity_id)


def entity_label(entity: str) -> str:
    return ENTITIES.get(entity, (entity, ""))[0]


# ── коментарі й рекомендації ─────────────────────────────────────────────────
COMMENT_KINDS = {
    "komentar": "Коментар",
    "rekomendatsiya": "Рекомендація",
    "pytannya": "Питання",
}

RESOLUTIONS = {
    "vrahovano": "Враховано",
    "ne_beru": "Не беру",
}


class Comment(BaseRow, table=True):
    """Коментар/рекомендація/питання на будь-якій сутності.

    ВАЖЛИВО: це НЕ `Activity`. Activity — слід контакту з контрагентом; вона рухає
    `last_contacted_at`, `is_stale` і автопереходи стадій. Внутрішнє обговорення
    через Activity зіпсувало б метрики воронки.
    """
    entity: str = Field(default="", index=True)
    entity_id: int = Field(default=0, index=True)
    author: str = Field(default="", index=True)
    text: str = ""
    kind: str = Field(default="komentar", index=True)
    parent_id: int | None = Field(default=None, foreign_key="comment.id", index=True)
    mentions: str = ""                       # CSV логінів, кого згадано

    # тільки для kind=rekomendatsiya: чи врахували
    resolution: str = Field(default="", index=True)
    resolved_by: str = ""
    resolved_at: datetime | None = None
    resolution_note: str = ""

    @property
    def kind_label(self) -> str:
        return COMMENT_KINDS.get(self.kind, self.kind)

    @property
    def is_recommendation(self) -> bool:
        return self.kind == "rekomendatsiya"

    @property
    def is_open_recommendation(self) -> bool:
        return self.is_recommendation and not self.resolution

    @property
    def mention_list(self) -> list[str]:
        return [m for m in (self.mentions or "").split(",") if m]


# ── затвердження ─────────────────────────────────────────────────────────────
APPROVAL_STATUSES = {
    "na_rozglyadi": "На розгляді",
    "zatverdzheno": "Затверджено",
    "na_doopratsyuvannya": "На доопрацювання",
    "vidklykano": "Відкликано",
}


class Approval(BaseRow, table=True):
    """Подання роботи на затвердження.

    Тверді правила (реалізуються у web-шарі):
      1. Автор НЕ затверджує себе — рішення приймає той, хто має CAP_APPROVE.
      2. «На доопрацювання» без коментаря не приймається — це і є «скажи, що поміняти».
      3. Затвердження ≠ завдання: рішення про роботу й доручення — різні канали.
    """
    entity: str = Field(default="", index=True)
    entity_id: int = Field(default=0, index=True)
    submitted_by: str = Field(default="", index=True)
    submitted_at: datetime = Field(default_factory=utcnow, index=True)
    note: str = ""                            # що подається і на що звернути увагу

    status: str = Field(default="na_rozglyadi", index=True)
    approver: str = ""
    decided_at: datetime | None = None
    decision_note: str = ""
    parent_approval_id: int | None = Field(default=None, foreign_key="approval.id")

    @property
    def status_label(self) -> str:
        return APPROVAL_STATUSES.get(self.status, self.status)

    @property
    def is_open(self) -> bool:
        return self.status == "na_rozglyadi"

    @property
    def days_waiting(self) -> int:
        end = self.decided_at or utcnow()
        return (end - self.submitted_at).days


# ── сповіщення ───────────────────────────────────────────────────────────────
NOTIF_KINDS = {
    "zgadka": "Вас згадали",
    "na_zatverdzhennya": "Подано на затвердження",
    "rishennya": "Рішення по вашій роботі",
    "komentar": "Новий коментар",
    "rekomendatsiya": "Рекомендація",
    "zavdannya": "Вам поставили завдання",
    "zavdannya_vidmova": "Від завдання відмовились",
    "zavdannya_pytannya": "Питання щодо завдання",
    "chat": "Вас згадали в чаті",
}


class Notification(BaseRow, table=True):
    """Персональне сповіщення. Читається лічильником у шапці (полінг 30 с).

    `user` — ім'я одержувача (як усюди в проєкті, рядок). Сповіщення СОБІ не шлемо.
    """
    user: str = Field(default="", index=True)
    kind: str = Field(default="komentar", index=True)
    text: str = ""
    url: str = ""
    entity: str = ""
    entity_id: int | None = None
    from_whom: str = ""
    read_at: datetime | None = Field(default=None, index=True)

    @property
    def kind_label(self) -> str:
        return NOTIF_KINDS.get(self.kind, self.kind)

    @property
    def is_unread(self) -> bool:
        return self.read_at is None

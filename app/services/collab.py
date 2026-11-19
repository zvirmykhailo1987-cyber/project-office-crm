# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mykhailo Zvir

"""Логіка співпраці: згадки · сповіщення · людиночитна стрічка (Ф8).

Три речі, які тут вирішуються:
  1. @згадка → кому саме (резолв за `User.login`, латиниця — надійний ключ;
     `name` кирилицею в схемі не унікальний за задумом).
  2. Подія → сповіщення потрібним людям (СОБІ ніколи).
  3. `AuditEntry` → фраза українською + посилання. Аудит уже пише 25 типів подій,
     але досі був невидимим — стрічка це і є його показ.
"""
import re
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import datetime
from collections.abc import Iterable

from sqlmodel import Session, func, select

from ..models import (
    Approval,
    AuditEntry,
    Comment,
    Notification,
    Task,
    User,
    entity_label,
    entity_url,
    utcnow,
)

MENTION_RE = re.compile(r"@([A-Za-z][A-Za-z0-9_.\-]{1,30})")


# ── згадки ───────────────────────────────────────────────────────────────────
def find_mentions(s: Session, text: str) -> list[User]:
    """@login → активні користувачі. Неіснуючий логін просто ігнорується."""
    logins = {m.lower() for m in MENTION_RE.findall(text or "")}
    if not logins:
        return []
    users = s.exec(select(User).where(User.active == True))  # noqa: E712
    return [u for u in users if (u.login or "").lower() in logins]


def mention_hint(s: Session) -> str:
    """Підказка для форми: кого можна згадати."""
    users = list(s.exec(select(User).where(User.active == True)))  # noqa: E712
    return " · ".join(f"@{u.login} — {u.name}" for u in users if u.login)


# ── сповіщення ───────────────────────────────────────────────────────────────
def notify(s: Session, *, user: str, kind: str, text: str, url: str = "",
           entity: str = "", entity_id: int | None = None,
           from_whom: str = "") -> Notification | None:
    """Створює сповіщення. Собі не шлемо — це шум, а не сигнал."""
    if not user or user == from_whom:
        return None
    n = Notification(user=user, kind=kind, text=text[:400], url=url,
                     entity=entity, entity_id=entity_id, from_whom=from_whom,
                     created_by=from_whom or "система")
    s.add(n)
    return n


def notify_many(s: Session, users: Iterable[str], **kw) -> int:
    seen, made = set(), 0
    for u in users:
        if u and u not in seen:
            seen.add(u)
            if notify(s, user=u, **kw):
                made += 1
    return made


def unread_count(s: Session, user_name: str) -> int:
    return s.exec(select(func.count()).select_from(Notification)
                  .where(Notification.user == user_name,
                         Notification.read_at.is_(None))).one()


def mark_read(s: Session, user_name: str, notif_id: int | None = None) -> int:
    stmt = select(Notification).where(Notification.user == user_name,
                                      Notification.read_at.is_(None))
    if notif_id:
        stmt = stmt.where(Notification.id == notif_id)
    n = 0
    for row in s.exec(stmt):
        row.read_at = utcnow()
        s.add(row)
        n += 1
    return n


# ── хто «власник» сутності (кому йде коментар) ───────────────────────────────
def owners_of(s: Session, entity: str, entity_id: int) -> list[str]:
    """Кого стосується подія на цій сутності: автор, відповідальний, виконавець."""
    from ..models import Deal, Document, Site, Task as T

    out: list[str] = []
    obj = None
    if entity == "deal":
        obj = s.get(Deal, entity_id)
        if obj:
            out += [obj.owner, obj.created_by]
    elif entity == "document":
        obj = s.get(Document, entity_id)
        if obj:
            out += [obj.created_by, obj.uploaded_by]
    elif entity == "task":
        obj = s.get(T, entity_id)
        if obj:
            out += [obj.assignee, obj.created_by]
    elif entity == "site":
        obj = s.get(Site, entity_id)
        if obj:
            out.append(obj.created_by)
    return [x for x in out if x and x != "import" and x != "правило"]


# ── стрічка: AuditEntry → людська фраза ──────────────────────────────────────
# Аудит пише 25 типів дій. Тут вони отримують мову й посилання.
# Фрази БЕЗ РОДУ: у команді троє жінок і двоє чоловіків, а «Оксана залишив коментар»
# — це та дрібниця, через яку система читається як чужа. Іменникова форма працює
# однаково для всіх: «Оксана · коментар · Угода».
ACTION_PHRASES = {
    "auth.login": "вхід у систему",
    "counterparty.edit": "правка картки контрагента",
    "deal.create": "нова угода",
    "deal.stage": "зміна стадії",
    "deal.stage.auto": "стадія змінилась сама",
    "deal.next_action": "наступна дія призначена",
    "activity.create": "контакт записано",
    "document.create": "документ прикріплено",
    "document.upload": "файл завантажено",
    "document.generate": "документ згенеровано",
    "document.workflow": "стан документа змінено",
    "document.finalize": "документ фінальний",
    "document.new_version": "нова версія документа",
    "outreach.sent": "лист надіслано",
    "outreach.reply": "відповідь зафіксовано",
    "settings.update": "налаштування офісу змінено",
    "site.status": "статус обдзвону змінено",
    "site.criteria": "анкету громади заповнено",
    "task.create": "нове завдання",
    "task.status": "статус завдання змінено",
    "task.accept": "завдання взято в роботу",
    "task.decline": "відмова від завдання",
    "task.sync": "завдання оновлено за правилом",
    "task.from_comment": "завдання з рекомендації",
    "ai.extract": "AI-витяг",
    "ai.extract.fail": "AI-витяг не вдався",
    "ai.confirm": "AI-чернетку підтверджено",
    "comment.create": "коментар",
    "comment.resolve": "рекомендацію закрито",
    "approval.submit": "подання на затвердження",
    "approval.decide": "рішення по затвердженню",
    "approval.withdraw": "подання відкликано",
    "entity.delete": "запис видалено",
    "seed.realni": "реальні дані заведено в систему",
    "chat.import": "переписку перенесено в архів",
    "program.create": "новий напрям угод",
    "program.edit": "напрям змінено",
    "program.archive": "напрям в архіві",
    "stage.create": "нова стадія воронки",
    "stage.edit": "стадію змінено",
    "stage.gates": "гейти стадії змінено",
    "stage.delete": "стадію прибрано",
    "stage.reorder": "порядок стадій змінено",
}

# дії, які не варто показувати в стрічці — шум
# Події входу — службовий журнал безпеки, не робота команди. `auth.login_failed`
# тим паче: його «автор» і деталі задає той, хто вводить логін, тобто будь-хто
# ззовні міг би писати довільний текст просто в стрічку офісу.
FEED_SKIP = {"auth.login", "auth.login_failed", "auth.logout_all"}


@dataclass
class FeedItem:
    at: datetime
    actor: str
    phrase: str
    detail: str
    url: str
    entity_label: str
    tier: int
    action: str

    @property
    def is_decision(self) -> bool:
        return self.action.startswith("approval.")


# ── «що змінилось відколи ви були» ───────────────────────────────────────────
# Групи подій, які варті окремого рядка при поверненні. Дрібніші дії (правка
# поля, зміна налаштування) у підсумок не йдуть — інакше блок перетвориться на
# другу стрічку й перестане відповідати на питання «що мені з цим робити».
ZMINY_HRUPY = [
    ("zavdannya", "завдання", ("task.create", "task.status", "task.accept",
                               "task.decline", "task.from_comment")),
    ("ugody", "по угодах", ("deal.create", "deal.stage", "deal.stage.auto",
                            "activity.create")),
    ("dokumenty", "документи", ("document.create", "document.finalize",
                                "document.new_version", "outreach.sent",
                                "outreach.reply")),
    ("rishennya", "затвердження", ("approval.submit", "approval.decide",
                                   "approval.withdraw")),
    ("obhovorennya", "обговорення", ("comment.create", "comment.resolve")),
]


@dataclass
class Zmina:
    kod: str
    pidpys: str
    skilky: int
    url: str


@dataclass
class Zminy:
    vid: datetime | None
    punkty: list[Zmina] = dataclass_field(default_factory=list)
    vsoho: int = 0
    pershyi_vhid: bool = False

    @property
    def je(self) -> bool:
        return self.vsoho > 0


def zminy_vid(s: Session, user_name: str, vid: datetime | None) -> Zminy:
    """Що зробили ІНШІ відколи людина востаннє дивилась «мій день».

    Свої дії не рахуємо: людина щойно їх зробила, показувати їй це як новину —
    шум. Це головна відповідь на режим «2–3 год розірваними заходами»: при
    поверненні найдорожче — згадати, що вже бачив.
    """
    z = Zminy(vid=vid)
    if vid is None:
        z.pershyi_vhid = True
        return z
    dii = [a for _, _, aa in ZMINY_HRUPY for a in aa]
    rows = list(s.exec(select(AuditEntry.action).where(
        AuditEntry.at > vid, AuditEntry.actor != user_name,
        AuditEntry.action.in_(dii))))
    if not rows:
        return z
    lich: dict[str, int] = {}
    for kod, _, aa in ZMINY_HRUPY:
        n = sum(1 for a in rows if a in aa)
        if n:
            lich[kod] = n
    URL = {"zavdannya": "/zavdannya", "ugody": "/voronky", "dokumenty": "/dokumenty",
           "rishennya": "/zatverdzhennya", "obhovorennya": "/strichka"}
    z.punkty = [Zmina(kod, pidpys, lich[kod], URL[kod])
                for kod, pidpys, _ in ZMINY_HRUPY if kod in lich]
    z.vsoho = sum(lich.values())
    return z


def feed(s: Session, *, limit: int = 80, actor: str = "",
         entity: str = "", entity_id: int | None = None) -> list[FeedItem]:
    """Стрічка команди або однієї сутності."""
    stmt = select(AuditEntry).where(AuditEntry.action.not_in(FEED_SKIP))
    if actor:
        stmt = stmt.where(AuditEntry.actor == actor)
    if entity:
        stmt = stmt.where(AuditEntry.entity == entity)
        if entity_id:
            stmt = stmt.where(AuditEntry.entity_id == entity_id)
    rows = list(s.exec(stmt.order_by(AuditEntry.at.desc()).limit(limit)))
    return [
        FeedItem(at=r.at, actor=r.actor,
                 phrase=ACTION_PHRASES.get(r.action, r.action),
                 detail=r.detail or "", url=entity_url(r.entity, r.entity_id),
                 entity_label=entity_label(r.entity) if r.entity else "",
                 tier=r.tier, action=r.action)
        for r in rows
    ]


# ── черга затверджень ────────────────────────────────────────────────────────
def pending_approvals(s: Session) -> list[Approval]:
    return list(s.exec(select(Approval).where(Approval.status == "na_rozglyadi")
                       .order_by(Approval.submitted_at, Approval.id)))


def approval_for(s: Session, entity: str, entity_id: int) -> Approval | None:
    """Останнє подання по сутності — його стан показується на картці."""
    # id — другий ключ: дві подачі можуть мати однаковий submitted_at (годинник
    # Windows ~15 мс), і без нього «останнім» випадково ставало старе подання.
    return s.exec(select(Approval)
                  .where(Approval.entity == entity, Approval.entity_id == entity_id)
                  .order_by(Approval.submitted_at.desc(), Approval.id.desc())).first()


def comments_for(s: Session, entity: str, entity_id: int) -> list[Comment]:
    return list(s.exec(select(Comment)
                       .where(Comment.entity == entity, Comment.entity_id == entity_id)
                       .order_by(Comment.created_at)))


def tasks_for(s: Session, entity: str, entity_id: int) -> list[Task]:
    """Завдання, привʼязані до сутності (через відповідний FK)."""
    col = {"deal": Task.deal_id, "site": Task.site_id,
           "counterparty": Task.counterparty_id, "document": Task.document_id,
           "cluster": Task.cluster_id}.get(entity)
    if col is None:
        return []
    return list(s.exec(select(Task).where(col == entity_id)
                       .order_by(Task.status, Task.due_date)))

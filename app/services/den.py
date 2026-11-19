"""«Мій день» під роль (ТЗ §3, сценарій О8) і KPI офісу (Додаток Б §6).

Кожен із чотирьох має бачити СВОЄ, без пошуку. Джерела рядків різні — завдання,
прострочені дії по угодах, черга документів, контроль відповідей — але екран один.
"""
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy import func
from sqlmodel import Session, select

from ..templating import plural
from ..models import (
    CAP_ADMIN,
    CAP_APPROVE,
    CAP_DATA_SERVICE,
    CAP_EDIT_ANY_DOC,
    CAP_SEND,
    KIND_PREZENTATSIYA,
    Approval,
    Counterparty,
    DataSource,
    Deal,
    Document,
    Notification,
    Outreach,
    Program,
    Score,
    Site,
    Stage,
    Task,
    User,
    utcnow,
)


@dataclass
class Item:
    """Один рядок «мого дня»: що зробити, чому це тут і куди клікнути."""
    kind: str
    title: str
    why: str
    url: str
    urgent: bool = False
    extra: str = ""


@dataclass
class Day:
    urgent: list[Item] = field(default_factory=list)
    today: list[Item] = field(default_factory=list)
    soon: list[Item] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.urgent) + len(self.today) + len(self.soon)

    def add(self, item: Item, bucket: str = "today"):
        getattr(self, bucket).append(item)


def _stage_titles(s: Session) -> dict[int, str]:
    return {st.id: st.title for st in s.exec(select(Stage))}


def build_day(s: Session, user: User) -> Day:
    d = Day()
    stages = _stage_titles(s)
    today = date.today()

    # 0. Непрочитані сповіщення — найсвіжіший сигнал, тому перед завданнями.
    #    Рішення по затвердженню, згадка й доручення — терміново: на них реагують сьогодні.
    hostri = ("rishennya", "zavdannya", "zgadka", "zavdannya_vidmova")
    for n in s.exec(select(Notification)
                    .where(Notification.user == user.name,
                           Notification.read_at.is_(None))
                    .order_by(Notification.created_at.desc()).limit(10)):
        d.add(Item("notif", n.text[:110], n.kind_label, n.url or "/spovishchennya",
                   urgent=n.kind in hostri, extra=n.from_whom),
              "urgent" if n.kind in hostri else "today")

    # 0b. Черга затверджень — лише тому, хто вирішує, і ніколи власне подання
    if user.can(CAP_APPROVE):
        for a in s.exec(select(Approval).where(Approval.status == "na_rozglyadi")
                        .order_by(Approval.submitted_at, Approval.id)):
            if a.submitted_by == user.name:
                continue
            d.add(Item("approval", a.note[:90] or f"{a.entity} #{a.entity_id}",
                       f"на затвердження від {a.submitted_by}",
                       f"/zatverdzhennya#a{a.id}",
                       urgent=a.days_waiting >= 1, extra="затвердження"),
                  "urgent" if a.days_waiting >= 1 else "today")

    # 1. Завдання користувача
    for t in s.exec(select(Task).where(Task.assignee == user.name,
                                       Task.status.in_(["open", "in_progress"]))
                    .order_by(Task.due_date)):
        chyye = f"доручення від {t.created_by}" if t.from_colleague else "завдання"
        it = Item("task", t.title, f"{chyye} · {t.note[:60]}" if t.note else chyye,
                  f"/zavdannya#t{t.id}", urgent=t.is_overdue,
                  extra="від колеги" if t.from_colleague else "")
        if t.is_overdue:
            d.add(Item("task", t.title,
                       f"прострочено з {t.due_date:%d.%m}", f"/zavdannya#t{t.id}", True),
                  "urgent")
        elif t.is_today:
            d.add(it, "today")
        elif t.due_date and (t.due_date - today).days <= 7:
            d.add(Item("task", t.title, f"до {t.due_date:%d.%m}", f"/zavdannya#t{t.id}"),
                  "soon")
        else:
            d.add(it, "soon")

    # 2. Угоди, де користувач — відповідальний
    mine = list(s.exec(select(Deal).where(Deal.owner == user.name,
                                          Deal.status == "active")))
    for deal in mine:
        st = stages.get(deal.stage_id, "")
        if deal.action_overdue:
            d.add(Item("deal", deal.title,
                       f"наступна дія прострочена: {deal.next_action_note or '—'}",
                       f"/voronky/{deal.id}", True, st), "urgent")
        elif deal.next_action_date == today:
            d.add(Item("deal", deal.title,
                       f"сьогодні: {deal.next_action_note or '—'}",
                       f"/voronky/{deal.id}", False, st), "today")
        elif not deal.next_action_date:
            d.add(Item("deal", deal.title, "наступну дію не призначено",
                       f"/voronky/{deal.id}", False, st), "soon")
        elif deal.is_stale:
            d.add(Item("deal", deal.title, f"без активності {deal.days_on_stage} дн.",
                       f"/voronky/{deal.id}", False, st), "soon")

    # 3. Роль Галини: черга на відправлення + прострочені відповіді
    if user.can(CAP_SEND):
        for doc in s.exec(select(Document)
                          .where(Document.workflow_status == "do_vidpravlennya")):
            d.add(Item("doc", doc.title, "чекає відправлення",
                       f"/dokumenty/{doc.id}", False, f"{doc.kind} v{doc.version}"),
                  "today")
        for o in s.exec(select(Outreach).where(Outreach.reply_status == "chekaiemo")):
            if o.is_overdue:
                d.add(Item("outreach", o.subject or "лист",
                           f"відповіді немає з {o.reply_due_date:%d.%m}",
                           "/lysty?tab=prostrocheni", True, o.recipient), "urgent")

    # 4. Роль Богдана: документи на правці й узгодженні
    if user.can(CAP_EDIT_ANY_DOC):
        for doc in s.exec(select(Document)
                          .where(Document.workflow_status.in_(["na_pravtsi",
                                                               "chernetka"]))):
            if doc.created_by != user.name or doc.workflow_status == "na_pravtsi":
                d.add(Item("doc", doc.title, "документ чекає правки/узгодження",
                           f"/dokumenty/{doc.id}", False,
                           f"{doc.kind} v{doc.version}"), "today")

    # 5. Роль Марка: попередні презентації, стан баз, картки на перевірку.
    #    Презентація — його продукт на старті проєкту, тому черга «проєкт заведено,
    #    презентації ще немає» стоїть ПЕРЕД технічними нагадуваннями про бази.
    if user.can(CAP_DATA_SERVICE):
        mayut_prezu = set(s.exec(
            select(Document.deal_id).where(Document.kind == KIND_PREZENTATSIYA,
                                           Document.deal_id.is_not(None))))
        for deal in s.exec(select(Deal).where(Deal.status == "active")
                           .order_by(Deal.created_at.desc()).limit(50)):
            if deal.id in mayut_prezu:
                continue
            dniv = (utcnow() - deal.created_at).days
            d.add(Item("preza", deal.title,
                       f"презентації ще немає · проєкт {dniv} дн." if dniv
                       else "презентації ще немає · заведено сьогодні",
                       f"/voronky/{deal.id}", urgent=dniv >= 7,
                       extra=stages.get(deal.stage_id, "")),
                  "urgent" if dniv >= 7 else "today")

        need = s.exec(select(func.count()).select_from(Counterparty)
                      .where(Counterparty.data_status == "needs_check")).one()
        if need:
            word = plural(need, "картка потребує", "картки потребують",
                          "карток потребують")
            d.add(Item("data", f"{need} {word} перевірки",
                       "якість довідника", "/dovidnyk?data_status=needs_check"), "soon")
        stale_src = [x for x in s.exec(select(DataSource)) if x.is_stale]
        for src in stale_src:
            d.add(Item("data", f"джерело «{src.code}» застаріло",
                       "давно не імпортувалось", "/bazy"), "soon")

    # 6. Роль Оксани: кого дзвонити — точки з високим балом і статусом «Новий»
    if not user.can(CAP_ADMIN) and not user.can(CAP_SEND) \
            and not user.can(CAP_EDIT_ANY_DOC):
        rows = list(s.exec(select(Site).where(Site.site_type == "biogaz_object",
                                              Site.member_status == "Новий",
                                              Site.cluster_id.is_not(None)).limit(400)))
        scores = {sc.site_id: sc for sc in s.exec(
            select(Score).where(Score.site_id.in_([r.id for r in rows] or [-1])))}
        rows.sort(key=lambda r: scores[r.id].value if r.id in scores else -1, reverse=True)
        for r in rows[:5]:
            sc = scores.get(r.id)
            d.add(Item("call", r.name, f"бал {sc.value if sc else '—'} · ще не дзвонили",
                       f"/obyekty/{r.id}", False, r.oblast), "today")

    return d


# ── KPI ──────────────────────────────────────────────────────────────────────

def kpi(s: Session, days: int = 30) -> dict:
    """KPI офісу. Два різні роди чисел — і різниця важлива:

    * ЗНІМОК (зараз): угоди в роботі, пайплайн, гігієна, відкриті завдання,
      прострочені відповіді — від періоду не залежать;
    * ПОТІК (за `days` днів): виграно/втрачено, надіслано/отримано відповідей,
      створено документів. Раніше `since` лише виводився на екран, а рахувалось
      усе за весь час — тож перемикач періоду нічого не міняв.
    """
    days = max(1, min(int(days or 30), 3650))
    since = date.today() - timedelta(days=days)
    vid = datetime.combine(since, datetime.min.time())
    deals = list(s.exec(select(Deal)))
    active = [d for d in deals if d.status == "active"]
    stages = {st.id: st for st in s.exec(select(Stage))}
    progs = {p.id: p for p in s.exec(select(Program))}

    # Ключ — id стадії, не назва: «Контакт» є в кількох напрямах, і за назвою
    # угоди різних воронок зливались в один стовпчик.
    by_stage: dict[int, dict] = {}
    for d in active:
        st = stages.get(d.stage_id)
        if st and st.id is not None:
            if st.id not in by_stage:
                prog = progs.get(d.program_id)
                by_stage[st.id] = {"title": st.title,
                                   "prog": prog.title if prog else "", "n": 0}
            by_stage[st.id]["n"] += 1

    outreach = list(s.exec(select(Outreach)))
    sent = [o for o in outreach if o.sent_at and o.sent_at >= vid]
    replied = [o for o in sent if o.reply_status == "otrymano"]
    overdue = [o for o in outreach if o.sent_at and o.is_overdue]

    tasks = list(s.exec(select(Task)))
    open_tasks = [t for t in tasks if t.is_open]

    docs = [d for d in s.exec(select(Document)) if d.created_at >= vid]
    # Результат угоди датуємо входом на термінальну стадію
    zakryti = [d for d in deals if d.stage_entered_at >= vid]

    return {
        "deals_total": len(deals),
        "deals_active": len(active),
        "deals_won": len([d for d in zakryti if d.status == "won"]),
        "deals_lost": len([d for d in zakryti if d.status == "lost"]),
        "by_stage": by_stage,
        "pipeline_value": round(sum((d.value or 0) * d.probability / 100
                                    for d in active)),
        "no_next_action": len([d for d in active if not d.next_action_date]),
        "overdue_actions": len([d for d in active if d.action_overdue]),
        "stale": len([d for d in active if d.is_stale]),
        "outreach_sent": len(sent),
        "outreach_replied": len(replied),
        "outreach_overdue": len(overdue),
        "reply_rate": round(100 * len(replied) / len(sent)) if sent else None,
        "tasks_open": len(open_tasks),
        "tasks_overdue": len([t for t in open_tasks if t.is_overdue]),
        "docs_total": len(docs),
        "docs_final": len([d for d in docs if d.is_final]),
        "since": since,
        "days": days,
    }

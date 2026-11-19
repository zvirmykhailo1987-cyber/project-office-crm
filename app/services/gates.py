"""Гейти воронки (ТЗ, Додаток Б §2).

Правило: гейт належить стадії, яку ПОКИДАЄШ. Перехід уперед можливий, лише якщо
для угоди є документ потрібного виду — фінальний і підписаний. Інакше перехід
блокується з переліком того, чого бракує.

Причина саме такої суворості — логіка `funnel.md`: незворотні зобов'язання
(гарантія громади) відкладені якнайпізніше і де-ризикнуті ДО прохання підписати.
Якщо гейт можна обійти кліком, воронка перестає відображати реальність.
"""
from dataclasses import dataclass

from sqlmodel import Session, select

from ..models import Deal, Document, Stage


@dataclass
class GateResult:
    allowed: bool
    missing: list[str]            # коди документів, яких бракує
    criteria: str = ""            # текст умови переходу — показуємо людині завжди
    is_backward: bool = False

    @property
    def reason(self) -> str:
        if self.allowed:
            return ""
        return "Не вистачає підписаних документів: " + ", ".join(self.missing)


def satisfied_doc_kinds(s: Session, deal_id: int) -> set[str]:
    """Види документів угоди, які реально закривають гейт: фінальні та підписані."""
    docs = s.exec(select(Document).where(Document.deal_id == deal_id))
    return {d.kind for d in docs if d.kind and d.is_final and d.signed_at}


def check(s: Session, deal: Deal, target: Stage) -> GateResult:
    """Чи можна перевести угоду на стадію `target`."""
    current = s.get(Stage, deal.stage_id)
    if current is None or target.id == current.id:
        return GateResult(True, [], target.gate_criteria)

    # Терміналь «відмова/відкладено» стоїть у кінці порядку: повернення з неї на робочу
    # стадію НЕ є кроком назад — інакше «Контакт → Відмова → Перемовини» обходить гейти.
    z_terminalu = current.is_terminal and not current.is_won and not target.is_terminal
    # Назад і у відкладено/відмову — завжди можна (off-ramp, funnel.md п.3)
    if not z_terminalu and (target.order <= current.order or (target.is_terminal and not target.is_won)):
        return GateResult(True, [], target.gate_criteria, is_backward=True)
    vid = -1 if z_terminalu else current.order        # з терміналу — перевіряємо всі стадії до цілі

    have = satisfied_doc_kinds(s, deal.id)
    missing: list[str] = []
    # перевіряємо ВСІ пройдені стадії, а не лише поточну: інакше гейт обходиться
    # стрибком через стадію
    for st in s.exec(select(Stage).where(Stage.pipeline_id == current.pipeline_id)
                     .order_by(Stage.order)):
        if st.order < vid or st.order >= target.order or st.is_terminal:
            continue
        missing += [k for k in st.required_list if k not in have]
    # Виграшна термінальна стадія має ВЛАСНІ обовʼязкові документи («Угода» —
    # договір, «8. Два підписи» — Контракт #2). Цикл вище їх не бачив: він
    # перевіряє лише стадії, які покидаєш, тож «виграш» ставився без договору.
    if target.is_won:
        missing += [k for k in target.required_list if k not in have and k not in missing]

    return GateResult(not missing, missing, target.gate_criteria)


def avtoperekhid(s: Session, deal: Deal, podiya: str, actor: str) -> Stage | None:
    """Автоперехід угоди за правилом поточної стадії (`Stage.avto_umova`).

    Одне місце для обох умов: «aktyvnist» (записано контакт із результатом) і
    «dokument» (щойно закрито потрібний документ). Рух іде ЛИШЕ через гейт —
    автоматика не має обходити те, що зупинило б людину.
    Повертає нову стадію або None, якщо угода лишилась на місці.
    """
    from .. import audit
    from ..models import utcnow

    cur = s.get(Stage, deal.stage_id)
    if not cur or cur.avto_umova != podiya or not cur.avto_do_id:
        return None
    nxt = s.get(Stage, cur.avto_do_id)
    # Ціль мусить лишатись у тій самій воронці: стадію могли перенести або
    # видалити, і тоді угода поїхала б у чужий напрям.
    if not nxt or nxt.pipeline_id != deal.pipeline_id or nxt.id == cur.id:
        return None
    if podiya == "dokument":
        # «Потрібний документ» — це обовʼязкові документи стадії; без вимог
        # нема чого чекати, і рух за будь-яким файлом був би здогадкою.
        if not cur.required_list:
            return None
        have = satisfied_doc_kinds(s, deal.id)
        if any(k not in have for k in cur.required_list):
            return None
    if not check(s, deal, nxt).allowed:
        return None
    deal.stage_id = nxt.id
    deal.stage_entered_at = utcnow()
    deal.probability = nxt.default_probability
    deal.status = nxt.status_ugody
    deal.updated_at = utcnow()
    s.add(deal)
    audit.log(s, actor, "deal.stage.auto", entity="deal", entity_id=deal.id,
              tier=audit.TIER_CHANGE, detail=f"авто: {cur.title} → {nxt.title}")
    return nxt

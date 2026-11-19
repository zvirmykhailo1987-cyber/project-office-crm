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

    # Назад і у відкладено/відмову — завжди можна (off-ramp, funnel.md п.3)
    if target.order <= current.order or (target.is_terminal and not target.is_won):
        return GateResult(True, [], target.gate_criteria, is_backward=True)

    have = satisfied_doc_kinds(s, deal.id)
    missing: list[str] = []
    # перевіряємо ВСІ пройдені стадії, а не лише поточну: інакше гейт обходиться
    # стрибком через стадію
    for st in s.exec(select(Stage).where(Stage.pipeline_id == current.pipeline_id)
                     .order_by(Stage.order)):
        if st.order < current.order or st.order >= target.order or st.is_terminal:
            continue
        missing += [k for k in st.required_list if k not in have]

    return GateResult(not missing, missing, target.gate_criteria)

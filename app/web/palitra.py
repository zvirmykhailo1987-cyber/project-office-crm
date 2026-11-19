"""Командна палітра ⌘K — один пошук по всій системі.

ЧОМУ ЦЕ ГОЛОВНИЙ ПРИЙОМ САМЕ ТУТ. Система використовується 2–3 год на день
розірваними заходами по 15–20 хвилин. У такому режимі найдорожче — не швидкість
роботи всередині екрана, а шлях ДО потрібного екрана: згадати розділ, знайти
фільтр, проскролити список. Палітра прибирає цей шлях цілком: одна клавіша,
кілька літер, Enter.

Шукає в п'яти просторах одразу:
  дії      — те, що можна зробити прямо звідси (нове завдання, перенести чат)
  екрани   — розділи системи
  угоди    — за назвою
  завдання — за назвою, лише відкриті
  контрагенти — через наявний FTS5 (`app/fts.py:86 search_ids`), 46 145 записів

Порядок навмисний: дії й екрани йдуть першими навіть при слабшому збігу, бо
вони — вхід у роботу, а контрагент найчастіше потрібен уже всередині сценарію.
"""
from fastapi import APIRouter, Depends, Request
from sqlmodel import Session, select

from ..db import get_session
from ..fts import search_ids
from ..models import Counterparty, Deal, Task, User
from .deps import current_user, render

router = APIRouter()

# Екрани й дії описані ДАНИМИ, а не розкидані по шаблону: так вони автоматично
# потрапляють і в палітру, і в майбутню довідку, і їх видно одним списком.
EKRANY = [
    ("Мій день", "/", "den", "огляд, що горить сьогодні"),
    ("Чат офісу", "/chat", "chat", "розмова команди, файли"),
    ("Завдання", "/zavdannya", "zavdannya", "свої й чужі, терміни"),
    ("Стрічка", "/strichka", "strichka", "що робить команда"),
    ("Воронки", "/voronky", "voronka", "угоди за стадіями"),
    ("Обʼєкти", "/obyekty", "obyekt", "біогаз, громади, парки"),
    ("Хаби", "/haby", "hab", "кластери ≥2 МВт"),
    ("Карта біоенергетики", "/karta", "karta", "21 тис. точок, фільтри"),
    ("Карта елеваторів", "/karta-elevatory", "karta", "громади-кандидати"),
    ("Документи", "/dokumenty", "dokument", "версії, затвердження"),
    ("Листи", "/lysty", "lyst", "реєстр вихідних, відповіді"),
    ("Затвердження", "/zatverdzhennya", "zatverdzhennya", "черга на рішення"),
    ("Довідник", "/dovidnyk", "dovidnyk", "контрагенти й особи"),
    ("Бази даних", "/bazy", "bazy", "імпорти, конфлікти"),
    ("AI-витяг", "/ai", "ai", "рахунки, анкети, контакти"),
    ("KPI", "/kpi", "kpi", "показники офісу"),
    ("Напрями угод", "/napryamy", "nalashtuvannya", "воронки, стадії, гейти"),
    ("Дизайн-система", "/dyzayn", "nalashtuvannya", "усі елементи в усіх станах"),
]

DIYI = [
    ("Нове завдання", "/zavdannya#nove", "plus", "поставити собі або колезі"),
    ("Новий проєкт", "/voronky/nova", "plus", "завести угоду у воронці"),
    ("Новий напрям угод", "/napryamy#novyi", "plus", "своя воронка зі стадіями"),
    ("Перенести переписку", "/chat", "chat", "експорт WhatsApp в архів"),
    ("Завантажити файл на витяг", "/ai", "ai", "рахунок, анкета, список"),
]


def _zbih(zapyt: str, tekst: str) -> int:
    """Нечіткий збіг: 0 — немає, більше — краще.

    Свідомо просте порівняння, а не Левенштейн: у палітрі важлива передбачуваність.
    Людина набирає початок слова й очікує побачити його зверху — саме це й рахуємо.
    """
    z, t = zapyt.lower().strip(), tekst.lower()
    if not z:
        return 0
    if t.startswith(z):
        return 100
    if z in t:
        return 60
    # усі літери запиту в порядку (типовий «нечіткий» пошук) — найслабший збіг
    i = 0
    for ch in t:
        if i < len(z) and ch == z[i]:
            i += 1
    return 25 if i == len(z) else 0


@router.get("/api/palitra")
def palitra(request: Request, q: str = "", user: User = Depends(current_user),
            s: Session = Depends(get_session)):
    """Результати для ⌘K. Порожній запит показує дії й екрани — не порожнечу."""
    zapyt = q.strip()
    hrupy: list[tuple[str, list[dict]]] = []

    def dodaty(nazva: str, elementy: list[dict]):
        if elementy:
            hrupy.append((nazva, elementy))

    def vidfiltruy(dzherelo, mezha: int):
        if not zapyt:
            return [{"nz": n, "url": u, "ik": i, "pid": p}
                    for n, u, i, p in dzherelo][:mezha]
        z = [(_zbih(zapyt, n) + _zbih(zapyt, p) // 3, n, u, i, p)
             for n, u, i, p in dzherelo]
        z = sorted([x for x in z if x[0] > 0], key=lambda x: -x[0])
        return [{"nz": n, "url": u, "ik": i, "pid": p} for _, n, u, i, p in z[:mezha]]

    dodaty("Дії", vidfiltruy(DIYI, 4))
    dodaty("Екрани", vidfiltruy(EKRANY, 6 if zapyt else 8))

    if zapyt:
        ugody = [d for d in s.exec(select(Deal).where(Deal.status == "active"))
                 if _zbih(zapyt, d.title)]
        ugody.sort(key=lambda d: -_zbih(zapyt, d.title))
        dodaty("Угоди", [{"nz": d.title, "url": f"/voronky/{d.id}", "ik": "voronka",
                          "pid": d.owner or ""} for d in ugody[:5]])

        zavd = [t for t in s.exec(select(Task).where(
            Task.status.in_(["open", "in_progress"]))) if _zbih(zapyt, t.title)]
        zavd.sort(key=lambda t: -_zbih(zapyt, t.title))
        dodaty("Завдання", [{"nz": t.title, "url": f"/zavdannya#t{t.id}",
                             "ik": "zavdannya", "pid": t.assignee} for t in zavd[:5]])

        # Контрагенти — через наявний FTS5, не через LIKE по 46 тисячах рядків
        ids = search_ids(s, zapyt, limit=6)
        if ids:
            cps = {c.id: c for c in s.exec(select(Counterparty)
                                           .where(Counterparty.id.in_(ids)))}
            dodaty("Контрагенти", [
                {"nz": cps[i].name, "url": f"/dovidnyk/{i}", "ik": "dovidnyk",
                 "pid": " · ".join(x for x in (cps[i].oblast, cps[i].edrpou) if x)}
                for i in ids if i in cps])

    return render(request, "_palitra_rez.html", user=user, hrupy=hrupy, q=zapyt)

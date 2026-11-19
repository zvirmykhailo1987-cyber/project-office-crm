"""Напрями й воронки як ДАНІ, не код (ТЗ §5, Додаток А §1.1, §1.5).

Дві стартові воронки різної природи: біогаз — 10 транзакційних стадій з мʼякими
переходами; елеватор — 9 стадій із жорсткими гейтами. Третій і подальші напрями
додаються З ІНТЕРФЕЙСУ, без правки коду — і цей файл тепер це справді забезпечує.

ЩО ЗМІНИЛОСЬ 06.08.2026 і чому. Раніше поведінка воронки в трьох місцях
впізнавалась за РЯДКОВИМ КОДОМ стадії:
  · `voronky.py` ставив угоді статус «відкладено» лише якщо код був `vidkladeno`;
  · `templating.py` фарбував стадію червоним, якщо в коді траплялось `vidmov`;
  · автоперехід був зашитий як `dzvonyty → kontakt`.
Тобто нова воронка отримувала половину логіки лише за збігу назв кодів. Тепер це
поля: `rezultat`, `avto_umova`, `avto_do_id`. Код більше не читає коди.
"""

from sqlmodel import Field

from .base import BaseRow

# Чим закінчується угода на термінальній стадії. Порожньо = стадія робоча.
# Це замінює впізнавання за кодом: раніше «відкладено» вгадувалось із рядка.
REZULTATY = {
    "": "Робоча стадія",
    "vygrash": "Виграш",
    "vidkladeno": "Відкладено",
    "vtrata": "Втрата",
}
# Результат → статус угоди (`Deal.status`). Один словник замість трьох if-ів.
REZULTAT_STATUS = {"vygrash": "won", "vidkladeno": "paused", "vtrata": "lost"}

# За якою подією угода сама переходить далі. Порожньо = не переходить.
AVTO_UMOVY = {
    "": "не переходить сама",
    "aktyvnist": "щойно записано контакт із результатом",
    "dokument": "щойно прикріплено потрібний документ",
}

# Моделі балу, які можна призначити напряму. Порожньо — балу немає, і це
# нормально: підряд чи грант оцінювати формулою біогаз-точки безглуздо.
MODELI_BALU = {
    "": "балу немає",
    "fat_score": "FAT_SCORE — біогаз-обʼєкт",
    "hromada_screening": "Скринінг громади C1–C9",
    "elevator_ranking": "Рейтинг кандидата під елеватор",
}


class Program(BaseRow, table=True):
    """Напрям угод. `code` унікальний і незмінний після створення — на нього
    посилаються шаблони листів (`DocTemplate.program_code`) та імпортери."""
    code: str = Field(index=True, unique=True)
    title: str = ""
    opys: str = ""                 # чим цей напрям відрізняється — для людини
    color: str = "#0f6fc4"
    active: bool = True            # архівний напрям зникає з вибору для НОВИХ угод
    order: int = 0
    model_balu: str = ""           # ключ із MODELI_BALU


class Pipeline(BaseRow, table=True):
    program_id: int = Field(foreign_key="program.id", index=True)
    code: str = Field(index=True)
    title: str = ""
    is_default: bool = True


class Stage(BaseRow, table=True):
    pipeline_id: int = Field(foreign_key="pipeline.id", index=True)
    order: int = Field(default=0, index=True)
    code: str = Field(default="", index=True)
    title: str = ""
    color: str = "#64748b"
    goal: str = ""                 # ціль стадії (для елеватора — з funnel.md)
    gate_criteria: str = ""        # умова переходу далі, словами
    required_doc_kinds: str = ""   # CSV кодів документів, без яких перехід блокується
    key_docs: str = ""             # CSV підказки «які листи тут доречні»
    default_probability: int = 0

    # Чим закінчується угода тут. `is_terminal`/`is_won` лишились, бо їх читають
    # гейти й тести, але тепер вони ПОХІДНІ від `rezultat` — одне джерело правди.
    rezultat: str = ""
    is_terminal: bool = False
    is_won: bool = False

    # Автоперехід як дані, а не як зашите правило `dzvonyty → kontakt`.
    avto_umova: str = ""
    avto_do_id: int | None = Field(default=None, foreign_key="stage.id")

    @property
    def required_list(self) -> list[str]:
        return [x.strip() for x in (self.required_doc_kinds or "").split(",") if x.strip()]

    @property
    def key_docs_list(self) -> list[str]:
        return [x.strip() for x in (self.key_docs or "").split(",") if x.strip()]

    @property
    def rezultat_label(self) -> str:
        return REZULTATY.get(self.rezultat, self.rezultat)

    @property
    def status_ugody(self) -> str:
        """Який статус отримає угода, що дійшла сюди."""
        return REZULTAT_STATUS.get(self.rezultat, "active")

    def synhronizuvaty_prapory(self) -> None:
        """`is_terminal`/`is_won` виводяться з `rezultat`, а не задаються окремо.

        Інакше можливий стан «термінальна, але без результату» або «виграш, але
        не термінальна» — і воронка починає поводитись по-різному в різних
        місцях коду. Викликається перед кожним записом стадії.
        """
        self.is_terminal = bool(self.rezultat)
        self.is_won = self.rezultat == "vygrash"

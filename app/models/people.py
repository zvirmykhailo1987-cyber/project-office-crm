"""Користувачі системи, ролі й аудит дій (ТЗ §2, §9).

Ролі описані за фактичним розподілом роботи в офісі, а не за проєктною
структурою з team_structure.md.
"""
from datetime import datetime

from sqlmodel import Field, SQLModel

from .base import BaseRow, utcnow

# роль → людська назва. Це підпис під іменем на кожному екрані, тому три вимоги:
# 1) називає СФЕРУ роботи, а не місце в ієрархії — жодних «затверджує» чи «керує»
#    (владні дії живуть у правах доступу, а не в підписі під іменем);
# 2) однакова схема «А й Б» і рівна довжина (22–23 знаки) — щоб у списку жодна
#    роль не виглядала вагомішою за інші;
# 3) називає фах, а не лише функцію в офісі.
ROLES = {
    "rozrobnyk": "Розробка ПЗ й аналітика",
    "perehovorschyk_dok": "Інвестори й переговори",
    "perehovory_kontakt": "Громади й підприємства",
    "zapyty_lysty": "Запити й кореспонденція",
}

# роль → чим фактично займається (для інтерфейсу).
# Уточнення Марка 05.08.2026: він не шукає інвесторів і не веде переговорів —
# тому нові проєкти приносить Богдан. Це не деталь опису: від цього залежить,
# хто власник угоди й чия черга роботи в «моєму дні».
#
# Тон (уточнення 06.08.2026): це перше, що людина бачить на вході, тому підпис має
# називати РОБОТУ, а не владу над іншими. «Ставить задачі» й «затверджує роботу або
# каже, що поміняти» прибрано — на екрані входу це читалось як попередження, хто тут
# начальник. Хто що може — живе в правах доступу нижче, і там воно доречне.
# Три складові в кожному рядку й рівна довжина — щоб жоден опис не важив більше.
ROLE_HINTS = {
    "rozrobnyk": "аналітика й пошук даних, попередні презентації та стратегії, "
                 "розробка й бази даних",
    "perehovorschyk_dok": "пошук інвесторів і переговори, напрямки нових проєктів, "
                          "погодження матеріалів",
    "perehovory_kontakt": "переговори з громадами, перший контакт із підприємствами, "
                          "звʼязки в регіонах",
    "zapyty_lysty": "підготовка запитів і листів, листування з громадами, "
                    "супровід договорів",
}

# Дозволи. Усі бачать усе (ТЗ §2) — розмежування за ДІЯМИ.
CAP_ADMIN = "admin"              # користувачі, ключі, налаштування
CAP_DATA_SERVICE = "data"        # імпорти, дедуп, перерахунки, ваги скорингу
CAP_DELETE = "delete"            # видалення записів
CAP_EDIT_ANY_DOC = "doc_any"     # правити чужі документи (створює нову версію)
CAP_SEND = "send"                # позначати «надіслано», вести реєстр запитів
CAP_CRM = "crm"                  # контрагенти, активності, угоди — є у всіх
# Затвердження роботи. СВІДОМО не входить у набір адміністратора: за процесом офісу
# затверджує Богдан, і автор не має змоги затвердити сам себе (Ф8).
CAP_APPROVE = "approve"

ROLE_CAPS: dict[str, set[str]] = {
    "rozrobnyk": {CAP_ADMIN, CAP_DATA_SERVICE, CAP_DELETE, CAP_EDIT_ANY_DOC, CAP_SEND, CAP_CRM},
    "perehovorschyk_dok": {CAP_EDIT_ANY_DOC, CAP_APPROVE, CAP_CRM},
    "perehovory_kontakt": {CAP_CRM},
    "zapyty_lysty": {CAP_SEND, CAP_CRM},
}


class User(BaseRow, table=True):
    name: str = Field(index=True)
    login: str = Field(default="", index=True)   # латиницею, для входу
    password_hash: str = ""
    email: str = ""
    role: str = Field(default="perehovory_kontakt", index=True)
    active: bool = True
    last_login_at: datetime | None = None
    # Коли людина востаннє дивилась «мій день». Окремо від last_login_at: сесія
    # тримається тижнями, а система використовується 2–3 год на день розірваними
    # заходами — тобто повернень набагато більше, ніж входів, і саме між ними
    # треба показати, що змінилось.
    bachyv_do: datetime | None = None

    @property
    def caps(self) -> set[str]:
        return ROLE_CAPS.get(self.role, set())

    def can(self, cap: str) -> bool:
        return cap in self.caps

    @property
    def role_label(self) -> str:
        return ROLES.get(self.role, self.role)


class AuditEntry(SQLModel, table=True):
    """Журнал дій. Tier за ТЗ §9: 0 читання · 1 зміна · 2 масова · 3 незворотна."""
    id: int | None = Field(default=None, primary_key=True)
    at: datetime = Field(default_factory=utcnow, index=True)
    actor: str = Field(default="", index=True)
    action: str = Field(default="", index=True)
    entity: str = ""
    entity_id: int | None = None
    tier: int = Field(default=1, index=True)
    confirmed: bool = False
    detail: str = ""

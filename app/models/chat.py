"""Внутрішній чат офісу — заміна робочій групі у WhatsApp (Ф10).

Навіщо свій чат, коли є WhatsApp: аудит переписки 05.08.2026 показав, що домовленості
губляться між привітаннями й фінансовими документами, а база контактів на 37 МБ ходить
zip-архівом по чотирьох телефонах. Офіційно підключити наявні групи WhatsApp неможливо
(Business API бачить лише розмови зі своїм бізнес-номером), а неофіційний міст — це
ризик бану робочого номера. Тому чат живе тут.

Розмежування з рештою моделей — те саме, що вже діє між `Comment` і `Activity`:
  `Activity`     — слід контакту з КОНТРАГЕНТОМ, рухає воронку;
  `Comment`      — обговорення ПРИВ'ЯЗАНЕ до картки (угоди, документа, витягу);
  `ChatMessage`  — розмова команди, яка НЕ прив'язана ні до чого конкретного.
Через це чат не пише в `AuditEntry` кожне повідомлення: інакше «Стрічка» перестала б
показувати роботу й показувала б балаканину.

Ідентичність людини — рядок `user.name`, як усюди в проєкті (не FK).
"""
from datetime import datetime

from sqlmodel import Field

from .base import BaseRow, utcnow

# Код єдиної кімнати, що сіється при першому запуску.
KIMNATA_OFIS = "ofis"


class ChatRoom(BaseRow, table=True):
    """Кімната: жива розмова офісу або архів перенесеної переписки.

    `uchasnyky` — CSV імен тих, кому кімната видима. ПОРОЖНЬО = видима всім.
    Це не «права доступу» взагалі (в ОФІС усі бачать усе за ТЗ §2), а вузьке
    правило саме для архівів особистих чатів: у переписці двох людей є речі, які
    стосуються лише їх, і механічно висипати їх усій команді було б зловживанням
    тим, що технічно це можливо.
    """
    code: str = Field(default=KIMNATA_OFIS, index=True)
    title: str = "Загальна група офісу"
    is_default: bool = True
    archived: bool = False               # архів — тільки читання, без полінгу
    uchasnyky: str = ""                  # CSV імен; порожньо = усі
    dzherelo: str = ""                   # звідки перенесено, напр. "whatsapp"
    wa_chat: str = ""                    # оригінальна назва чату в джерелі
    poryadok: int = 0

    @property
    def uchasnyky_list(self) -> list[str]:
        return [x.strip() for x in (self.uchasnyky or "").split(",") if x.strip()]

    def vydyma(self, imya: str) -> bool:
        u = self.uchasnyky_list
        return not u or imya in u


class ChatMessage(BaseRow, table=True):
    """Повідомлення.

    Видалення М'ЯКЕ. У WhatsApp «Ви видалили це повідомлення» рве нитку відповідей і
    ховає контекст рішення — в аудиті 11 таких дірок, які вже не відновити. Тут текст
    ховається, а місце в стрічці, автор і час лишаються.
    """
    room_id: int = Field(foreign_key="chatroom.id", index=True)
    author: str = Field(default="", index=True)
    text: str = ""
    reply_to_id: int | None = Field(default=None, foreign_key="chatmessage.id",
                                       index=True)
    mentions: str = ""                       # CSV логінів, кого згадано

    # Необов'язкова прив'язка до картки — поліморфна, як у `Comment` і `AuditEntry`.
    entity: str = Field(default="", index=True)
    entity_id: int | None = Field(default=None, index=True)

    edited_at: datetime | None = None
    deleted_at: datetime | None = Field(default=None, index=True)
    deleted_by: str = ""

    # Для перенесеної переписки: експорт БЕЗ медіа зберігає лише згадку про файл.
    # Фіксуємо це прямо, а не мовчимо — інакше в архіві виглядало б, ніби файлу
    # взагалі не було, і людина шукала б його там, де його немає.
    media_vtracheno: bool = False
    vidsutni_fayly: str = ""             # CSV імен, згаданих в експорті, без байтів

    @property
    def vidsutni_list(self) -> list[str]:
        return [x.strip() for x in (self.vidsutni_fayly or "").split(",") if x.strip()]

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    @property
    def is_edited(self) -> bool:
        return self.edited_at is not None

    @property
    def mention_list(self) -> list[str]:
        return [m for m in (self.mentions or "").split(",") if m]


class ChatAttachment(BaseRow, table=True):
    """Файл при повідомленні.

    `filename` — ім'я на диску (латиниця, згенероване нами), `original_name` — як назвав
    людина. Два поля, а не одне, бо кирилиця в іменах ламає частину інструментів, але
    показувати треба саме те, що людина впізнає.
    """
    message_id: int = Field(foreign_key="chatmessage.id", index=True)
    filename: str = ""
    original_name: str = ""
    size: int = 0
    mime: str = ""
    sha256: str = Field(default="", index=True)
    # Заповнюється, якщо файл перенесли в реєстр документів — щоб не переносити двічі.
    promoted_document_id: int | None = Field(default=None,
                                                foreign_key="document.id")

    @property
    def size_label(self) -> str:
        if self.size >= 1024 * 1024:
            return f"{self.size / (1024 * 1024):.1f} МБ"
        if self.size >= 1024:
            return f"{self.size // 1024} КБ"
        return f"{self.size} Б"

    @property
    def ext(self) -> str:
        _, _, e = self.original_name.rpartition(".")
        return e.lower() if e else ""


class ChatRead(BaseRow, table=True):
    """Докуди людина дочитала. Один рядок на пару (кімната, людина)."""
    room_id: int = Field(foreign_key="chatroom.id", index=True)
    user: str = Field(default="", index=True)
    last_read_message_id: int = 0
    read_at: datetime = Field(default_factory=utcnow)

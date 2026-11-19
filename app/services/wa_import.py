"""Перенесення переписки WhatsApp в архівні кімнати ОФІС.

Ідемпотентність — на детермінованому ключі рядка (`WaMsg.klyuch` = sha1 від
часу+автора+тексту). Повторний імпорт того самого експорту дає «нових 0»; імпорт
свіжішого експорту тієї самої переписки додає лише те, чого ще не було. Це та сама
планка, що для решти імпортерів проєкту (`ImportRun.is_idempotent`).

Розділення відповідальності: `wa_parser` лише читає файл і нічого не знає про БД;
цей модуль лише пише в БД і нічого не знає про формати WhatsApp.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from sqlmodel import Session, select

from .. import config
from ..models import ChatAttachment, ChatMessage, ChatRoom, utcnow
from .wa_parser import Rozbir

# Ім'я в WhatsApp — це підпис у чужій адресній книзі, а не логін. Щоб архів читався
# як робота тих самих людей, зводимо ЛИШЕ точні збіги, підтверджені аудитом
# 05.08.2026. Невідоме ім'я лишаємо як є — краще чужий підпис, ніж вигаданий свій.
IMENA = {
    "Marko": "Марко",
    "Ви": "Марко",                      # у власному експорті свої репліки — «Ви»
    "You": "Марко",
    "Oksana Demo": "Оксана",
    "Halyna Demo": "Галина",
    "Богдан": "Богдан",
}


def imya(syre: str) -> str:
    return IMENA.get((syre or "").strip(), (syre or "").strip())


@dataclass
class Zvit:
    kimnata: str = ""
    nova_kimnata: bool = False
    novyh: int = 0
    vzhe_bulo: int = 0
    media_dodano: int = 0
    media_vidhyleno: list[str] = field(default_factory=list)
    media_bez_baytiv: int = 0
    systemnyh: int = 0
    nerozibranyh: int = 0
    dvoznachna_data: bool = False

    @property
    def idempotentno(self) -> bool:
        return self.novyh == 0

    def yak_tekst(self) -> str:
        ch = [f"кімната «{self.kimnata}»{' (створено)' if self.nova_kimnata else ''}",
              f"нових {self.novyh}", f"вже було {self.vzhe_bulo}"]
        if self.media_dodano:
            ch.append(f"файлів {self.media_dodano}")
        if self.media_bez_baytiv:
            ch.append(f"згадок про файли без байтів {self.media_bez_baytiv}")
        if self.media_vidhyleno:
            ch.append(f"файлів відхилено {len(self.media_vidhyleno)}")
        if self.systemnyh:
            ch.append(f"системних пропущено {self.systemnyh}")
        if self.nerozibranyh:
            ch.append(f"НЕ РОЗІБРАНО {self.nerozibranyh}")
        if self.dvoznachna_data:
            ch.append("порядок день/місяць недоказовий — прочитано як ДД.ММ")
        return " · ".join(ch)


def _kimnata(s: Session, *, kod: str, nazva: str, uchasnyky: str,
             wa_chat: str, poryadok: int, hto: str) -> tuple[ChatRoom, bool]:
    room = s.exec(select(ChatRoom).where(ChatRoom.code == kod)).first()
    if room:
        return room, False
    room = ChatRoom(code=kod, title=nazva, is_default=False, archived=True,
                    uchasnyky=uchasnyky, dzherelo="whatsapp", wa_chat=wa_chat,
                    poryadok=poryadok, created_by=hto)
    s.add(room)
    s.flush()
    return room, True


def _zberehty_media(s: Session, msg_id: int, nomer: int, imya_faylu: str,
                    danci: bytes) -> ChatAttachment | None:
    """Кладе файл з архіву експорту у сховище чату за ТИМИ САМИМИ правилами.

    Правила беремо з веб-шару чату, а не дублюємо: якщо там колись заборонять ще
    один тип, тут це має спрацювати автоматично. Інакше імпорт став би дірою в
    захисті, який ми щойно поставили на ручне завантаження.
    """
    from ..web.chat import DOZVOLENI, ZABORONENI, _slug

    bezpechne = Path(imya_faylu).name
    ext = Path(bezpechne).suffix.lower()
    if not ext or ext in ZABORONENI or ext not in DOZVOLENI:
        return None
    dest = config.CHAT_DIR / f"wa_{msg_id}_{nomer}_{_slug(Path(bezpechne).stem)}{ext}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(danci)
    return ChatAttachment(message_id=msg_id, filename=dest.name,
                          original_name=bezpechne, size=len(danci),
                          sha256=hashlib.sha256(danci).hexdigest(),
                          created_by="whatsapp-import")


def importuvaty(s: Session, rozbir: Rozbir, *, kod: str, nazva: str,
                uchasnyky: str = "", wa_chat: str = "", poryadok: int = 0,
                hto: str = "імпорт") -> Zvit:
    """Переносить розібрану переписку в архівну кімнату. Ідемпотентно."""
    z = Zvit(systemnyh=rozbir.systemnyh, nerozibranyh=rozbir.nerozibranyh,
             dvoznachna_data=rozbir.dvoznachna_data)
    room, nova = _kimnata(s, kod=kod, nazva=nazva, uchasnyky=uchasnyky,
                          wa_chat=wa_chat or nazva, poryadok=poryadok, hto=hto)
    z.kimnata, z.nova_kimnata = room.title, nova

    prefiks = f"wa:{kod}:"
    vzhe = {m.uid for m in s.exec(select(ChatMessage)
                                  .where(ChatMessage.room_id == room.id))}

    # Сортуємо за часом: id у кімнаті мають іти в тому ж порядку, що й розмова,
    # інакше повторний імпорт старішого експорту перемішав би стрічку.
    for m in sorted(rozbir.povidomlennya, key=lambda x: x.when):
        uid = prefiks + m.klyuch
        if uid in vzhe:
            z.vzhe_bulo += 1
            continue
        vidsutni = list(m.vkladennya)
        msg = ChatMessage(
            room_id=room.id, author=imya(m.avtor), text=m.text,
            media_vtracheno=m.media_vtracheno, created_by="whatsapp-import")
        msg.uid = uid
        msg.created_at = m.when          # час із переписки, а не час імпорту
        msg.updated_at = m.when
        s.add(msg)
        s.flush()

        for n, fn in enumerate(m.vkladennya, start=1):
            danci = rozbir.media_v_arhivi.get(Path(fn).name)
            if danci is None:
                continue
            a = _zberehty_media(s, msg.id, n, fn, danci)
            if a is None:
                z.media_vidhyleno.append(fn)
            else:
                s.add(a)
                z.media_dodano += 1
                vidsutni.remove(fn)
        if vidsutni:
            msg.vidsutni_fayly = ",".join(vidsutni)
            z.media_bez_baytiv += len(vidsutni)
            s.add(msg)
        vzhe.add(uid)
        z.novyh += 1

    room.updated_at = utcnow()
    s.add(room)
    return z

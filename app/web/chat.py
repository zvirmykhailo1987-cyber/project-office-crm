"""Чат офісу (Ф10): кімната, повідомлення, вкладення.

Замінює робочу групу у WhatsApp. Чому не інтеграція з самим WhatsApp: офіційний
Business API від Meta бачить лише розмови зі своїм бізнес-номером — наявних груп не
бачить узагалі; неофіційний міст порушує умови й ризикує баном робочого номера, на
якому тримається звʼязок із громадами. Тому розмова живе тут.

Найважливіше в цьому файлі — не чат, а правила щодо файлів (`_perevir_imya`,
`_zberehty_potokom`, `download`). Через чат у систему потрапляє довільний файл від
людини й повертається іншим людям, тож це найкоротший шлях доставити чужий код на
компʼютери команди. Тому: чорний список → білий список → віддача завжди без відкриття
в браузері (крім картинок із жорстко заданим типом).
"""
import hashlib
import unicodedata
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import and_, or_
from sqlmodel import Session, func, select

from .. import audit, config
from ..db import get_session
from ..i18n import _
from ..models import (
    CAP_CRM,
    CAP_DATA_SERVICE,
    CAP_DELETE,
    KIMNATA_OFIS,
    ChatAttachment,
    ChatMessage,
    ChatRead,
    ChatRoom,
    Document,
    Task,
    User,
    utcnow,
)
from ..services import collab, wa_import, wa_parser
from .deps import current_user, data_z_formy, redirect, render, require_cap
from .dokumenty import DOZVOLENI_ROZSHYRENNYA, _slug

router = APIRouter()

# ── правила щодо файлів ──────────────────────────────────────────────────────

# Спершу чорний список. Він потрібен НАВІТЬ при білому: білий колись розширять, а це
# те, що не можна пускати ніколи. `.svg` тут не помилка — це картинка, яка вміє
# виконувати скрипт при відкритті, тому її немає й серед документів.
ZABORONENI = {
    ".exe", ".msi", ".bat", ".cmd", ".com", ".scr", ".pif", ".cpl",
    ".ps1", ".psm1", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".wsh", ".hta",
    ".jar", ".dll", ".so", ".lnk", ".reg", ".apk", ".deb", ".rpm", ".sh", ".svg",
}

# Білий список: усе з реєстру документів плюс те, що реально ходить у робочій групі.
ARHIVY = {".zip", ".7z", ".rar", ".tar", ".gz", ".tgz"}
DANI = {".json", ".geojson", ".xml", ".kml", ".kmz", ".gpx", ".sqlite", ".db",
        ".parquet", ".yaml", ".yml", ".sql"}
MEDIA = {".mp3", ".ogg", ".opus", ".m4a", ".wav", ".mp4", ".webm", ".mov"}
DOZVOLENI = (DOZVOLENI_ROZSHYRENNYA | ARHIVY | DANI | MEDIA) - ZABORONENI

# Картинки показуємо в стрічці — інакше чат марний для скріншотів правок.
# Тип віддаємо зі своєї мапи, а не з того, що надіслав браузер: заголовок від клієнта
# тут керував би тим, як браузер виконає вміст.
KARTYNKY = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".webp": "image/webp", ".gif": "image/gif"}

MAX_ROZMIR = 100 * 1024 * 1024        # 100 МБ — zip бази контактів важив 37 МБ
SHMAT = 1024 * 1024                   # читаємо потоком, а не всім файлом у память
STORINKA = 60                         # скільки повідомлень показуємо одразу


class FaylVidhyleno(Exception):
    """Файл не пройшов перевірку. Текст — те, що побачить людина."""


def _perevir_imya(imya_syre: str) -> tuple[str, str]:
    """Повертає (безпечне базове імʼя, розширення) або кидає `FaylVidhyleno`.

    Перевірка йде за ОСТАННІМ суфіксом, тому «звіт.pdf.exe» читається як `.exe`.
    """
    imya = Path(imya_syre or "").name          # відрізаємо шлях: захист від ../
    imya = unicodedata.normalize("NFC", imya).strip()
    if not imya or imya in (".", ".."):
        raise FaylVidhyleno("Файл без імені")
    ext = Path(imya).suffix.lower()
    if not ext:
        raise FaylVidhyleno(f"«{imya}»: файл без розширення не приймаємо")
    if ext in ZABORONENI:
        raise FaylVidhyleno(f"«{imya}»: {ext} — виконуваний тип, заборонено назавжди")
    if ext not in DOZVOLENI:
        raise FaylVidhyleno(f"«{imya}»: тип {ext} не в переліку дозволених")
    return imya, ext


async def _zberehty_potokom(fayl: UploadFile, imya: str, ext: str,
                            msg_id: int, nomer: int) -> ChatAttachment:
    """Пише файл на диск шматками, рахуючи розмір і sha256 на льоту.

    Чому не `await fayl.read()` одним куском, як у документах: там ліміт 60 МБ і файл
    цілком лягає в память ще ДО перевірки розміру. Тут ліміт 100 МБ і кілька файлів за
    раз, тому обриваємо на перевищенні й прибираємо недописане.
    """
    dest = config.CHAT_DIR / f"{msg_id}_{nomer}_{_slug(Path(imya).stem)}{ext}"
    dest.parent.mkdir(parents=True, exist_ok=True)

    h = hashlib.sha256()
    rozmir = 0
    try:
        with dest.open("wb") as out:
            while True:
                shmat = await fayl.read(SHMAT)
                if not shmat:
                    break
                rozmir += len(shmat)
                if rozmir > MAX_ROZMIR:
                    raise FaylVidhyleno(
                        f"«{imya}»: більше за {MAX_ROZMIR // (1024 * 1024)} МБ")
                h.update(shmat)
                out.write(shmat)
    except Exception:
        dest.unlink(missing_ok=True)      # недописаний файл не лишаємо ніколи
        raise
    if rozmir == 0:
        dest.unlink(missing_ok=True)
        raise FaylVidhyleno(f"«{imya}»: порожній файл")

    return ChatAttachment(message_id=msg_id, filename=dest.name, original_name=imya,
                          size=rozmir, mime=KARTYNKY.get(ext, ""),
                          sha256=h.hexdigest())


# ── кімната ──────────────────────────────────────────────────────────────────

def _kimnata(s: Session, kod: str = "") -> ChatRoom:
    """Кімната за кодом; без коду — жива кімната офісу.

    Сіється в `db.bootstrap()`, але тест може стартувати без неї.
    """
    if kod:
        room = s.exec(select(ChatRoom).where(ChatRoom.code == kod)).first()
        if room:
            return room
    room = s.exec(select(ChatRoom).where(ChatRoom.code == KIMNATA_OFIS)).first()
    if not room:
        room = ChatRoom(code=KIMNATA_OFIS, title="Загальна група офісу",
                        is_default=True, created_by="система")
        s.add(room)
        s.commit()
        s.refresh(room)
    return room


def _kimnaty(s: Session, imya: str) -> list[ChatRoom]:
    """Кімнати, видимі цій людині: жива перша, далі архіви за порядком.

    Фільтр застосовуємо в КОДІ, а не в запиті: `uchasnyky` — CSV, і `LIKE '%ім'я%'`
    зловив би «Оксана» всередині «Оксанка». Кімнат одиниці, тож ціна нульова.
    """
    vsi = list(s.exec(select(ChatRoom).order_by(ChatRoom.archived,
                                                ChatRoom.poryadok, ChatRoom.id)))
    return [r for r in vsi if r.vydyma(imya)]


def _vydyme(s: Session, m: ChatMessage | None, user: User) -> None:
    """404, якщо повідомлення лежить у кімнаті, якої людина не бачить.

    Правило «архів особистого чату бачать лише учасники» досі діяло тільки на
    екрані кімнати: файл, «у завдання», «у документи», правка й видалення
    приймали будь-який id — тобто вміст закритої кімнати діставався прямим
    запитом. 404, а не 403: чужа кімната не має навіть підтверджувати існування.
    """
    if m is None:
        return
    room = s.get(ChatRoom, m.room_id)
    if room is not None and not room.vydyma(user.name):
        raise HTTPException(404, _("Не знайдено"))


def _kursor_ranishe(s: Session, do: int):
    """Умова «раніше за повідомлення `do`» за тим самим ключем, що й сортування.

    Стрічка впорядкована за (created_at, id), а «раніші» досі різались за id —
    у перенесеному архіві id відбиває порядок імпорту, тож частина старших
    реплік губилась між сторінками, а лічильник обіцяв не те число.
    """
    pivot = s.get(ChatMessage, do)
    if pivot is None:
        return ChatMessage.id < do
    return or_(ChatMessage.created_at < pivot.created_at,
               and_(ChatMessage.created_at == pivot.created_at, ChatMessage.id < pivot.id))


def _vkladennya(s: Session, msg_ids: list[int]) -> dict[int, list[ChatAttachment]]:
    """Вкладення пачкою — щоб не робити запит на кожне повідомлення у стрічці."""
    if not msg_ids:
        return {}
    out: dict[int, list[ChatAttachment]] = {}
    for a in s.exec(select(ChatAttachment)
                    .where(ChatAttachment.message_id.in_(msg_ids))
                    .order_by(ChatAttachment.id)):
        out.setdefault(a.message_id, []).append(a)
    return out


def _dochytav(s: Session, room_id: int, user_name: str) -> ChatRead:
    r = s.exec(select(ChatRead).where(ChatRead.room_id == room_id,
                                      ChatRead.user == user_name)).first()
    if not r:
        r = ChatRead(room_id=room_id, user=user_name, created_by=user_name)
        s.add(r)
    return r


def neprochytani(s: Session, user_name: str) -> int:
    """Скільки нових повідомлень для лічильника в шапці. Свої не рахуємо."""
    room = s.exec(select(ChatRoom).where(ChatRoom.code == KIMNATA_OFIS)).first()
    if not room:
        return 0
    r = s.exec(select(ChatRead).where(ChatRead.room_id == room.id,
                                      ChatRead.user == user_name)).first()
    mezha = r.last_read_message_id if r else 0
    return s.exec(select(func.count()).select_from(ChatMessage).where(
        ChatMessage.room_id == room.id,
        ChatMessage.id > mezha,
        ChatMessage.author != user_name,
        ChatMessage.deleted_at.is_(None))).one()


def _kontekst(s: Session, rows: list[ChatMessage]) -> dict:
    """Спільний контекст для повного екрана й для HTMX-частини."""
    vidpovidi = {m.reply_to_id for m in rows if m.reply_to_id}
    batky = {}
    if vidpovidi:
        batky = {m.id: m for m in s.exec(select(ChatMessage)
                                         .where(ChatMessage.id.in_(vidpovidi)))}
    return {
        "rows": rows,
        "files": _vkladennya(s, [m.id for m in rows]),
        "batky": batky,
        # потрібні й у порції полінгу — там теж є форма «у завдання» з вибором виконавця
        "users": list(s.exec(select(User).where(User.active == True))),  # noqa: E712
    }


@router.get("/chat/lichylnyk")
def badge(request: Request, user: User = Depends(current_user),
          s: Session = Depends(get_session)):
    """Лічильник у навігації. Полінг 30 с — той самий ритм, що в сповіщень."""
    return render(request, "chat/_lichylnyk.html", user=user,
                  n=neprochytani(s, user.name),
                  aktyvna=request.headers.get("HX-Current-URL", "").endswith("/chat"))


@router.get("/chat")
def room(request: Request, q: str = "", kimnata: str = "", do: int = 0,
         user: User = Depends(current_user), s: Session = Depends(get_session)):
    room = _kimnata(s, kimnata)
    if not room.vydyma(user.name):
        # Архів особистої переписки видно лише її учасникам — див. ChatRoom.uchasnyky
        return redirect("/chat?err=" + quote("Ця кімната вам не видима"))

    # Умови збираємо списком, бо той самий набір потрібен і для сторінки,
    # і для лічильника «скільки лишилось раніших».
    umovy = [ChatMessage.room_id == room.id]
    stmt = select(ChatMessage).where(umovy[0])
    if do:
        # «раніші»: в архіві на 251 повідомлення без цього видно лише останні 60,
        # і до старших не дістатись інакше як пошуком
        stmt = stmt.where(_kursor_ranishe(s, do))
    if q.strip():
        # Пошук і по тексту, і по іменах файлів: «де той zip» — типовий запит
        znaydeni = {a.message_id for a in s.exec(
            select(ChatAttachment).where(
                ChatAttachment.original_name.ilike(f"%{q.strip()}%")))}
        umova = ChatMessage.text.ilike(f"%{q.strip()}%")
        if znaydeni:
            umova = umova | ChatMessage.id.in_(znaydeni)
        umovy.append(umova)
    for u in umovy[1:]:
        stmt = stmt.where(u)
    # Порядок за ЧАСОМ, не за id: у перенесеній переписці id відбиває послідовність
    # імпорту, а не розмови — свіжіший експорт може принести старіші репліки.
    rows = list(s.exec(stmt.order_by(ChatMessage.created_at.desc(),
                                     ChatMessage.id.desc()).limit(STORINKA)))[::-1]

    # Скільки лишилось «раніших» — рахуємо з ТИМИ САМИМИ умовами, що й сторінку.
    # Інакше в режимі пошуку кнопка обіцяла б «ще 149», маючи на увазі всі старіші
    # повідомлення кімнати, а не старіші ЗБІГИ.
    ranishi = 0
    if rows:
        lich = select(func.count()).select_from(ChatMessage).where(
            _kursor_ranishe(s, rows[0].id))
        for u in umovy:
            lich = lich.where(u)
        ranishi = s.exec(lich).one()

    if not q.strip() and not do and rows:
        r = _dochytav(s, room.id, user.name)
        r.last_read_message_id = max(r.last_read_message_id,
                                     max(m.id for m in rows))
        r.read_at = utcnow()
        s.add(r)
        s.commit()

    return render(request, "chat/kimnata.html", user=user, room=room, q=q,
                  kimnaty=_kimnaty(s, user.name), ranishi=ranishi,
                  do=rows[0].id if rows else 0,
                  vsoho=s.exec(select(func.count()).select_from(ChatMessage)
                               .where(ChatMessage.room_id == room.id)).one(),
                  mention_hint=collab.mention_hint(s),
                  ostannii=max((m.id for m in rows), default=0),
                  **_kontekst(s, rows))


@router.get("/chat/novi")
def novi(request: Request, after: int = 0, user: User = Depends(current_user),
         s: Session = Depends(get_session)):
    """Тільки нові повідомлення — те, що дотягує полінг HTMX кожні 5 секунд."""
    room = _kimnata(s)
    rows = list(s.exec(select(ChatMessage)
                       .where(ChatMessage.room_id == room.id, ChatMessage.id > after)
                       .order_by(ChatMessage.id)))
    if rows:
        r = _dochytav(s, room.id, user.name)
        r.last_read_message_id = max(r.last_read_message_id, rows[-1].id)
        r.read_at = utcnow()
        s.add(r)
        s.commit()
    return render(request, "chat/_potik.html", user=user, room=room,
                  ostannii=rows[-1].id if rows else after, **_kontekst(s, rows))


@router.post("/chat")
async def send(request: Request, text: str = Form(""), reply_to: str = Form(""),
               fayly: list[UploadFile] = File(default=[]),
               user: User = Depends(require_cap(CAP_CRM)),
               s: Session = Depends(get_session)):
    room = _kimnata(s)
    tekst = (text or "").strip()

    # Спершу перевіряємо ВСІ імена і лише потім щось пишемо: інакше при трьох файлах,
    # де третій заборонений, перші два вже осіли б на диску окремим повідомленням.
    prynyati: list[tuple[UploadFile, str, str]] = []
    try:
        for f in fayly:
            if not f or not (f.filename or "").strip():
                continue
            imya, ext = _perevir_imya(f.filename)
            prynyati.append((f, imya, ext))
    except FaylVidhyleno as e:
        return redirect(f"/chat?err={quote(str(e))}")

    if not tekst and not prynyati:
        return redirect("/chat")

    # Відповідь — лише на повідомлення ЦІЄЇ кімнати: інакше в стрічці офісу
    # цитувався б текст із закритого архіву, якого автор відповіді не бачить.
    reply_id = int(reply_to) if reply_to.strip().isdigit() else None
    if reply_id is not None:
        batko = s.get(ChatMessage, reply_id)
        if batko is None or batko.room_id != room.id:
            raise HTTPException(400, _("Відповідати можна лише на повідомлення цієї кімнати"))

    zgadani = collab.find_mentions(s, tekst)
    msg = ChatMessage(
        room_id=room.id, author=user.name, text=tekst,
        reply_to_id=reply_id,
        mentions=",".join(u.login for u in zgadani if u.login),
        created_by=user.name)
    s.add(msg)
    s.flush()

    zbereheni: list[ChatAttachment] = []
    try:
        for n, (f, imya, ext) in enumerate(prynyati, start=1):
            zbereheni.append(await _zberehty_potokom(f, imya, ext, msg.id, n))
    except FaylVidhyleno as e:
        for a in zbereheni:                    # прибираємо вже записані з цієї пачки
            (config.CHAT_DIR / a.filename).unlink(missing_ok=True)
        s.rollback()
        return redirect(f"/chat?err={quote(str(e))}")

    for a in zbereheni:
        s.add(a)
    if zbereheni:
        # Повідомлення в журнал не пишемо (залило б «Стрічку»), а файли — пишемо:
        # це те, що потім шукають і за що відповідають.
        audit.log(s, user.name, "chat.file", entity="chat_message", entity_id=msg.id,
                  tier=audit.TIER_CHANGE,
                  detail=f"files: {len(zbereheni)}")   # no names: rooms may be private

    if zgadani:
        collab.notify_many(s, [u.name for u in zgadani], kind="chat",
                           text=f"{user.name}: {(tekst or 'файл')[:70]}",
                           url=f"/chat#m{msg.id}", entity="chat_message",
                           entity_id=msg.id, from_whom=user.name)
    s.commit()
    return redirect(f"/chat#m{msg.id}")


@router.get("/chat/fayl/{att_id}")
def download(att_id: int, user: User = Depends(current_user),
             s: Session = Depends(get_session)):
    """Віддача файлу. За замовчуванням — завантаження, ніколи не відкриття."""
    a = s.get(ChatAttachment, att_id)
    if not a:
        return redirect("/chat")
    _vydyme(s, s.get(ChatMessage, a.message_id), user)
    path = config.CHAT_DIR / a.filename
    # Ще одна перевірка шляху: імʼя в БД наше, але припускати цього не будемо
    if not path.exists() or path.parent.resolve() != config.CHAT_DIR.resolve():
        return redirect("/chat?err=" + quote("Файл не знайдено на диску"))

    ext = Path(a.original_name).suffix.lower()
    imya = quote(a.original_name)
    if ext in KARTYNKY:
        rozm = f"inline; filename*=UTF-8''{imya}"
        typ = KARTYNKY[ext]
    else:
        rozm = f"attachment; filename*=UTF-8''{imya}"
        typ = "application/octet-stream"
    # nosniff обовʼязковий: без нього браузер вгадує тип за вмістом і може виконати
    # те, що ми віддали як просто байти
    return FileResponse(path, media_type=typ, headers={
        "Content-Disposition": rozm,
        "X-Content-Type-Options": "nosniff",
    })


@router.post("/chat/{msg_id}/redahuvaty")
def edit(msg_id: int, text: str = Form(""), user: User = Depends(require_cap(CAP_CRM)),
         s: Session = Depends(get_session)):
    m = s.get(ChatMessage, msg_id)
    _vydyme(s, m, user)
    if not m or m.is_deleted or m.author != user.name or not text.strip():
        return redirect("/chat")
    m.text = text.strip()
    m.edited_at = utcnow()
    m.mentions = ",".join(u.login for u in collab.find_mentions(s, m.text) if u.login)
    m.updated_at = utcnow()
    s.add(m)
    s.commit()
    return redirect(f"/chat#m{msg_id}")


@router.post("/chat/{msg_id}/vydalyty")
def delete(msg_id: int, user: User = Depends(require_cap(CAP_CRM)),
           s: Session = Depends(get_session)):
    """Мʼяке видалення: текст ховаємо, рядок лишаємо.

    В аудиті WhatsApp 11 повідомлень позначені «Ви видалили це повідомлення» — і разом
    із ними зникли гілки відповідей. Тут відповідь на видалене лишається читабельною.
    """
    m = s.get(ChatMessage, msg_id)
    _vydyme(s, m, user)
    if not m or m.is_deleted:
        return redirect("/chat")
    if m.author != user.name and not user.can(CAP_DELETE):
        return redirect("/chat?err=" + quote("Видалити може автор або адміністратор"))
    m.deleted_at, m.deleted_by = utcnow(), user.name
    m.updated_at = utcnow()
    s.add(m)
    audit.log(s, user.name, "chat.delete", entity="chat_message", entity_id=msg_id,
              tier=audit.TIER_CHANGE, detail=f"message {m.id}")   # no text: rooms may be private
    s.commit()
    return redirect("/chat")


@router.post("/chat/{msg_id}/u-zavdannya")
def to_task(msg_id: int, assignee: str = Form(""), due_date: str = Form(""),
            user: User = Depends(require_cap(CAP_CRM)),
            s: Session = Depends(get_session)):
    """Повідомлення → завдання. За зразком `collab.comment_to_task`.

    Це і є та річ, якої бракувало: у чаті сказали «зроби X до Y» — і воно не зникло
    між репліками, а лягло у список із виконавцем і терміном.
    """
    m = s.get(ChatMessage, msg_id)
    _vydyme(s, m, user)
    if not m or m.is_deleted or not (m.text or "").strip():
        return redirect("/chat")
    termin = data_z_formy(due_date)
    t = Task(title=m.text.strip()[:120], note=f"з чату, {m.author}, "
                                              f"{m.created_at:%d.%m.%Y %H:%M}",
             assignee=assignee or user.name,
             due_date=termin,
             source="vid_komentarya",
             # автор повідомлення, а не той, хто натиснув: доручення в чаті дав він
             created_by=m.author)
    s.add(t)
    s.flush()
    audit.log(s, user.name, "chat.to_task", entity="task", entity_id=t.id,
              tier=audit.TIER_CHANGE, detail=f"task {t.id}")   # no text: rooms may be private
    if t.assignee != user.name:
        collab.notify(s, user=t.assignee, kind="zavdannya",
                      text=f"з чату: {t.title[:70]}", url=f"/zavdannya#t{t.id}",
                      entity="task", entity_id=t.id, from_whom=user.name)
    s.commit()
    return redirect(f"/zavdannya#t{t.id}")


@router.post("/chat/perenesty")
async def perenesty(request: Request, nazva: str = Form(""),
                    osobystyi: str = Form(""), fayl: UploadFile = File(...),
                    user: User = Depends(require_cap(CAP_DATA_SERVICE)),
                    s: Session = Depends(get_session)):
    """Перенести експорт WhatsApp в архівну кімнату.

    Файл експорту може бути великим (.zip з медіа), тому читаємо його з тим самим
    лімітом, що й вкладення. Розбір — детермінований, без мовної моделі.
    """
    imya_syre = Path(fayl.filename or "").name
    if Path(imya_syre).suffix.lower() not in (".txt", ".zip", ".json"):
        return redirect("/chat?err=" + quote(
            "Очікую .txt або .zip з «Експорту чату» WhatsApp (або .json зібраного)"))

    # Шматки — у список, склеюємо один раз: `danci += shmat` копіював увесь
    # накопичений буфер на кожному мегабайті (квадратично на 100 МБ).
    shmatky: list[bytes] = []
    rozmir = 0
    while True:
        shmat = await fayl.read(SHMAT)
        if not shmat:
            break
        rozmir += len(shmat)
        if rozmir > MAX_ROZMIR:
            return redirect("/chat?err=" + quote(
                f"Експорт більший за {MAX_ROZMIR // (1024 * 1024)} МБ"))
        shmatky.append(shmat)
    danci = b"".join(shmatky)
    del shmatky

    try:
        rozbir = wa_parser.rozibraty(imya_syre, danci)
    except wa_parser.ArkhivVidhyleno as e:
        return redirect("/chat?err=" + quote(str(e)))
    if not rozbir.povidomlennya:
        return redirect("/chat?err=" + quote(
            f"З файлу не вдалося прочитати жодного повідомлення "
            f"(формат: {rozbir.format}, нерозібраних рядків {rozbir.nerozibranyh})"))

    tytul = nazva.strip() or Path(imya_syre).stem
    kod = "wa-" + (_slug(tytul).lower().strip("_") or "arhiv")
    poryadok = len(list(s.exec(select(ChatRoom).where(ChatRoom.archived == True)))) + 1  # noqa: E712

    z = wa_import.importuvaty(
        s, rozbir, kod=kod, nazva=tytul,
        # Особистий чат бачить лише той, хто його переніс: у переписці двох людей
        # є речі, що стосуються лише їх, і висипати їх усій команді було б
        # зловживанням тим, що технічно це можливо.
        uchasnyky=user.name if osobystyi else "",
        wa_chat=tytul, poryadok=poryadok, hto=user.name)

    audit.log(s, user.name, "chat.import", entity="chat_message", entity_id=None,
              tier=audit.TIER_BULK, detail=z.yak_tekst()[:200])
    s.commit()
    return redirect(f"/chat?kimnata={kod}&zvit=" + quote(z.yak_tekst()))


@router.post("/chat/fayl/{att_id}/v-dokumenty")
def promote(att_id: int, kind: str = Form("fayl"), title: str = Form(""),
            user: User = Depends(require_cap(CAP_CRM)),
            s: Session = Depends(get_session)):
    """Перенести файл із чату в реєстр документів.

    Ідемпотентно: `promoted_document_id` не дає створити другий документ на той самий
    файл. Байти НЕ копіюємо — документ посилається на той самий шлях у сховищі чату.
    """
    a = s.get(ChatAttachment, att_id)
    if not a:
        return redirect("/chat")
    _vydyme(s, s.get(ChatMessage, a.message_id), user)
    if a.promoted_document_id:
        return redirect(f"/dokumenty/{a.promoted_document_id}")

    path = config.CHAT_DIR / a.filename
    if not path.exists():
        return redirect("/chat?err=" + quote("Файл не знайдено на диску"))

    doc = Document(title=title.strip() or Path(a.original_name).stem,
                   kind=kind.strip() or "fayl",
                   doc_group_uid=f"chat:{a.id}", version=1,
                   filename=a.filename, stored_path=str(path), size=a.size,
                   workflow_status="chernetka", uploaded_by=user.name,
                   created_by=user.name)
    s.add(doc)
    s.flush()
    a.promoted_document_id = doc.id
    a.updated_at = utcnow()
    s.add(a)
    audit.log(s, user.name, "chat.promote", entity="document", entity_id=doc.id,
              tier=audit.TIER_CHANGE, detail=a.original_name[:80])
    s.commit()
    return redirect(f"/dokumenty/{doc.id}")

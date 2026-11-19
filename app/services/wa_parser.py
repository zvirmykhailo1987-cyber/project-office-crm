"""Розбір експорту WhatsApp — детермінований, без мовної моделі.

ЧОМУ саме експорт, а не інтеграція: офіційний Business API від Meta бачить лише
розмови зі своїм бізнес-номером і не бачить наявних груп; неофіційний міст порушує
умови сервісу й ризикує баном робочого номера. Вбудований «Експорт чату» — єдиний
законний спосіб дістати історію, і він безкоштовний.

Підтримуємо три джерела:
  1. `.txt` з експорту — два формати, iOS і Android (див. `RE_IOS`, `RE_ANDROID`);
  2. `.zip` з експорту «з медіа» — той самий `.txt` плюс файли;
  3. власний зібраний JSON (`private_not_for_repo/audyt-whatsapp_.../syri/`).

Тверде правило: нічого не домислюємо. Рядок, який не розібрався в повідомлення,
не вигадується — він або доклеюється до попереднього (це багаторядковий текст),
або лягає в `nerozibrani` і рахується у звіті.
"""
from __future__ import annotations

import io
import json
import re
import unicodedata
import zipfile
import zlib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# WhatsApp пхає в експорт невидимі знаки напрямку тексту (U+200E/U+200F) і вузький
# нерозривний пробіл перед AM/PM (U+202F). Без їх зняття не спрацьовує жоден регекс.
NEVYDYMI = dict.fromkeys(map(ord, "\u200e\u200f\u202a\u202c\u202f\ufeff"), None)

# iOS:     [05.08.2026, 11:25:13] Автор: текст
RE_IOS = re.compile(
    r"^\[(\d{1,2})[./\-](\d{1,2})[./\-](\d{2,4}),\s+"
    r"(\d{1,2}):(\d{2})(?::(\d{2}))?\s*([APap]\.?[Mm]\.?)?\]\s*(.*)$")
# Android: 05.08.2026, 11:25 - Автор: текст
RE_ANDROID = re.compile(
    r"^(\d{1,2})[./\-](\d{1,2})[./\-](\d{2,4}),\s+"
    r"(\d{1,2}):(\d{2})(?::(\d{2}))?\s*([APap]\.?[Mm]\.?)?\s+-\s+(.*)$")

# «Автор: текст» — ім'я не може містити двокрапку, тому беремо до першої.
RE_AVTOR = re.compile(r"^([^:\n]{1,64}):\s?(.*)$", re.S)

# Позначки вкладень — українською й англійською, обидві платформи.
RE_DOLUCHENO = re.compile(
    r"(?:^|\s)([\w\-. ()]+\.\w{2,5})\s*\((?:файл долучено|file attached)\)", re.I)
RE_IOS_DOLUCHENO = re.compile(r"<(?:attached|долучено):\s*([^>]+)>", re.I)
RE_BEZ_MEDIA = re.compile(r"<(?:Медіа не долучено|Media omitted|"
                          r"Файл не долучено|attached file omitted)>", re.I)

# Системні рядки без автора — їх у чат не тягнемо, але рахуємо.
SYSTEMNI = (
    "захищені наскрізним шифруванням", "end-to-end encrypted",
    "створив цю групу", "created this group", "додав", "added",
    "змінив тему", "changed the subject", "змінив опис", "changed this group's",
    "приєднався за посиланням", "joined using this group's invite link",
    "вийшов", "left", "видалив", "removed", "змінив свій номер",
    "changed their phone number", "Ви видалили це повідомлення",
    "This message was deleted", "Повідомлення видалено",
)


@dataclass
class WaMsg:
    when: datetime
    avtor: str
    text: str
    vkladennya: list[str] = field(default_factory=list)
    media_vtracheno: bool = False        # «<Медіа не долучено>» — файл був, але його немає

    @property
    def klyuch(self) -> str:
        """Детермінований ключ рядка — на ньому тримається ідемпотентність імпорту."""
        import hashlib
        syre = f"{self.when.isoformat()}|{self.avtor}|{self.text}"
        return hashlib.sha1(syre.encode("utf-8"), usedforsecurity=False).hexdigest()[:20]


@dataclass
class Rozbir:
    povidomlennya: list[WaMsg] = field(default_factory=list)
    systemnyh: int = 0
    nerozibranyh: int = 0
    zrazky_nerozibranyh: list[str] = field(default_factory=list)
    format: str = ""
    den_pershyi: bool = True            # чи прочитано дату як ДД.ММ (а не ММ/ДД)
    dvoznachna_data: bool = False       # обидва числа ≤ 12 — порядок недоказовий
    media_v_arhivi: dict[str, bytes] = field(default_factory=dict)

    @property
    def vsoho(self) -> int:
        return len(self.povidomlennya)


def _chysla_dat(rows: list[tuple[int, int]]) -> tuple[bool, bool]:
    """Визначає, що йде першим — день чи місяць. Повертає (день_перший, двозначно).

    ЧОМУ не вгадуємо мовчки: 05.08 — це і 5 серпня, і 8 травня. Якщо в усьому файлі
    жодне перше число не більше 12, порядок недоказовий; тоді беремо український
    (день першим) і ЧЕСНО кажемо про це у звіті, а не вдаємо впевненість.
    """
    persha_bilshe = any(a > 12 for a, _ in rows)
    druha_bilshe = any(b > 12 for _, b in rows)
    if persha_bilshe and not druha_bilshe:
        return True, False
    if druha_bilshe and not persha_bilshe:
        return False, False
    return True, True


def _rik(r: int) -> int:
    return r if r >= 100 else (2000 + r if r < 70 else 1900 + r)


def _godyna(g: int, ampm: str | None) -> int:
    if not ampm:
        return g
    a = ampm.lower().replace(".", "")
    if a.startswith("p") and g != 12:
        return g + 12
    if a.startswith("a") and g == 12:
        return 0
    return g


def _systemne(text: str) -> bool:
    t = text.strip()
    return any(m.lower() in t.lower() for m in SYSTEMNI)


def _vydilyty_vkladennya(text: str) -> tuple[str, list[str], bool]:
    """Витягує імена вкладень із тексту, лишаючи сам текст читабельним."""
    fayly: list[str] = []
    for rx in (RE_DOLUCHENO, RE_IOS_DOLUCHENO):
        for m in rx.finditer(text):
            imya = m.group(1).strip()
            if imya and imya not in fayly:
                fayly.append(imya)
        text = rx.sub("", text)
    vtracheno = bool(RE_BEZ_MEDIA.search(text))
    text = RE_BEZ_MEDIA.sub("", text)
    return text.strip(), fayly, vtracheno


def rozibraty_txt(syryi: str) -> Rozbir:
    """Розбирає текст експорту. Формат визначається за першим розпізнаним рядком."""
    r = Rozbir()
    ryadky = syryi.translate(NEVYDYMI).replace("\r\n", "\n").split("\n")

    # Перший прохід: збираємо сирі заголовки, щоб визначити порядок день/місяць
    zaholovky: list[tuple[re.Match, str]] = []
    for ln in ryadky:
        m = RE_IOS.match(ln)
        if m:
            zaholovky.append((m, "ios"))
            continue
        m = RE_ANDROID.match(ln)
        if m:
            zaholovky.append((m, "android"))
    if not zaholovky:
        r.format = "невідомий"
        r.nerozibranyh = len([x for x in ryadky if x.strip()])
        r.zrazky_nerozibranyh = [x for x in ryadky if x.strip()][:3]
        return r

    r.format = zaholovky[0][1]
    r.den_pershyi, r.dvoznachna_data = _chysla_dat(
        [(int(m.group(1)), int(m.group(2))) for m, _ in zaholovky])

    rx = RE_IOS if r.format == "ios" else RE_ANDROID
    potochne: WaMsg | None = None

    def zakryty():
        nonlocal potochne
        if potochne is None:
            return
        t, f, v = _vydilyty_vkladennya(potochne.text)
        potochne.text, potochne.vkladennya, potochne.media_vtracheno = t, f, v
        if t or f or v:
            r.povidomlennya.append(potochne)
        else:
            r.systemnyh += 1
        potochne = None

    for ln in ryadky:
        m = rx.match(ln)
        if not m:
            if potochne is not None and ln.strip():
                potochne.text += "\n" + ln           # багаторядкове повідомлення
            elif ln.strip():
                r.nerozibranyh += 1
                if len(r.zrazky_nerozibranyh) < 3:
                    r.zrazky_nerozibranyh.append(ln[:120])
            continue

        zakryty()
        a, b, rik, gg, hv, sek, ampm, hvist = m.groups()
        den, mis = (int(a), int(b)) if r.den_pershyi else (int(b), int(a))
        try:
            koly = datetime(_rik(int(rik)), mis, den,
                            _godyna(int(gg), ampm), int(hv), int(sek or 0))
        except ValueError:
            r.nerozibranyh += 1
            continue

        ma = RE_AVTOR.match(hvist)
        if not ma or _systemne(hvist):
            r.systemnyh += 1
            continue
        avtor, tekst = ma.group(1).strip(), ma.group(2)
        if _systemne(tekst) and not tekst.strip():
            r.systemnyh += 1
            continue
        potochne = WaMsg(when=koly, avtor=avtor, text=tekst)

    zakryty()
    return r


class ArkhivVidhyleno(ValueError):
    """Архів не пройшов межі (zip-бомба, забагато файлів, битий zip)."""


# Межі на розпакування. 100 МБ стиснутого zip-а може розгорнутись у гігабайти —
# класична zip-бомба кладе процес на памʼяті ще до першого повідомлення.
ZIP_MAX_FAYLIV = 2000
ZIP_MAX_RAZOM = 200 * 1024 * 1024
ZIP_MAX_FAYL = 100 * 1024 * 1024
ZIP_SHMAT = 1024 * 1024


def _prochytaty_obmezheno(z: zipfile.ZipFile, info: zipfile.ZipInfo, zalyshok: int) -> bytes:
    """Читає член архіву шматками й обриває на перевищенні меж.

    Заявленому `file_size` не довіряємо до кінця: заголовок можна підробити,
    тому лічимо реально розпаковані байти.
    """
    mezha = min(ZIP_MAX_FAYL, zalyshok)
    shmatky: list[bytes] = []
    rozmir = 0
    with z.open(info) as f:
        while True:
            shmat = f.read(ZIP_SHMAT)
            if not shmat:
                break
            rozmir += len(shmat)
            if rozmir > mezha:
                raise ArkhivVidhyleno(
                    f"Архів розпаковується більше ніж у {ZIP_MAX_RAZOM // (1024 * 1024)} МБ "
                    f"або файл «{Path(info.filename).name}» завеликий")
            shmatky.append(shmat)
    return b"".join(shmatky)


def rozibraty_zip(danci: bytes) -> Rozbir:
    """Експорт «з медіа»: усередині один `.txt` і файли поруч."""
    try:
        z = zipfile.ZipFile(io.BytesIO(danci))
    except zipfile.BadZipFile:
        raise ArkhivVidhyleno("Файл не є коректним zip-архівом") from None
    infos = [i for i in z.infolist() if not i.is_dir()]
    if len(infos) > ZIP_MAX_FAYLIV:
        raise ArkhivVidhyleno(f"В архіві понад {ZIP_MAX_FAYLIV} файлів")
    # Перша лінія — заявлені розміри, ДО будь-якого читання
    if any(i.file_size > ZIP_MAX_FAYL for i in infos):
        raise ArkhivVidhyleno(
            f"Файл в архіві більший за {ZIP_MAX_FAYL // (1024 * 1024)} МБ")
    if sum(i.file_size for i in infos) > ZIP_MAX_RAZOM:
        raise ArkhivVidhyleno(
            f"Архів розпаковується більше ніж у {ZIP_MAX_RAZOM // (1024 * 1024)} МБ")

    txt = [i for i in infos if i.filename.lower().endswith(".txt")]
    if not txt:
        r = Rozbir()
        r.format = "zip без .txt"
        return r
    # Найбільший .txt — це і є переписка (дрібні бувають службовими)
    holovnyi = max(txt, key=lambda i: i.file_size)
    try:
        zalyshok = ZIP_MAX_RAZOM
        syre = _prochytaty_obmezheno(z, holovnyi, zalyshok)
        zalyshok -= len(syre)
        r = rozibraty_txt(syre.decode("utf-8", "replace"))
        for i in infos:
            if i.filename == holovnyi.filename:
                continue
            bayty = _prochytaty_obmezheno(z, i, zalyshok)
            zalyshok -= len(bayty)
            r.media_v_arhivi[Path(i.filename).name] = bayty
    except (zipfile.BadZipFile, zlib.error, EOFError) as e:
        raise ArkhivVidhyleno(f"Пошкоджений zip-архів: {type(e).__name__}") from None
    return r


def rozibraty_json(obj: dict, kod_chatu: str = "") -> Rozbir:
    """Власний зібраний формат: `{"z_datoyu": [{chat,kod,data,chas,avtor,text}]}`.

    Це те, що зібрано з екрана під час аудиту 05.08.2026. Формат не має секунд і
    вкладень — вони інвентаризовані окремо в реєстрі аудиту.
    """
    r = Rozbir()
    r.format = "zibranyi-json"
    for m in obj.get("z_datoyu", []):
        if kod_chatu and m.get("kod") != kod_chatu:
            continue
        try:
            d, mis, rik = (int(x) for x in m["data"].split("."))
            gg, hv = (int(x) for x in m["chas"].split(":"))
            koly = datetime(rik, mis, d, gg, hv)
        except (KeyError, ValueError):
            r.nerozibranyh += 1
            continue
        tekst = (m.get("text") or "").strip()
        if not tekst:
            r.systemnyh += 1
            continue
        t, f, v = _vydilyty_vkladennya(tekst)
        r.povidomlennya.append(WaMsg(when=koly, avtor=(m.get("avtor") or "").strip(),
                                     text=t, vkladennya=f, media_vtracheno=v))
    return r


def rozibraty(imya: str, danci: bytes) -> Rozbir:
    """Єдиний вхід: визначає джерело за розширенням."""
    suf = Path(unicodedata.normalize("NFC", imya)).suffix.lower()
    if suf == ".zip":
        return rozibraty_zip(danci)
    tekst = danci.decode("utf-8", "replace")
    if suf == ".json":
        try:
            return rozibraty_json(json.loads(tekst))
        except json.JSONDecodeError:
            r = Rozbir()
            r.format = "зіпсований JSON"
            return r
    return rozibraty_txt(tekst)

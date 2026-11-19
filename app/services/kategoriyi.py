"""Категорія підприємства за ТЗ.

НАВІЩО ОКРЕМИЙ КЛАСИФІКАТОР, КОЛИ Є `waste_category`. Категорія сировини
(«вологі → біогаз») відповідає на питання «що з цього можна взяти»; Богдану
на карті потрібна відповідь на «ЩО ЦЕ ЗА ПІДПРИЄМСТВО» — ВРХ, свинокомплекс,
пташник, переробний завод, рослинництво. Це різні осі: молокозавод дає
сироватку (вологі), але шукають його як «молокозавод», а не як «джерело
вологих відходів».

ДВІ РІЗНІ ОСІ:
  • КАТЕГОРІЯ — СУГОБО за ОСНОВНИМ КВЕД. Вкладки/фільтри/колір карти мають бути
    чисті: ВРХ=01.41/42, Свинарство=01.46, Птахівництво=01.47, Змішане=01.50,
    переробка за 10.x, Рослинництво за 01.1–01.3. Ані ДПСС, ані поголівʼя, ані
    `all_kved` категорію НЕ визначають. (Історія: спершу `all_kved` загнав 3026
    рослинників у свинарство через секундарний 01.46 — 26.08 прибрано; але потім
    поголівʼя ВРХ так само дало 236 рослинників у ВРХ — тому 27.08 категорію
    зведено СУГОБО до осн. КВЕД.)
  • ОПЕРАЦІЇ — реальні тваринницькі потужності (ДПСС «свиноферма/ВРХ/птахо…» +
    поголівʼя vrh/pigs/poultry). Це ОКРЕМА позначка «є реальна ферма», а не
    категорія: компанія-рослинник із поголівʼям лишається рослинництвом у
    вкладці, але має операцію `vrh`. Види — лише тваринницькі.

ЧОМУ ОСНОВНИЙ КВЕД ЯК ОСНОВА КАТЕГОРІЇ. Покриття осн. КВЕД — 95%; поголівʼя —
1–2% (виміряно 25.08.2026: ВРХ 328, свині 514, птиця 70 з 20 865). Категорія на
поголівʼї зробила б 98% бази «нікчим» (Г15) і змішала б рослинників у тваринні
вкладки.

Виклики:
    vyvedy_kategoriyi(kved, all_kved, activity_text, dpss, vrh, pigs, poultry)
        → (kats, slid, osnovna, operatsiyi, op_slid)   # all_kved — сумісність
    vyvedy_operatsiyi(dpss, vrh, pigs, poultry, activity_text) → (op, op_slid)
Калібрування: python -m app.services.kategoriyi --selftest
"""
from __future__ import annotations

# ── Словник категорій: слаг → людська назва ─────────────────────────────────
# Слаги латиницею: вони їдуть в URL-стан карти й у кому-список у SQLite.
KATEGORIYI = {
    # тваринництво (ієрархія ТЗ: Тваринництво → ВРХ / Свинарство / Птахівництво)
    "vrh":          "ВРХ",
    "svyni":        "Свинарство",
    "ptytsia":      "Птахівництво",
    "tvarynnytstvo": "Тваринництво (інше/н/д)",
    "zmishane":     "Змішане (потребує уточнення)",
    # агропереробка — підтипи рівно за ТЗ
    "spyrt":        "Спиртовий/біоетанольний завод",
    "tsukor":       "Цукровий завод",
    "moloko":       "Молокозавод",
    "miaso":        "Мʼясокомбінат",
    "oliya":        "Олійноекстракційний завод",
    "krokhmal":     "Крохмальний завод",
    "kombikorm":    "Комбікормовий завод",
    "pyvo":         "Пивоварня",
    "kharch":       "Інше харчове виробництво",
    # рослинництво
    "roslynnytstvo": "Рослинництво",
}

PEREROBKA = {"spyrt", "tsukor", "moloko", "miaso", "oliya", "krokhmal",
             "kombikorm", "pyvo", "kharch"}
TVARYNNYTSTVO = {"vrh", "svyni", "ptytsia", "tvarynnytstvo", "zmishane"}

# ── КВЕД → категорія ────────────────────────────────────────────────────────
# Префіксне зіставлення; ДОВШИЙ префікс перевіряється першим, інакше «10.4»
# зʼїв би «10.41» і ОЕЗ став би «іншим харчовим».
_KVED = [
    ("01.41", "vrh"), ("01.42", "vrh"),
    ("01.46", "svyni"),
    ("01.47", "ptytsia"),
    ("01.43", "tvarynnytstvo"), ("01.44", "tvarynnytstvo"),
    ("01.45", "tvarynnytstvo"), ("01.49", "tvarynnytstvo"),
    ("01.50", "zmishane"),        # змішане с/г — за таблицею ТЗ «потребує уточнення»
    ("11.01", "spyrt"), ("20.14", "spyrt"),
    ("10.81", "tsukor"), ("10.82", "tsukor"),
    ("10.51", "moloko"), ("10.52", "moloko"),
    ("10.11", "miaso"), ("10.12", "miaso"), ("10.13", "miaso"),
    ("10.41", "oliya"), ("10.42", "oliya"),
    ("10.62", "krokhmal"),
    ("10.91", "kombikorm"), ("10.92", "kombikorm"),
    ("11.05", "pyvo"), ("11.06", "pyvo"),
    ("10.", "kharch"), ("11.", "kharch"),
    # рослинництво: 01.1x–01.3x (зернові, овочі, фрукти, багаторічні, розсадники)
    ("01.1", "roslynnytstvo"), ("01.2", "roslynnytstvo"),
    ("01.3", "roslynnytstvo"),
    # 01.6x (допоміжна діяльність у с/г) — НЕ дає категорії сама по собі:
    # «послуги для рослинництва» ≠ «рослинництво», це виміряно 919 записами
    # 01.61, серед яких елеватори й МТС. Хто реально сіє — має 01.1x у all_kved.
]

# ── ДПСС: текст типу потужності → категорія ─────────────────────────────────
_DPSS = [
    ("свин", "svyni"),
    ("врх", "vrh"), ("велик", "vrh"), ("корів", "vrh"), ("молочн", "vrh"),
    # «пташ» окремо від «птах»: слово «пташник» НЕ містить підрядка «птах»
    # (пта-Ш-ник) — перший бойовий прогін ДПСС пропустив 9 пташників ФГ «УЛАР»
    ("пташ", "ptytsia"),
    ("птах", "ptytsia"), ("птиц", "ptytsia"), ("курей", "ptytsia"),
    ("курч", "ptytsia"), ("індик", "ptytsia"), ("несуч", "ptytsia"),
    ("вівц", "tvarynnytstvo"), ("коз", "tvarynnytstvo"),
    ("кролик", "tvarynnytstvo"), ("бджол", "tvarynnytstvo"),
    ("тварин", "tvarynnytstvo"),
    # гола «ферма» без виду — тваринництво н/д; стоїть ОСТАННЬОЮ, бо
    # «свиноферма»/«птахоферма» мусять спійматися своїми ключами вище
    ("ферма", "tvarynnytstvo"), ("ферм", "tvarynnytstvo"),
]


def _ye_poholivya(v) -> bool:
    """Чи свідчить значення колонки поголівʼя, що тварини Є.

    У живих xlsx колонка «Свині» містить не лише числа: трапляється «так» —
    людина знає, що свині є, але не порахувала. Падати на цьому не можна
    (перший прогін по файлу Богдана впав саме тут), а мовчки викидати —
    тим паче: «так» несе категорію. Числом вважається число, істиною —
    непорожній текст, окрім явних заперечень.
    """
    if v is None:
        return False
    try:
        return float(str(v).replace(" ", "").replace(",", ".")) > 0
    except ValueError:
        return str(v).strip().lower() not in ("", "-", "—", "ні", "нема",
                                              "немає", "no", "n/a", "нд", "н/д")


def _kved_kategorija(kod: str) -> str | None:
    kod = kod.strip()
    for pref, kat in _KVED:
        if kod.startswith(pref):
            return kat
    return None


def vyvedy_operatsiyi(dpss: str = "", vrh=None, pigs=None, poultry=None,
                      activity_text: str = "") -> tuple[list[str], str]:
    """РЕАЛЬНІ операції ферми з держреєстру ДПСС + поголівʼя → (список, слід).

    Окремо від категорії: «реальна ферма» —
    це позначка, а не категорія. Компанія з осн. КВЕД 01.11, але поголівʼям ВРХ,
    лишається РОСЛИННИЦТВОМ у вкладках, а тут отримує операцію `vrh`.
    Види — лише тваринницькі (ДПСС/поголівʼя не свідчать про переробку/рослинництво).
    """
    op: dict[str, str] = {}

    def dodaty(slag: str, dzherelo: str):
        if slag and slag not in op:
            op[slag] = dzherelo

    d = (dpss or "").lower()
    for kluch, slag in _DPSS:
        if kluch in d:
            dodaty(slag, "ДПСС")
    if _ye_poholivya(vrh):
        dodaty("vrh", "поголівʼя")
    if _ye_poholivya(pigs):
        dodaty("svyni", "поголівʼя")
    if _ye_poholivya(poultry):
        dodaty("ptytsia", "поголівʼя")
    # «Тваринництво» без деталі з напрямів — слабка операція, лише якщо виду не видно
    if not (op.keys() & {"vrh", "svyni", "ptytsia"}):
        if "тваринництво" in (activity_text or "").lower():
            dodaty("tvarynnytstvo", "напрями діяльності")
    if "tvarynnytstvo" in op and (op.keys() & {"vrh", "svyni", "ptytsia"}):
        del op["tvarynnytstvo"]

    poriadok = [s for s in KATEGORIYI if s in op]
    slid = "; ".join(f"{s}←{op[s]}" for s in poriadok)
    return poriadok, slid


def vyvedy_kategoriyi(kved: str = "", all_kved: str = "",
                      activity_text: str = "", dpss: str = "",
                      vrh=None, pigs=None, poultry=None
                      ) -> tuple[list[str], str, str, list[str], str]:
    """→ (категорія[слаги], слід, первинний слаг, операції[слаги], слід операцій).

    КАТЕГОРІЯ — СУГОБО за ОСНОВНИМ КВЕД: вкладки
    й фільтри мають бути чисті (ВРХ=01.41/42, свині=01.46, птиця=01.47). ДПСС,
    поголівʼя та `all_kved` категорію НЕ визначають.

    ОПЕРАЦІЇ — реальні тваринницькі потужності (ДПСС/поголівʼя) окремою позначкою:
    компанія-рослинник із поголівʼям лишається рослинництвом у вкладці, але має
    операцію `vrh`. `all_kved` лишено в сигнатурі для сумісності викликів.
    """
    osn_slag = _kved_kategorija(kved or "")
    kats = [osn_slag] if osn_slag else []
    slid = f"{osn_slag}←осн. КВЕД {(kved or '').strip()}" if osn_slag else ""
    osnovna = osn_slag or ""

    operatsiyi, op_slid = vyvedy_operatsiyi(dpss, vrh, pigs, poultry, activity_text)
    return kats, slid, osnovna, operatsiyi, op_slid


def _selftest() -> int:
    """Калібрування на відомих парах (Г16) — і здорових, і межових."""
    zbiy = []

    def kat(nazva, rezultat, *ochik):
        """Перевірка КАТЕГОРІЇ (rezultat[0])."""
        fakt = rezultat[0]
        if set(fakt) != set(ochik):
            zbiy.append(f"{nazva} [кат]: очікував {sorted(ochik)}, отримав {sorted(fakt)}")

    def op(nazva, rezultat, *ochik):
        """Перевірка ОПЕРАЦІЙ (rezultat[3])."""
        fakt = rezultat[3]
        if set(fakt) != set(ochik):
            zbiy.append(f"{nazva} [оп]: очікував {sorted(ochik)}, отримав {sorted(fakt)}")

    def osn(nazva, rezultat, ochik):
        if rezultat[2] != ochik:
            zbiy.append(f"{nazva}: осн. очікував '{ochik}', отримав '{rezultat[2]}'")

    # ── КАТЕГОРІЯ — СУГОБО за осн. КВЕД ──────────────────────────────────────
    # осн. 01.11 + поголівʼя ВРХ 500 → КАТЕГОРІЯ рослинництво (НЕ ВРХ),
    # а ВРХ іде в ОПЕРАЦІЇ. Саме поголівʼя заганяло 236 рослинників у ВРХ.
    r = vyvedy_kategoriyi(kved="01.11", vrh=500)
    kat("осн.01.11 + поголівʼя ВРХ", r, "roslynnytstvo")
    op("осн.01.11 + поголівʼя ВРХ → операція vrh", r, "vrh")
    osn("осн.01.11 первинна рослинництво", r, "roslynnytstvo")

    # вторинний КВЕД у all_kved категорію НЕ дає (і в операції теж — не тваринне)
    kat("осн.01.11 + втор.01.46 у all_kved → лише рослинництво",
        vyvedy_kategoriyi(kved="01.11", all_kved="01.11;01.46;10.51"), "roslynnytstvo")

    # ДПСС-свиноферма при осн. 01.11 → категорія рослинництво, операція svyni
    r = vyvedy_kategoriyi(kved="01.11", dpss='ТОВ "ГАДЗ-АГРО" свиноферма')
    kat("ДПСС свиноферма → категорія рослинництво", r, "roslynnytstvo")
    op("ДПСС свиноферма → операція svyni", r, "svyni")
    if "svyni←ДПСС" not in r[4]:
        zbiy.append(f"слід операцій не називає ДПСС: {r[4]}")

    # осн. 01.46 → категорія свинарство; операцій НЕМАЄ (немає ДПСС/поголівʼя)
    r = vyvedy_kategoriyi(kved="01.46")
    kat("осн.01.46 → свинарство", r, "svyni")
    op("осн.01.46 → без операцій", r)
    osn("осн.01.46 первинна свині", r, "svyni")

    # первинна = категорія осн. КВЕД; НЕ підмінюється операцією
    osn("осн.68.20 + ДПСС ВРХ → первинна ПОРОЖНЯ (не с/г КВЕД)",
        vyvedy_kategoriyi(kved="68.20", dpss="молочна ферма ВРХ"), "")
    op("осн.68.20 + ДПСС ВРХ → операція vrh",
       vyvedy_kategoriyi(kved="68.20", dpss="молочна ферма ВРХ"), "vrh")

    # 01.50 — змішане (потребує уточнення)
    kat("осн.01.50 → змішане", vyvedy_kategoriyi(kved="01.50"), "zmishane")

    # підтипи переробки за ТЗ (осн. КВЕД), без операцій
    kat("цукровий", vyvedy_kategoriyi(kved="10.81"), "tsukor")
    kat("ОЕЗ не «інше»", vyvedy_kategoriyi(kved="10.41"), "oliya")
    kat("харчове-решта", vyvedy_kategoriyi(kved="10.71"), "kharch")
    kat("мʼясокомбінат", vyvedy_kategoriyi(kved="10.11"), "miaso")

    # 01.61 (послуги) — ні категорії, ні операції
    kat("01.61 без категорії", vyvedy_kategoriyi(kved="01.61"))

    # порожній вхід → усе порожнє (Г15)
    kat("порожнє — категорія", vyvedy_kategoriyi())
    op("порожнє — операції", vyvedy_kategoriyi())
    osn("порожнє — первинна", vyvedy_kategoriyi(), "")

    # ── ОПЕРАЦІЇ (vyvedy_operatsiyi напряму) ────────────────────────────────
    op("майданчик пташник", ("", "", "") + vyvedy_operatsiyi(dpss="ФГ пташник"), "ptytsia")
    op("гола ферма", ("", "", "") + vyvedy_operatsiyi(dpss="Ферма"), "tvarynnytstvo")
    op("свиноферма не «ферма»", ("", "", "") + vyvedy_operatsiyi(dpss="свиноферма"), "svyni")
    op("свині='так'", ("", "", "") + vyvedy_operatsiyi(pigs="так"), "svyni")
    op("ВРХ='ні' → без операцій", ("", "", "") + vyvedy_operatsiyi(vrh="ні"))
    op("тег «Тваринництво» без виду", ("", "", "") + vyvedy_operatsiyi(
        activity_text="Зернові, Тваринництво"), "tvarynnytstvo")
    op("тег НЕ дублює ВРХ", ("", "", "") + vyvedy_operatsiyi(
        vrh=10, activity_text="Тваринництво"), "vrh")

    for x in zbiy:
        print("  ✘", x)
    print("SELFTEST kategoriyi:",
          "OK — категорія сугубо осн.КВЕД; операції ДПСС/поголівʼя окремо"
          if not zbiy else "ПРОВАЛЕНО")
    return 0 if not zbiy else 1


if __name__ == "__main__":
    import io
    import sys
    # UTF-8 на stdout: під git-push хуком (Windows, cp1251, без PYTHONIOENCODING)
    # друк слагів зі слідом «←» і «ʼ» падав UnicodeEncodeError → selftest давав
    # exit 1 і гейт «червонів» на здоровому коді (Г7). Інші скрипти це роблять.
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                      errors="replace")
    except (AttributeError, ValueError):
        pass
    if "--selftest" in sys.argv or "--self-test" in sys.argv:
        raise SystemExit(_selftest())
    print(__doc__)

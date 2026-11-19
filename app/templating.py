"""Jinja2-оточення + фільтри для української подачі чисел і дат."""
from datetime import datetime, UTC

from fastapi.templating import Jinja2Templates

from . import config, i18n

# One skin: `templates_v2/` (the earlier "studiya" skin is not part of this demo).
templates = Jinja2Templates(directory=str(config.BASE_DIR / "templates_v2"))


def num(v, dash: str = "—") -> str:
    """1234567 → '1 234 567' (нерозривні пробіли), None/'' → '—'."""
    if v in (None, "", "немає даних"):
        return dash
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if f == int(f):
        return f"{int(f):,}".replace(",", " ")
    return f"{f:,.1f}".replace(",", " ").replace(".", ",")


def dt(v, fmt: str = "%d.%m.%Y") -> str:
    if not v:
        return "—"
    if isinstance(v, str):
        return v
    return v.strftime(fmt)


def ago(v: datetime | None) -> str:
    if not v:
        return "—"
    d = (datetime.now(UTC).replace(tzinfo=None) - v).days
    if d == 0:
        return i18n.translate("сьогодні")
    if d == 1:
        return i18n.translate("вчора")
    return i18n.translate(f"{d} дн. тому")


def plural(n: int, one: str, few: str, many: str) -> str:
    """Українська форма множини: 1 угода · 2 угоди · 5 угод (EN: deal / deals)."""
    n = abs(int(n or 0))
    if i18n.current() == "en":
        return i18n.translate(one if n == 1 else many)
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def ton_stadiyi(stage) -> str:
    """Колір стадії з ПАЛІТРИ, а не з БД.

    У `seed_pipelines` стадіям колись прописали hex Tailwind (slate/indigo/violet/…):
    вісім кольорових родин на одному екрані, до того ж inline-hex не реагує на тему —
    у темній «Новий» ставав нечитним. Тримаємо одну родину (акцент) і показуємо
    прогрес насиченістю; окремий колір мають лише три термінальні стани.
    """
    # Колір термінальної стадії — з її РЕЗУЛЬТАТУ, не з коду. Раніше «втрата»
    # впізнавалась підрядком «vidmov» у коді, тож стадія відмови з будь-якою
    # іншою назвою мовчки ставала сірою «паузою».
    rez = getattr(stage, "rezultat", "") or ""
    if rez == "vygrash" or getattr(stage, "is_won", 0):
        return "var(--dobre)"
    if rez == "vtrata":
        return "var(--krytych)"
    if rez == "vidkladeno" or getattr(stage, "is_terminal", 0):
        return "var(--prygl)"
    # 0 % → приглушений, 100 % → повний акцент: рух видно навіть у сірому друці
    chastka = max(12, min(100, int(stage.default_probability or 0) + 12))
    return f"color-mix(in srgb, var(--akcent) {chastka}%, var(--prygl))"


def url_for_entity(entity: str, entity_id) -> str:
    """Посилання на картку сутності зі стрічки/черги затверджень."""
    from .models import entity_url
    return entity_url(entity, entity_id) or "#"


def misce(cp, dash: str = "—") -> str:
    """«Де» одним рядком: місто/громада + область, без порожніх шматків.

    ЧОМУ ОКРЕМИЙ ФІЛЬТР. У трьох шаблонах стояло
    `{{ city or hromada or '—' }}{% if oblast %}, {{ oblast }}{% endif %}` —
    і коли міста немає, виходило «—, Львівська», тобто «НЕМАЄ ДАНИХ, Львівська».
    Прочерк означає відсутність даних; клеїти до нього справжнє значення —
    те саме, що складати «невідомо» з числом. Побачено на картці угоди
    07.08.2026, той самий рядок був на «Обʼєктах» і в «Довіднику».

    Одна копія замість трьох ще й тому, що три копії розходяться: перша ж
    правка доїжджає не всюди.
    """
    chastyny = [getattr(cp, "city", "") or getattr(cp, "hromada", ""),
                getattr(cp, "oblast", "")]
    zhyvi = [str(x).strip() for x in chastyny if x and str(x).strip()]
    return ", ".join(zhyvi) if zhyvi else dash


def bezpechnyi_url(url: object) -> bool:
    """Чи можна ставити значення в `href`: лише http(s) з хостом.

    Сайт контрагента вводиться руками й приходить з імпортів — `javascript:`
    чи `data:` у такому полі виконались би одним кліком колеги.
    """
    from urllib.parse import urlsplit
    tekst = str(url or "").strip()
    if any(ord(ch) < 33 for ch in tekst):
        return False
    try:
        chastyny = urlsplit(tekst)
    except ValueError:
        return False
    return chastyny.scheme.lower() in ("http", "https") and bool(chastyny.netloc)


FILTRY = {
    "bezpechnyi_url": bezpechnyi_url,
    "misce": misce, "url_for_entity": url_for_entity, "ton": ton_stadiyi,
    "num": num, "plural": plural, "dt": dt, "ago": ago,
}


def zareyestruvaty(t: Jinja2Templates) -> Jinja2Templates:
    t.env.filters.update(FILTRY)
    t.env.filters["t"] = i18n.translate
    t.env.finalize = i18n.finalize
    t.env.globals["_"] = i18n.translate
    t.env.globals["LANGS"] = i18n.LANGS
    t.env.globals["js_i18n"] = i18n.js_catalog
    return t


zareyestruvaty(templates)

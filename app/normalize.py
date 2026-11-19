"""Нормалізація: телефони, ЄДРПОУ, назви (ключ дедуплікації)."""
import re

# багатослівні організаційно-правові форми — вирізаються ПЕРЕД токенізацією
OPF_PHRASES = [
    "товариство з обмеженою відповідальністю",
    "сільськогосподарське товариство з обмеженою відповідальністю",
    "приватне акціонерне товариство",
    "публічне акціонерне товариство",
    "закрите акціонерне товариство",
    "відкрите акціонерне товариство",
    "приватне підприємство",
    "державне підприємство",
    "фермерське господарство",
    "селянське (фермерське) господарство",
    "комунальне некомерційне підприємство",
    "комунальне підприємство",
    "громадська організація",
    "приватна агрофірма",
    "сільськогосподарський виробничий кооператив",
    "мале приватне підприємство",
]

OPF_TOKENS = {
    "тов", "тзов", "пп", "прат", "зат", "ват", "пат", "фг", "сфг", "кнп",
    "го", "дп", "кп", "спд", "фоп", "мпп", "свк", "паф", "ооо",
    "llc", "gmbh", "srl", "sarl", "bv", "spa", "ltd", "inc", "ag", "sa",
    "kg", "co", "corp", "sro",
}


def norm_phone(v: str) -> str:
    """0001234567 → +380001234567; 380… → +380…; інше — як є (закордонні)."""
    d = re.sub(r"\D", "", v or "")
    if not d:
        return (v or "").strip()
    if d.startswith("380"):
        return "+" + d
    if len(d) == 10 and d.startswith("0"):
        return "+38" + d
    return (v or "").strip()


def norm_edrpou(v) -> str:
    """Тільки цифри, доповнення нулями зліва до 8 знаків ('992829' → '00992829')."""
    d = re.sub(r"\D", "", str(v or ""))
    if not d:
        return ""
    return d.zfill(8) if len(d) <= 8 else d


def norm_name(v: str) -> str:
    """Ключ дедуплікації: lowercase, без лапок/дужок/ОПФ-токенів, стислі пробіли."""
    s = (v or "").lower()
    s = re.sub(r"[«»\"'`’]", "", s)
    s = re.sub(r"\([^)]*\)", " ", s)
    for p in OPF_PHRASES:
        s = s.replace(p, " ")
    toks = [t for t in re.split(r"[\s,;]+", s)
            if t and t.strip(".") not in OPF_TOKENS]
    return " ".join(toks)


def parse_name_country(v: str, default_country: str = "UA"):
    """'Schwarze-Robitec (DE)' → ('Schwarze-Robitec', 'DE'); без дужок → (name, default)."""
    s = (v or "").strip()
    m = re.search(r"\(([A-Za-z]{2})\)\s*$", s)
    if m:
        return s[: m.start()].strip(), m.group(1).upper()
    return s, default_country


def name_key(name: str, country: str = "UA") -> str:
    return norm_name(name) + "|" + (country or "UA").upper()


def norm_place(v: str) -> str:
    """'м. Бучач' → 'бучач', 'село Трибухівці' → 'трибухівці' (для ключа дедупу)."""
    s = (v or "").lower().strip()
    s = re.sub(r"^(м|с|смт|с-ще|селище|село|місто)[.\s]+", "", s)
    return re.sub(r"\s+", " ", s).strip()

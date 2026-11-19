# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mykhailo Zvir

"""Задати пароль користувачеві. Запускати ТАМ, ДЕ ЖИВЕ СИСТЕМА.

ЧОМУ ЦЕ ОКРЕМИЙ ІНСТРУМЕНТ, А НЕ РЯДОК SQL. Хеш — argon2id з власною сіллю
(`app.security.hash_pw`), тож порахувати його руками для UPDATE неможливо, а
пароль не має з'являтися ні в SQL-історії, ні в аргументах команди.

Запуск на сервері:
    docker exec -it <контейнер> python -m app.parol bohdan
    docker exec -it <контейнер> python -m app.parol --usim      # усім, випадкові

Пароль вводиться приховано й НЕ береться з аргументів: аргументи видно в
`ps` і в історії оболонки.
"""
from __future__ import annotations

import getpass
import secrets
import string
import sys

from sqlmodel import Session, select

from .db import engine
from .models.people import User
from .security import hash_pw

# Без схожих символів: пароль диктують голосом і переписують з паперу,
# а «l/1/I» та «O/0» у цьому місці коштують зайвого дзвінка.
ABETKA = "".join(c for c in string.ascii_letters + string.digits if c not in "lI1O0")


def vypadkovyi(dovzhyna: int = 14) -> str:
    return "".join(secrets.choice(ABETKA) for _ in range(dovzhyna))


def zadaty(login: str, parol: str) -> bool:
    with Session(engine) as s:
        u = s.exec(select(User).where(User.login == login)).first()
        if not u:
            return False
        u.password_hash = hash_pw(parol)
        s.add(u)
        s.commit()
    return True


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2

    if argv[0] == "--usim":
        with Session(engine) as s:
            lohiny = [u.login for u in s.exec(select(User)) if u.login]
        print("Нові паролі — показані ОДИН раз, роздати особисто:\n")
        for lg in sorted(lohiny):
            p = vypadkovyi()
            zadaty(lg, p)
            print(f"  {lg:12} {p}")
        print("\nЦе єдиний показ. Далі хеш, і відновити пароль неможливо —")
        print("лише задати новий цією ж командою.")
        return 0

    login = argv[0]
    with Session(engine) as s:
        if not s.exec(select(User).where(User.login == login)).first():
            print(f"немає користувача з логіном {login!r}", file=sys.stderr)
            return 2

    p1 = getpass.getpass(f"новий пароль для {login}: ")
    if len(p1) < 8:
        print("закоротко — щонайменше 8 символів", file=sys.stderr)
        return 2
    if p1 != getpass.getpass("ще раз: "):
        print("не збіглись", file=sys.stderr)
        return 2

    zadaty(login, p1)
    print(f"пароль для {login} змінено")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

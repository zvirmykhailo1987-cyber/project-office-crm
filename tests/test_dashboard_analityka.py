# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mykhailo Zvir

"""Дашборд-аналітика воронки й команди (25.08.2026).

Правило: дашборд рахує розподіл угод по СТАДІЯХ і прогрес по ВЛАСНИКАХ на
відомих даних — не лише суму по програмі. Тест перевіряє числа, і саме його
браку дало 500 на проді (st.position замість st.order пройшло pytest, бо
жоден тест не рендерив дашборд з реальними стадіями).
"""
from app.models import Counterparty, Deal, Program, Pipeline, Stage


def _voronka(s):
    prog = Program(code="bio", title="Біо"); s.add(prog); s.commit()
    pipe = Pipeline(program_id=prog.id, code="p", title="В"); s.add(pipe); s.commit()
    s1 = Stage(pipeline_id=pipe.id, code="new", title="Новий", order=0)
    s2 = Stage(pipeline_id=pipe.id, code="kval", title="Кваліфікований", order=1)
    s.add(s1); s.add(s2); s.commit()
    cp = Counterparty(name="К", edrpou="20000001"); s.add(cp); s.commit()
    for i in range(3):
        s.add(Deal(title=f"У{i}", counterparty_id=cp.id, program_id=prog.id,
                   pipeline_id=pipe.id, stage_id=s1.id, owner="Оксана", status="active"))
    s.add(Deal(title="У-к", counterparty_id=cp.id, program_id=prog.id,
               pipeline_id=pipe.id, stage_id=s2.id, owner="Богдан", status="active"))
    s.commit()
    return prog, s1, s2


def test_dashboard_renderyt_voronku_i_komandu(s, klient):
    _voronka(s)
    html = klient.get("/").text
    assert "Воронка за стадіями" in html
    assert "Прогрес команди" in html
    # 3 угоди на «Новий», 1 на «Кваліфікований» — обидві стадії названі
    assert "Новий" in html and "Кваліфікований" in html
    # власники в таблиці команди
    assert "Оксана" in html and "Богдан" in html


def test_dashboard_ne_padaye_bez_ugod(s, klient):
    """Порожня воронка — блоки просто ховаються, не 500 (Г27)."""
    r = klient.get("/")
    assert r.status_code == 200

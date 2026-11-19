"""Воронки як дані + гейти (ТЗ, Додаток Б; критерій приймання Ф2)."""
import pytest
from sqlmodel import select

from app.models import Deal, Document, Pipeline, Program, Stage, utcnow
from app.seed_pipelines import seed
from app.services import gates


@pytest.fixture()
def seeded(s):
    seed(s)
    return s


def _pipe(s, code):
    prog = s.exec(select(Program).where(Program.code == code)).one()
    return s.exec(select(Pipeline).where(Pipeline.program_id == prog.id)).one(), prog


def _stages(s, pipe_id):
    return list(s.exec(select(Stage).where(Stage.pipeline_id == pipe_id)
                       .order_by(Stage.order)))


def _deal(s, code="elevator", at=0):
    pipe, prog = _pipe(s, code)
    st = _stages(s, pipe.id)
    d = Deal(program_id=prog.id, pipeline_id=pipe.id, stage_id=st[at].id, title="тест")
    s.add(d)
    s.commit()
    return d, st


def _doc(s, deal_id, kind, *, final=True, signed=True):
    s.add(Document(deal_id=deal_id, kind=kind, is_final=final,
                   signed_at=utcnow().date() if signed else None))
    s.commit()


# ── структура воронок ────────────────────────────────────────────────────────

def test_biogaz_has_10_stages(seeded):
    pipe, _ = _pipe(seeded, "biogaz")
    st = _stages(seeded, pipe.id)
    assert len(st) == 10
    assert [x.title for x in st][:3] == ["Новий", "Дзвонити", "Контакт встановлено"]


def test_elevator_has_9_stages_ending_with_two_signatures(seeded):
    pipe, _ = _pipe(seeded, "elevator")
    st = _stages(seeded, pipe.id)
    assert len(st) == 9
    assert st[-1].title.startswith("8.")
    assert st[-1].is_terminal and st[-1].is_won


def test_seed_ne_chipaye_napovnenu_bazu(seeded):
    """Посів дає СТАРТОВИЙ стан і замовкає.

    Раніше `seed()` виконувався на кожному старті застосунку й перезаписував
    кожне поле стадії значенням із коду. Щойно зʼявився екран редагування
    воронок, це означало б: людина міняє назву стадії — після перезапуску
    зміна зникає, тихо й без сліду в журналі. Тому тепер посів на непорожній
    базі не робить нічого й прямо про це звітує.
    """
    again = seed(seeded)
    assert again["propushcheno"] == 1
    assert (again["programs"], again["pipelines"], again["stages"],
            again["updated"]) == (0, 0, 0, 0)


def test_pravka_stadiyi_perezhyvaye_posiv(seeded):
    """Головна перевірка всієї зміни: правка НЕ затирається посівом."""
    st = seeded.exec(select(Stage).where(Stage.code == "dzvonyty")).first()
    st.title = "Мій власний підпис стадії"
    st.default_probability = 42
    seeded.add(st)
    seeded.commit()

    seed(seeded)                      # так, як це станеться на наступному старті

    seeded.refresh(st)
    assert st.title == "Мій власний підпис стадії"
    assert st.default_probability == 42


def test_elevator_gates_match_funnel(seeded):
    pipe, _ = _pipe(seeded, "elevator")
    req = {st.code: st.required_list for st in _stages(seeded, pipe.id)}
    assert req["s1"] == ["INV-05"]     # Контракт #1
    assert req["s4"] == ["COM-06"]     # MOU громади — головне горло
    assert req["s5"] == ["FARM-02"]    # LOI фермерів на обсяг
    assert req["s7"] == ["COM-07"]
    assert req["s8"] == ["COM-09"]     # Контракт #2


# ── поведінка гейтів ─────────────────────────────────────────────────────────

def test_gate_blocks_forward_without_signed_document(seeded):
    d, st = _deal(seeded, "elevator", at=1)          # на стадії 1 (мандат)
    res = gates.check(seeded, d, st[2])
    assert not res.allowed and res.missing == ["INV-05"]


def test_gate_opens_after_final_signed_document(seeded):
    d, st = _deal(seeded, "elevator", at=1)
    _doc(seeded, d.id, "INV-05")
    assert gates.check(seeded, d, st[2]).allowed


def test_draft_document_does_not_open_gate(seeded):
    d, st = _deal(seeded, "elevator", at=1)
    _doc(seeded, d.id, "INV-05", final=False)
    assert not gates.check(seeded, d, st[2]).allowed


def test_unsigned_final_document_does_not_open_gate(seeded):
    d, st = _deal(seeded, "elevator", at=1)
    _doc(seeded, d.id, "INV-05", signed=False)
    assert not gates.check(seeded, d, st[2]).allowed


def test_gate_cannot_be_bypassed_by_skipping_stages(seeded):
    """Стрибок з 1 на 8 має вимагати ВСІ гейти проміжних стадій — і власний
    документ виграшної стадії «8. Два підписи» (COM-09, Контракт #2)."""
    d, st = _deal(seeded, "elevator", at=1)
    res = gates.check(seeded, d, st[8])
    assert set(res.missing) == {"INV-05", "COM-06", "FARM-02", "COM-07", "COM-09"}


def test_backward_move_always_allowed(seeded):
    d, st = _deal(seeded, "elevator", at=4)
    res = gates.check(seeded, d, st[2])
    assert res.allowed and res.is_backward


def test_offramp_to_paused_always_allowed(seeded):
    """Вихід без штрафу з будь-якої стадії — funnel.md п.3."""
    d, st = _deal(seeded, "biogaz", at=5)
    vidkladeno = [x for x in st if x.code == "vidkladeno"][0]
    assert gates.check(seeded, d, vidkladeno).allowed


def test_biogaz_kp_stage_requires_document(seeded):
    d, st = _deal(seeded, "biogaz", at=5)             # «КП надіслано»
    assert not gates.check(seeded, d, st[6]).allowed
    _doc(seeded, d.id, "KP")
    assert gates.check(seeded, d, st[6]).allowed


def test_document_of_other_deal_does_not_open_gate(seeded):
    d1, st = _deal(seeded, "elevator", at=1)
    d2, _ = _deal(seeded, "elevator", at=1)
    _doc(seeded, d2.id, "INV-05")
    assert not gates.check(seeded, d1, st[2]).allowed


# ── стан угоди ───────────────────────────────────────────────────────────────

def test_stale_flag_after_21_days(seeded):
    from datetime import timedelta
    d, _ = _deal(seeded, "biogaz", at=1)
    d.stage_entered_at = utcnow() - timedelta(days=22)
    assert d.is_stale
    d.last_contacted_at = utcnow()
    assert not d.is_stale


def test_overdue_next_action(seeded):
    from datetime import date, timedelta
    d, _ = _deal(seeded, "biogaz", at=1)
    d.next_action_date = date.today() - timedelta(days=1)
    assert d.action_overdue
    d.next_action_date = date.today() + timedelta(days=1)
    assert not d.action_overdue

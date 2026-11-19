"""Ролі, дозволи, Tier3-гейт (ТЗ §2, §9)."""
import pytest
from sqlmodel import select
from fastapi import HTTPException

from app import audit
from app.models import (
    CAP_ADMIN,
    CAP_DATA_SERVICE,
    CAP_DELETE,
    CAP_SEND,
    ROLES,
    User,
)


def test_four_roles_exist():
    assert set(ROLES) == {"rozrobnyk", "perehovorschyk_dok",
                          "perehovory_kontakt", "zapyty_lysty"}


def test_only_marko_administers():
    marko = User(name="Марко", role="rozrobnyk")
    assert marko.can(CAP_ADMIN) and marko.can(CAP_DATA_SERVICE)
    assert marko.can(CAP_DELETE)
    for role in ("perehovorschyk_dok", "perehovory_kontakt", "zapyty_lysty"):
        u = User(name="хтось", role=role)
        assert not u.can(CAP_ADMIN)
        assert not u.can(CAP_DELETE)
        assert not u.can(CAP_DATA_SERVICE)


def test_halyna_sends_others_do_not():
    assert User(name="Галина", role="zapyty_lysty").can(CAP_SEND)
    assert not User(name="Оксана", role="perehovory_kontakt").can(CAP_SEND)


def test_bohdan_edits_any_document():
    assert User(name="Богдан", role="perehovorschyk_dok").can("doc_any")
    assert not User(name="Оксана", role="perehovory_kontakt").can("doc_any")


def test_tier3_requires_confirmation():
    with pytest.raises(HTTPException) as e:
        audit.require_confirm("entity.delete", confirmed=False)
    assert e.value.status_code == 428
    audit.require_confirm("entity.delete", confirmed=True)   # не кидає


def test_audit_writes_entry(s):
    audit.log(s, "Марко", "counterparty.edit", entity="counterparty", entity_id=1,
              tier=audit.TIER_CHANGE, detail="phone")
    s.commit()
    from app.models import AuditEntry
    e = s.exec(select(AuditEntry)).one()
    assert e.actor == "Марко" and e.tier == 1

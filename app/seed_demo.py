# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mykhailo Zvir

"""Synthetic demo data for OFIS.

Everything is invented: organisations, communities, people, sites, deals and
messages. E-mails use ``*.example`` (RFC 2606), tax codes use the ``99`` prefix,
phones use the never-assigned ``+380 00`` range. The generator is deterministic
(fixed seed) and idempotent: a second run changes nothing.

    python -m app.seed_demo            # fill an empty database
    python -m app.seed_demo --reset    # drop all tables first (demo/dev only)
"""
from __future__ import annotations

import random
import sys
from datetime import timedelta

from sqlmodel import Session, func, select

from . import config
from .models import (Activity, Approval, ChatMessage, ChatRoom, Cluster, ClusterMember, Comment,
                     Counterparty, Deal, DocTemplate, Document, KIMNATA_OFIS, Notification, Person,
                     Pipeline, Program, Setting, Site, Stage, Task, User, utcnow)
from .security import hash_pw

SEED = 2026

DEMO_USERS = {
    "marko": ("Marko Avenir", "rozrobnyk"),
    "bohdan": ("Bohdan Quillan", "perehovorschyk_dok"),
    "oksana": ("Oksana Linvel", "perehovory_kontakt"),
    "halyna": ("Halyna Brookfeld", "zapyty_lysty"),
}


def demo_password(login: str) -> str:
    return f"{login}-demo-2026"


OBLASTS = ["Ternopil oblast", "Lviv oblast", "Khmelnytskyi oblast", "Rivne oblast", "Ivano-Frankivsk oblast"]
TOWNS = ["Zelenyi Hai", "Sosnivka", "Bilyi Kamin", "Verkhivka", "Ozerna", "Krasnopillia", "Lisove",
         "Stavky", "Pidhirne", "Dubrava", "Richytsia", "Kalynivka", "Yablunivka", "Lypovets", "Myrne"]
ORG_A = ["Green", "Golden", "Silver", "River", "Sunny", "Northern", "Oak", "Maple", "Clear", "Prairie",
         "Valley", "Hill", "Meadow", "Amber", "Linden"]
ORG_B = ["Fields", "Harvest", "Dairy", "Grain", "Agro", "Farm", "Poultry", "Orchard", "Milk", "Seeds"]
ORG_C = ["LLC", "Farm", "Cooperative", "Agroholding", "Enterprise"]
FIRST = ["Andrii", "Olena", "Taras", "Iryna-Demo", "Serhii", "Nadiia", "Petro", "Liudmyla", "Yurii",
         "Kateryna", "Roman", "Sofiia", "Vasyl", "Daryna", "Ihor", "Marta"]
LAST = ["Demo-Koval", "Sample-Bondar", "Test-Shevchuk", "Demo-Tkachuk", "Sample-Melnyk",
        "Test-Kravets", "Demo-Hrytsenko", "Sample-Savchuk", "Test-Lysenko", "Demo-Rudenko"]
ROLES = ["Director", "Chief agronomist", "Energy manager", "Head of community", "Deputy head"]

# Placeholders use the [bracket] syntax the document generator resolves (app/services/docgen.py).
TEMPLATES = [
    ("COM-01", "Introductory letter to a community", "COM", "elevator", "s3",
     "[date]\n\nDear [Recipient name, title],\n\nWe would like to present a grain storage project "
     "for [Community].\n\nKind regards,\n[Project Office — name / contacts]"),
    ("BIO-01", "Feedstock request to a farm", "BIO", "biogaz", "kontakt",
     "[date]\n\nDear [Recipient name, title],\n\nWe are assessing a biogas plant in your area and ask "
     "for manure and residue volumes for the last year.\n\n[Project Office — name / contacts]"),
    ("INV-01", "Investor teaser cover letter", "INV", "biogaz", "kp",
     "[date]\n\nDear [Recipient name, title],\n\nPlease find attached the preliminary teaser for the "
     "[Community] biogas hub.\n\nPRELIMINARY — figures are estimates.\n\n[Project Office — name / contacts]"),
]

CHAT = [
    ("bohdan", "Good morning! The Zelenyi Hai council meets on Thursday — we need the screening by Wednesday."),
    ("oksana", "I called the head of Sosnivka: interested, asked for the grain volume estimate."),
    ("marko", "Updated the biogas hub scores; two new sites passed the 2 MW threshold."),
    ("halyna", "Letters to three farms are sent, replies due next Friday."),
    ("bohdan", "@marko please prepare the teaser for the River Fields hub."),
    ("marko", "On it, draft by tomorrow."),
]


def _users(s: Session) -> dict[str, User]:
    out: dict[str, User] = {}
    for login, (name, role) in DEMO_USERS.items():
        u = s.exec(select(User).where(User.login == login)).first() or User(login=login, name=name, role=role)
        u.name, u.role, u.active = name, role, True
        if not u.password_hash:
            u.password_hash = hash_pw(demo_password(login))
        u.email = f"{login}@ofis.example"
        s.add(u)
        out[login] = u
    s.commit()
    for u in out.values():
        s.refresh(u)
    return out


def _phone(rnd: random.Random) -> str:
    return f"+38000{rnd.randint(1000000, 9999999)}"


def seed(s: Session) -> dict:
    from .fts import fts_upsert
    from .services import scoring

    if config.IS_PROD:
        raise RuntimeError("seed_demo refuses to run in production")
    rnd = random.Random(SEED)  # noqa: S311 — deterministic demo data, not security  # nosec B311
    today = utcnow().date()
    # A database with any password-bearing account, counterparty, deal or document is never touched: a production
    # image started without OFIS_ENV=production must not get the published demo passwords.
    if (s.exec(select(Counterparty)).first() or s.exec(select(Deal)).first()
            or s.exec(select(Document)).first()
            or s.exec(select(User).where(func.length(User.password_hash) > 0)).first()):
        return {"skipped": True}
    users = _users(s)
    made: dict[str, int] = {}

    # ── organisations, communities, people ──────────────────────────────────
    orgs: list[Counterparty] = []
    used: set[str] = set()
    while len(orgs) < 48:
        name = f"{rnd.choice(ORG_A)} {rnd.choice(ORG_B)} {rnd.choice(ORG_C)}"
        if name in used:
            continue
        used.add(name)
        slug = name.lower().replace(" ", "-")
        cp = Counterparty(name=name, legal_name=name, edrpou=f"99{len(orgs) + 100001:06d}",
                          oblast=rnd.choice(OBLASTS), city=rnd.choice(TOWNS),
                          phone=_phone(rnd), email=f"office@{slug}.example", website=f"https://{slug}.example",
                          kved="01.41" if "Dairy" in name or "Milk" in name else "01.11",
                          category="pidpryyemstvo", roles="fermer" if len(orgs) % 3 else "postachalnyk_syrovyny",
                          source="demo", data_status="verified" if len(orgs) % 4 else "needs_check",
                          financially_active=len(orgs) % 5 != 0, created_by="seed_demo")
        orgs.append(cp)
    hromady = [Counterparty(name=f"{t} community", oblast=OBLASTS[i % len(OBLASTS)], city=t, hromada=t,
                            edrpou=f"99{200001 + i:06d}", email=f"rada@{t.lower().replace(' ', '-')}.example",
                            phone=_phone(rnd), category="hromada", roles="gromada", source="demo",
                            data_status="verified", created_by="seed_demo")
               for i, t in enumerate(TOWNS[:10])]
    for cp in orgs + hromady:
        s.add(cp)
    s.commit()
    for cp in orgs + hromady:
        s.refresh(cp)
        persons = []
        for k in range(1 + (cp.id % 2)):
            first, last = rnd.choice(FIRST), rnd.choice(LAST)
            p = Person(counterparty_id=cp.id, name=f"{first} {last}", role=ROLES[(cp.id + k) % len(ROLES)],
                       email=f"{first.lower()}.{last.lower()}@{cp.email.split('@')[1]}",
                       phone=_phone(rnd), is_primary=k == 0, source="demo")
            s.add(p)
            persons.append(p)
        s.flush()
        fts_upsert(s, cp, persons)
    s.commit()
    made["counterparties"] = len(orgs) + len(hromady)

    progs = {p.code: p for p in s.exec(select(Program))}
    # ── biogas sites + hubs ──────────────────────────────────────────────────
    sites: list[Site] = []
    for i, cp in enumerate(orgs[:36]):
        kw = rnd.choice([350, 600, 900, 1200, 1800, 2400, 3000])
        site = Site(site_type="biogaz_object", program_id=progs["biogaz"].id, counterparty_id=cp.id,
                    name=cp.name, oblast=cp.oblast, city=cp.city, hromada=cp.city,
                    lat=round(48.7 + rnd.random() * 1.7, 5), lon=round(24.2 + rnd.random() * 2.6, 5),
                    geo_precision="settlement", waste_category="manure" if i % 2 else "crop residues",
                    waste_type="cattle manure" if i % 2 else "maize silage",
                    est_feedstock_t_year=float(kw * 28), est_biogas_m3_year=float(kw * 4200),
                    est_capacity_kw_el=float(kw), vrh_head=kw if i % 2 else None,
                    dist_road_km=round(rnd.uniform(0.3, 8), 1), dist_power_km=round(rnd.uniform(0.5, 15), 1),
                    dist_gas_km=round(rnd.uniform(1, 25), 1), infra_score=round(rnd.uniform(0.3, 0.95), 2),
                    sector="≥2МВт" if kw >= 2000 else "", source="demo", created_by="seed_demo")
        s.add(site)
        sites.append(site)
    s.commit()
    for site in sites:
        s.refresh(site)
        res = scoring.score_site(site, has_phone=True, has_email=True)
        if res:
            scoring.save(s, site.id, res)
    hubs = []
    for h in range(4):
        members = sites[h * 6:(h + 1) * 6]
        anchor = members[0]
        total_kw = sum(m.est_capacity_kw_el or 0 for m in members)
        hub = Cluster(name=f"{anchor.city} biogas hub", site_code=f"HUB-{h + 1:02d}", type="CLUSTER_BIOMETHANE",
                      type_label="Біометан", oblast=anchor.oblast, total_kw=total_kw, total_mw=round(total_kw / 1000, 2),
                      n_donors=len(members), radius_km=20, waste_mix="manure, maize silage",
                      anchor_name=anchor.name, capex_eur=round(total_kw * 3100, -3),
                      revenue_eur_y=round(total_kw * 820, -3), payback_y=round(rnd.uniform(5.5, 8.5), 1),
                      irr_pct=round(rnd.uniform(11, 19), 1), lat=anchor.lat, lon=anchor.lon,
                      stage=["Новий", "Попередня оцінка", "Pre-FS", "Девелопмент"][h], owner=users["marko"].name,
                      notes="PRELIMINARY — synthetic estimate for the demo.", source="demo")
        s.add(hub)
        s.flush()
        for m in members:
            m.cluster_id, m.is_anchor = hub.id, m.id == anchor.id
            s.add(ClusterMember(cluster_id=hub.id, site_id=m.id, edrpou=""))
            s.add(m)
        hubs.append(hub)
    s.commit()
    made["sites"], made["hubs"] = len(sites), len(hubs)

    # ── communities screened for the grain-elevator programme ────────────────
    for i, h in enumerate(hromady):
        site = Site(site_type="hromada_lokatsiya", program_id=progs["elevator"].id, counterparty_id=h.id,
                    name=h.name, oblast=h.oblast, hromada=h.city, city=h.city,
                    lat=round(49.0 + rnd.random() * 1.3, 5), lon=round(24.6 + rnd.random() * 2.2, 5),
                    geo_precision="hromada", c1_grain_t_yr=float(rnd.randrange(40_000, 220_000, 5_000)),
                    c2_underserved_ha=float(rnd.randrange(4_000, 18_000, 500)), c3_ind_park=float(i % 2),
                    c4_rail=float(i % 3 != 0), c5_road=1.0, c6_power_mw=float(i % 4 != 1),
                    c7_guarantee_ready=None if i % 5 == 0 else float(i % 2), c8_ahc_ready=float(i % 3 == 0),
                    c9_transparency=1.0, source="demo", created_by="seed_demo")
        s.add(site)
        s.flush()
        res = scoring.score_site(site)
        if res:
            scoring.save(s, site.id, res)
        if i % 2 == 0:      # candidates also carry the external ranking — a separate method
            scoring.save(s, site.id, scoring.elevator_ranking(float(40 + 3 * i), "demo candidate ranking"))
    s.commit()

    # ── deals in both pipelines ─────────────────────────────────────────────
    deals: list[Deal] = []
    owners = [users["bohdan"], users["oksana"], users["marko"]]
    for code, partners in (("biogaz", orgs[:14]), ("elevator", hromady[:8])):
        prog = progs[code]
        pipe = s.exec(select(Pipeline).where(Pipeline.program_id == prog.id)).first()
        stages = list(s.exec(select(Stage).where(Stage.pipeline_id == pipe.id).order_by(Stage.order)))
        open_stages = [st for st in stages if not st.is_terminal] or stages
        for i, cp in enumerate(partners):
            st = open_stages[i % len(open_stages)]
            d = Deal(program_id=prog.id, pipeline_id=pipe.id, stage_id=st.id,
                     title=(f"Biogas plant {cp.city}" if code == "biogaz" else f"Grain elevator {cp.city}"),
                     counterparty_id=cp.id, owner=owners[i % 3].name,
                     value=float(rnd.randrange(400_000, 6_000_000, 50_000)), currency="EUR",
                     probability=st.default_probability or 0,
                     next_action_date=today + timedelta(days=rnd.randint(-3, 14)),
                     next_action_note=rnd.choice(["Call the director", "Send the term sheet",
                                                  "Site visit", "Prepare screening", "Follow up on letter"]),
                     last_contacted_at=utcnow() - timedelta(days=rnd.randint(0, 20)),
                     stage_entered_at=utcnow() - timedelta(days=rnd.randint(1, 40)),
                     notes="Synthetic demo deal.", created_by="seed_demo")
            s.add(d)
            deals.append(d)
    s.commit()
    for d in deals:
        s.refresh(d)
        for k in range(2):
            s.add(Activity(type=["Дзвінок", "Email", "Зустріч"][(d.id + k) % 3],
                           when=utcnow() - timedelta(days=rnd.randint(1, 30)), author=d.owner,
                           deal_id=d.id, counterparty_id=d.counterparty_id,
                           outcome="Positive" if k else "Requested more data",
                           note="Discussed feedstock volumes and the next step."))
    s.commit()
    made["deals"] = len(deals)

    # ── documents (small generated files) ───────────────────────────────────
    config.DOCS_DIR.mkdir(parents=True, exist_ok=True)
    docs: list[Document] = []
    for i, d in enumerate(deals[:6]):
        fn = f"demo_teaser_{d.id}.md"
        path = config.DOCS_DIR / fn
        body = (f"# {d.title}\n\nPRELIMINARY — synthetic demo document.\n\n"
                f"Estimated value: {d.value:,.0f} EUR\n").encode()
        path.write_bytes(body)
        doc = Document(title=f"Teaser — {d.title}", kind="INV-01", doc_group_uid=f"demo-{d.id}", version=1,
                       filename=fn, stored_path=str(path), size=len(body), deal_id=d.id,
                       counterparty_id=d.counterparty_id,
                       workflow_status=["chernetka", "na_pravtsi", "uzhodzheno"][i % 3], uploaded_by=users["marko"].name, created_by="seed_demo")
        s.add(doc)
        docs.append(doc)
    from .services.docgen import find_placeholders
    for code, title, group, program, stage_code, body in TEMPLATES:
        s.add(DocTemplate(code=code, title=title, group=group, program_code=program, stage_code=stage_code,
                          lang="en", body_md=body, placeholders=",".join(find_placeholders(body)),
                          created_by="seed_demo"))
    for key, value in (("office.contacts", "office@ofis.example"),
                       ("office.signature", "Project Office demo team")):
        st = s.get(Setting, key)
        if st and not st.value:
            st.value = value
            s.add(st)
    s.commit()

    # ── tasks, collaboration, chat ──────────────────────────────────────────
    titles = ["Call the head of the community", "Prepare the screening sheet", "Send the feedstock request",
              "Update the hub teaser", "Check the council decision", "Collect manure volumes",
              "Book a site visit", "Review the term sheet", "Reply to the investor", "Verify the tax code"]
    for i in range(18):
        d = deals[i % len(deals)]
        assignee = list(users.values())[i % 4]
        s.add(Task(title=titles[i % len(titles)], note="Synthetic demo task.", assignee=assignee.name,
                   due_date=today + timedelta(days=rnd.randint(-4, 10)),
                   priority=["high", "normal", "low"][i % 3], status=["open", "in_progress", "done"][i % 3] if i % 5 else "open",
                   source="vid_koleghy" if i % 2 else "manual", deal_id=d.id, created_by=users["bohdan"].name))
    for d in deals[:5]:
        s.add(Comment(entity="deal", entity_id=d.id, author=users["bohdan"].name,
                      text="Let's prioritise this one — strong feedstock base.", kind="rekomendatsiya"))
    if docs:
        s.add(Approval(entity="document", entity_id=docs[0].id, submitted_by=users["marko"].name,
                       note="Teaser ready for review.", status="na_rozglyadi"))
        s.add(Notification(user=users["bohdan"].name, kind="na_zatverdzhennya",
                           text="Teaser submitted for approval", url=f"/dokumenty/{docs[0].id}",
                           entity="document", entity_id=docs[0].id, from_whom=users["marko"].name))
    room = s.exec(select(ChatRoom).where(ChatRoom.code == KIMNATA_OFIS)).first()
    if room:
        room.title = "Office team"
        s.add(room)
        start = utcnow() - timedelta(hours=len(CHAT) * 3)
        for i, (login, text) in enumerate(CHAT):
            m = ChatMessage(room_id=room.id, author=users[login].name, text=text,
                            mentions="marko" if "@marko" in text else "", created_by=users[login].name)
            m.created_at = start + timedelta(hours=i * 3)
            s.add(m)
    s.commit()
    made["tasks"] = 18

    from .models import AuditEntry
    actions = ["deal.stage", "task.create", "task.status", "comment.create", "document.create",
               "deal.next_action", "approval.submit"]
    people = [u.name for u in users.values()]
    for day in range(14):
        for k in range(1 + (day * 7) % 4):
            d = deals[(day + k) % len(deals)]
            s.add(AuditEntry(at=utcnow() - timedelta(days=day, hours=k * 2), actor=people[(day + k) % 4],
                             action=actions[(day + k) % len(actions)], entity="deal", entity_id=d.id,
                             tier=1, detail=d.title[:60]))
    s.commit()
    return made


def main(argv: list[str]) -> int:
    if config.IS_PROD:
        print("seed_demo refuses to run in production", file=sys.stderr)
        return 2
    from sqlmodel import SQLModel

    from .db import engine, init_db
    if "--reset" in argv:
        SQLModel.metadata.drop_all(engine)
        with engine.connect() as c:
            c.exec_driver_sql("DROP TABLE IF EXISTS cp_fts")
            c.commit()
    init_db()
    with Session(engine) as s:
        print("demo data:", seed(s))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

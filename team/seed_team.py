# -*- coding: utf-8 -*-
"""Seed do modulo de Gestao do Time a partir dos dois Excel de origem.

Idempotente (upsert por chave natural). NAO faz drop_all — roda sobre a base
existente do Portal de Consolidacao. Ordem sugerida:
    python seed.py            # base do portal (empresas-ancora, competencias, exemplo)
    python -m team.seed_team  # camada do time (carteira, membros, metas, agenda)
"""
import os
import re
import unicodedata
from datetime import datetime, timedelta, date

import openpyxl

from app import app
from models import db, User, Company, Competency, Submission
from team.models import (TeamMember, CompanyAssignment, Project, Milestone,
                         Activity, ClosingTemplateItem, Indicator,
                         ensure_alert_defaults)
from team import engine

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# os arquivos de origem ficam na pasta-mae do projeto (Documentos/Claude/...)
XLSX_DIR = os.environ.get(
    "PORTAL_CONTROLADORIA_XLSX",
    os.path.join(os.path.dirname(BASE), "Portal Controladoria"))
CTRL_XLSX = os.path.join(XLSX_DIR, "Controladoria.xlsx")
METAS_XLSX = os.path.join(XLSX_DIR, "Metas Controladoria 2026.xlsx")

TEAM_PASS = "jfsa@2026T"

# codigos canonicos p/ casar com o seed base do portal (empresas-ancora)
CODE_OVERRIDES = {
    "Eldorado": "ELDORADO", "Ambar Geração": "AMBAR_GER",
    "Ambar Distribuição": "AMBAR_DIS", "LHG": "LHG", "Flora": "FLORA",
    "Mgas": "MGAS", "J&F S.A.": "JF_SA", "J&F Urbanismo": "JF_URB",
    "Fluxus": "FLUXUS", "Canal": "CANAL", "Logás": "LOGAS",
    "+55": "MAIS55", "Fazenda Luta": "FAZENDA_LUTA", "Aeronave": "AERONAVE",
    "Araguaia": "ARAGUAIA",
}
# empresas cujo envio dispara o relogio do D+2 (consolidacao do grupo)
ANCHOR_CODES = ["ELDORADO", "AMBAR_GER", "AMBAR_DIS", "LHG", "FLORA",
                "FLUXUS", "CANAL", "MGAS", "LOGAS"]


def slug_code(name):
    if name in CODE_OVERRIDES:
        return CODE_OVERRIDES[name]
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_").upper()
    return s[:40] or "EMP"


# --------------------------------------------------------------------------
# Membros do time (nomes reais das metas + 3 a definir que seguem o senior)
# --------------------------------------------------------------------------
def seed_members():
    defs = [
        # (nome, papel, is_manager, email, segue_senior)
        # Só pessoas reais — nada de "a definir": placeholders ressuscitavam a cada
        # seed e voltavam mesmo depois de excluídos. Cadastre vagas pelo admin.
        ("Fernando Edelson", "gestor", True, "fernando.edelson@jfsa.com.br", False),
        ("Jeferson Bittencourt", "senior", False, "jeferson.bittencourt@jfsa.com.br", False),
        ("Augusto Gobo", "especialista", False, "augusto.gobo@jfsa.com.br", False),
        ("Crysthian Oliveira", "especialista", False, "crysthian.oliveira@jfsa.com.br", False),
        ("Daniel Benedetti", "especialista", False, "daniel.benedetti@jfsa.com.br", False),
    ]
    colors = ["#16324f", "#1d5da8", "#1e8a4e", "#b97509", "#7a5cc0",
              "#0f766e", "#9a3d6b", "#5f6b82"]
    by_name = {}
    senior = None
    for i, (name, role, mgr, email, follows) in enumerate(defs):
        m = TeamMember.query.filter_by(name=name).first()
        if not m:
            m = TeamMember(name=name, active=True)   # active só ao criar
            db.session.add(m)
        m.role_label = role
        m.is_manager = mgr
        m.color = colors[i % len(colors)]
        m.sort_order = i * 10
        # NÃO reativa quem foi inativado de propósito no admin
        # login (controladoria) para quem tem e-mail definido
        if email:
            u = User.query.filter_by(email=email).first()
            if not u:
                u = User(email=email, display_name=name, role="controladoria",
                         must_change_password=True, active=True)
                u.set_password(TEAM_PASS)
                db.session.add(u)
                db.session.flush()
            m.user_id = u.id
        by_name[name] = m
        if role == "senior":
            senior = m
    db.session.flush()
    # os 3 "a definir" seguem o painel do senior
    if senior:
        for name, _r, _m, _e, follows in defs:
            if follows:
                by_name[name].follows_member_id = senior.id
    db.session.commit()
    return by_name


# --------------------------------------------------------------------------
# Empresas + carteira (Controladoria.xlsx -> Planilha1)
# --------------------------------------------------------------------------
def seed_companies_and_assignments(caminho=None):
    """Empresas + carteira da planilha. `caminho`: arquivo enviado pela tela de
    Administracao (se omitido, usa a pasta padrao). Nao mexe no responsavel."""
    caminho = caminho or CTRL_XLSX
    if not os.path.exists(caminho):
        print(f"  [aviso] {caminho} nao encontrado — pulando carteira.")
        return 0, 0
    wb = openpyxl.load_workbook(caminho, data_only=True)
    ws = wb["Planilha1"]
    n_comp = n_assign = 0
    for row in ws.iter_rows(min_row=2, values_only=True):
        name = row[0]
        if not name or str(name).strip() in ("", "Total Geral"):
            continue
        name = str(name).strip()
        flow = (row[1] or "").strip() if row[1] else None
        segment = (row[2] or "").strip() if row[2] else None
        resp = (row[3] or "").strip() if row[3] else None
        deliverables = []
        for idx, label in ((4, "Painel"), (5, "Endividamento"),
                           (6, "Consolidação"), (7, "Auxiliares")):
            if row[idx] and str(row[idx]).strip().upper() == "X":
                deliverables.append(label)
        load_real = _num(row[8]); load_ideal = _num(row[9]); production = _num(row[10])
        seat = _num(row[11])
        code = slug_code(name)

        c = Company.query.filter_by(code=code).first()
        if not c:
            c = Company(code=code, name=name, canonical_label=name, active=True)
            db.session.add(c)
            db.session.flush()
            n_comp += 1
        # atualiza carteira (assignment por empresa)
        a = CompanyAssignment.query.filter_by(company_id=c.id).first()
        if not a:
            a = CompanyAssignment(company_id=c.id)
            db.session.add(a)
            n_assign += 1
        a.seat = seat
        a.segment = segment
        a.flow = flow
        a.responsibility = resp
        a.deliverables = deliverables
        a.load_real = load_real
        a.load_ideal = load_ideal or 4
        a.production = production
    db.session.commit()
    return n_comp, n_assign


# --------------------------------------------------------------------------
# Projetos
# --------------------------------------------------------------------------
def seed_projects(members):
    jeferson = members.get("Jeferson Bittencourt")
    crysthian = members.get("Crysthian Oliveira")
    daniel = members.get("Daniel Benedetti")
    fernando = members.get("Fernando Edelson")
    defs = [
        # (nome, code, owner, status, confidential, alvo)
        ("Implantação EPM (FCCS/PBCS)", "EPM", crysthian, "ativo", False, date(2026, 10, 31)),
        ("Projeto BI - Qlik", "QLIK", crysthian, "ativo", False, date(2026, 9, 30)),
        ("Reestruturação +55", "MAIS55", daniel, "ativo", False, date(2026, 12, 31)),
        ("Portal de criação de releases", "RELEASE", fernando, "ativo", False, date(2026, 8, 31)),
        ("Revisão da estrutura de fechamento", "FECHAMENTO", crysthian, "ativo", False, date(2026, 9, 30)),
        ("Implantação empresa Aeronave", "AERONAVE", daniel, "planejado", False, None),
        ("Projeto Bond", "BOND", jeferson, "ativo", True, None),
        ("Estruturação de custeio +55", "CUSTEIO55", daniel, "ativo", False, date(2026, 12, 31)),
    ]
    n = 0
    for name, code, owner, status, conf, alvo in defs:
        p = Project.query.filter_by(name=name).first()
        if not p:
            p = Project(name=name)
            db.session.add(p)
            n += 1
        p.code = code
        p.owner_member_id = owner.id if owner else None
        p.status = status
        p.confidential = conf
        p.target_date = alvo
        p.start_date = p.start_date or date(2026, 1, 15)
    db.session.commit()
    return n


# --------------------------------------------------------------------------
# Indicadores / metas (Metas Controladoria 2026.xlsx)
# --------------------------------------------------------------------------
def _target_type(label):
    if not label:
        return "manual"
    s = str(label).strip().upper()
    if s.startswith("D+") or s.startswith("D-") or s.isdigit():
        return "prazo_du"
    if re.match(r"[A-Z]{3}/?\d", s) or s in ("DEZ", "AGO", "SET", "OUT", "TBD"):
        return "data_marco"
    return "manual"


def seed_indicators(members, caminho=None):
    """Metas por pessoa (uma aba por pessoa). `caminho`: arquivo enviado pela tela."""
    caminho = caminho or METAS_XLSX
    if not os.path.exists(caminho):
        print(f"  [aviso] {caminho} nao encontrado — pulando indicadores.")
        return 0
    name_map = {
        "Augusto": "Augusto Gobo", "Crysthian": "Crysthian Oliveira",
        "Daniel": "Daniel Benedetti", "Jeferson": "Jeferson Bittencourt",
        "Fernando": "Fernando Edelson",
    }
    wb = openpyxl.load_workbook(caminho, data_only=True)
    n = 0
    for sheet in wb.sheetnames:
        member = members.get(name_map.get(sheet))
        if not member:
            continue
        ws = wb[sheet]
        # metas: procurar linhas com 'RESULTADO' na col D(4), num na col E(5),
        # titulo na col F(6), indicador na col M(13)
        rationais = {}
        # coleta racional (META 1..N) das linhas de consideracoes
        for row in ws.iter_rows(values_only=True):
            for ci, cell in enumerate(row):
                if isinstance(cell, str) and re.match(r"^META\s+\d+$", cell.strip()):
                    seqn = int(re.findall(r"\d+", cell)[0])
                    # racional costuma estar mais a direita na mesma linha
                    tail = [c for c in row[ci + 1:] if isinstance(c, str) and c.strip()]
                    if tail:
                        rationais[seqn] = tail[-1].strip()
        for row in ws.iter_rows(values_only=True):
            dim = row[3] if len(row) > 3 else None
            seqn = row[4] if len(row) > 4 else None
            title = row[5] if len(row) > 5 else None
            target = row[12] if len(row) > 12 else None
            if (isinstance(dim, str) and dim.strip() == "RESULTADO"
                    and isinstance(seqn, int) and title):
                title = str(title).strip()
                tlabel = str(target).strip() if target is not None else None
                exists = Indicator.query.filter_by(member_id=member.id, seq=seqn,
                                                   title=title).first()
                if exists:
                    continue
                ind = Indicator(
                    member_id=member.id, seq=seqn, title=title,
                    dimension="RESULTADO", target_label=tlabel,
                    target_type=_target_type(tlabel),
                    rational=rationais.get(seqn), year=2026)
                db.session.add(ind)
                n += 1
    db.session.commit()
    return n


def vincula_catalogo():
    """Liga cada indicador sem definicao ao catalogo (reaproveita a de mesmo nome,
    senao cria). Deixa o Catalogo de indicadores completo apos a importacao."""
    from team.models import IndicatorDef
    novas = 0
    for i in Indicator.query.filter(Indicator.indicator_def_id.is_(None)).all():
        d = IndicatorDef.query.filter(
            db.func.lower(IndicatorDef.title) == (i.title or "").lower()).first()
        if not d:
            d = IndicatorDef(title=i.title, dimension=i.dimension or "RESULTADO",
                             target_type=i.target_type or "manual", rational=i.rational)
            db.session.add(d)
            db.session.flush()
            novas += 1
        i.indicator_def_id = d.id
    db.session.commit()
    return novas


# --------------------------------------------------------------------------
# Agenda de fechamento (template)
# --------------------------------------------------------------------------
def seed_closing_template(members):
    m = members
    defs = [
        # (titulo, owner, base, offset, insumo_codes, auto, deliverable, prio)
        ("Fechamento mensal — custos J&F Holding", m.get("Crysthian Oliveira"),
         "deadline", 0, [], False, "Custos", "alta"),
        ("Consolidação de resultado do grupo", m.get("Daniel Benedetti"),
         "insumo", 2, ANCHOR_CODES, False, "Consolidação", "critica"),
        ("Consolidação dos fluxos de endividamento", m.get("Augusto Gobo"),
         "insumo", 2, ANCHOR_CODES, False, "Endividamento", "alta"),
        ("Fechamento de custos +55/Araguaia/Luta/Aeronave", m.get("Daniel Benedetti"),
         "insumo", 2, ["MAIS55", "ARAGUAIA", "FAZENDA_LUTA", "AERONAVE"], False, "Custos", "alta"),
        ("Emissão dos releases de resultado gerencial", m.get("Jeferson Bittencourt"),
         "insumo", 2, ANCHOR_CODES, False, "Release", "alta"),
        ("Fechamento +55 e Urbanismo (painéis)", m.get("Augusto Gobo"),
         "insumo", 2, ["MAIS55", "JF_URB"], False, "Painel", "media"),
        ("Controle de backups para emissão do Bond", m.get("Jeferson Bittencourt"),
         "fixed_bd", 3, [], False, "Bond", "media"),
    ]
    n = 0
    for i, (title, owner, base, off, codes, auto, deliv, prio) in enumerate(defs):
        it = ClosingTemplateItem.query.filter_by(title=title).first()
        if not it:
            it = ClosingTemplateItem(title=title)
            db.session.add(it)
            n += 1
        it.kind = "fechamento"
        it.member_id = owner.id if owner else None
        it.due_base = base
        it.due_offset = off
        it.insumo_codes = codes
        it.auto_metric = auto
        it.deliverable = deliv
        it.priority = prio
        it.sort_order = i * 10
        it.active = True
    # itens auto-metrica: "Receber e validar envio — <ancora>" (conclui na submissao)
    for j, code in enumerate(ANCHOR_CODES):
        c = Company.query.filter_by(code=code).first()
        if not c:
            continue
        title = f"Receber e validar envio — {c.name}"
        it = ClosingTemplateItem.query.filter_by(title=title).first()
        if not it:
            it = ClosingTemplateItem(title=title)
            db.session.add(it)
            n += 1
        it.kind = "recorrente"
        it.company_id = c.id
        it.member_id = None
        it.due_base = "deadline"
        it.due_offset = -1
        it.auto_metric = True
        it.deliverable = "Envio"
        it.priority = "media"
        it.sort_order = 200 + j
        it.active = True
    db.session.commit()
    return n


# --------------------------------------------------------------------------
# Competencias (garante base) + submissoes-ancora p/ provar o D+2
# --------------------------------------------------------------------------
def ensure_competencies():
    from engine.calendar_br import deadline_for_competency
    if Competency.query.count() == 0:
        for mo in range(1, 7):
            dl = deadline_for_competency(2026, mo, nth=5)
            st = "fechada" if mo < 6 else "aberta"
            db.session.add(Competency(year=2026, month=mo, deadline=dl, status=st,
                                      closed_at=datetime.utcnow() if st == "fechada" else None))
        db.session.commit()
    return app._current_competency()


def seed_anchor_submissions(comp):
    """Cria submissoes aceitas das ancoras (se ainda nao houver) para que o
    relogio do D+2 resolva a partir de dados reais. Datas escalonadas."""
    if not comp:
        return 0
    n = 0
    base_day = date(comp.year, comp.month, 1)
    # simula chegada ao longo dos primeiros dias uteis do mes seguinte
    arrival0 = engine.add_business_days(comp.deadline, -3) if comp.deadline else base_day
    for i, code in enumerate(ANCHOR_CODES):
        c = Company.query.filter_by(code=code).first()
        if not c:
            continue
        exists = Submission.query.filter_by(company_id=c.id, competency_id=comp.id).first()
        if exists:
            continue
        when = engine.add_business_days(arrival0, i // 3)  # 3 por dia util
        sub = Submission(company_id=c.id, competency_id=comp.id, version=1,
                         filename=f"{code}_seed.xlsm", stored_path=None,
                         status="aprovado", n_errors=0, n_warnings=0,
                         submitted_at=datetime.combine(when, datetime.min.time()).replace(hour=10),
                         is_current=True)
        db.session.add(sub)
        n += 1
    db.session.commit()
    return n


# --------------------------------------------------------------------------
# Orquestracao
# --------------------------------------------------------------------------
def _num(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return 0


def seed(with_anchor_subs=True, generate=True):
    with app.app_context():
        db.create_all()
        ensure_alert_defaults()
        members = seed_members()
        nc, na = seed_companies_and_assignments()
        npj = seed_projects(members)
        ni = seed_indicators(members)
        nt = seed_closing_template(members)
        comp = ensure_competencies()
        nsub = seed_anchor_submissions(comp) if with_anchor_subs else 0

        created = 0
        if generate and comp:
            created = engine.generate_closing_activities(comp)[0]
            engine.refresh_provisional_due_dates(comp)
            engine.resolve_auto_metrics(comp)

        print("\n=== SEED GESTÃO DO TIME ===")
        print(f"  Membros: {TeamMember.query.count()}  (login: "
              f"{TeamMember.query.filter(TeamMember.user_id.isnot(None)).count()})")
        print(f"  Empresas +{nc} (total {Company.query.count()})  "
              f"Carteira +{na} (total {CompanyAssignment.query.count()})")
        print(f"  Projetos +{npj} (total {Project.query.count()})")
        print(f"  Indicadores +{ni} (total {Indicator.query.count()})")
        print(f"  Itens de template +{nt} (total {ClosingTemplateItem.query.count()})")
        print(f"  Competência aberta: {comp.label if comp else '—'}")
        print(f"  Submissões-âncora criadas: {nsub}")
        print(f"  Atividades de fechamento geradas: {created} "
              f"(total {Activity.query.count()})")
        ready, rec, tot = engine.insumo_ready_date(comp, ANCHOR_CODES) if comp else (None, 0, 0)
        print(f"  Insumo âncora: {rec}/{tot} recebidas — "
              f"pronto em {ready.strftime('%d/%m/%Y') if ready else '(aguardando)'}")
        print("\n  Login do time (senha inicial jfsa@2026T):")
        for m in TeamMember.query.filter(TeamMember.user_id.isnot(None)).all():
            print(f"    {m.user.email}  ({m.name})")


if __name__ == "__main__":
    seed()

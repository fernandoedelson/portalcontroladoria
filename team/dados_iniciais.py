# -*- coding: utf-8 -*-
"""Cadastra os dados iniciais (paineis, catalogo, indicadores e carteira).

O arquivo `dados_iniciais/paineis_indicadores.json` foi recortado do app
original. Na subida do portal ele e aplicado UMA vez por versao:
- so ACRESCENTA o que falta, casando por chaves naturais (nome do painel/dono,
  titulo do indicador, codigo da empresa) — os IDs do banco nao importam;
- nunca apaga nem sobrescreve o que ja existe (edicoes feitas no portal ficam);
- se algum dono de painel ainda nao existir, nao marca como aplicado e tenta
  de novo na proxima subida.
Desligavel com a variavel PORTAL_SEM_DADOS_INICIAIS=1.
"""
import json
import os

from models import db, Company, get_setting, set_setting

ARQUIVO = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "dados_iniciais", "paineis_indicadores.json")
CHAVE = "dados_iniciais_versao"


def aplica(caminho=ARQUIVO, forcar=False):
    """Retorna um resumo (dict) do que foi cadastrado, ou None se nada a fazer."""
    if os.environ.get("PORTAL_SEM_DADOS_INICIAIS") == "1" or not os.path.exists(caminho):
        return None
    with open(caminho, encoding="utf-8") as fh:
        d = json.load(fh)
    versao = d.get("versao")
    if not forcar and get_setting(CHAVE) == versao:
        return None

    from team.models import TeamMember, CompanyAssignment, IndicatorDef, Indicator
    from team.models_workflow import Segment, Panel

    membros = {m.name: m for m in TeamMember.query.all()}
    r = {"segmentos": 0, "empresas": 0, "carteira": 0, "paineis": 0,
         "catalogo": 0, "indicadores": 0, "pendentes": 0}

    # ---- segmentos ----
    ja = {s.name for s in Segment.query.all()}
    for s in d.get("segmentos", []):
        if s["name"] not in ja:
            db.session.add(Segment(name=s["name"], sort_order=s.get("sort_order") or 100,
                                   active=bool(s.get("active", 1))))
            r["segmentos"] += 1

    # ---- empresas ----
    for e in d.get("empresas", []):
        if not Company.query.filter_by(code=e["code"]).first():
            db.session.add(Company(code=e["code"], name=e["name"],
                                   canonical_label=e.get("canonical_label") or e["name"],
                                   active=bool(e.get("active", 1))))
            r["empresas"] += 1
    db.session.flush()

    # ---- carteira: so cria a linha da empresa que ainda nao tem ----
    for a in d.get("carteira", []):
        c = Company.query.filter_by(code=a["empresa"]).first()
        if not c or CompanyAssignment.query.filter_by(company_id=c.id).first():
            continue
        resp = membros.get(a.get("responsavel")) if a.get("responsavel") else None
        db.session.add(CompanyAssignment(
            company_id=c.id, member_id=resp.id if resp else None, seat=a.get("seat"),
            segment=a.get("segment"), flow=a.get("flow"), responsibility=a.get("responsibility"),
            deliverables_json=a.get("deliverables_json") or "[]",
            load_real=a.get("load_real") or 0, load_ideal=a.get("load_ideal") or 4,
            production=a.get("production") or 0, note=a.get("note")))
        r["carteira"] += 1

    # ---- paineis (pessoal casa pelo dono; equipe pelo nome) ----
    def acha_painel(nome, tipo, dono):
        if tipo == "pessoal":
            m = membros.get(dono)
            return Panel.query.filter_by(kind="pessoal", owner_member_id=m.id).first() if m else None
        return Panel.query.filter_by(kind=tipo, name=nome).first()

    for p in d.get("paineis", []):
        if acha_painel(p["name"], p["kind"], p.get("dono")):
            continue
        if p["kind"] == "pessoal" and p.get("dono") not in membros:
            r["pendentes"] += 1                      # dono ainda nao cadastrado
            continue
        db.session.add(Panel(
            name=p["name"], kind=p["kind"],
            owner_member_id=membros[p["dono"]].id if p["kind"] == "pessoal" else None,
            color=p.get("color") or "#1d5da8", active=bool(p.get("active", 1)),
            sort_order=p.get("sort_order") or 100))
        r["paineis"] += 1
    db.session.flush()

    # ---- catalogo ----
    def acha_def(titulo):
        return IndicatorDef.query.filter(
            db.func.lower(IndicatorDef.title) == (titulo or "").lower()).first()

    for c in d.get("catalogo", []):
        if acha_def(c["title"]):
            continue
        db.session.add(IndicatorDef(
            title=c["title"], dimension=c.get("dimension") or "RESULTADO",
            target_type=c.get("target_type") or "manual", unit=c.get("unit"),
            rational=c.get("rational"), auto_source=c.get("auto_source"),
            active=bool(c.get("active", 1)), sort_order=c.get("sort_order") or 100))
        r["catalogo"] += 1
    db.session.flush()

    # ---- indicadores (metas de cada painel) ----
    for i in d.get("indicadores", []):
        painel = acha_painel(i.get("painel"), i.get("painel_tipo"), i.get("painel_dono"))
        if not painel:
            r["pendentes"] += 1
            continue
        if Indicator.query.filter_by(panel_id=painel.id, seq=i.get("seq"), title=i["title"]).first():
            continue
        defn = acha_def(i.get("definicao") or i["title"])
        resp = membros.get(i.get("responsavel")) if i.get("responsavel") else None
        db.session.add(Indicator(
            indicator_def_id=defn.id if defn else None, panel_id=painel.id,
            member_id=resp.id if resp else painel.owner_member_id, seq=i.get("seq"),
            title=i["title"], dimension=i.get("dimension") or "RESULTADO",
            target_label=i.get("target_label"), target_type=i.get("target_type") or "manual",
            rational=i.get("rational"), auto_source=i.get("auto_source"),
            year=i.get("year") or 2026, active=bool(i.get("active", 1)),
            sort_order=i.get("sort_order") or 100))
        r["indicadores"] += 1

    db.session.commit()
    if not r["pendentes"]:
        set_setting(CHAVE, versao)                   # completo: nao reaplica
    return r

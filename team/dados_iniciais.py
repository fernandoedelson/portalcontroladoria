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


def ajustes_unicos():
    """Correções de cadastro pedidas pelo usuário, aplicadas UMA vez cada
    (marcadas em Setting) — edições posteriores feitas no portal ficam."""
    from team.models import IndicatorDef, Indicator
    feitos = []
    # 2026-09-10: "todos os indicadores são prazo" (vieram como RESULTADO)
    chave = "ajuste_dimensao_prazo_v1"
    if get_setting(chave) != "ok":
        n = IndicatorDef.query.update({IndicatorDef.dimension: "PRAZO"}, synchronize_session=False)
        n += Indicator.query.update({Indicator.dimension: "PRAZO"}, synchronize_session=False)
        db.session.commit()
        set_setting(chave, "ok")
        feitos.append(f"dimensão PRAZO em {n} registro(s)")
    return feitos


METAS = os.path.join(os.path.dirname(ARQUIVO), "metas_2026.json")


def _norm(s):
    import unicodedata
    s = unicodedata.normalize("NFD", str(s or "")).lower()
    return " ".join("".join(c for c in s if not unicodedata.combining(c)).split())


def _acha_membro(nome, membros):
    """Casa o dono da aba com o membro cadastrado: nome igual; senão todas as
    palavras do cadastro dentro do nome da planilha, com o mesmo primeiro nome
    ("Thiago Forte" ~ "Thiago de Bellis Forte"); senão primeiro nome único."""
    alvo = _norm(nome)
    for m in membros:
        if _norm(m.name) == alvo:
            return m
    pal = set(alvo.split())
    prim = alvo.split()[0] if alvo else ""
    cand = [m for m in membros if _norm(m.name).split()[:1] == [prim]
            and set(_norm(m.name).split()) <= pal]
    if len(cand) == 1:
        return cand[0]
    cand = [m for m in membros if _norm(m.name).split()[:1] == [prim]]
    return cand[0] if len(cand) == 1 else None


def atualiza_metas(caminho=METAS):
    """Aplica a planilha de metas (dados_iniciais/metas_2026.json) aos painéis.

    Por painel, UMA vez (marca em Setting — edições feitas depois no portal ficam):
    - indicador de mesmo nome: atualiza nº, meta, peso, unidade, sentido, escala e racional;
    - indicador novo: cria (ligado ao catálogo, dimensão PRAZO);
    - indicador que saiu da planilha: INATIVA (o histórico de resultados fica guardado).
    Dono não encontrado: não cria ninguém; fica pendente e tenta na próxima subida.
    Retorna lista de textos do que foi feito."""
    if not os.path.exists(caminho):
        return []
    from team.models import TeamMember, IndicatorDef, Indicator
    from team.models_workflow import Panel
    with open(caminho, encoding="utf-8") as fh:
        d = json.load(fh)
    membros = TeamMember.query.all()
    feitos = []
    for p in d.get("paineis", []):
        chave = ("metas26v2:" + _norm(p["dono"]).replace(" ", "_"))[:60]
        if get_setting(chave) == "ok":
            continue
        m = _acha_membro(p["dono"], membros)
        if not m:
            feitos.append(f"PENDENTE: {p['dono']} não está cadastrado no time")
            continue
        painel = Panel.query.filter_by(kind="pessoal", owner_member_id=m.id).first()
        if not painel:
            painel = Panel(name=m.name, kind="pessoal", owner_member_id=m.id,
                           color=m.color or "#1d5da8", sort_order=m.sort_order or 100)
            db.session.add(painel)
            db.session.flush()
        atuais = {}
        for i in (Indicator.query.filter_by(panel_id=painel.id)
                  .order_by(Indicator.active.desc(), Indicator.id).all()):
            atuais.setdefault(_norm(i.title), i)        # ativo tem preferência
        vistos, novos, mudados = set(), 0, 0
        for x in p["metas"]:
            k = _norm(x["title"])
            vistos.add(k)
            i = atuais.get(k)
            if not i:
                defn = IndicatorDef.query.filter(
                    db.func.lower(IndicatorDef.title) == x["title"].lower()).first()
                if not defn:
                    defn = IndicatorDef(title=x["title"], dimension="PRAZO",
                                        target_type=x.get("target_type") or "manual",
                                        unit=x.get("unit"), rational=x.get("rational"))
                    db.session.add(defn)
                    db.session.flush()
                i = Indicator(indicator_def_id=defn.id, panel_id=painel.id, member_id=m.id,
                              title=x["title"], dimension="PRAZO",
                              target_type=x.get("target_type") or "manual", year=2026)
                db.session.add(i)
                novos += 1
            else:
                mudados += 1
            i.seq = x["seq"]
            i.target_label = x.get("target_label")
            i.weight = x.get("weight")
            i.unit = x.get("unit")
            i.direction = x.get("direction")
            i.scale_min, i.scale_obj, i.scale_sup = x.get("scale_min"), x.get("scale_obj"), x.get("scale_sup")
            if x.get("rational"):
                i.rational = x["rational"]
            i.active = True
        saem = [i for k, i in atuais.items() if k not in vistos and i.active]
        for i in saem:
            i.active = False
        db.session.commit()
        set_setting(chave, "ok")
        feitos.append(f"{m.name}: {mudados} atualizado(s), {novos} novo(s), {len(saem)} inativado(s)")
    return feitos


def liga_catalogo():
    """Indicador sem ligação ao catálogo -> liga à definição de MESMO nome, se
    existir (não cria definição). Sem a ligação, a medição única (um indicador
    medido em todos os painéis) não enxerga o indicador. Retorna quantos ligou."""
    from team.models import IndicatorDef, Indicator
    defs = {(d.title or "").strip().lower(): d.id for d in IndicatorDef.query.all()}
    n = 0
    for i in Indicator.query.filter(Indicator.indicator_def_id.is_(None)).all():
        did = defs.get((i.title or "").strip().lower())
        if did:
            i.indicator_def_id = did
            n += 1
    if n:
        db.session.commit()
    return n


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

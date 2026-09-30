# -*- coding: utf-8 -*-
"""Report interno do andamento do fechamento: quem já enviou os 3 documentos.

Os 3 documentos são as macro-atividades por entidade (Painel, Consolidação =
"Template" no quadro, e Endividamento). Cada uma é uma atividade com prazo e
responsável; o "OK" dela (concluída) é o que marca o envio.

Regra de envio (automático, em dia útil):
  • começa no dia útil seguinte ao prazo do fechamento (ajustável);
  • depois só repete enquanto houver documento em atraso — entrega postergada
    (nova data combinada ou prazo realinhado) só volta a aparecer se o novo
    prazo também falhar;
  • quando tudo estiver entregue sai um último e-mail de "fechamento completo".
"""
import fuso
from datetime import datetime
from html import escape

from models import db, Company, get_setting, set_setting
from team.models import Activity, ClosingTemplateItem, CompanyAssignment

# (macro no cronograma, rótulo da coluna)
DOCS = [("Painel", "Painel"), ("Consolidação", "Template"), ("Endividamento", "Endividamento")]
GRUPOS = [("consolidado", "Consolidado"), ("demais", "Demais")]

PADRAO = {"report_ativo": "1", "report_inicio_du": "1"}


def config(chave):
    v = get_setting(chave)
    return v if v not in (None, "") else PADRAO.get(chave, "")


def ligado():
    return str(config("report_ativo")) == "1"


def grupo_da_empresa(company, assign=None):
    """Grupo no quadro: o escolhido no cadastro; sem escolha, Consolidado se a
    entidade entrega a Consolidação."""
    g = getattr(company, "grupo_report", None)
    if g in ("consolidado", "demais"):
        return g
    if assign and "Consolidação" in assign.deliverables:
        return "consolidado"
    return "demais"


def _ultima_revisao(a):
    from team.models_workflow import DeadlineRevision
    return (DeadlineRevision.query.filter_by(entity_type="activity", entity_id=a.id,
                                             status="aplicada")
            .order_by(DeadlineRevision.created_at.desc()).first())


def _celula(a, ref):
    """Situação de um documento de uma empresa."""
    if a is None:
        return {"estado": "na", "rotulo": "—"}
    if a.status == "concluida":
        d = fuso.local(a.done_at).date() if a.done_at else None
        return {"estado": "entregue", "data": d, "rotulo": "Entregue",
                "activity_id": a.id}
    rev = _ultima_revisao(a)
    postergada = bool(a.nova_data) or bool(rev and rev.old_date and rev.new_date
                                           and rev.new_date > rev.old_date)
    efetivo = a.nova_data or a.due_date
    motivo = (a.nova_data_motivo if a.nova_data else (rev.reason if rev else None))
    if a.nova_data:                      # combinada: o prazo original segue valendo no app
        original, nova = a.due_date, a.nova_data
    elif postergada:                     # prazo realinhado: original = antes da 1ª revisão
        from team.models_workflow import DeadlineRevision
        primeira = (DeadlineRevision.query.filter_by(entity_type="activity", entity_id=a.id,
                                                     status="aplicada")
                    .order_by(DeadlineRevision.created_at.asc()).first())
        original, nova = (primeira.old_date if primeira else rev.old_date), a.due_date
    else:
        original = nova = None
    base = {"prazo": efetivo, "motivo": motivo, "activity_id": a.id,
            "responsavel": a.member.name if a.member else None,
            "postergada": postergada, "original": original, "nova": nova}
    if efetivo and efetivo < ref:
        from team.engine import business_days_between
        dias = max(business_days_between(efetivo, ref) or 0, 1)       # dias úteis de atraso
        return dict(base, estado="atrasada", dias=dias,
                    rotulo=("Atrasada (prazo postergado vencido)" if postergada else "Atrasada"))
    if postergada:
        return dict(base, estado="postergada", rotulo="Postergada")
    return dict(base, estado="pendente", rotulo="No prazo")


def montar(comp, ref=None):
    """Quadro completo da competência: linhas por grupo, totais e pendências."""
    ref = ref or fuso.hoje()
    itens = {it.id: it.macro for it in ClosingTemplateItem.query.filter(
        ClosingTemplateItem.macro.in_([m for m, _ in DOCS])).all()}
    acts = {}
    if comp and itens:
        for a in (Activity.query.filter(Activity.competency_id == comp.id,
                                        Activity.template_id.in_(list(itens)),
                                        Activity.status != "cancelada").all()):
            acts[(a.company_id, itens[a.template_id])] = a
    assigns = {x.company_id: x for x in CompanyAssignment.query.all()}
    ids = {cid for cid, _m in acts}
    empresas = (Company.query.filter(Company.id.in_(ids)).order_by(Company.name).all()
                if ids else [])
    grupos = {g: [] for g, _ in GRUPOS}
    pend, tot = [], {"docs": 0, "entregues": 0, "atrasadas": 0, "postergadas": 0}
    posterg = []
    completas = 0
    for c in empresas:
        cels, aplica, ok = {}, 0, 0
        for macro, rot in DOCS:
            cel = _celula(acts.get((c.id, macro)), ref)
            cels[macro] = cel
            if cel["estado"] == "na":
                continue
            aplica += 1
            tot["docs"] += 1
            if cel["estado"] == "entregue":
                ok += 1
                tot["entregues"] += 1
            else:
                if cel["estado"] == "atrasada":
                    tot["atrasadas"] += 1
                elif cel["estado"] == "postergada":
                    tot["postergadas"] += 1
                pend.append({"empresa": c.name, "doc": rot, **cel})
                if cel.get("postergada") and cel.get("original") and cel.get("nova"):
                    posterg.append({"empresa": c.name, "doc": rot, "original": cel["original"],
                                    "nova": cel["nova"], "motivo": cel.get("motivo"),
                                    "estado": cel["estado"]})
        datas = [x["data"] for x in cels.values()
                 if x["estado"] == "entregue" and x.get("data")]
        completa = aplica > 0 and ok == aplica
        completas += 1 if completa else 0
        obs = "; ".join(f"{rot}: {cels[m]['motivo']}" for m, rot in DOCS
                        if cels[m].get("motivo") and cels[m]["estado"] in ("postergada", "atrasada"))
        grupos[grupo_da_empresa(c, assigns.get(c.id))].append({
            "empresa": c, "cels": cels, "completa": completa,
            "data": max(datas) if completa and datas else None, "obs": obs})
    pend.sort(key=lambda x: (x["estado"] != "atrasada", -(x.get("dias") or 0), x["empresa"]))
    return {"comp": comp, "ref": ref, "grupos": grupos, "pendencias": pend,
            "postergacoes": posterg,
            "totais": tot, "n_empresas": len(empresas), "empresas_completas": completas,
            "completo": bool(tot["docs"]) and tot["entregues"] == tot["docs"]}


# ------------------------------------------------------------------ e-mail
_COR = {"entregue": "#C6EFCE", "atrasada": "#FFC7CE", "postergada": "#FFEB9C",
        "pendente": "#EDEDED", "na": "#FFFFFF"}
_SIM = {"entregue": "&#10004;", "atrasada": "&#10008;", "postergada": "&#9203;",
        "pendente": "&#8226;", "na": "&ndash;"}


def _linhas_cel(cel):
    """Texto pequeno da célula, em linhas (vai dentro do quadro colorido)."""
    e = cel["estado"]
    if e == "entregue":
        return [cel["data"].strftime("%d/%m")] if cel.get("data") else []
    if e == "atrasada":
        n = cel.get("dias") or 1
        return [f"{n} d.u. de atraso", "prazo " + cel["prazo"].strftime("%d/%m")] if cel.get("prazo") \
            else [f"{n} d.u. de atraso"]
    if e == "postergada" and cel.get("prazo"):
        return ["até " + cel["prazo"].strftime("%d/%m")]
    if e == "pendente" and cel.get("prazo"):
        return ["prazo " + cel["prazo"].strftime("%d/%m")]
    return []


def assunto(q):
    t = q["totais"]
    comp = q["comp"].label if q["comp"] else ""
    if q["completo"]:
        return f"Fechamento {comp}: todos os documentos entregues"
    return (f"Fechamento {comp}: {t['entregues']}/{t['docs']} documentos entregues"
            + (f", {t['atrasadas']} em atraso" if t["atrasadas"] else ""))


def html(q, link):
    t = q["totais"]
    comp = escape(q["comp"].label) if q["comp"] else ""
    th = "background:#1F4E79;color:#fff;padding:7px 10px;border:1px solid #fff;font-size:13px;"
    td = "padding:6px 10px;border:1px solid #BFD3E6;font-size:13px;"
    linhas = []
    for g, rot in GRUPOS:
        if not q["grupos"][g]:
            continue
        linhas.append(f'<tr><td colspan="6" style="{td}background:#F0F3F6;font-weight:bold;">{escape(rot)}</td></tr>')
        for l in q["grupos"][g]:
            cs = ""
            for macro, _r in DOCS:
                cel = l["cels"][macro]
                pequeno = "<br>".join(escape(x) for x in _linhas_cel(cel))
                marca = " *" if cel.get("postergada") and cel["estado"] in ("postergada", "atrasada") else ""
                cs += (f'<td style="{td}background:{_COR[cel["estado"]]};text-align:center;">'
                       f'<b>{_SIM[cel["estado"]]}</b>{marca}'
                       f'<div style="font-size:11px;color:#333;line-height:1.3;">{pequeno}</div></td>')
            data = l["data"].strftime("%d/%m") if l["data"] else ""
            linhas.append(f'<tr><td style="{td}">{escape(l["empresa"].name)}</td>'
                          f'<td style="{td}text-align:center;">{data}</td>{cs}'
                          f'<td style="{td}font-size:12px;">{escape(l["obs"])}</td></tr>')
    rodape = ""
    if q.get("postergacoes"):
        li = "".join(
            f'<li>* {escape(p["empresa"])} — {escape(p["doc"])}: prazo original '
            f'<b>{p["original"].strftime("%d/%m")}</b>, nova data combinada <b>{p["nova"].strftime("%d/%m")}</b>'
            + (f' ({escape(p["motivo"])})' if p.get("motivo") else "") + "</li>"
            for p in q["postergacoes"])
        rodape = (f'<p style="margin:14px 0 4px;font-size:12px;"><b>Entregas postergadas</b></p>'
                  f'<ul style="margin:0;padding-left:16px;font-size:12px;list-style:none;">{li}</ul>')
    resumo = ("Todos os documentos foram entregues." if q["completo"] else
              f'{t["entregues"]} de {t["docs"]} documentos entregues · {q["empresas_completas"]} de {q["n_empresas"]} empresas completas'
              + (f' · {t["atrasadas"]} em atraso' if t["atrasadas"] else "")
              + (f' · {t["postergadas"]} postergado(s)' if t["postergadas"] else ""))
    return f"""<div style="font-family:Segoe UI,Arial,sans-serif;color:#1b2a3a;max-width:760px;">
<h2 style="margin:0 0 2px;color:#1F4E79;">Status Fechamento</h2>
<div style="font-style:italic;font-size:13px;margin-bottom:10px;">{comp} — até o momento deste envio ({q["ref"].strftime("%d/%m/%Y")})</div>
<div style="font-size:14px;margin-bottom:12px;"><b>{escape(resumo)}</b></div>
<table style="border-collapse:collapse;width:100%;">
<tr><th style="{th}text-align:left;">Empresa</th><th style="{th}">Data</th><th style="{th}">Painel</th>
<th style="{th}">Template</th><th style="{th}">Endividamento</th><th style="{th}">Obs</th></tr>
{''.join(linhas)}
</table>{rodape}
<p style="font-size:12px;color:#556;margin-top:14px;">&#10004; entregue &nbsp; &#10008; atrasado (dias úteis) &nbsp; &#9203; postergado (nova data combinada) &nbsp; &#8226; no prazo.
Quadro ao vivo: <a href="{escape(link)}">{escape(link)}</a></p></div>"""


def texto(q):
    t = q["totais"]
    out = [f"Status Fechamento — {q['comp'].label if q['comp'] else ''} ({q['ref'].strftime('%d/%m/%Y')})",
           f"{t['entregues']} de {t['docs']} documentos entregues; {t['atrasadas']} em atraso; "
           f"{t['postergadas']} postergado(s)."]
    for p in q["pendencias"]:
        out.append(f"  • {p['empresa']} — {p['doc']}: {p['rotulo']}"
                   + (f" (prazo {p['prazo'].strftime('%d/%m')})" if p.get("prazo") else ""))
    return "\n".join(out)


# ------------------------------------------------------------------- envio
def envia(comp, ref=None, destinos=None, tipo="manual", dry_run=False):
    """Manda o report (um e-mail por destinatário). Retorna dict com o resultado."""
    from team import alerts, comunicacao
    ref = ref or fuso.hoje()
    q = montar(comp, ref)
    lista = destinos if destinos is not None else comunicacao.emails_da_lista("report")
    res = {"enviados": 0, "falhas": [], "destinos": len(lista), "assunto": assunto(q),
           "completo": q["completo"]}
    if not lista:
        res["falhas"].append("lista 'Report do fechamento' vazia")
        return res
    if dry_run:
        return res
    link = alerts.PORTAL_URL.rstrip("/") + "/report-fechamento"
    corpo_html, corpo_txt = html(q, link), texto(q)
    for e in lista:
        ok, err = alerts.send_email(e, res["assunto"], corpo_txt, lista=True, html=corpo_html)
        if ok:
            res["enviados"] += 1
        else:
            res["falhas"].append(f"{e}: {err}")
    if res["falhas"] and tipo != "teste":
        comunicacao.avisa_sistema("report_fechamento", "O report do fechamento não chegou a todos",
                                  "\n".join(res["falhas"][:30]))
    if comp and tipo != "teste":
        set_setting(f"report_enviado_{comp.id}", ref.isoformat())
        if q["completo"]:
            set_setting(f"report_final_{comp.id}", "1")
        db.session.commit()
    return res


def decide(comp, ref):
    """O que o envio automático deve fazer hoje: 'inicial' | 'diario' | 'final' | None."""
    if not ligado() or not comp or not comp.deadline:
        return None
    from team.engine import add_business_days
    try:
        off = int(config("report_inicio_du"))
    except ValueError:
        off = 1
    inicio = add_business_days(comp.deadline, max(off, 0)) if off else comp.deadline
    if ref < inicio:
        return None
    ja = get_setting(f"report_enviado_{comp.id}")
    if ja == ref.isoformat():
        return None
    q = montar(comp, ref)
    if not q["totais"]["docs"]:
        return None
    if q["completo"]:
        return None if get_setting(f"report_final_{comp.id}") == "1" else "final"
    if not ja:
        return "inicial"
    return "diario" if q["totais"]["atrasadas"] > 0 else None


def rodar(ref=None):
    """Chamado pelo agendador diário."""
    from models import current_competency
    ref = ref or fuso.hoje()
    comp = current_competency()
    tipo = decide(comp, ref)
    if not tipo:
        return {"pulado": "sem envio hoje"}
    r = envia(comp, ref, tipo=tipo)
    r["tipo"] = tipo
    return r

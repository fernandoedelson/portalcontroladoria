# -*- coding: utf-8 -*-
"""Aviso de prazo às empresas — e-mail automático para os contatos de cada uma.

Duas mensagens, ambas só em dia útil:
  • "data"  — no 1º dia útil do mês: avisa QUANDO o fechamento vence;
  • "vence" — no próprio dia do prazo daquela empresa.

O prazo é o do SEGMENTO da empresa (Administração › Organização › Segmentos):
Holdinhas no 2º dia útil, Investimentos no 8º, o resto no 5º. Empresa sem
segmento usa o padrão (5º). O e-mail de atraso ficou de fora de propósito:
depende de amarrar a atividade do cronograma.
"""
import fuso
from datetime import datetime

from models import db, Company, get_setting, current_competency
from team.models_workflow import (CompanyNoticeLog, Segment,
                                  AVISO_ASSUNTO_DATA, AVISO_CORPO_DATA,
                                  AVISO_ASSUNTO_VENCE, AVISO_CORPO_VENCE)
from team.models import CompanyAssignment

PADRAO = {
    "aviso_emp_ativo": "1",
    "aviso_emp_assunto_data": AVISO_ASSUNTO_DATA,
    "aviso_emp_corpo_data": AVISO_CORPO_DATA,
    "aviso_emp_assunto_vence": AVISO_ASSUNTO_VENCE,
    "aviso_emp_corpo_vence": AVISO_CORPO_VENCE,
}
MESES = ["", "janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho",
         "agosto", "setembro", "outubro", "novembro", "dezembro"]


def config(chave):
    v = get_setting(chave)
    return v if v not in (None, "") else PADRAO.get(chave, "")


def ligado():
    return str(config("aviso_emp_ativo")) == "1"


def _mes_seguinte(comp):
    return (comp.year + 1, 1) if comp.month == 12 else (comp.year, comp.month + 1)


def prazo_da_empresa(company_id, comp, prazos_seg=None, assigns=None):
    """(data, nº do dia útil) do prazo daquela empresa na competência."""
    from engine.calendar_br import business_days
    if not comp:
        return None, None
    if prazos_seg is None:
        prazos_seg = {s.name: (s.prazo_du or 5) for s in Segment.query.all()}
    if assigns is None:
        assigns = {a.company_id: a for a in CompanyAssignment.query.all()}
    a = assigns.get(company_id)
    du = prazos_seg.get(a.segment if a else None, 5) or 5
    dias = business_days(*_mes_seguinte(comp))
    if not dias:
        return None, du
    return dias[min(du, len(dias)) - 1], du


def empresas_do_aviso():
    """Empresas ativas marcadas para receber, com pelo menos um contato ativo."""
    out = []
    for c in Company.query.filter_by(active=True, avisar_prazo=True).order_by(Company.name):
        emails = [x.email for x in c.contatos if x.active and x.email]
        if emails:
            out.append((c, emails))
    return out


def _texto(chave, empresa, comp, prazo, du):
    dados = {"empresa": empresa.name, "competencia": comp.label if comp else "",
             "prazo": prazo.strftime("%d/%m/%Y") if prazo else "",
             "du": du or "", "mes_envio": MESES[prazo.month] if prazo else "",
             "hoje": fuso.hoje().strftime("%d/%m/%Y")}
    try:
        from team import agenda
        dados["agendar"] = agenda.url_para_empresa(empresa.id)
    except Exception:
        dados["agendar"] = ""
    base = config(chave)
    if not dados["agendar"]:          # agendamento desligado: some a linha do convite
        base = "\n".join(l for l in base.split("\n") if "{agendar}" not in l)
    try:
        return base.format(**dados)
    except (KeyError, IndexError, ValueError):
        return base          # marcador digitado errado não derruba o envio


def _ja_enviado(company_id, comp_id, kind):
    return (CompanyNoticeLog.query
            .filter_by(company_id=company_id, competency_id=comp_id, kind=kind)
            .filter(CompanyNoticeLog.status != "falhou").count() > 0)


def envia(kind, empresa, emails, comp, prazo, du, quem=None, dry_run=False):
    """Manda um aviso e registra. Retorna (ok, detalhe)."""
    from team import alerts
    assunto = _texto(f"aviso_emp_assunto_{kind}", empresa, comp, prazo, du)
    corpo = _texto(f"aviso_emp_corpo_{kind}", empresa, comp, prazo, du)
    if dry_run:
        return True, "simulado"
    ok, err = alerts.send_email(", ".join(emails), assunto, corpo)
    log = CompanyNoticeLog(
        company_id=empresa.id, competency_id=(comp.id if comp else None), kind=kind,
        to_addr=", ".join(emails)[:400], subject=assunto[:240],
        status=("enviado" if ok else ("simulado" if err == "email_desligado" else "falhou")),
        detail=(err or "")[:300], sent_at=datetime.utcnow(), sent_by=quem)
    db.session.add(log)
    db.session.commit()
    return ok, (err or "")


def emails_de_controle():
    """Para quem vai o protocolo de envio: a lista "Protocolo" (Comunicação).
    O administrador não recebe por ser administrador."""
    from team import comunicacao
    return comunicacao.emails_da_lista("protocolo")


def avisa_controle(ref, comp, enviados, falhas, dry_run=False):
    """Manda ao administrador o comprovante do que foi disparado às empresas."""
    from team import alerts
    if falhas and not dry_run:
        from team import comunicacao
        comunicacao.avisa_sistema("avisos_empresas", f"{len(falhas)} aviso(s) de prazo às empresas falharam",
                                  "\n".join(f"{e}: {d}" for e, d in falhas[:30]))
    if not enviados and not falhas:
        return False
    destinos = emails_de_controle()
    if not destinos or dry_run:
        return False
    linhas = [f"Avisos de prazo enviados em {ref.strftime('%d/%m/%Y')}"
              + (f" — fechamento {comp.label}" if comp else ""), ""]
    for kind, empresa, emails, prazo in enviados:
        rot = "aviso da data" if kind == "data" else "vence hoje"
        linhas.append(f"  • {empresa} — {rot} — para {', '.join(emails)}"
                      + (f" — prazo {prazo.strftime('%d/%m/%Y')}" if prazo else ""))
    if falhas:
        linhas += ["", "Falhas:"] + [f"  • {e} — {d}" for e, d in falhas]
    linhas += ["", f"Total: {len(enviados)} enviado(s)"
               + (f", {len(falhas)} falha(s)." if falhas else ".")]
    corpo = "\n".join(linhas)
    assunto = (f"[Controle] {len(enviados)} aviso(s) de prazo enviado(s) às empresas"
               + (f" — {comp.label}" if comp else ""))
    ok, _err = alerts.send_email(", ".join(destinos), assunto, corpo, lista=True)
    return ok


def rodar(ref=None, quem=None, forcar=None, dry_run=False):
    """Roda os avisos do dia. `forcar` ('data'|'vence') ignora a regra de data.

    Idempotente: cada empresa recebe uma vez por competência e por tipo.
    """
    ref = ref or fuso.hoje()
    resumo = {"data": 0, "vence": 0, "falhas": 0, "pulado": None}
    if not ligado() and not forcar:
        resumo["pulado"] = "avisos desligados"
        return resumo
    if not fuso.pode_avisar(ref) and not forcar:
        resumo["pulado"] = "dia não útil"
        return resumo
    comp = current_competency()
    if not comp:
        resumo["pulado"] = "sem competência"
        return resumo
    from engine.calendar_br import business_days
    dias_mes = business_days(ref.year, ref.month)
    primeiro_du = dias_mes[0] if dias_mes else None
    prazos_seg = {s.name: (s.prazo_du or 5) for s in Segment.query.all()}
    assigns = {a.company_id: a for a in CompanyAssignment.query.all()}

    enviados, falhas = [], []
    for empresa, emails in empresas_do_aviso():
        prazo, du = prazo_da_empresa(empresa.id, comp, prazos_seg, assigns)
        for kind, quando in (("data", primeiro_du), ("vence", prazo)):
            if forcar and forcar != kind:
                continue
            if not forcar and (quando is None or ref != quando):
                continue
            if not forcar and _ja_enviado(empresa.id, comp.id, kind):
                continue
            ok, det = envia(kind, empresa, emails, comp, prazo, du,
                            quem=quem, dry_run=dry_run)
            resumo[kind] += 1 if ok else 0
            resumo["falhas"] += 0 if ok else 1
            (enviados if ok else falhas).append(
                (kind, empresa.name, emails, prazo) if ok else (empresa.name, det))
    resumo["controle"] = avisa_controle(ref, comp, enviados, falhas, dry_run=dry_run)
    return resumo

# -*- coding: utf-8 -*-
"""Motor de atividades: prazos em dia util, geracao mensal e farol do fechamento."""
import fuso
from datetime import date, timedelta, datetime
from functools import lru_cache

from models import db, Company, Competency, Submission
from engine.calendar_br import _br_holidays, business_days  # reutiliza feriados BR
from team.models import (Activity, ClosingTemplateItem, TeamMember)


# --------------------------------------------------------------------------
# Aritmetica de dias uteis (feriados nacionais BR) — feriados memoizados por ano
# --------------------------------------------------------------------------
@lru_cache(maxsize=256)
def _year_holidays(year):
    """Feriados nacionais do ano (memoizado — evita recomputo em loop/lote)."""
    return frozenset(_br_holidays(year))


def is_business_day(d, hol=None):
    """True se d e dia util (seg-sex, exceto feriado nacional)."""
    return d.weekday() < 5 and d not in _year_holidays(d.year)


def add_business_days(start, n):
    """Soma (ou subtrai, se n<0) n dias uteis a partir de `start`.

    n=0 retorna o proprio start se for dia util, senao o proximo dia util.
    """
    if start is None:
        return None
    d = start
    if n == 0:
        while not is_business_day(d):
            d += timedelta(days=1)
        return d
    step = 1 if n > 0 else -1
    remaining = abs(n)
    while remaining > 0:
        d += timedelta(days=step)
        if is_business_day(d):
            remaining -= 1
    return d


def business_days_between(a, b):
    """Numero de dias uteis de a (exclusive) ate b (inclusive). Sinalizado."""
    if a is None or b is None:
        return None
    if a == b:
        return 0
    step = 1 if b > a else -1
    d, count = a, 0
    while d != b:
        d += timedelta(days=step)
        if is_business_day(d):
            count += step
    return count


# --------------------------------------------------------------------------
# Insumo: quando as empresas-ancora "chegaram" (dispara o D+2)
# --------------------------------------------------------------------------
def insumo_ready_date(competency, codes):
    """Data em que a ultima das empresas `codes` teve submissao aceita na competencia.

    Retorna (date|None, recebidas, total). None enquanto faltar alguma.
    """
    if not competency or not codes:
        return None, 0, len(codes or [])
    codes = list(codes)
    total = len(codes)
    companies = {c.code: c for c in Company.query.filter(Company.code.in_(codes)).all()}
    last = None
    received = 0
    for code in codes:
        c = companies.get(code)
        if not c:
            return None, received, total   # empresa nao cadastrada => nao pronto
        sub = (Submission.query
               .filter_by(company_id=c.id, competency_id=competency.id, is_current=True)
               .filter(Submission.status.in_(["aprovado", "aprovado_com_excecao"]))
               .order_by(Submission.version.desc()).first())
        if not sub:
            return None, received, total
        received += 1
        when = (sub.submitted_at.date() if sub.submitted_at else fuso.hoje())
        if last is None or when > last:
            last = when
    return last, received, total


# --------------------------------------------------------------------------
# Calculo de prazo de uma atividade a partir da sua regra
# --------------------------------------------------------------------------
def compute_due(rule, competency):
    """Retorna (due_date|None, provisional_bool) para uma regra de prazo.

    rule = {"base": "deadline"|"insumo"|"fixed_bd", "offset": int, "codes": [...]}
    """
    base = (rule or {}).get("base", "deadline")
    offset = int((rule or {}).get("offset", 0) or 0)

    if base == "deadline":
        if not competency or not competency.deadline:
            return None, True
        return add_business_days(competency.deadline, offset), False

    if base == "insumo":
        ready, _rec, _tot = insumo_ready_date(competency, (rule or {}).get("codes", []))
        if ready is None:
            # ainda nao chegou: prazo provisorio ancorado no 5o DU (proxy de planejamento)
            if competency and competency.deadline:
                return add_business_days(competency.deadline, offset), True
            return None, True
        return add_business_days(ready, offset), False

    if base == "fixed_bd":
        # offset = n-esimo dia util do mes seguinte a competencia
        if not competency:
            return None, True
        y, mo = (competency.year + 1, 1) if competency.month == 12 else \
                (competency.year, competency.month + 1)
        days = business_days(y, mo)
        if not days:
            return None, True
        idx = min(max(offset, 1), len(days)) - 1
        return days[idx], False

    return None, True


# --------------------------------------------------------------------------
# Geracao mensal a partir do template de fechamento
# --------------------------------------------------------------------------
def generate_closing_activities(competency, created_by=None):
    """SINCRONIZA as Activity da competência com o cronograma (template).

    Regenerar reconcilia (não só adiciona):
      - cria as que faltam;
      - ATUALIZA as que ainda existem (responsável, prazo, prioridade, título);
      - REMOVE as em aberto que saíram do cronograma (item inativado/removido ou
        empresa fora do escopo);
      - PRESERVA as concluídas/canceladas (são histórico — nunca mexe nelas).
    Retorna (criadas, atualizadas, removidas, preservadas).
    """
    from team.models import CompanyAssignment
    items = (ClosingTemplateItem.query.filter_by(active=True)
             .order_by(ClosingTemplateItem.sort_order, ClosingTemplateItem.id).all())
    assigns = (CompanyAssignment.query.join(Company)
               .filter(Company.active.is_(True)).all())
    by_company = {a.company_id: a for a in assigns}

    # 1) conjunto DESEJADO: chave (template_id, company_id) -> dados
    desejado = {}
    for it in items:
        rule = {"base": it.due_base, "offset": it.due_offset,
                "codes": it.insumo_codes}
        due, provisional = compute_due(rule, competency)
        if it.per_company:                       # todas as empresas da carteira
            alvos = [(a.company_id, a.member_id or it.member_id) for a in assigns]
        elif it.company_id:                      # uma empresa específica
            a = by_company.get(it.company_id)
            alvos = [(it.company_id, (a.member_id if a else None) or it.member_id)]
        else:                                    # atividade geral do time
            alvos = [(None, it.member_id)]
        for cid, mid in alvos:
            desejado[(it.id, cid)] = {"it": it, "member_id": mid, "due": due,
                                      "prov": provisional, "rule": rule}

    # 2) reconcilia com o que já existe (só atividades geradas por template)
    existentes = Activity.query.filter_by(
        competency_id=competency.id, origin="template").all()
    criadas = atualizadas = removidas = preservadas = 0
    vistos = set()
    for act in existentes:
        chave = (act.template_id, act.company_id)
        if act.status in ("concluida", "cancelada"):
            preservadas += 1
            vistos.add(chave)                    # não recria por cima do concluído
            continue
        d = desejado.get(chave)
        if d:                                    # ainda no cronograma -> ATUALIZA
            it = d["it"]
            act.title = it.title
            act.kind = it.kind
            act.priority = it.priority
            act.member_id = d["member_id"]
            act.due_date = d["due"]
            act.due_provisional = d["prov"]
            act.auto_metric = it.auto_metric
            act.sort_order = it.sort_order
            act.due_rule = d["rule"]
            atualizadas += 1
            vistos.add(chave)
        else:                                    # saiu do cronograma -> REMOVE
            db.session.delete(act)
            removidas += 1

    # 3) cria as que faltam
    for chave, d in desejado.items():
        if chave in vistos:
            continue
        it = d["it"]
        act = Activity(
            title=it.title, kind=it.kind, origin="template",
            member_id=d["member_id"], company_id=chave[1],
            competency_id=competency.id, template_id=it.id,
            status="pendente", priority=it.priority,
            due_date=d["due"], due_provisional=d["prov"],
            auto_metric=it.auto_metric, created_by=created_by,
            sort_order=it.sort_order)
        act.due_rule = d["rule"]
        db.session.add(act)
        criadas += 1
    db.session.commit()
    return criadas, atualizadas, removidas, preservadas


def ensure_competency(year, month, nth=5):
    """Garante a Competency de (year, month) — cria 'planejada' se faltar."""
    from engine.calendar_br import deadline_for_competency
    comp = Competency.query.filter_by(year=year, month=month).first()
    if comp:
        return comp
    try:
        dl = deadline_for_competency(year, month, nth=nth)
    except Exception:
        dl = None
    comp = Competency(year=year, month=month, deadline=dl, status="planejada")
    db.session.add(comp)
    db.session.commit()
    return comp


def generate_for_range(pairs, created_by=None, nth=5):
    """Sincroniza o cronograma para uma lista de (year, month). Cria as
    competências que faltarem. Retorna
    (criadas, atualizadas, removidas, preservadas, n_competencias)."""
    tot_c = tot_a = tot_r = tot_p = 0
    comps = [ensure_competency(y, m, nth=nth) for (y, m) in pairs]
    for comp in comps:
        c, a, r, p = generate_closing_activities(comp, created_by=created_by)
        tot_c += c
        tot_a += a
        tot_r += r
        tot_p += p
    return tot_c, tot_a, tot_r, tot_p, len(comps)


def refresh_provisional_due_dates(competency):
    """Recalcula prazos de atividades cujo prazo dependia de insumo ainda nao chegado.

    Chamar quando uma submissao das ancoras e aceita. Retorna nº atualizadas.
    """
    acts = (Activity.query.filter_by(competency_id=competency.id, origin="template")
            .filter(Activity.status.in_(["pendente", "em_andamento", "bloqueada"])).all())
    n = 0
    for a in acts:
        rule = a.due_rule
        if not rule:
            continue
        due, provisional = compute_due(rule, competency)
        if due != a.due_date or provisional != a.due_provisional:
            a.due_date = due
            a.due_provisional = provisional
            n += 1
    if n:
        db.session.commit()
    return n


# --------------------------------------------------------------------------
# Auto-metrica: conclui atividades com prova no proprio motor de fechamento
# --------------------------------------------------------------------------
def resolve_auto_metrics(competency):
    """Atividades auto_metric ligadas a uma empresa concluem quando a submissao
    daquela empresa/competencia e aceita (a submissao e a evidencia).
    Retorna nº concluidas nesta passada."""
    acts = (Activity.query.filter_by(competency_id=competency.id, auto_metric=True)
            .filter(Activity.status.in_(["pendente", "em_andamento"]))
            .filter(Activity.company_id.isnot(None)).all())
    n = 0
    for a in acts:
        sub = (Submission.query
               .filter_by(company_id=a.company_id, competency_id=competency.id,
                          is_current=True)
               .filter(Submission.status.in_(["aprovado", "aprovado_com_excecao"]))
               .order_by(Submission.version.desc()).first())
        if sub:
            a.status = "concluida"
            a.done_at = sub.submitted_at or datetime.utcnow()
            n += 1
    if n:
        db.session.commit()
    return n


# --------------------------------------------------------------------------
# Farol do fechamento
# --------------------------------------------------------------------------
def farol(competency, ref=None):
    """Contadores de status das atividades de fechamento da competencia."""
    ref = ref or fuso.hoje()
    counts = {"total": 0, "concluida": 0, "atrasada": 0, "vence_hoje": 0,
              "aguardando": 0, "pendente": 0, "em_andamento": 0,
              "bloqueada": 0, "cancelada": 0}
    acts = (Activity.query.filter_by(competency_id=competency.id)
            .filter(Activity.kind.in_(["fechamento", "recorrente"])).all()) \
        if competency else []
    for a in acts:
        counts["total"] += 1
        st = a.effective_status(ref)
        counts[st] = counts.get(st, 0) + 1
    done = counts["concluida"]
    counts["pct"] = round(done / counts["total"] * 100) if counts["total"] else 0
    return counts


def member_farol(competency=None, ref=None):
    """Carga EM ABERTO por membro — espelha a Fila do Dia.

    Conta as atividades em aberto de qualquer competência (antes ficava preso à
    competência atual, então uma atrasada de outro mês aparecia na Fila mas somava
    0 no cartão da pessoa). Ignora prazos provisórios (aguardando insumo)."""
    ref = ref or fuso.hoje()
    out = {}
    acts = (Activity.query
            .filter(Activity.status.in_(["pendente", "em_andamento", "bloqueada"]))
            .all())
    for a in acts:
        if a.due_provisional:
            continue
        mid = a.member_id or 0
        d = out.setdefault(mid, {"total": 0, "atrasada": 0, "vence_hoje": 0,
                                 "aberta": 0})
        d["total"] += 1
        d["aberta"] += 1
        st = a.effective_status(ref)
        if st == "atrasada":
            d["atrasada"] += 1
        elif st == "vence_hoje":
            d["vence_hoje"] += 1
    return out


# --------------------------------------------------------------------------
# Consultas para o Painel do Dia
# --------------------------------------------------------------------------
def day_panel(ref=None, member_id=None, kind_filter=None):
    """Atividades relevantes para 'hoje': atrasadas, vencendo hoje e em andamento.

    kind_filter: 'projeto' (só projetos) | 'fechamento' (tudo menos projeto) | None.
    Retorna dict com listas ordenadas por prioridade/prazo.
    """
    ref = ref or fuso.hoje()
    q = Activity.query.filter(Activity.status.in_(["pendente", "em_andamento", "bloqueada"]))
    if member_id:
        q = q.filter(Activity.member_id == member_id)
    if kind_filter == "projeto":
        q = q.filter(Activity.kind == "projeto")
    elif kind_filter == "fechamento":
        q = q.filter(Activity.kind != "projeto")
    acts = q.all()
    overdue, today, soon = [], [], []
    for a in acts:
        if a.due_provisional:
            continue
        if a.is_overdue(ref):
            overdue.append(a)
        elif a.is_due_today(ref):
            today.append(a)
        elif a.due_date and 0 < (a.due_date - ref).days <= 3:
            soon.append(a)
    prio = {"critica": 0, "alta": 1, "media": 2, "baixa": 3}
    key = lambda a: (a.due_date or date.max, prio.get(a.priority, 2))
    overdue.sort(key=key)
    today.sort(key=lambda a: prio.get(a.priority, 2))
    soon.sort(key=key)
    return {"overdue": overdue, "today": today, "soon": soon,
            "n_overdue": len(overdue), "n_today": len(today), "n_soon": len(soon)}

# -*- coding: utf-8 -*-
"""Aviso da Liderança: férias que começam em breve.

Roda no ciclo diário (só em dia útil). Cada período de férias aprovado gera UM aviso,
guardado em Absence.aviso_lideranca_em. A antecedência é configurável (Comunicação ›
Envios automáticos; padrão 15 dias). Férias cadastradas já em cima da hora avisam na primeira
rodada seguinte, desde que ainda não tenham começado.

O aviso sai por notificação no portal (que também vira push no celular) e por e-mail.
"""
from datetime import datetime, timedelta

from models import db, notify, get_setting, set_setting, User
from team.models_workflow import Absence
from team.movimentos import liderancas_uids

ANTECEDENCIA_PADRAO = 15
ANTECEDENCIA_MIN, ANTECEDENCIA_MAX = 1, 90


def antecedencia_dias():
    try:
        n = int(get_setting("ferias_aviso_dias") or ANTECEDENCIA_PADRAO)
    except (TypeError, ValueError):
        n = ANTECEDENCIA_PADRAO
    return max(ANTECEDENCIA_MIN, min(ANTECEDENCIA_MAX, n))


def salva_antecedencia(valor):
    """Grava a antecedência (1 a 90 dias). Retorna o valor gravado ou None se inválido."""
    try:
        n = int(str(valor).strip())
    except (TypeError, ValueError):
        return None
    if not ANTECEDENCIA_MIN <= n <= ANTECEDENCIA_MAX:
        return None
    set_setting("ferias_aviso_dias", str(n))
    return n


def _texto(a, ref):
    faltam = (a.start_date - ref).days
    nome = a.member.name if a.member else "Alguém do time"
    return (f"{nome} sai de férias em {a.start_date.strftime('%d/%m/%Y')} "
            f"(daqui a {faltam} dia{'s' if faltam != 1 else ''}), "
            f"até {a.end_date.strftime('%d/%m/%Y')} · {a.dias} dia{'s' if a.dias != 1 else ''}.")


def avisa_ferias_proximas(ref):
    """Avisa a Liderança das férias aprovadas que começam dentro da antecedência. Retorna quantas."""
    limite = ref + timedelta(days=antecedencia_dias())
    pend = (Absence.query.filter(Absence.status == "aprovada", Absence.kind == "ferias",
                                 Absence.aviso_lideranca_em.is_(None),
                                 Absence.start_date > ref, Absence.start_date <= limite)
            .order_by(Absence.start_date).all())
    if not pend:
        return 0
    uids = sorted(liderancas_uids())
    textos = [_texto(a, ref) for a in pend]
    for t in textos:
        for uid in uids:
            notify(uid, "Férias à vista", t, kind="ausencia", url="/ausencias")
    _envia_email(uids, textos)
    agora = datetime.utcnow()
    for a in pend:
        a.aviso_lideranca_em = agora      # marca mesmo sem Liderança cadastrada: não reavisa depois
    db.session.commit()
    return len(pend)


def _envia_email(uids, textos):
    """Um e-mail por pessoa da Liderança, com todos os períodos que entraram na janela."""
    from team import alerts
    if not uids or not textos:
        return
    assunto = ("Férias à vista" if len(textos) == 1 else f"Férias à vista ({len(textos)} períodos)")
    corpo = ("\n".join(f"• {t}" for t in textos)
             + f"\n\nProgramação completa: {alerts.PORTAL_URL.rstrip('/')}/ausencias")
    for u in User.query.filter(User.id.in_(uids), User.active.is_(True)).all():
        if u.email:
            alerts.send_email(u.email, f"{assunto} — Portal Controladoria", corpo)

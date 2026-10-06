# -*- coding: utf-8 -*-
"""Aviso da Liderança: férias que começam em até 15 dias.

Roda no ciclo diário (só em dia útil). Cada período de férias aprovado gera UM aviso,
guardado em Absence.aviso_lideranca_em. Férias cadastradas já em cima da hora (menos
de 15 dias) avisam na primeira rodada seguinte, desde que ainda não tenham começado.
"""
from datetime import datetime, timedelta

from models import db, notify
from team.models_workflow import Absence
from team.movimentos import liderancas_uids

ANTECEDENCIA_DIAS = 15


def avisa_ferias_proximas(ref):
    """Notifica a Liderança das férias aprovadas que começam em até 15 dias. Retorna quantas."""
    limite = ref + timedelta(days=ANTECEDENCIA_DIAS)
    pend = (Absence.query.filter(Absence.status == "aprovada", Absence.kind == "ferias",
                                 Absence.aviso_lideranca_em.is_(None),
                                 Absence.start_date > ref, Absence.start_date <= limite)
            .order_by(Absence.start_date).all())
    if not pend:
        return 0
    uids = sorted(liderancas_uids())
    n = 0
    for a in pend:
        faltam = (a.start_date - ref).days
        nome = a.member.name if a.member else "Alguém do time"
        txt = (f"{nome} sai de férias em {a.start_date.strftime('%d/%m/%Y')} "
               f"(daqui a {faltam} dia{'s' if faltam != 1 else ''}), "
               f"até {a.end_date.strftime('%d/%m/%Y')} · {a.dias} dia{'s' if a.dias != 1 else ''}.")
        for uid in uids:
            notify(uid, "Férias à vista", txt, kind="ausencia", url="/ausencias")
        a.aviso_lideranca_em = datetime.utcnow()      # marca mesmo sem Liderança cadastrada: não reavisa depois
        n += 1
    db.session.commit()
    return n

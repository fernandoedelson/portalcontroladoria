# -*- coding: utf-8 -*-
"""Quem é avisado de quê quando uma atividade se mexe.

• Liderança (perfil): recebe as escaladas de atraso e toda mudança de prazo.
• Líder do projeto: recebe TODA movimentação das atividades do projeto dele
  (status, prazo, responsável, nota, atividade nova).
Nada aqui exige aprovação: é informação.
"""
from models import db, User, notify


def liderancas_uids():
    return {u.id for u in User.query.filter_by(role="lideranca", active=True).all()}


def lideres_projeto_uids(a):
    """Líder (owner) e gestor do projeto da atividade, como usuários."""
    p = getattr(a, "project", None)
    if not p:
        return set()
    ids = set()
    for m in (p.owner, getattr(p, "manager", None)):
        if m and m.user_id:
            ids.add(m.user_id)
    return ids


def avisa(uids, titulo, msg, url, autor_id=None):
    """Notifica cada usuário uma vez, menos quem fez a mudança."""
    n = 0
    for uid in sorted(set(uids or ())):
        if uid and uid != autor_id:
            notify(uid, titulo, msg[:380], kind="prazo", url=url)
            n += 1
    return n


def movimento_projeto(a, texto, autor_id=None, titulo=None):
    """Avisa o líder do projeto de qualquer movimentação numa atividade dele."""
    if not getattr(a, "project_id", None):
        return 0
    nome = a.project.name if a.project else "Projeto"
    return avisa(lideres_projeto_uids(a), titulo or f"Projeto {nome}", texto,
                 f"/atividade/{a.id}", autor_id)


def prazo_alterado(a, antigo, novo, motivo, autor_nome, autor_id=None):
    """Toda mudança de prazo vai para a Liderança e para o líder do projeto."""
    de = antigo.strftime("%d/%m/%Y") if antigo else "sem prazo"
    para = novo.strftime("%d/%m/%Y") if novo else "sem prazo"
    txt = (f"{autor_nome} mudou o prazo de “{a.title[:80]}”: {de} → {para}. "
           f"Motivo: {motivo}")
    uids = liderancas_uids() | lideres_projeto_uids(a)
    return avisa(uids, "Prazo alterado", txt, f"/atividade/{a.id}", autor_id)

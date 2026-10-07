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


def movimento_do_prazo(antigo, novo):
    """Texto curto do movimento: 'adiado em 3 dias úteis (5 corridos)' ou 'antecipado em 1 dia útil (1 corrido)'."""
    if not antigo or not novo:
        return "prazo definido"
    if novo == antigo:
        return "mesma data"
    from team.engine import business_days_between
    corridos = abs((novo - antigo).days)
    uteis = abs(business_days_between(antigo, novo) or 0)
    verbo = "adiado" if novo > antigo else "antecipado"
    u = f"{uteis} dia{'s' if uteis != 1 else ''} útil" if uteis == 1 else f"{uteis} dias úteis"
    c = f"{corridos} corrido{'s' if corridos != 1 else ''}"
    return f"{verbo} em {u} ({c})"


def prazo_alterado(a, antigo, novo, motivo, autor_nome, autor_id=None, rev=None):
    """Toda mudança de prazo vai para a Liderança e para o líder do projeto: notificação (push) com um
    resumo simples e e-mail na hora."""
    de = antigo.strftime("%d/%m/%Y") if antigo else "sem prazo"
    para = novo.strftime("%d/%m/%Y") if novo else "sem prazo"
    txt = (f"{autor_nome} mudou o prazo de “{a.title[:80]}”: {de} → {para}. "
           f"Motivo: {motivo}")
    uids = liderancas_uids() | lideres_projeto_uids(a)
    url = f"/prazo/{rev.id}" if rev is not None and getattr(rev, "id", None) else f"/atividade/{a.id}"
    n = avisa(uids, "Prazo alterado", txt, url, autor_id)
    envia_email_prazo(a, antigo, novo, motivo, autor_nome, rev, uids - {autor_id} if autor_id else uids)
    return n


def corpo_email_prazo(a, antigo, novo, motivo, autor_nome, quando, link):
    de = antigo.strftime("%d/%m/%Y") if antigo else "sem prazo"
    para = novo.strftime("%d/%m/%Y") if novo else "sem prazo"
    linhas = ["Prazo alterado", "", f"Atividade: {a.title}"]
    if a.company:
        linhas.append(f"Empresa: {a.company.name}")
    if a.member:
        linhas.append(f"Responsável: {a.member.name}")
    if a.competency:
        linhas.append(f"Competência: {a.competency.label}")
    linhas += ["",
               f"Data atual (antes): {de}",
               f"Nova data: {para}",
               f"Movimento: {movimento_do_prazo(antigo, novo)}",
               f"Alterado por: {autor_nome}" + (f", em {quando}" if quando else ""),
               "", f"Justificativa: {motivo}", "", f"Ver no portal: {link}"]
    return "\n".join(linhas)


def envia_email_prazo(a, antigo, novo, motivo, autor_nome, rev, uids, extras=()):
    """E-mail imediato da mudança de prazo. `lista=True`: quem está aqui foi escolhido de propósito
    (Liderança e líderes do projeto), então vale até para quem é administrador. Falha de e-mail não
    derruba a alteração. Retorna quantos e-mails saíram."""
    from team import alerts
    import fuso
    emails = []
    for u in User.query.filter(User.id.in_(list(uids or ())), User.active.is_(True)).all():
        if u.email and u.email not in emails:
            emails.append(u.email)
    for e in extras:
        if e and e not in emails:
            emails.append(e)
    if not emails:
        return 0
    link = alerts.PORTAL_URL.rstrip("/") + (f"/prazo/{rev.id}" if rev is not None and getattr(rev, "id", None)
                                           else f"/atividade/{a.id}")
    quando = None
    if rev is not None and getattr(rev, "created_at", None):
        quando = fuso.local(rev.created_at).strftime("%d/%m às %H:%M")
    assunto = f"Prazo alterado: {a.title[:90]}" + (f" — {a.company.name}" if a.company else "")
    corpo = corpo_email_prazo(a, antigo, novo, motivo, autor_nome, quando, link)
    n = 0
    for e in emails:
        ok, _err = alerts.send_email(e, assunto, corpo, lista=True)
        n += 1 if ok else 0
    return n

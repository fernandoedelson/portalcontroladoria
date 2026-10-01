# -*- coding: utf-8 -*-
"""Agendamento de reuniões: a empresa escolhe um horário livre pelo link do e-mail.

Tudo dentro do portal (nenhum dado vai a terceiros). A disponibilidade é a que a
Controladoria cadastra — o portal não lê a agenda do Outlook — e cada reserva
gera um convite .ics para a empresa e para os participantes fixos.
"""
import fuso
import secrets
import uuid
from datetime import datetime, timedelta, timezone, date, time

from models import db, Company, get_setting, set_setting
from team.models_workflow import (AgendaJanela, AgendaBloqueio, AgendaLink, Reuniao,
                                  ListaEmail)

PADRAO = {
    "ag_ativo": "1",
    "ag_duracao": "30",          # minutos
    "ag_antecedencia_h": "24",   # antecedência mínima, em horas
    "ag_janela_dias": "30",      # até quantos dias à frente se pode marcar
    "ag_titulo": "Reunião de resultados com a Controladoria J&F",
    "ag_local": "",              # link do Teams ou sala
    "ag_organizador": "",        # e-mail que recebe os avisos (além da lista de participantes)
    "ag_offset_du": "1",         # regra geral: reunião N dias úteis após o prazo de entrega
}
DIAS = ["Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado", "Domingo"]


def cfg(chave):
    v = get_setting(chave)
    return v if v not in (None, "") else PADRAO.get(chave, "")


def cfg_int(chave, padrao):
    try:
        return int(cfg(chave))
    except (TypeError, ValueError):
        return padrao


def ativo():
    return str(cfg("ag_ativo")) == "1"


# ------------------------------------------------------------------- links
def link_da_empresa(company_id, cria=True):
    l = AgendaLink.query.filter_by(company_id=company_id, teste_email=None).first()
    if not l and cria:
        l = AgendaLink(company_id=company_id, token=secrets.token_urlsafe(16))
        db.session.add(l)
        db.session.commit()
    return l


def url_publica(link, base=None):
    from team import alerts
    return (base or alerts.PORTAL_URL).rstrip("/") + "/agendar/" + link.token


def url_para_empresa(company_id):
    """Link pronto para ir no corpo do e-mail ('' se o agendamento está desligado)."""
    if not ativo() or not company_id:
        return ""
    l = link_da_empresa(company_id)
    return url_publica(l) if l.active else ""


# ------------------------------------------------------------------- horários
def _hm(txt):
    h, m = txt.split(":")
    return time(int(h), int(m))


def membro_da_empresa(company_id):
    """Responsável da Controladoria pela empresa (carteira) ou None."""
    from team.models import CompanyAssignment
    if not company_id:
        return None
    a = CompanyAssignment.query.filter_by(company_id=company_id).first()
    return a.member if a and a.member and a.member.active else None


def dia_reuniao(company_id, comp=None):
    """Dia da reunião da empresa. Padrão: o dia útil seguinte ao prazo de entrega dela
    (prazo no 5º d.u. -> reunião no 6º). Quem decide é a Controladoria: a regra geral
    (`ag_offset_du`) e, por empresa, uma data fixa ou outro número de dias úteis."""
    from models import current_competency
    from team.avisos_empresas import prazo_da_empresa
    from team.engine import add_business_days
    l = (AgendaLink.query.filter_by(company_id=company_id, teste_email=None).first()
         if company_id else None)
    if l and l.dia_fixo:
        return l.dia_fixo
    comp = comp or current_competency()
    prazo, _du = prazo_da_empresa(company_id, comp)
    if not prazo:
        return None
    off = l.offset_du if (l and l.offset_du is not None) else cfg_int("ag_offset_du", 1)
    return add_business_days(prazo, max(off, 0)) if off else prazo


def regra_do_dia(company_id):
    """Texto curto de como o dia da empresa é definido (para a tela)."""
    l = AgendaLink.query.filter_by(company_id=company_id, teste_email=None).first()
    if l and l.dia_fixo:
        return "data fixa"
    if l and l.offset_du is not None:
        return f"{l.offset_du} d.u. após o prazo"
    return "regra geral"


def horarios_livres(member_id=None, company_id=None, ref=None):
    """{date: [datetime,...]} dos horários livres da agenda de UM responsável.

    `member_id=None` = agenda geral. Com `company_id`, só o dia da reunião daquela
    empresa (dia útil seguinte ao prazo). Reuniões de responsáveis diferentes podem
    se sobrepor; para o mesmo responsável, não."""
    ref = ref or fuso.agora()
    dur = timedelta(minutes=cfg_int("ag_duracao", 30))
    minimo = ref + timedelta(hours=cfg_int("ag_antecedencia_h", 24))
    limite = ref.date() + timedelta(days=cfg_int("ag_janela_dias", 30))
    janelas = {}
    for j in AgendaJanela.query.filter(AgendaJanela.active.is_(True),
                                       AgendaJanela.member_id == member_id).all():
        janelas.setdefault(j.weekday, []).append((_hm(j.inicio), _hm(j.fim)))
    if not janelas:
        return {}
    ocupados = [(r.inicio, r.fim) for r in Reuniao.query.filter(
        Reuniao.status == "marcada", Reuniao.fim > ref, Reuniao.member_id == member_id,
        db.or_(Reuniao.teste.is_(False), Reuniao.teste.is_(None))).all()]
    ocupados += [(b.inicio, b.fim) for b in AgendaBloqueio.query.filter(
        AgendaBloqueio.fim > ref,
        db.or_(AgendaBloqueio.member_id.is_(None), AgendaBloqueio.member_id == member_id)).all()]
    so_dia = dia_reuniao(company_id) if company_id else None
    if company_id and not so_dia:
        return {}
    out = {}
    d = ref.date()
    while d <= limite:
        if (so_dia is None or d == so_dia) and d.weekday() in janelas and fuso.dia_util(d):
            for ini, fim in janelas[d.weekday()]:
                cur = datetime.combine(d, ini)
                fim_dt = datetime.combine(d, fim)
                while cur + dur <= fim_dt:
                    if cur >= minimo and not any(cur < o_fim and cur + dur > o_ini
                                                 for o_ini, o_fim in ocupados):
                        out.setdefault(d, []).append(cur)
                    cur += dur
        d += timedelta(days=1)
    return out


def livres_do_link(link):
    """Horários livres para quem abriu este link: a agenda do responsável da empresa."""
    mem = membro_da_empresa(link.company_id)
    return horarios_livres(mem.id if mem else None, link.company_id), mem


# ---------------------------------------------------------------- convite .ics
def _utc(dt_local):
    """Horário de Brasília (sem fuso) -> UTC no formato do iCalendar."""
    return dt_local.replace(tzinfo=fuso.FUSO).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _esc(t):
    return (t or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def ics(r, participantes, cancelar=False, organizador=None):
    from team import alerts
    org = organizador or alerts.SMTP_FROM or "controladoria@jfsa.com.br"
    linhas = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Controladoria J&F//Portal//PT",
              "CALSCALE:GREGORIAN", f"METHOD:{'CANCEL' if cancelar else 'REQUEST'}",
              "BEGIN:VEVENT", f"UID:{r.uid}", f"SEQUENCE:{r.sequencia or 0}",
              f"DTSTAMP:{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
              f"DTSTART:{_utc(r.inicio)}", f"DTEND:{_utc(r.fim)}",
              f"SUMMARY:{_esc(cfg('ag_titulo'))}" + (f" — {_esc(r.company.name)}" if r.company else ""),
              f"STATUS:{'CANCELLED' if cancelar else 'CONFIRMED'}",
              f"ORGANIZER;CN=Controladoria J&F:mailto:{org}"]
    if cfg("ag_local"):
        linhas.append(f"LOCATION:{_esc(cfg('ag_local'))}")
    linhas.append(f"DESCRIPTION:{_esc('Reunião de resultados com a Controladoria J&F.')}")
    linhas += ["TRANSP:OPAQUE", "X-MICROSOFT-CDO-BUSYSTATUS:BUSY"]
    for e in participantes:
        linhas.append(f"ATTENDEE;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:{e}")
    linhas += ["END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(linhas) + "\r\n"


def contatos_da_empresa(company_id):
    """E-mails dos responsáveis cadastrados na empresa (Entidades › Detalhes)."""
    c = db.session.get(Company, company_id) if company_id else None
    return [x.email for x in c.contatos if x.active and x.email] if c else []


def participantes_fixos():
    lista = [x.email for x in ListaEmail.query.filter_by(tipo="agenda", active=True).all()]
    org = cfg("ag_organizador")
    if org and org.lower() not in [e.lower() for e in lista]:
        lista.append(org)
    return lista


def participantes_da_empresa(company_id, membro=None):
    """Quem entra em TODO convite da empresa: os responsáveis cadastrados nela, o
    responsável da Controladoria por ela (carteira) e as lideranças."""
    from models import User
    from team.models import CompanyAssignment
    emails = []
    if company_id:
        c = db.session.get(Company, company_id)
        if c:
            emails += [x.email for x in c.contatos if x.active and x.email]
        if membro is None:
            a = CompanyAssignment.query.filter_by(company_id=company_id).first()
            membro = a.member if a else None
    if membro and membro.user and membro.user.email:
        emails.append(membro.user.email)
    emails += [u.email for u in User.query.filter_by(role="lideranca", active=True).all() if u.email]
    return emails


def participantes(r):
    """Todos os e-mails do convite, sem repetir (o solicitante vem primeiro)."""
    vistos, out = set(), []
    for e in [r.email] + participantes_da_empresa(r.company_id, r.member) + participantes_fixos():
        k = (e or "").strip().lower()
        if k and k not in vistos:
            vistos.add(k)
            out.append(e.strip())
    return out


def _enviar(r, cancelar=False):
    from team import alerts
    todos = participantes(r)
    simulado = None
    if r.teste:
        link = db.session.get(AgendaLink, r.link_id) if r.link_id else None
        destino = (link.teste_email if link else None) or r.email
        simulado = ("SIMULAÇÃO — nada foi enviado às pessoas abaixo; em produção este "
                    "e-mail iria para: " + ", ".join(todos) + ".\n\n")
        todos = [destino]
    metodo = "CANCEL" if cancelar else "REQUEST"
    convite = (ics(r, todos, cancelar=cancelar), metodo)
    quando = f"{r.inicio.strftime('%d/%m/%Y')} às {r.inicio.strftime('%H:%M')}"
    empresa = r.company.name if r.company else ""
    titulo = cfg("ag_titulo")
    if cancelar:
        assunto = f"Cancelada: {titulo} — {quando}"
        corpo = f"A reunião de {quando} foi cancelada.\n"
    else:
        assunto = f"{titulo} — {quando}"
        corpo = (f"{titulo}" + (f" — {empresa}" if empresa else "") + f".\nQuando: {quando} ({cfg_int('ag_duracao', 30)} min)\n"
                 + (f"Com: {r.member.name}\n" if r.member else "")
                 + (f"Local: {cfg('ag_local')}\n" if cfg("ag_local") else "")
                 + f"\nPara cancelar ou remarcar: {alerts.PORTAL_URL.rstrip('/')}/agendar/cancelar/{r.token_cancelar}\n")
    if simulado:
        assunto, corpo = "[SIMULAÇÃO] " + assunto, simulado + corpo
    falhas = []
    for e in todos:
        ok, err = alerts.send_email(e, assunto, corpo, lista=True, calendario=convite)
        if not ok and err != "email_desligado":
            falhas.append(f"{e}: {err}")
    return falhas


# ---------------------------------------------------------------- reservar
def reservar(link, inicio, email=None):
    """Cria a reunião se o horário continua livre. Retorna (reuniao, erro).

    Quem marca é a própria empresa: o convite vai aos responsáveis cadastrados nela (e às
    demais pessoas do convite). Só se a empresa não tiver ninguém cadastrado é que se pede
    um e-mail (`email`)."""
    contatos = contatos_da_empresa(link.company_id)
    email = (email or "").strip().lower()
    if not contatos:
        if "@" not in email or "." not in email.split("@")[-1]:
            return None, "Informe um e-mail válido para receber o convite."
        contatos = [email]
    livres, mem = livres_do_link(link)
    if inicio not in livres.get(inicio.date(), []):
        return None, "Esse horário acabou de ser ocupado. Escolha outro."
    ontem = datetime.utcnow() - timedelta(days=1)
    if (not link.teste_email and Reuniao.query.filter(
            Reuniao.link_id == link.id, Reuniao.status == "marcada", Reuniao.criada_em >= ontem).count() >= 3):
        return None, "Limite de reservas por dia atingido para esta empresa. Fale com a Controladoria."
    c = db.session.get(Company, link.company_id) if link.company_id else None
    r = Reuniao(link_id=link.id, company_id=link.company_id, teste=bool(link.teste_email),
                member_id=(mem.id if mem else None),
                nome=(c.name if c else "Reunião")[:120], email=contatos[0][:160],
                inicio=inicio, fim=inicio + timedelta(minutes=cfg_int("ag_duracao", 30)),
                uid=f"{uuid.uuid4()}@controladoria-jf", token_cancelar=secrets.token_urlsafe(16))
    db.session.add(r)
    db.session.commit()
    r._falhas = _enviar(r)
    return r, None


def cancelar(r):
    if r.status == "cancelada":
        return
    r.status = "cancelada"
    r.sequencia = (r.sequencia or 0) + 1
    db.session.commit()
    _enviar(r, cancelar=True)


def criar_simulacao(company_id, email):
    """Link de simulação: abre a mesma página da empresa, mas tudo vai só para `email`."""
    l = AgendaLink(company_id=company_id or None, token=secrets.token_urlsafe(16),
                   teste_email=email.strip().lower())
    db.session.add(l)
    db.session.commit()
    return l


def encerrar_simulacao(link):
    """Apaga o link de simulação e as reservas feitas por ele."""
    Reuniao.query.filter_by(link_id=link.id).delete()
    db.session.delete(link)
    db.session.commit()


def enviar_teste(email, company_id):
    """Manda o convite EXATAMENTE como a empresa receberia, só para `email`, sem criar
    reserva. Usa o primeiro horário livre da agenda do responsável (ou amanhã 09:00)."""
    from team import alerts
    c = db.session.get(Company, company_id) if company_id else None
    mem = membro_da_empresa(company_id)
    livres = horarios_livres(mem.id if mem else None, company_id)
    if livres:
        inicio = livres[sorted(livres)[0]][0]
    else:
        inicio = datetime.combine(fuso.hoje() + timedelta(days=1), time(9, 0))
    r = Reuniao(nome="Teste de agendamento", email=email,
                inicio=inicio, fim=inicio + timedelta(minutes=cfg_int("ag_duracao", 30)),
                uid=f"teste-{uuid.uuid4()}@controladoria-jf", token_cancelar=secrets.token_urlsafe(8),
                sequencia=0, company_id=company_id)
    r.company, r.member = c, mem
    convite = (ics(r, [email]), "REQUEST")
    quando = f"{inicio.strftime('%d/%m/%Y')} às {inicio.strftime('%H:%M')}"
    corpo = (f"[TESTE] Reunião marcada para {quando} ({cfg_int('ag_duracao', 30)} min)"
             + (f" — {c.name}" if c else "") + ".\n"
             + (f"Com: {mem.name}\n" if mem else "")
             + (f"Local: {cfg('ag_local')}\n" if cfg("ag_local") else "")
             + "\nEste é um convite de teste: nenhuma reserva foi criada. O convite aparece na própria mensagem, com os botões Aceitar/Recusar do Outlook.\n"
             f"Link da página que a empresa usa: {url_para_empresa(company_id) or '(agendamento desligado)'}\n")
    return alerts.send_email(email, f"[TESTE] Reunião confirmada: Controladoria J&F — {quando}",
                             corpo, lista=True, calendario=convite), quando

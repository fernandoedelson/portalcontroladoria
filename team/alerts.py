# -*- coding: utf-8 -*-
"""Canais de alerta e ciclo de cobranca do modulo de Gestao do Time.

Politica de sigilo:
- E-mail e Painel (rede interna): podem conter o nome da atividade.
- WhatsApp (Twilio, terceiro): SOMENTE mensagem generica, sem nome de
  atividade/projeto. Ver `_whatsapp_generic`.

Ambos os canais externos sao "guardados": se a dependencia/credencial nao
existir, o envio e registrado como 'simulado' e nada quebra. Isso deixa a
ferramenta plenamente utilizavel na fase de validacao local.
"""
import os
from datetime import date, datetime

from models import db, User, Notification, log_audit
from team.models import (Activity, TeamMember, AlertChannelSetting, AlertLog)


# --------------------------------------------------------------------------
# Configuracao (variaveis de ambiente; defaults seguros para validacao local)
# --------------------------------------------------------------------------
def _flag(name, default=False):
    v = os.environ.get(name)
    if v is None:
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "on", "sim")


EMAIL_ENABLED = _flag("TEAM_ALERTS_EMAIL", False)       # Outlook COM
WHATSAPP_ENABLED = _flag("TEAM_ALERTS_WHATSAPP", False)  # Twilio (num. aprovado)
PORTAL_URL = os.environ.get("TEAM_PORTAL_URL", "http://127.0.0.1:5050")


# --------------------------------------------------------------------------
# Canal: E-mail via Outlook COM (caixa corporativa local, sem SMTP)
# --------------------------------------------------------------------------
def send_email_outlook(to_addr, subject, body):
    """Envia e-mail pela instancia local do Outlook (COM).

    Retorna (ok, error). Se pywin32/Outlook indisponivel ou desligado por flag,
    retorna (False, motivo) sem lancar excecao.
    """
    if not EMAIL_ENABLED:
        return False, "email_desligado"
    if not to_addr:
        return False, "sem_destinatario"
    try:
        import pythoncom  # noqa
        import win32com.client as win32
        pythoncom.CoInitialize()
        try:
            outlook = win32.Dispatch("Outlook.Application")
            mail = outlook.CreateItem(0)  # olMailItem
            mail.To = to_addr
            mail.Subject = subject
            mail.Body = body
            mail.Send()
            return True, None
        finally:
            pythoncom.CoUninitialize()
    except Exception as e:   # pragma: no cover - depende de ambiente Windows/Outlook
        return False, f"{type(e).__name__}: {e}"


# --------------------------------------------------------------------------
# Canal: WhatsApp via Twilio (SOMENTE conteudo generico)
# --------------------------------------------------------------------------
def _whatsapp_generic(n_items):
    """Mensagem generica sem qualquer dado sensivel (nome de atividade/projeto)."""
    if n_items <= 0:
        return None
    plural = "atividade" if n_items == 1 else "atividades"
    return (f"Controladoria J&F: voce tem {n_items} {plural} pendente(s) que "
            f"requerem atencao hoje. Acesse o portal para os detalhes: {PORTAL_URL}")


def send_whatsapp(to_number, generic_message):
    """Envia WhatsApp generico via Twilio. Guardado por flag/credencial.

    Nunca inclua nome de atividade/projeto aqui — o texto ja chega pronto e
    generico de `_whatsapp_generic`.
    """
    if not WHATSAPP_ENABLED:
        return False, "whatsapp_desligado"
    if not to_number or not generic_message:
        return False, "sem_destinatario_ou_msg"
    sid = os.environ.get("TWILIO_ACCOUNT_SID")
    token = os.environ.get("TWILIO_AUTH_TOKEN")
    from_wa = os.environ.get("TWILIO_WHATSAPP_FROM")  # ex.: 'whatsapp:+14155238886'
    if not (sid and token and from_wa):
        return False, "credenciais_twilio_ausentes"
    try:  # pragma: no cover - depende de credencial/externo
        from twilio.rest import Client
        client = Client(sid, token)
        to = to_number if to_number.startswith("whatsapp:") else f"whatsapp:{to_number}"
        client.messages.create(body=generic_message, from_=from_wa, to=to)
        return True, None
    except Exception as e:   # pragma: no cover
        return False, f"{type(e).__name__}: {e}"


# --------------------------------------------------------------------------
# Canal: Painel (notificacao interna — reusa Notification do app hospedeiro)
# --------------------------------------------------------------------------
def send_panel(member, title, message, url="/time"):
    """Cria notificacao no portal para o usuario ligado ao membro (se houver).

    Nao faz commit — o ciclo de alertas comita uma unica vez ao final (lote).
    """
    if not member or not member.user_id:
        return False, "membro_sem_login"
    db.session.add(Notification(user_id=member.user_id, title=title,
                                message=message, kind="cobranca", url=url))
    return True, None


# --------------------------------------------------------------------------
# Registro / idempotencia
# --------------------------------------------------------------------------
def _already_sent(dedup_key):
    return db.session.query(AlertLog.id).filter_by(dedup_key=dedup_key).first() is not None


def _record(event_key, channel, member_id, activity_id, dedup_key,
            subject, body, status, error=None):
    # Sem commit por linha: o ciclo comita uma unica vez ao final (autoflush
    # mantem os registros visiveis ao _already_sent dentro do mesmo ciclo).
    db.session.add(AlertLog(event_key=event_key, channel=channel, member_id=member_id,
                            activity_id=activity_id, dedup_key=dedup_key, subject=subject,
                            body=body, status=status, error=error))


def _dispatch(event_key, setting, member, activity, subject, body,
              n_generic=1, ref=None):
    """Envia um evento por todos os canais ligados na matriz, com idempotencia.

    Retorna lista de dicts {channel, status, error}.
    """
    ref = ref or date.today()
    out = []
    aid = activity.id if activity else None
    mid = member.id if member else None
    base = f"{ref.isoformat()}:{event_key}:{mid}:{aid}"

    # painel
    if setting.painel and member and member.user_id:
        dk = base + ":painel"
        if not _already_sent(dk):
            ok, err = send_panel(member, subject, body)
            _record(event_key, "painel", mid, aid, dk, subject, body,
                    "enviado" if ok else "falha", err)
            out.append({"channel": "painel", "status": "enviado" if ok else "falha",
                        "error": err})

    # email
    if setting.email and member:
        to = member.user.email if member.user else None
        dk = base + ":email"
        if not _already_sent(dk):
            ok, err = send_email_outlook(to, subject, body)
            _record(event_key, "email", mid, aid, dk, subject, body,
                    "enviado" if ok else "simulado", err)
            out.append({"channel": "email", "status": "enviado" if ok else "simulado",
                        "error": err})

    # whatsapp (generico!)
    if setting.whatsapp and member:
        generic = _whatsapp_generic(n_generic)
        to = _member_phone(member)
        dk = base + ":whatsapp"
        if generic and not _already_sent(dk):
            ok, err = send_whatsapp(to, generic)
            _record(event_key, "whatsapp", mid, aid, dk, "(generico)", generic,
                    "enviado" if ok else "simulado", err)
            out.append({"channel": "whatsapp", "status": "enviado" if ok else "simulado",
                        "error": err})
    return out


def _member_phone(member):
    # telefone ainda nao modelado; placeholder para quando entrar no cadastro
    return None


def _manager():
    return TeamMember.query.filter_by(is_manager=True, active=True).first()


# --------------------------------------------------------------------------
# Ciclo de alertas (idempotente por dia) — o "controle e aviso de execucao/atraso"
# --------------------------------------------------------------------------
def run_alert_cycle(ref=None, dry_run=False):
    """Varre atividades abertas e dispara lembretes/vencimentos/atrasos conforme a matriz.

    Idempotente: cada (dia, evento, membro, atividade, canal) so dispara uma vez.
    Se dry_run=True, apenas conta o que seria enviado (sem registrar/enviar).
    Retorna um resumo.
    """
    from team.engine import add_business_days
    ref = ref or date.today()
    settings = {s.event_key: s for s in AlertChannelSetting.query.all()}
    summary = {"lembrete_previo": 0, "vence_hoje": 0, "atraso": 0,
               "por_canal": {}, "detalhe": []}

    open_acts = (Activity.query
                 .filter(Activity.status.in_(["pendente", "em_andamento", "bloqueada"]))
                 .filter(Activity.due_provisional.is_(False))
                 .filter(Activity.due_date.isnot(None)).all())

    # agrupa contagem por membro para a mensagem generica do WhatsApp
    per_member_open = {}
    for a in open_acts:
        per_member_open[a.member_id] = per_member_open.get(a.member_id, 0) + 1

    for a in open_acts:
        member = a.member
        n_open = per_member_open.get(a.member_id, 1)

        # --- lembrete previo (X dias uteis antes) ---
        s = settings.get("lembrete_previo")
        if s and a.due_date:
            trigger = add_business_days(a.due_date, -(s.lead_days or 1))
            if trigger == ref and a.due_date > ref:
                subject = f"Lembrete: “{a.title}” vence em {a.due_date.strftime('%d/%m')}"
                body = _body(a)
                summary["lembrete_previo"] += 1
                if not dry_run:
                    res = _dispatch("lembrete_previo", s, member, a, subject, body,
                                    n_generic=n_open, ref=ref)
                    _tally(summary, res)

        # --- vence hoje ---
        s = settings.get("vence_hoje")
        if s and a.due_date == ref:
            subject = f"Vence hoje: “{a.title}”"
            body = _body(a)
            summary["vence_hoje"] += 1
            if not dry_run:
                res = _dispatch("vence_hoje", s, member, a, subject, body,
                                n_generic=n_open, ref=ref)
                _tally(summary, res)

        # --- atraso (com escalada ao gestor) ---
        s = settings.get("atraso")
        if s and a.due_date < ref:
            atraso_du = abs(a.days_to_due(ref) or 0)
            subject = f"ATRASO: “{a.title}” (venceu {a.due_date.strftime('%d/%m')})"
            body = _body(a)
            summary["atraso"] += 1
            if not dry_run:
                res = _dispatch("atraso", s, member, a, subject, body,
                                n_generic=n_open, ref=ref)
                _tally(summary, res)
                # escalada: notifica o gestor no painel
                if s.escalate_manager:
                    mgr = _manager()
                    if mgr and mgr.user_id and mgr.id != (member.id if member else None):
                        dk = f"{ref.isoformat()}:atraso_escalada:{a.id}"
                        if not _already_sent(dk):
                            who = member.name if member else "sem responsável"
                            msg = (f"Atividade “{a.title}” de {who} está atrasada "
                                   f"({a.due_date.strftime('%d/%m/%Y')}).")
                            db.session.add(Notification(
                                user_id=mgr.user_id, title="Escalada de atraso",
                                message=msg, kind="cobranca", url="/time"))
                            _record("atraso", "painel", mgr.id, a.id, dk,
                                    "Escalada de atraso", msg, "enviado")
                            _tally(summary, [{"channel": "escalada", "status": "enviado"}])

    if not dry_run:
        db.session.commit()   # persiste todos os AlertLog/Notification do ciclo em lote
        log_audit(None, "ciclo_alertas", "team",
                  f"prev={summary['lembrete_previo']} hoje={summary['vence_hoje']} "
                  f"atraso={summary['atraso']}")
    return summary


def _body(a):
    parts = [a.title]
    if a.company:
        parts.append(f"Empresa: {a.company.name}")
    if a.competency:
        parts.append(f"Competência: {a.competency.label}")
    if a.due_date:
        parts.append(f"Prazo: {a.due_date.strftime('%d/%m/%Y')}")
    if a.member:
        parts.append(f"Responsável: {a.member.name}")
    parts.append(f"\nAbra o portal: {PORTAL_URL}/atividade/{a.id}")
    return "\n".join(parts)


def _tally(summary, results):
    for r in results:
        key = f"{r['channel']}:{r['status']}"
        summary["por_canal"][key] = summary["por_canal"].get(key, 0) + 1

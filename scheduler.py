# -*- coding: utf-8 -*-
"""Agendador do portal — o que faz o sistema avisar sozinho.

Sem isto, a régua de avisos e a matriz de alertas só disparam quando alguém
clica; ou seja, o portal só cobra quem já está olhando para ele. Roda numa
thread daemon junto da aplicação (nao exige servico externo).

Configuracao (aba Administracao ou tabela settings):
    scheduler_enabled   "1" liga / "0" desliga           (padrao: ligado)
    scheduler_hour      hora do disparo diario (0-23)     (padrao: 8)
    digest_weekday      dia do resumo semanal (0=segunda) (padrao: 0)
"""
import fuso
import threading
import traceback
from datetime import datetime, date

_thread = None
_lock = threading.Lock()
CHECK_INTERVAL = 300           # confere a cada 5 minutos


def _log(app, msg):
    try:
        app.logger.info("[scheduler] %s", msg)
    except Exception:
        print(f"[scheduler] {msg}")


def _get(app, key, default):
    from models import get_setting
    v = get_setting(key)
    return v if v not in (None, "") else default


def _already_ran(app, key, hoje):
    from models import get_setting
    return _s(get_setting(f"last_{key}")) == hoje.isoformat()


def _s(v):
    return str(v) if v is not None else ""


def _mark(app, key, hoje):
    from models import set_setting
    set_setting(f"last_{key}", hoje.isoformat())


def run_daily_tasks(app, force=False):
    """Executa as tarefas do dia. Idempotente: nao repete no mesmo dia."""
    from models import db, User, notify
    from team import alerts as team_alerts

    resultado = {"alertas": None, "cobrancas": 0, "resumo": False, "tarefas": 0}
    hoje = fuso.hoje()

    # 1) ciclo de alertas do time (lembrete previo, vence hoje, atraso)
    if force or not _already_ran(app, "alertas", hoje):
        try:
            resultado["alertas"] = team_alerts.run_alert_cycle()
            _mark(app, "alertas", hoje)
            _log(app, f"ciclo de alertas: {resultado['alertas']}")
        except Exception:
            _log(app, "falha no ciclo de alertas:\n" + traceback.format_exc())

    # 1b) aviso de vencimento das tarefas pessoais
    if force or not _already_ran(app, "tarefas", hoje):
        try:
            resultado["tarefas"] = _avisar_tarefas(app, hoje)
            _mark(app, "tarefas", hoje)
            if resultado["tarefas"]:
                _log(app, f"avisos de tarefa: {resultado['tarefas']}")
        except Exception:
            _log(app, "falha nos avisos de tarefa:\n" + traceback.format_exc())

    # (cobranca das empresas removida: pertencia ao modulo de Consolidacao)

    # 3) resumo semanal para a controladoria
    dia_resumo = int(_get(app, "digest_weekday", 0))
    if (force or hoje.weekday() == dia_resumo) and (
            force or not _already_ran(app, "resumo", hoje)):
        try:
            from workflow_routes import envia_resumo
            snap = envia_resumo()
            _mark(app, "resumo", hoje)
            resultado["resumo"] = True
            _log(app, f"resumo semanal enviado (e-mail {snap.email_ok}/{snap.destinos})"
                 + (f"\n{snap.email_falhas}" if snap.email_falhas else ""))
        except Exception:
            _log(app, "falha no resumo:\n" + traceback.format_exc())

    return resultado


def _avisar_tarefas(app, hoje):
    """Notifica o dono de cada tarefa com aviso ligado.

    Comeca na data do aviso (vencimento - antecedencia escolhida) e continua
    uma vez por dia (reminded_on) ate a tarefa ser concluida — inclusive em atraso.
    """
    from datetime import timedelta
    from models import db, notify, User
    from team.models import TeamMember
    from team.models_workflow import PersonalTask
    from team import alerts as canais

    maior_antecedencia = max(d for d, _ in PersonalTask.ANTECEDENCIAS)
    candidatas = PersonalTask.query.filter(
        PersonalTask.remind.is_(True),
        PersonalTask.done.is_(False),
        PersonalTask.due_date.isnot(None),
        PersonalTask.due_date <= hoje + timedelta(days=maior_antecedencia),
        db.or_(PersonalTask.reminded_on.is_(None),
               PersonalTask.reminded_on < hoje)).all()
    pend = [t for t in candidatas if t.data_do_aviso() <= hoje]
    enviados = 0
    for t in pend:
        faltam = (t.due_date - hoje).days
        if faltam > 0:
            quando, titulo = f"vence em {faltam} dia(s)", "Tarefa a vencer"
        elif faltam == 0:
            quando, titulo = "vence hoje", "Tarefa vence hoje"
        else:
            quando, titulo = f"venceu há {-faltam} dia(s)", "Tarefa atrasada"
        texto = (f"“{t.title[:120]}” {quando} "
                 f"({t.due_date.strftime('%d/%m/%Y')}).")
        notify(t.user_id, titulo, texto, kind="tarefa", url="/tarefas")
        # tarefa pessoal: o e-mail vai so para o dono, pode trazer o titulo
        u = db.session.get(User, t.user_id)
        if u and u.email:
            ok, err = canais.send_email(
                u.email, f"{titulo} — Portal Controladoria",
                f"{texto}\n\nAbra suas tarefas: {canais.PORTAL_URL}/tarefas")
            if err and err != "email_desligado":
                _log(app, f"aviso de tarefa por e-mail falhou ({u.email}): {err}")
        # WhatsApp (terceiro): mensagem generica, sem o titulo da tarefa
        m = TeamMember.query.filter_by(user_id=t.user_id).first()
        numero = canais.normaliza_whatsapp(m.whatsapp) if m else None
        if numero:
            ok, err = canais.send_whatsapp(
                numero, "Controladoria J&F: você tem uma tarefa pessoal vencendo. "
                        f"Veja em {canais.PORTAL_URL}/tarefas")
            if err and err != "whatsapp_desligado":
                _log(app, f"aviso de tarefa por WhatsApp falhou: {err}")
        t.reminded_on = hoje
        enviados += 1
    if enviados:
        db.session.commit()
    return enviados


def _loop(app):
    import time
    while True:
        try:
            with app.app_context():
                from models import get_setting
                if _s(_get(app, "scheduler_enabled", "1")) == "1":
                    hora = int(_get(app, "scheduler_hour", 8))
                    agora = fuso.agora()
                    if agora.hour >= hora:
                        run_daily_tasks(app)
        except Exception:
            _log(app, "erro no loop:\n" + traceback.format_exc())
        time.sleep(CHECK_INTERVAL)


def start(app):
    """Sobe a thread do agendador (uma vez por processo)."""
    global _thread
    with _lock:
        if _thread and _thread.is_alive():
            return _thread
        if app.config.get("TESTING"):
            return None
        _thread = threading.Thread(target=_loop, args=(app,), daemon=True,
                                   name="portal-scheduler")
        _thread.start()
        _log(app, "agendador iniciado")
        return _thread


def status():
    return {"ativo": bool(_thread and _thread.is_alive()),
            "intervalo_s": CHECK_INTERVAL}

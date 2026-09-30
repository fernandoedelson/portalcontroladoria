# -*- coding: utf-8 -*-
"""Rotas do agendamento: tela interna (controladoria) e páginas públicas por token."""
import fuso
import secrets
from datetime import datetime, timedelta
from functools import wraps

from flask import (render_template, request, redirect, url_for, flash, abort)
from flask_login import login_required, current_user

from models import db, Company, log_audit, set_setting
from team import agenda
from team.models_workflow import AgendaJanela, AgendaBloqueio, AgendaLink, Reuniao


def register_agenda_routes(app):

    def controladoria_required(f):
        @wraps(f)
        @login_required
        def wrap(*a, **k):
            if not current_user.is_controladoria:
                abort(403)
            return f(*a, **k)
        return wrap

    def _volta():
        return redirect(url_for("team_agenda_reunioes"))

    # ==================================================================
    # TELA INTERNA
    # ==================================================================
    @app.route("/agenda-reunioes")
    @controladoria_required
    def team_agenda_reunioes():
        from team.models_workflow import ListaEmail
        links = {l.company_id: l for l in AgendaLink.query.all()}
        empresas = Company.query.filter_by(active=True).order_by(Company.name).all()
        proximas = (Reuniao.query.filter(Reuniao.status == "marcada",
                                         Reuniao.fim >= fuso.agora())
                    .order_by(Reuniao.inicio).all())
        passadas = (Reuniao.query.filter(Reuniao.fim < fuso.agora())
                    .order_by(Reuniao.inicio.desc()).limit(10).all())
        return render_template(
            "team/agenda_reunioes.html", cfg=agenda.cfg, ativo=agenda.ativo(), dias=agenda.DIAS,
            janelas=AgendaJanela.query.order_by(AgendaJanela.weekday, AgendaJanela.inicio).all(),
            bloqueios=AgendaBloqueio.query.filter(AgendaBloqueio.fim >= fuso.agora())
            .order_by(AgendaBloqueio.inicio).all(),
            empresas=empresas, links=links, proximas=proximas, passadas=passadas,
            url_publica=agenda.url_publica,
            geral=links.get(None),
            fixos=ListaEmail.query.filter_by(tipo="agenda").count())

    @app.route("/agenda-reunioes/config", methods=["POST"])
    @controladoria_required
    def team_agenda_config():
        f = request.form
        set_setting("ag_ativo", "1" if f.get("ativo") else "0")
        for chave, campo, lo, hi in (("ag_duracao", "duracao", 10, 240),
                                     ("ag_antecedencia_h", "antecedencia", 0, 720),
                                     ("ag_janela_dias", "janela", 1, 180)):
            v = f.get(campo, type=int)
            if v is not None and lo <= v <= hi:
                set_setting(chave, str(v))
        set_setting("ag_titulo", (f.get("titulo") or "").strip()[:120] or agenda.PADRAO["ag_titulo"])
        set_setting("ag_local", (f.get("local") or "").strip()[:240])
        org = (f.get("organizador") or "").strip().lower()
        set_setting("ag_organizador", org if "@" in org else "")
        db.session.commit()
        flash("Configuração do agendamento salva.", "success")
        return _volta()

    @app.route("/agenda-reunioes/janela", methods=["POST"])
    @controladoria_required
    def team_agenda_janela_add():
        dias = [int(x) for x in request.form.getlist("dia") if x.isdigit() and 0 <= int(x) <= 6]
        ini, fim = request.form.get("inicio") or "", request.form.get("fim") or ""
        try:
            ok = agenda._hm(ini) < agenda._hm(fim)
        except Exception:
            ok = False
        if not dias or not ok:
            flash("Escolha os dias e um horário inicial antes do final.", "warning")
            return _volta()
        for d in dias:
            db.session.add(AgendaJanela(weekday=d, inicio=ini, fim=fim))
        db.session.commit()
        flash(f"{len(dias)} janela(s) incluída(s).", "success")
        return _volta()

    @app.route("/agenda-reunioes/janela/<int:jid>/remover", methods=["POST"])
    @controladoria_required
    def team_agenda_janela_del(jid):
        j = db.session.get(AgendaJanela, jid) or abort(404)
        db.session.delete(j)
        db.session.commit()
        return _volta()

    @app.route("/agenda-reunioes/bloqueio", methods=["POST"])
    @controladoria_required
    def team_agenda_bloqueio_add():
        try:
            d1 = datetime.strptime(request.form.get("inicio_dia") + " " + (request.form.get("inicio_hora") or "00:00"), "%Y-%m-%d %H:%M")
            d2 = datetime.strptime((request.form.get("fim_dia") or request.form.get("inicio_dia")) + " " + (request.form.get("fim_hora") or "23:59"), "%Y-%m-%d %H:%M")
        except (TypeError, ValueError):
            flash("Informe o dia (e, se quiser, o horário) do bloqueio.", "warning")
            return _volta()
        if d2 <= d1:
            flash("O fim precisa ser depois do início.", "warning")
            return _volta()
        db.session.add(AgendaBloqueio(inicio=d1, fim=d2, motivo=(request.form.get("motivo") or "").strip()[:160] or None))
        db.session.commit()
        flash("Período bloqueado: não aparece para agendamento.", "success")
        return _volta()

    @app.route("/agenda-reunioes/bloqueio/<int:bid>/remover", methods=["POST"])
    @controladoria_required
    def team_agenda_bloqueio_del(bid):
        b = db.session.get(AgendaBloqueio, bid) or abort(404)
        db.session.delete(b)
        db.session.commit()
        return _volta()

    @app.route("/agenda-reunioes/link/<int:cid>/<acao>", methods=["POST"])
    @controladoria_required
    def team_agenda_link(cid, acao):
        """cid = id da empresa (0 = link geral)."""
        comp = cid or None
        l = AgendaLink.query.filter_by(company_id=comp).first()
        if acao == "criar" and not l:
            agenda.link_da_empresa(comp)
        elif l and acao == "alternar":
            l.active = not l.active
        elif l and acao == "renovar":
            l.token = secrets.token_urlsafe(16)
            l.active = True
            flash("Novo link gerado: o anterior deixou de funcionar.", "success")
        db.session.commit()
        return _volta()

    @app.route("/agenda-reunioes/<int:rid>/cancelar", methods=["POST"])
    @controladoria_required
    def team_agenda_cancelar(rid):
        r = db.session.get(Reuniao, rid) or abort(404)
        agenda.cancelar(r)
        log_audit(current_user.id, "reuniao_cancelada", "reuniao", str(rid))
        flash("Reunião cancelada e o cancelamento foi enviado aos participantes.", "success")
        return _volta()

    # ==================================================================
    # PÁGINAS PÚBLICAS (sem login) — só mostram horários livres
    # ==================================================================
    @app.route("/agendar/<token>", methods=["GET", "POST"])
    def agendar_publico(token):
        l = AgendaLink.query.filter_by(token=token, active=True).first()
        if not l or not agenda.ativo():
            return render_template("agendar.html", indisponivel=True), 404
        erro, feito = None, None
        if request.method == "POST":
            try:
                inicio = datetime.strptime(request.form.get("horario") or "", "%Y-%m-%dT%H:%M")
            except ValueError:
                inicio = None
            if not inicio:
                erro = "Escolha um horário."
            else:
                feito, erro = agenda.reservar(l, request.form.get("nome"), request.form.get("email"),
                                              request.form.get("assunto"), inicio)
        livres = {} if feito else agenda.horarios_livres()
        return render_template("agendar.html", link=l, livres=livres, erro=erro, feito=feito,
                               titulo=agenda.cfg("ag_titulo"), local=agenda.cfg("ag_local"),
                               duracao=agenda.cfg_int("ag_duracao", 30), dias=agenda.DIAS,
                               pre_email=request.form.get("email", ""), pre_nome=request.form.get("nome", ""))

    @app.route("/agendar/cancelar/<token>", methods=["GET", "POST"])
    def agendar_cancelar(token):
        r = Reuniao.query.filter_by(token_cancelar=token).first() or abort(404)
        feito = False
        if request.method == "POST" and r.status == "marcada":
            agenda.cancelar(r)
            feito = True
        return render_template("agendar.html", cancelar=r, feito_cancel=feito,
                               titulo=agenda.cfg("ag_titulo"))

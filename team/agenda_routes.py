# -*- coding: utf-8 -*-
"""Rotas do agendamento: tela interna (cada responsável com a sua agenda) e páginas públicas."""
import fuso
import secrets
from datetime import datetime, timedelta
from functools import wraps

from flask import (render_template, request, redirect, url_for, flash, abort)
from flask_login import login_required, current_user

from models import db, Company, log_audit, set_setting
from team import agenda
from team.models import TeamMember
from team.models_workflow import AgendaJanela, AgendaBloqueio, AgendaLink, Reuniao


def register_agenda_routes(app):

    def team_required(f):
        @wraps(f)
        @login_required
        def wrap(*a, **k):
            if not current_user.is_team:
                abort(403)
            return f(*a, **k)
        return wrap

    def controladoria_required(f):
        @wraps(f)
        @login_required
        def wrap(*a, **k):
            if not current_user.is_controladoria:
                abort(403)
            return f(*a, **k)
        return wrap

    def _meu_membro():
        return TeamMember.query.filter_by(user_id=current_user.id).first()

    def _pode_agenda(member_id):
        """Gestão vê/edita qualquer agenda; o profissional, só a própria."""
        if current_user.is_controladoria:
            return True
        m = _meu_membro()
        return bool(m and member_id == m.id)

    def _membro_arg():
        """Agenda em edição: ?membro=ID, 0 = agenda geral. Padrão: a minha."""
        v = request.values.get("membro")
        if v is not None and v != "":
            try:
                n = int(v)
            except ValueError:
                n = None
            return (n or None) if n is not None else None
        m = _meu_membro()
        return m.id if m else None

    def _volta(membro_id):
        return redirect(url_for("team_agenda_reunioes", membro=(membro_id or 0)))

    # ==================================================================
    # TELA INTERNA
    # ==================================================================
    @app.route("/agenda-reunioes")
    @team_required
    def team_agenda_reunioes():
        from team.models_workflow import ListaEmail
        gestao = current_user.is_controladoria
        membro_id = _membro_arg()
        if not _pode_agenda(membro_id):
            membro_id = (_meu_membro().id if _meu_membro() else None)
            if not _pode_agenda(membro_id):
                abort(403)
        membros = (TeamMember.query.filter_by(active=True).order_by(TeamMember.name).all()
                   if gestao else [m for m in [_meu_membro()] if m])
        agora = fuso.agora()
        conflito = None
        if request.args.get("conf"):
            dias_c = [int(x) for x in request.args.getlist("dia") if x.isdigit()]
            ini_c, fim_c = request.args.get("inicio", ""), request.args.get("fim", "")
            try:
                i1, f1 = agenda._hm(ini_c), agenda._hm(fim_c)
                sob = [j for j in AgendaJanela.query.filter(AgendaJanela.member_id == membro_id,
                                                            AgendaJanela.weekday.in_(dias_c)).all()
                       if agenda._hm(j.inicio) < f1 and agenda._hm(j.fim) > i1]
                if sob:
                    conflito = {"dias": dias_c, "inicio": ini_c, "fim": fim_c, "existentes": sob}
            except Exception:
                conflito = None
        links = {l.company_id: l for l in AgendaLink.query.filter(AgendaLink.teste_email.is_(None)).all()}
        simulacoes = AgendaLink.query.filter(AgendaLink.teste_email.isnot(None)).order_by(AgendaLink.id.desc()).all()
        real = db.or_(Reuniao.teste.is_(False), Reuniao.teste.is_(None))
        q_prox = Reuniao.query.filter(Reuniao.status == "marcada", Reuniao.fim >= agora, real)
        q_pass = Reuniao.query.filter(Reuniao.fim < agora, real)
        if not gestao:
            q_prox = q_prox.filter(Reuniao.member_id == membro_id)
            q_pass = q_pass.filter(Reuniao.member_id == membro_id)
        empresas = []
        if gestao:
            for c in Company.query.filter_by(active=True).order_by(Company.name).all():
                mem = agenda.membro_da_empresa(c.id)
                empresas.append({"c": c, "link": links.get(c.id), "resp": mem,
                                 "dia": agenda.dia_reuniao(c.id),
                                 "regra": agenda.regra_do_dia(c.id)})
        return render_template(
            "team/agenda_reunioes.html", cfg=agenda.cfg, ativo=agenda.ativo(), dias=agenda.DIAS,
            gestao=gestao, membros=membros, membro_id=membro_id,
            membro=(db.session.get(TeamMember, membro_id) if membro_id else None),
            janelas=AgendaJanela.query.filter(AgendaJanela.member_id == membro_id)
            .order_by(AgendaJanela.weekday, AgendaJanela.inicio).all(),
            bloqueios=AgendaBloqueio.query.filter(
                AgendaBloqueio.fim >= agora,
                db.or_(AgendaBloqueio.member_id == membro_id, AgendaBloqueio.member_id.is_(None)))
            .order_by(AgendaBloqueio.inicio).all(),
            empresas=empresas, geral=links.get(None), url_publica=agenda.url_publica,
            simulacoes=simulacoes, conflito=conflito,
            ha_duplicadas=(lambda js: len({(j.weekday, j.inicio, j.fim) for j in js}) < len(js))(
                AgendaJanela.query.filter(AgendaJanela.member_id == membro_id).all()),
            proximas=q_prox.order_by(Reuniao.inicio).all(),
            passadas=q_pass.order_by(Reuniao.inicio.desc()).limit(10).all(),
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
        off = f.get("offset_du", type=int)
        if off is not None and 0 <= off <= 15:
            set_setting("ag_offset_du", str(off))
        set_setting("ag_titulo", (f.get("titulo") or "").strip()[:120] or agenda.PADRAO["ag_titulo"])
        set_setting("ag_local", (f.get("local") or "").strip()[:240])
        org = (f.get("organizador") or "").strip().lower()
        set_setting("ag_organizador", org if "@" in org else "")
        db.session.commit()
        flash("Configuração do agendamento salva.", "success")
        return _volta(_membro_arg())

    @app.route("/agenda-reunioes/janela", methods=["POST"])
    @team_required
    def team_agenda_janela_add():
        mid = _membro_arg()
        if not _pode_agenda(mid):
            abort(403)
        dias = [int(x) for x in request.form.getlist("dia") if x.isdigit() and 0 <= int(x) <= 6]
        ini, fim = request.form.get("inicio") or "", request.form.get("fim") or ""
        try:
            ok = agenda._hm(ini) < agenda._hm(fim)
        except Exception:
            ok = False
        if not dias or not ok:
            flash("Escolha os dias e um horário inicial antes do final.", "warning")
            return _volta(mid)
        i1, f1 = agenda._hm(ini), agenda._hm(fim)
        existentes = AgendaJanela.query.filter(AgendaJanela.member_id == mid,
                                               AgendaJanela.weekday.in_(dias)).all()
        sobrepoe = [j for j in existentes if agenda._hm(j.inicio) < f1 and agenda._hm(j.fim) > i1]
        acao = request.form.get("acao")
        if sobrepoe and acao not in ("substituir", "somar"):
            # não grava nada: pergunta o que fazer (substituir ou somar mesmo assim)
            from urllib.parse import urlencode
            qs = urlencode([("membro", mid or 0), ("conf", 1), ("inicio", ini), ("fim", fim)]
                           + [("dia", d) for d in dias])
            return redirect(url_for("team_agenda_reunioes") + "?" + qs + "#horarios")
        if acao == "substituir":
            for j in sobrepoe:
                db.session.delete(j)
        n = 0
        for d in dias:
            ja = any(j.weekday == d and j.inicio == ini and j.fim == fim and j not in sobrepoe_ou_vazio(acao, sobrepoe)
                     for j in existentes)
            if not ja:
                db.session.add(AgendaJanela(member_id=mid, weekday=d, inicio=ini, fim=fim))
                n += 1
        db.session.commit()
        flash(f"{n} horário(s) incluído(s)." + (" Os que já existiam foram substituídos." if acao == "substituir" and sobrepoe else "")
              + ("" if n == len(dias) else " Os idênticos já cadastrados foram ignorados."), "success")
        return _volta(mid)

    def sobrepoe_ou_vazio(acao, sobrepoe):
        return sobrepoe if acao == "substituir" else []

    @app.route("/agenda-reunioes/janela/duplicadas", methods=["POST"])
    @team_required
    def team_agenda_janela_dup():
        """Apaga horários IDÊNTICOS repetidos da agenda (mantém um de cada)."""
        mid = _membro_arg()
        if not _pode_agenda(mid):
            abort(403)
        vistos, n = set(), 0
        for j in AgendaJanela.query.filter(AgendaJanela.member_id == mid).order_by(AgendaJanela.id).all():
            k = (j.weekday, j.inicio, j.fim)
            if k in vistos:
                db.session.delete(j)
                n += 1
            vistos.add(k)
        db.session.commit()
        flash(f"{n} horário(s) repetido(s) removido(s)." if n else "Não havia horários repetidos.", "success")
        return _volta(mid)

    @app.route("/agenda-reunioes/janela/<int:jid>/remover", methods=["POST"])
    @team_required
    def team_agenda_janela_del(jid):
        j = db.session.get(AgendaJanela, jid) or abort(404)
        if not _pode_agenda(j.member_id):
            abort(403)
        mid = j.member_id
        db.session.delete(j)
        db.session.commit()
        return _volta(mid)

    @app.route("/agenda-reunioes/bloqueio", methods=["POST"])
    @team_required
    def team_agenda_bloqueio_add():
        mid = _membro_arg()
        if not _pode_agenda(mid):
            abort(403)
        try:
            d1 = datetime.strptime(request.form.get("inicio_dia") + " " + (request.form.get("inicio_hora") or "00:00"), "%Y-%m-%d %H:%M")
            d2 = datetime.strptime((request.form.get("fim_dia") or request.form.get("inicio_dia")) + " " + (request.form.get("fim_hora") or "23:59"), "%Y-%m-%d %H:%M")
        except (TypeError, ValueError):
            flash("Informe o dia (e, se quiser, o horário) do bloqueio.", "warning")
            return _volta(mid)
        if d2 <= d1:
            flash("O fim precisa ser depois do início.", "warning")
            return _volta(mid)
        para_todos = bool(request.form.get("todos")) and current_user.is_controladoria
        db.session.add(AgendaBloqueio(member_id=(None if para_todos else mid), inicio=d1, fim=d2,
                                      motivo=(request.form.get("motivo") or "").strip()[:160] or None))
        db.session.commit()
        flash("Período bloqueado" + (" para todas as agendas." if para_todos else "."), "success")
        return _volta(mid)

    @app.route("/agenda-reunioes/bloqueio/<int:bid>/remover", methods=["POST"])
    @team_required
    def team_agenda_bloqueio_del(bid):
        b = db.session.get(AgendaBloqueio, bid) or abort(404)
        if b.member_id is None and not current_user.is_controladoria:
            abort(403)
        if b.member_id is not None and not _pode_agenda(b.member_id):
            abort(403)
        mid = b.member_id
        db.session.delete(b)
        db.session.commit()
        return _volta(_membro_arg() if mid is None else mid)

    @app.route("/agenda-reunioes/link/<int:cid>/<acao>", methods=["POST"])
    @controladoria_required
    def team_agenda_link(cid, acao):
        """cid = id da empresa (0 = link geral)."""
        comp = cid or None
        l = AgendaLink.query.filter_by(company_id=comp, teste_email=None).first()
        if acao == "criar" and not l:
            agenda.link_da_empresa(comp)
        elif l and acao == "alternar":
            l.active = not l.active
        elif l and acao == "renovar":
            l.token = secrets.token_urlsafe(16)
            l.active = True
            flash("Novo link gerado: o anterior deixou de funcionar.", "success")
        db.session.commit()
        return _volta(_membro_arg())

    @app.route("/agenda-reunioes/link/<int:cid>/dia", methods=["POST"])
    @controladoria_required
    def team_agenda_link_dia(cid):
        """Define o dia da reunião de UMA empresa: data fixa, ou N dias úteis após o prazo,
        ou volta para a regra geral (campos vazios)."""
        l = agenda.link_da_empresa(cid or None)
        fixo = request.form.get("dia_fixo")
        off = request.form.get("offset_du", type=int)
        try:
            l.dia_fixo = datetime.strptime(fixo, "%Y-%m-%d").date() if fixo else None
        except ValueError:
            l.dia_fixo = None
        l.offset_du = off if (off is not None and 0 <= off <= 15 and not l.dia_fixo) else None
        db.session.commit()
        log_audit(current_user.id, "agenda_dia_empresa", "company", f"{cid} fixo={l.dia_fixo} off={l.offset_du}")
        flash("Dia da reunião da empresa atualizado.", "success")
        return _volta(_membro_arg())

    @app.route("/agenda-reunioes/teste", methods=["POST"])
    @controladoria_required
    def team_agenda_teste():
        """Convite de teste para o e-mail de quem clicou (ninguém mais recebe)."""
        cid = request.form.get("company_id", type=int) or None
        destino = (request.form.get("destino") or "").strip().lower()
        if "@" not in destino or "." not in destino.split("@")[-1]:
            flash("Informe um e-mail válido para receber o teste.", "warning")
            return _volta(_membro_arg())
        (ok, err), quando = agenda.enviar_teste(destino, cid)
        flash(f"Convite de teste enviado para {destino} ({quando}). Abra o anexo no Outlook." if ok
              else f"Não foi possível enviar o teste: {err}. Em Comunicação, veja se o canal de e-mail está ativo.",
              "success" if ok else "warning")
        return _volta(_membro_arg())

    @app.route("/agenda-reunioes/simular", methods=["POST"])
    @controladoria_required
    def team_agenda_simular():
        """Simula a jornada: e-mail de aviso com o link de agendamento -> a empresa marca ->
        convite -> cancelamento. TUDO chega só ao e-mail indicado; nada vai às pessoas reais
        e a reserva não ocupa a agenda real."""
        from team import alerts as al, avisos_empresas as av
        from models import current_competency
        cid = request.form.get("company_id", type=int) or None
        destino = (request.form.get("destino") or "").strip().lower()
        kind = request.form.get("tipo") if request.form.get("tipo") in ("data", "vence") else "data"
        c = db.session.get(Company, cid) if cid else None
        if not c or "@" not in destino or "." not in destino.split("@")[-1]:
            flash("Escolha a empresa e informe o e-mail que vai receber a simulação.", "warning")
            return _volta(_membro_arg())
        link = agenda.criar_simulacao(c.id, destino)
        comp = current_competency()
        prazo, du = av.prazo_da_empresa(c.id, comp)
        url = agenda.url_publica(link)
        assunto, corpo_av, html_av = av.montar_aviso(kind, c, comp, prazo, du, agendar=url)
        assunto = "[SIMULAÇÃO] " + assunto
        topo = (f"SIMULAÇÃO da jornada — empresa {c.name}. Este e-mail só foi para você; a empresa não recebeu nada.\n"
                "Passo a passo: 1) leia o aviso abaixo  2) clique em “Clique aqui”  3) escolha um horário e confirme  "
                "4) o convite chega neste mesmo endereço, direto na mensagem  5) teste o cancelamento pelo link do convite.\n\n"
                "----- e-mail que a empresa recebe -----\n\n")
        corpo = topo + corpo_av
        html_sim = ('<div style="background:#FFF4D6;border:1px solid #B5832A;padding:8px 10px;margin-bottom:12px;'
                    'font:13px Segoe UI,Arial,sans-serif;"><b>SIMULAÇÃO</b> — este e-mail só foi para você; a empresa não recebeu nada. '
                    '1) clique em “Clique aqui” 2) escolha um horário e confirme 3) o convite chega neste endereço, direto na mensagem 4) teste o cancelamento.</div>'
                    + html_av)
        ok, err = al.send_email(destino, assunto, corpo, lista=True, html=html_sim)
        log_audit(current_user.id, "agenda_simulacao", "company", f"{c.id} -> {destino}")
        if ok:
            flash(f"Simulação iniciada: o aviso de {c.name} foi para {destino}. Abra o e-mail, clique no link e marque um horário.", "success")
        else:
            flash(f"O e-mail não saiu ({err}), mas o link de simulação foi criado: use “Abrir página” na tabela abaixo. "
                  "Confira em Comunicação se o canal de e-mail está ativo.", "warning")
        return _volta(_membro_arg())

    @app.route("/agenda-reunioes/simulacao/<int:lid>/encerrar", methods=["POST"])
    @controladoria_required
    def team_agenda_simulacao_encerrar(lid):
        l = AgendaLink.query.filter(AgendaLink.id == lid, AgendaLink.teste_email.isnot(None)).first() or abort(404)
        agenda.encerrar_simulacao(l)
        flash("Simulação encerrada: link e reservas de teste apagados.", "success")
        return _volta(_membro_arg())

    @app.route("/agenda-reunioes/<int:rid>/cancelar", methods=["POST"])
    @team_required
    def team_agenda_cancelar(rid):
        r = db.session.get(Reuniao, rid) or abort(404)
        if not _pode_agenda(r.member_id):
            abort(403)
        agenda.cancelar(r)
        log_audit(current_user.id, "reuniao_cancelada", "reuniao", str(rid))
        flash("Reunião cancelada e o cancelamento foi enviado aos participantes.", "success")
        return _volta(r.member_id)

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
                feito, erro = agenda.reservar(l, inicio, email=request.form.get("email"))
        livres, mem = ({}, agenda.membro_da_empresa(l.company_id)) if feito else agenda.livres_do_link(l)
        dia = agenda.dia_reuniao(l.company_id) if l.company_id else None
        return render_template("agendar.html", link=l, livres=livres, erro=erro, feito=feito,
                               titulo=agenda.cfg("ag_titulo"), local=agenda.cfg("ag_local"),
                               duracao=agenda.cfg_int("ag_duracao", 30), dias=agenda.DIAS,
                               responsavel=(mem.name if mem else None), dia=dia,
                               precisa_email=not agenda.contatos_da_empresa(l.company_id),
                               pre_email=request.form.get("email", ""))

    @app.route("/agendar/cancelar/<token>", methods=["GET", "POST"])
    def agendar_cancelar(token):
        r = Reuniao.query.filter_by(token_cancelar=token).first() or abort(404)
        feito = False
        if request.method == "POST" and r.status == "marcada":
            agenda.cancelar(r)
            feito = True
        return render_template("agendar.html", cancelar=r, feito_cancel=feito,
                               titulo=agenda.cfg("ag_titulo"))

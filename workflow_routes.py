# -*- coding: utf-8 -*-
"""Recursos que tiram atrito do uso diario.

Regra de ouro deste modulo: cada recurso precisa devolver mais tempo ao time do
que consome. Quando isso acontece, o dado de gestao vira subproduto — ninguem
precisa ser cobrado para manter o portal atualizado.
"""
import os
import fuso
import re
import json
from datetime import datetime, date, timedelta
from functools import wraps

from flask import (render_template, request, redirect, url_for, flash, abort,
                   send_file, jsonify)
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename

from config import Config
from models import (db, User, Company, Competency, log_audit, notify)
from team.models import TeamMember, Activity
from team.models_workflow import (ActivityNote, ActivityFile, Absence,
                                  UserNote, TaskList, PersonalTask, AtaReuniao,
                                  DefinitionList, Definition, DigestSnapshot)

ANEXO_DIR = os.path.join(Config.UPLOAD_DIR, "_anexos")
ALLOWED_ANEXO = {".pdf", ".xlsx", ".xlsm", ".xls", ".csv", ".png", ".jpg",
                 ".jpeg", ".docx", ".pptx", ".txt", ".msg", ".eml", ".zip"}
ABERTAS = ["pendente", "em_andamento", "bloqueada"]


# (sync_activities_for_submission e compute_closing_metrics removidos:
#  pertenciam ao modulo de Consolidacao)


def weekly_digest_text():
    """Resumo do fechamento pelo CUMPRIMENTO DO CRONOGRAMA (atividades).

    Base = as atividades de fechamento da competência corrente (geradas pelo
    Cronograma). A foto dos envios só aparece se a Consolidação estiver ligada.
    """
    from collections import Counter
    from models import current_competency
    comp = current_competency()
    if not comp:
        return "Nenhuma competência cadastrada (crie em Administração › Sistema)."
    hoje = fuso.hoje()
    acts = Activity.query.filter_by(competency_id=comp.id, kind="fechamento").all()
    total = len(acts)
    concluidas = sum(1 for a in acts if a.status == "concluida")
    andamento = sum(1 for a in acts if a.status == "em_andamento")
    atrasadas = [a for a in acts if a.status in ABERTAS
                 and a.due_date and a.due_date < hoje]
    linhas = [f"Fechamento {comp.label}"]
    if total == 0:
        linhas.append("  Nenhuma atividade gerada ainda — use o Cronograma para gerar.")
    else:
        linhas += [
            f"  Atividades do cronograma: {total}",
            f"  Concluídas: {concluidas}/{total}",
            f"  Em andamento: {andamento}",
            f"  Atrasadas: {len(atrasadas)}",
        ]
    if comp.deadline:
        prazo = comp.deadline.strftime('%d/%m/%Y')
        pendentes = total - concluidas
        concluido = (total > 0 and pendentes == 0) or comp.status == "fechada"
        if concluido:
            # terminou: diz QUANDO terminou frente ao prazo, nada de "vencido"
            fim = max((a.done_at for a in acts if a.done_at), default=None) or comp.closed_at
            fim = fuso.local(fim)          # gravado em UTC; o dia é o de Brasília
            if fim:
                atraso = (fim.date() - comp.deadline).days
                quando = ("no prazo" if atraso <= 0
                          else f"{atraso} dia(s) após o prazo")
                linhas.append(f"  Prazo do fechamento: {prazo} — concluído em "
                              f"{fim.strftime('%d/%m/%Y')} ({quando})")
            else:
                linhas.append(f"  Prazo do fechamento: {prazo} — concluído")
        else:
            d = (comp.deadline - hoje).days
            if d < 0:
                situacao = f"vencido há {-d} dia(s), {pendentes} atividade(s) em aberto"
            elif d == 0:
                situacao = f"vence hoje, {pendentes} atividade(s) em aberto"
            else:
                situacao = f"faltam {d} dia(s)"
            linhas.append(f"  Prazo do fechamento: {prazo} ({situacao})")
    if atrasadas:
        cnt = Counter((a.member.name if a.member else "sem responsável")
                      for a in atrasadas)
        linhas.append("  Atrasadas por: "
                      + ", ".join(f"{n} ({q})" for n, q in cnt.most_common(6)))
    return "\n".join(linhas)


_MOTIVO_EMAIL = {
    "email_desligado": "canal de e-mail desligado no servidor (faltam SMTP_USER/SMTP_PASSWORD)",
    "smtp_sem_senha": "falta a variável SMTP_PASSWORD no servidor",
    "sem_destinatario": "usuário sem e-mail cadastrado",
}


def envia_resumo(enviado_por=None, base_url=None):
    """Gera o resumo, guarda a foto e manda para a controladoria por
    painel/push (abrindo a foto) e por e-mail. Retorna o DigestSnapshot."""
    from team import alerts
    texto = weekly_digest_text()
    snap = DigestSnapshot(texto=texto, sent_by=enviado_por)
    db.session.add(snap)
    db.session.commit()
    caminho = f"/resumo/{snap.id}"
    base = (base_url or os.environ.get("TEAM_PORTAL_URL")
            or os.environ.get("RENDER_EXTERNAL_URL") or alerts.PORTAL_URL)
    link = base.rstrip("/") + caminho
    alvo = User.query.filter(User.role.in_(["controladoria", "admin"]),
                             User.active.is_(True)).all()
    falhas, ok = [], 0
    for u in alvo:
        notify(u.id, "Resumo do fechamento", texto[:380], kind="resumo", url=caminho)
        certo, erro = alerts.send_email(
            u.email, "Resumo do fechamento — Controladoria J&F",
            f"{texto}\n\nAbrir no portal: {link}\n")
        if certo:
            ok += 1
        else:
            falhas.append(f"{u.display_name or u.email}: {_MOTIVO_EMAIL.get(erro, erro)}")
    snap.email_ok = ok
    snap.email_falhas = "\n".join(falhas) or None
    snap.destinos = len(alvo)
    db.session.commit()
    return snap


# ==========================================================================
def register_workflow_routes(app):

    def team_required(f):
        @wraps(f)
        @login_required
        def wrap(*a, **k):
            if not current_user.is_controladoria:
                abort(403)
            return f(*a, **k)
        return wrap

    def any_team_required(f):
        """Qualquer perfil do time (inclui profissional)."""
        @wraps(f)
        @login_required
        def wrap(*a, **k):
            if not current_user.is_team:
                abort(403)
            return f(*a, **k)
        return wrap

    def _member_of_current_user():
        return TeamMember.query.filter_by(user_id=current_user.id).first()

    def _int(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    # ------------------------------------------------------------------
    # ATIVIDADES — concluir em 1 clique, em lote e reatribuir
    # ------------------------------------------------------------------
    @app.route("/atividade/<int:aid>/concluir", methods=["POST"])
    @team_required
    def wf_concluir(aid):
        a = db.session.get(Activity, aid) or abort(404)
        a.status = "concluida"
        a.done_at = datetime.utcnow()
        a.done_by = current_user.id
        db.session.commit()
        log_audit(current_user.id, "atividade_concluida", "activity", str(aid))
        if request.form.get("ajax"):
            return jsonify(ok=True)
        flash(f"“{a.title[:60]}” concluída.", "success")
        return redirect(request.referrer or url_for("team_atividades"))

    @app.route("/atividades/concluir-lote", methods=["POST"])
    @team_required
    def wf_concluir_lote():
        ids = request.form.getlist("ids")
        n = 0
        for aid in ids:
            a = db.session.get(Activity, int(aid))
            if a and a.status in ABERTAS:
                a.status = "concluida"
                a.done_at = datetime.utcnow()
                a.done_by = current_user.id
                n += 1
        db.session.commit()
        log_audit(current_user.id, "atividades_lote", "activity", f"{n} concluídas")
        flash(f"{n} atividade(s) concluída(s).", "success")
        return redirect(request.referrer or url_for("team_atividades"))

    @app.route("/atividades/reatribuir-lote", methods=["POST"])
    @team_required
    def wf_reatribuir_lote():
        """Repasse com histórico — férias, saída, redistribuição de carga."""
        de = request.form.get("de_member_id", type=int)
        para = request.form.get("para_member_id", type=int)
        ini = request.form.get("de_data")
        fim = request.form.get("ate_data")
        if not para:
            flash("Escolha para quem repassar.", "danger")
            return redirect(request.referrer or url_for("team_capacidade"))
        q = Activity.query.filter(Activity.status.in_(ABERTAS))
        q = q.filter_by(member_id=de) if de else q.filter(Activity.member_id.is_(None))
        try:
            if ini:
                q = q.filter(Activity.due_date >= datetime.strptime(ini, "%Y-%m-%d").date())
            if fim:
                q = q.filter(Activity.due_date <= datetime.strptime(fim, "%Y-%m-%d").date())
        except ValueError:
            pass
        alvo = db.session.get(TeamMember, para)
        origem = db.session.get(TeamMember, de) if de else None
        n = 0
        for a in q.all():
            a.member_id = para
            db.session.add(ActivityNote(
                activity_id=a.id, user_id=current_user.id, kind="sistema",
                body=(f"Repassada de {origem.name if origem else 'sem responsável'} "
                      f"para {alvo.name}.")))
            n += 1
        db.session.commit()
        log_audit(current_user.id, "atividades_repassadas", "activity",
                  f"{n} -> {alvo.name}")
        flash(f"{n} atividade(s) repassada(s) para {alvo.name}.", "success")
        return redirect(request.referrer or url_for("team_capacidade"))

    # ------------------------------------------------------------------
    # NOTAS (andamento / bloqueio)
    # ------------------------------------------------------------------
    @app.route("/atividade/<int:aid>/nota", methods=["POST"])
    @team_required
    def wf_nota(aid):
        a = db.session.get(Activity, aid) or abort(404)
        body = (request.form.get("body") or "").strip()
        kind = request.form.get("kind") or "comentario"
        if not body:
            flash("Escreva algo antes de enviar.", "danger")
            return redirect(url_for("team_atividade", aid=aid))
        db.session.add(ActivityNote(activity_id=aid, user_id=current_user.id,
                                    kind=kind, body=body))
        if kind == "bloqueio":
            a.status = "bloqueada"
        elif kind == "desbloqueio" and a.status == "bloqueada":
            a.status = "em_andamento"
        db.session.commit()
        # avisa o gestor quando algo trava
        if kind == "bloqueio":
            for m in TeamMember.query.filter_by(is_manager=True).all():
                if m.user_id and m.user_id != current_user.id:
                    notify(m.user_id, "Atividade bloqueada",
                           f"{a.title[:70]} — {body[:90]}",
                           kind="bloqueio", url=url_for("team_atividade", aid=aid))
        return redirect(url_for("team_atividade", aid=aid))

    # ------------------------------------------------------------------
    # ANEXOS
    # ------------------------------------------------------------------
    @app.route("/atividade/<int:aid>/anexo", methods=["POST"])
    @team_required
    def wf_anexo(aid):
        db.session.get(Activity, aid) or abort(404)
        f = request.files.get("arquivo")
        if not f or not f.filename:
            flash("Selecione um arquivo.", "danger")
            return redirect(url_for("team_atividade", aid=aid))
        ext = os.path.splitext(f.filename)[1].lower()
        if ext not in ALLOWED_ANEXO:
            flash(f"Formato {ext} não permitido.", "danger")
            return redirect(url_for("team_atividade", aid=aid))
        pasta = os.path.join(ANEXO_DIR, str(aid))
        os.makedirs(pasta, exist_ok=True)
        stamp = fuso.agora().strftime("%Y%m%d%H%M%S")
        nome = secure_filename(f"{stamp}_{f.filename}")
        caminho = os.path.join(pasta, nome)
        f.save(caminho)
        db.session.add(ActivityFile(
            activity_id=aid, filename=f.filename, stored_path=caminho,
            size_bytes=os.path.getsize(caminho), uploaded_by=current_user.id))
        db.session.commit()
        flash("Anexo adicionado.", "success")
        return redirect(url_for("team_atividade", aid=aid))

    @app.route("/anexo/<int:fid>")
    @team_required
    def wf_anexo_download(fid):
        af = db.session.get(ActivityFile, fid) or abort(404)
        if not os.path.exists(af.stored_path):
            abort(404)
        return send_file(af.stored_path, as_attachment=True,
                         download_name=af.filename)

    @app.route("/anexo/<int:fid>/excluir", methods=["POST"])
    @team_required
    def wf_anexo_delete(fid):
        af = db.session.get(ActivityFile, fid) or abort(404)
        aid = af.activity_id
        try:
            os.remove(af.stored_path)
        except OSError:
            pass
        db.session.delete(af)
        db.session.commit()
        return redirect(url_for("team_atividade", aid=aid))

    # ------------------------------------------------------------------
    # AUSENCIAS
    # ------------------------------------------------------------------
    def _gestor_users_ids():
        return [u.id for u in User.query.filter(
            User.role.in_(["controladoria", "admin"])).all()]

    @app.route("/ausencias", methods=["GET", "POST"])
    @any_team_required
    def wf_ausencias():
        eu_gestor = current_user.is_controladoria
        meu_membro = _member_of_current_user()
        if request.method == "POST":
            try:
                ini = datetime.strptime(request.form["start_date"], "%Y-%m-%d").date()
                fim = datetime.strptime(request.form["end_date"], "%Y-%m-%d").date()
            except (KeyError, ValueError):
                flash("Preencha as datas.", "danger")
                return redirect(url_for("wf_ausencias"))
            if fim < ini:
                ini, fim = fim, ini
            deleg_aprova = _int(request.form.get("aprova_delegado_id"))
            deleg_andam = _int(request.form.get("andamento_delegado_id"))
            if eu_gestor:
                # gestor cadastra direto (já aprovada) para qualquer pessoa
                mid = _int(request.form.get("member_id"))
                if not mid:
                    flash("Escolha a pessoa.", "danger")
                    return redirect(url_for("wf_ausencias"))
                a = Absence(member_id=mid, start_date=ini, end_date=fim,
                            kind=request.form.get("kind") or "ferias",
                            note=(request.form.get("note") or "").strip() or None,
                            created_by=current_user.id, status="aprovada",
                            decided_by=current_user.id, decided_at=datetime.utcnow(),
                            aprova_delegado_id=deleg_aprova,
                            andamento_delegado_id=deleg_andam)
                db.session.add(a)
                db.session.commit()
                comp = app._current_competency()
                if comp and comp.deadline and ini <= comp.deadline <= fim:
                    mm = db.session.get(TeamMember, mid)
                    flash(f"Atenção: {mm.name} estará fora no dia do prazo "
                          f"({comp.deadline.strftime('%d/%m')}). Considere repassar "
                          f"as atividades.", "warning")
                else:
                    flash("Ausência registrada (aprovada).", "success")
            else:
                # profissional SOLICITA a própria (precisa de aprovação)
                if not meu_membro:
                    flash("Seu usuário não está vinculado a uma pessoa do time.", "danger")
                    return redirect(url_for("wf_ausencias"))
                a = Absence(member_id=meu_membro.id, start_date=ini, end_date=fim,
                            kind=request.form.get("kind") or "ferias",
                            note=(request.form.get("note") or "").strip() or None,
                            created_by=current_user.id, status="solicitada",
                            requested_by=current_user.id,
                            aprova_delegado_id=deleg_aprova,
                            andamento_delegado_id=deleg_andam)
                db.session.add(a)
                db.session.commit()
                for uid in _gestor_users_ids():
                    notify(uid, "Solicitação de férias",
                           f"{meu_membro.name} solicitou {a.kind_pt.lower()} de "
                           f"{ini.strftime('%d/%m')} a {fim.strftime('%d/%m/%Y')}.",
                           kind="ferias", url="/ausencias")
                flash("Solicitação enviada para aprovação do gestor.", "success")
            return redirect(url_for("wf_ausencias"))

        hoje = fuso.hoje()
        base = Absence.query
        if not eu_gestor:                       # profissional vê só as próprias
            base = base.filter(Absence.member_id == (meu_membro.id if meu_membro else -1))
        pendentes = base.filter(Absence.status == "solicitada").order_by(
            Absence.start_date).all()
        futuras = base.filter(Absence.status == "aprovada",
                              Absence.end_date >= hoje).order_by(Absence.start_date).all()
        # aprovadas já terminadas, sem retorno confirmado (gestor confirma)
        aguardando_retorno = base.filter(
            Absence.status == "aprovada", Absence.real_return.is_(None),
            Absence.end_date < hoje).order_by(Absence.end_date.desc()).all()
        passadas = base.filter(Absence.status == "aprovada",
                               Absence.end_date < hoje).order_by(
            Absence.start_date.desc()).limit(20).all()
        membros = TeamMember.query.filter_by(active=True).order_by(
            TeamMember.sort_order, TeamMember.name).all()
        comp = app._current_competency()
        risco = []
        if comp and comp.deadline:
            for a in futuras:
                if a.cobre(comp.deadline):
                    n = (Activity.query.filter_by(member_id=a.member_id)
                         .filter(Activity.status.in_(ABERTAS)).count())
                    risco.append({"a": a, "n": n})
        descobertas = {}
        for a in futuras:
            acts = (Activity.query.filter_by(member_id=a.member_id)
                    .filter(Activity.status.in_(ABERTAS))
                    .filter(Activity.due_date.isnot(None))
                    .filter(Activity.due_date >= a.start_date)
                    .filter(Activity.due_date <= a.end_date)
                    .order_by(Activity.due_date).all())
            if acts:
                descobertas[a.id] = acts
        return render_template("team/ausencias.html", futuras=futuras,
                               passadas=passadas, membros=membros, hoje=hoje,
                               comp=comp, risco=risco, descobertas=descobertas,
                               pendentes=pendentes, aguardando_retorno=aguardando_retorno,
                               eu_gestor=eu_gestor, meu_membro=meu_membro)

    @app.route("/ausencia/<int:aid>/decidir", methods=["POST"])
    @team_required
    def wf_ausencia_decidir(aid):
        a = db.session.get(Absence, aid) or abort(404)
        dec = request.form.get("decisao")
        if dec not in ("aprovada", "recusada"):
            abort(400)
        a.status = dec
        a.decided_by = current_user.id
        a.decided_at = datetime.utcnow()
        a.decision_note = (request.form.get("decision_note") or "").strip() or None
        db.session.commit()
        if a.member and a.member.user_id:
            notify(a.member.user_id,
                   "Férias " + ("aprovadas" if dec == "aprovada" else "recusadas"),
                   f"{a.start_date.strftime('%d/%m')} a {a.end_date.strftime('%d/%m/%Y')}"
                   + (f" — {a.decision_note}" if a.decision_note else ""),
                   kind="ferias", url="/ausencias")
        flash(f"Solicitação {a.status_pt.lower()}.", "success")
        return redirect(url_for("wf_ausencias"))

    @app.route("/ausencia/<int:aid>/retorno", methods=["POST"])
    @team_required
    def wf_ausencia_retorno(aid):
        a = db.session.get(Absence, aid) or abort(404)
        rr = request.form.get("real_return")
        if rr:
            try:
                a.real_return = datetime.strptime(rr, "%Y-%m-%d").date()
            except ValueError:
                flash("Data de retorno inválida.", "danger")
                return redirect(url_for("wf_ausencias"))
        a.saida_confirmada = True
        db.session.commit()
        flash(f"Retorno de {a.member.name} confirmado.", "success")
        return redirect(url_for("wf_ausencias"))

    @app.route("/ausencia/<int:aid>/excluir", methods=["POST"])
    @any_team_required
    def wf_ausencia_delete(aid):
        a = db.session.get(Absence, aid) or abort(404)
        # profissional só pode cancelar a PRÓPRIA solicitação ainda pendente
        if not current_user.is_controladoria:
            m = _member_of_current_user()
            if not m or a.member_id != m.id or a.status != "solicitada":
                abort(403)
        db.session.delete(a)
        db.session.commit()
        flash("Ausência removida.", "success")
        return redirect(url_for("wf_ausencias"))

    # ------------------------------------------------------------------
    # RESUMO SEMANAL
    # ------------------------------------------------------------------
    @app.route("/resumo", methods=["GET", "POST"])
    @team_required
    def wf_resumo():
        if request.method == "POST" and not fuso.pode_avisar():
            from models import set_setting
            set_setting("resumo_pendente", "1")
            log_audit(current_user.id, "resumo_agendado", "digest", "dia não útil")
            flash("Hoje não é dia útil: o resumo sai automaticamente na manhã do "
                  "próximo dia útil (o portal não envia avisos em fim de semana e feriado).",
                  "info")
            return redirect(url_for("wf_resumo"))
        if request.method == "POST":
            snap = envia_resumo(current_user.id, request.url_root)
            n = snap.destinos
            log_audit(current_user.id, "resumo_enviado", "digest", f"{n} destinos")
            flash(f"Resumo enviado para {n} pessoa(s) da controladoria "
                  f"(painel/push) — e-mail: {snap.email_ok} de {n}.",
                  "success" if snap.email_ok == n else "warning")
            return redirect(url_for("wf_resumo_ver", sid=snap.id))
        enviados = (DigestSnapshot.query.order_by(DigestSnapshot.created_at.desc())
                    .limit(12).all())
        return render_template("team/resumo.html", texto=weekly_digest_text(),
                               snap=None, enviados=enviados)

    @app.route("/resumo/<int:sid>")
    @team_required
    def wf_resumo_ver(sid):
        snap = db.session.get(DigestSnapshot, sid) or abort(404)
        enviados = (DigestSnapshot.query.order_by(DigestSnapshot.created_at.desc())
                    .limit(12).all())
        return render_template("team/resumo.html", texto=snap.texto,
                               snap=snap, enviados=enviados)

    # ------------------------------------------------------------------
    # BLOCO DE NOTAS PESSOAL — privado, so o dono ve
    # ------------------------------------------------------------------
    def _minha_nota(nid):
        """Busca a nota garantindo que ela pertence ao usuario da sessao."""
        n = db.session.get(UserNote, nid)
        if not n or n.user_id != current_user.id:
            abort(404)      # 404 e nao 403: nao revela que a nota existe
        return n

    @app.route("/notas")
    @login_required
    def notas():
        busca = (request.args.get("q") or "").strip()
        q = UserNote.query.filter_by(user_id=current_user.id)
        if busca:
            like = f"%{busca}%"
            q = q.filter(db.or_(UserNote.title.ilike(like),
                                UserNote.body.ilike(like)))
        # data de edição desc dentro de cada grupo
        todas = q.order_by(UserNote.updated_at.desc()).all()
        atual = None
        nid = request.args.get("n", type=int)
        if nid:
            atual = _minha_nota(nid)
        # grupos: fixadas primeiro; depois por cor (ordem de CORES); data desc dentro
        fixadas = [n for n in todas if n.pinned]
        grupos = []
        for cor, label in UserNote.CORES:
            itens = [n for n in todas if not n.pinned and n.color == cor]
            if itens:
                grupos.append({"cor": cor, "label": label, "itens": itens})
        return render_template("notas.html", fixadas=fixadas, grupos=grupos,
                               total=len(todas), atual=atual, busca=busca,
                               cores=UserNote.CORES)

    @app.route("/notas/nova", methods=["POST"])
    @login_required
    def nota_nova():
        n = UserNote(user_id=current_user.id, title="", body="")
        db.session.add(n)
        db.session.commit()
        return redirect(url_for("notas", n=n.id))

    @app.route("/nota/<int:nid>/salvar", methods=["POST"])
    @login_required
    def nota_salvar(nid):
        n = _minha_nota(nid)
        n.title = (request.form.get("title") or "")[:200]
        n.body = request.form.get("body") or ""
        cor = request.form.get("color")
        if cor and cor in dict(UserNote.CORES):
            n.color = cor
        # escrita a mao: so atualiza quando o editor de caneta mandou os tracos
        ink_raw = request.form.get("ink")
        if ink_raw:
            ink = _limpa_tinta(ink_raw)
            if ink is None:
                if request.form.get("ajax"):
                    return jsonify(ok=False, erro="Traços inválidos ou grandes demais."), 400
            else:
                n.ink_json = json.dumps(ink, separators=(",", ":")) if ink["strokes"] else None
                thumb = request.form.get("ink_thumb") or ""
                n.ink_thumb = (thumb if (ink["strokes"] and thumb.startswith("data:image/png;base64,")
                                         and len(thumb) < 400_000) else None)
        n.updated_at = datetime.utcnow()
        db.session.commit()
        if request.form.get("ajax"):
            return jsonify(ok=True, titulo=n.titulo_exibicao,
                           quando=fuso.local(n.updated_at).strftime("%H:%M"))
        return redirect(url_for("notas", n=n.id))

    _COR_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")

    def _limpa_tinta(raw):
        """Valida e reconstroi a pagina manuscrita so com numeros, cores #hex e
        ferramentas conhecidas (nada vindo do navegador passa cru). None = invalido."""
        if len(raw) > 6_000_000:
            return None
        try:
            d = json.loads(raw)
        except Exception:
            return None
        if not isinstance(d, dict) or not isinstance(d.get("strokes"), list):
            return None
        try:
            h = min(max(float(d.get("h") or 1300), 400.0), 80000.0)
        except (TypeError, ValueError):
            h = 1300.0
        try:                                   # páginas (A4) e rolagem: v = para baixo, h = para o lado
            pag = min(max(int(d.get("pag") or 0), 0), 50)
        except (TypeError, ValueError):
            pag = 0
        direcao = "h" if d.get("dir") == "h" else "v"
        tracos = []
        for s in d["strokes"][:20000]:
            if not isinstance(s, dict):
                continue
            cor = s.get("c") if isinstance(s.get("c"), str) and _COR_HEX.match(s.get("c")) else "#1f2937"
            try:
                w = min(max(float(s.get("w") or 2.6), 0.5), 40.0)
            except (TypeError, ValueError):
                w = 2.6
            tool = s.get("t") if s.get("t") in ("pen", "hl", "er") else "pen"   # er = borracha
            pts = []
            for p in (s.get("p") or [])[:5000]:
                if isinstance(p, (list, tuple)) and len(p) >= 2:
                    try:
                        x, y = round(float(p[0]), 1), round(float(p[1]), 1)
                        pr = round(min(max(float(p[2]), 0.0), 1.0), 2) if len(p) > 2 else 0.5
                    except (TypeError, ValueError):
                        continue
                    pts.append([x, y, pr])
            if pts:
                tracos.append({"c": cor, "w": w, "t": tool, "p": pts})
        out = {"h": h, "strokes": tracos, "dir": direcao}
        if pag:
            out["pag"] = pag
        return out

    @app.route("/nota/<int:nid>/fixar", methods=["POST"])
    @login_required
    def nota_fixar(nid):
        n = _minha_nota(nid)
        n.pinned = not n.pinned
        db.session.commit()
        if request.form.get("ajax"):
            return jsonify(ok=True, fixada=n.pinned)
        return redirect(url_for("notas", n=n.id))

    @app.route("/nota/<int:nid>/excluir", methods=["POST"])
    @login_required
    def nota_excluir(nid):
        n = _minha_nota(nid)
        db.session.delete(n)
        db.session.commit()
        flash("Nota excluída.", "success")
        return redirect(url_for("notas"))

    # ------------------------------------------------------------------
    # LISTAS DE TAREFAS PESSOAIS — gestao do dia, privadas por usuario
    # ------------------------------------------------------------------
    def _minha_lista(lid):
        l = db.session.get(TaskList, lid)
        if not l or l.user_id != current_user.id:
            abort(404)
        return l

    def _minha_tarefa(tid):
        t = db.session.get(PersonalTask, tid)
        if not t or t.user_id != current_user.id:
            abort(404)
        return t

    def _parse_due(valor):
        valor = (valor or "").strip()
        if not valor:
            return None
        try:
            return datetime.strptime(valor, "%Y-%m-%d").date()
        except ValueError:
            return None

    @app.route("/tarefas")
    @login_required
    def tarefas():
        listas = (TaskList.query.filter_by(user_id=current_user.id)
                  .order_by(TaskList.sort_order, TaskList.id).all())
        if not listas:
            # primeira visita: cria uma lista padrao para nao mostrar tela vazia
            l = TaskList(user_id=current_user.id, name="Minhas tarefas")
            db.session.add(l)
            db.session.commit()
            listas = [l]
        hoje = fuso.hoje()
        return render_template("tarefas.html", listas=listas, hoje=hoje)

    @app.route("/tarefas/lista/nova", methods=["POST"])
    @login_required
    def tarefa_lista_nova():
        nome = (request.form.get("name") or "").strip() or "Nova lista"
        n = TaskList.query.filter_by(user_id=current_user.id).count()
        l = TaskList(user_id=current_user.id, name=nome[:120], sort_order=100 + n)
        db.session.add(l)
        db.session.commit()
        return redirect(url_for("tarefas"))

    @app.route("/tarefas/lista/<int:lid>/renomear", methods=["POST"])
    @login_required
    def tarefa_lista_renomear(lid):
        l = _minha_lista(lid)
        nome = (request.form.get("name") or "").strip()
        if nome:
            l.name = nome[:120]
            db.session.commit()
        if request.form.get("ajax"):
            return jsonify(ok=True, nome=l.name)
        return redirect(url_for("tarefas"))

    @app.route("/tarefas/lista/<int:lid>/excluir", methods=["POST"])
    @login_required
    def tarefa_lista_excluir(lid):
        l = _minha_lista(lid)
        db.session.delete(l)
        db.session.commit()
        flash("Lista excluída.", "success")
        return redirect(url_for("tarefas"))

    @app.route("/tarefa/nova", methods=["POST"])
    @login_required
    def tarefa_nova():
        l = _minha_lista(request.form.get("list_id", type=int) or 0)
        titulo = (request.form.get("title") or "").strip()
        if not titulo:
            return redirect(url_for("tarefas"))
        n = PersonalTask.query.filter_by(list_id=l.id).count()
        raw = request.form.get("remind_days")
        dias = _parse_dias(raw)
        t = PersonalTask(list_id=l.id, user_id=current_user.id,
                         title=titulo[:300],
                         due_date=_parse_due(request.form.get("due_date")),
                         # escolher qualquer opcao que nao seja "Sem aviso" liga o aviso
                         remind=bool(request.form.get("remind"))
                                or (raw not in (None, "", "-1")),
                         remind_days=dias,
                         sort_order=100 + n)
        db.session.add(t)
        db.session.commit()
        return redirect(url_for("tarefas"))

    def _parse_dias(valor):
        """Antecedencia do aviso: so os valores oferecidos na tela (0 = no dia)."""
        try:
            d = int(valor)
        except (TypeError, ValueError):
            return 0
        return d if d in dict(PersonalTask.ANTECEDENCIAS) else 0

    def _aplica_status(t, novo):
        """Muda o status e mantém done/done_at em sincronia."""
        if novo not in dict(PersonalTask.STATUSES):
            return
        t.status = novo
        t.done = (novo == "concluido")
        t.done_at = datetime.utcnow() if t.done else None

    @app.route("/tarefa/<int:tid>/toggle", methods=["POST"])
    @login_required
    def tarefa_toggle(tid):
        t = _minha_tarefa(tid)
        _aplica_status(t, "nao_iniciado" if t.status == "concluido" else "concluido")
        db.session.commit()
        if request.form.get("ajax"):
            return jsonify(ok=True, done=t.done, status=t.status)
        return redirect(url_for("tarefas"))

    @app.route("/tarefa/<int:tid>/lembrete", methods=["POST"])
    @login_required
    def tarefa_lembrete(tid):
        """Liga/desliga o aviso de uma tarefa existente, ou muda a antecedencia.

        Com `dias` no formulario: liga o aviso com essa antecedencia
        (`dias=-1` desliga — é a opção "Sem aviso" da lista).
        Sem `dias`: alterna ligado/desligado."""
        t = _minha_tarefa(tid)
        if request.form.get("dias") == "-1":
            t.remind = False
        elif "dias" in request.form:
            t.remind = True
            t.remind_days = _parse_dias(request.form.get("dias"))
        else:
            t.remind = not t.remind
        if t.remind:
            t.reminded_on = None      # (re)ligou ou mudou: volta a avisar
        db.session.commit()
        if request.form.get("ajax"):
            return jsonify(ok=True, remind=t.remind, dias=t.remind_days or 0)
        return redirect(url_for("tarefas"))

    @app.route("/tarefa/<int:tid>/status", methods=["POST"])
    @login_required
    def tarefa_status(tid):
        t = _minha_tarefa(tid)
        _aplica_status(t, (request.form.get("status") or "").strip())
        db.session.commit()
        if request.form.get("ajax"):
            return jsonify(ok=True, done=t.done, status=t.status)
        return redirect(url_for("tarefas"))

    @app.route("/tarefa/<int:tid>/editar", methods=["POST"])
    @login_required
    def tarefa_editar(tid):
        t = _minha_tarefa(tid)
        titulo = (request.form.get("title") or "").strip()
        if titulo:
            t.title = titulo[:300]
        if "due_date" in request.form:
            nova = _parse_due(request.form.get("due_date"))
            t.due_date = nova
            if nova and nova >= fuso.hoje():
                t.reminded_on = None      # reabre o aviso se remarcou pra frente
        if "remind" in request.form:
            t.remind = bool(request.form.get("remind"))
        if "remind_days" in request.form:
            t.remind_days = _parse_dias(request.form.get("remind_days"))
            t.reminded_on = None
        db.session.commit()
        if request.form.get("ajax"):
            return jsonify(ok=True)
        return redirect(url_for("tarefas"))

    @app.route("/tarefa/<int:tid>/excluir", methods=["POST"])
    @login_required
    def tarefa_excluir(tid):
        t = _minha_tarefa(tid)
        db.session.delete(t)
        db.session.commit()
        return redirect(url_for("tarefas"))

    # ------------------------------------------------------------------
    # DEFINICOES GERAIS — compartilhadas: todo o time ve; controladoria edita
    # ------------------------------------------------------------------
    @app.route("/definicoes")
    @any_team_required
    def definicoes():
        listas = DefinitionList.query.order_by(DefinitionList.sort_order, DefinitionList.id).all()
        if not listas and current_user.is_controladoria:
            l = DefinitionList(name="Definições gerais", created_by=current_user.id)
            db.session.add(l)
            db.session.commit()
            listas = [l]
        return render_template("team/definicoes.html", listas=listas,
                               pode_editar=current_user.is_controladoria,
                               busca=(request.args.get("q") or "").strip()[:100],
                               total=Definition.query.count())

    def _def_volta(lid=None):
        return redirect(url_for("definicoes") + (f"#lista-{lid}" if lid else ""))

    @app.route("/definicoes/lista/nova", methods=["POST"])
    @team_required
    def definicao_lista_nova():
        nome = (request.form.get("name") or "").strip() or "Nova lista"
        n = DefinitionList.query.count()
        l = DefinitionList(name=nome[:120], sort_order=100 + n, created_by=current_user.id)
        db.session.add(l)
        db.session.commit()
        log_audit(current_user.id, "definicao_lista_criada", "definicoes", l.name)
        return _def_volta(l.id)

    @app.route("/definicoes/lista/<int:lid>/renomear", methods=["POST"])
    @team_required
    def definicao_lista_renomear(lid):
        l = db.session.get(DefinitionList, lid) or abort(404)
        nome = (request.form.get("name") or "").strip()
        if nome:
            l.name = nome[:120]
            db.session.commit()
        if request.form.get("ajax"):
            return jsonify(ok=True, nome=l.name)
        return _def_volta(lid)

    @app.route("/definicoes/lista/<int:lid>/excluir", methods=["POST"])
    @team_required
    def definicao_lista_excluir(lid):
        l = db.session.get(DefinitionList, lid) or abort(404)
        log_audit(current_user.id, "definicao_lista_excluida", "definicoes",
                  f"{l.name} ({len(l.itens)} itens)")
        db.session.delete(l)
        db.session.commit()
        flash(f"Lista “{l.name}” excluída.", "success")
        return _def_volta()

    @app.route("/definicao/nova", methods=["POST"])
    @team_required
    def definicao_nova():
        l = db.session.get(DefinitionList, request.form.get("list_id", type=int) or 0) or abort(404)
        texto = (request.form.get("text") or "").strip()
        if texto:
            n = Definition.query.filter_by(list_id=l.id).count()
            db.session.add(Definition(list_id=l.id, text=texto[:2000], sort_order=100 + n,
                                      created_by=current_user.id))
            db.session.commit()
        return _def_volta(l.id)

    @app.route("/definicao/<int:did>/editar", methods=["POST"])
    @team_required
    def definicao_editar(did):
        d = db.session.get(Definition, did) or abort(404)
        texto = (request.form.get("text") or "").strip()
        if texto and texto != d.text:
            d.text = texto[:2000]
            d.updated_by = current_user.id
            d.updated_at = datetime.utcnow()
            db.session.commit()
        if request.form.get("ajax"):
            return jsonify(ok=True)
        return _def_volta(d.list_id)

    @app.route("/definicao/<int:did>/excluir", methods=["POST"])
    @team_required
    def definicao_excluir(did):
        d = db.session.get(Definition, did) or abort(404)
        lid = d.list_id
        log_audit(current_user.id, "definicao_excluida", "definicoes", d.text[:120])
        db.session.delete(d)
        db.session.commit()
        return _def_volta(lid)

    # ------------------------------------------------------------------
    # ATAS DE REUNIÃO — captura estruturada (do prompt sobre a transcrição)
    # ------------------------------------------------------------------
    def _extrai_json(raw):
        """Aceita o JSON puro OU a saída completa do prompt (ata + bloco ```json)."""
        import re
        raw = (raw or "").strip()
        m = re.search(r"```json\s*(\{.*?\}|\[.*?\])\s*```", raw, re.DOTALL)
        if m:
            return m.group(1)
        m = re.search(r"```\s*(\{.*?\}|\[.*?\])\s*```", raw, re.DOTALL)
        if m:
            return m.group(1)
        return raw

    def _match_member(nome):
        if not nome:
            return None
        nome = str(nome).strip().lower()
        membros = TeamMember.query.filter_by(active=True).all()
        for m in membros:
            if m.name.strip().lower() == nome:
                return m
        prim = nome.split()[0] if nome.split() else nome
        for m in membros:
            if m.name.strip().lower().split()[0] == prim:
                return m
        for m in membros:
            if nome in m.name.lower() or m.name.lower() in nome:
                return m
        return None

    def _match_company(nome):
        if not nome:
            return None
        nome = str(nome).strip().lower()
        comps = Company.query.all()
        for c in comps:
            if c.name.strip().lower() == nome or (c.code or "").lower() == nome:
                return c
        for c in comps:
            if nome and (nome in c.name.lower() or c.name.lower() in nome):
                return c
        return None

    def _norm_ym(v):
        v = (v or "").strip()
        m = re.match(r"(\d{4})-(\d{1,2})", v) if v else None
        return f"{m.group(1)}-{int(m.group(2)):02d}" if m else (v[:7] if v else None)

    def _pdate(v):
        try:
            return datetime.strptime((v or "").strip()[:10], "%Y-%m-%d").date()
        except Exception:
            return None

    @app.route("/atas")
    @team_required
    def atas():
        f_emp = request.args.get("company_id", type=int)
        f_tipo = request.args.get("tipo")
        q = AtaReuniao.query
        if f_emp:
            q = q.filter(AtaReuniao.company_id == f_emp)
        if f_tipo in ("resultado", "geral"):
            q = q.filter(AtaReuniao.tipo == f_tipo)
        lista = q.order_by(AtaReuniao.data_reuniao.is_(None),
                           AtaReuniao.data_reuniao.desc(), AtaReuniao.id.desc()).all()
        empresas = Company.query.filter_by(active=True).order_by(Company.name).all()
        return render_template("team/atas.html", atas=lista, empresas=empresas,
                               f_emp=f_emp, f_tipo=f_tipo)

    @app.route("/atas/nova", methods=["GET", "POST"])
    @team_required
    def ata_nova():
        if request.method == "POST" and request.form.get("etapa") == "salvar":
            return _ata_salvar()
        if request.method == "POST":                 # etapa: analisar o JSON colado
            try:
                dados = json.loads(_extrai_json(request.form.get("json")))
            except Exception as e:
                flash(f"JSON inválido: {e}. Cole a saída do prompt (ou só o bloco JSON).",
                      "danger")
                return redirect(url_for("ata_nova"))
            if not isinstance(dados, dict):
                flash("Para importar vários de uma vez, use 'Importar histórico'.", "warning")
                return redirect(url_for("ata_nova"))
            meta = dados.get("meta") or {}
            comp_match = _match_company(meta.get("empresa"))
            acoes = []
            for a in (dados.get("acoes") or []):
                mm = _match_member(a.get("responsavel"))
                acoes.append({"tarefa": a.get("tarefa", ""),
                              "responsavel": a.get("responsavel", ""),
                              "member_id": mm.id if mm else "",
                              "prazo": a.get("prazo") or "",
                              "prazo_texto": a.get("prazo_texto") or "",
                              "prioridade": a.get("prioridade") or "media"})
            membros = TeamMember.query.filter_by(active=True).order_by(
                TeamMember.sort_order, TeamMember.name).all()
            return render_template("team/ata_confirmar.html", dados=dados, meta=meta,
                                   raw=_extrai_json(request.form.get("json")),
                                   comp_match=comp_match, acoes=acoes, membros=membros,
                                   empresas=Company.query.filter_by(active=True)
                                   .order_by(Company.name).all())
        return render_template("team/ata_nova.html")

    def _ata_salvar():
        raw = request.form.get("raw") or "{}"
        try:
            dados = json.loads(raw)
        except Exception:
            dados = {}
        meta = dados.get("meta") or {}
        tipo = meta.get("tipo") if meta.get("tipo") in ("resultado", "geral") else \
            (request.form.get("tipo") or "geral")
        cid = _int(request.form.get("company_id"))
        ata = AtaReuniao(
            tipo=tipo,
            titulo=(meta.get("titulo") or request.form.get("titulo") or "Ata")[:240],
            data_reuniao=_pdate(meta.get("data_reuniao")),
            company_id=cid if tipo == "resultado" else None,
            comp_ym=_norm_ym(meta.get("competencia")) if tipo == "resultado" else None,
            resumo=dados.get("resumo_executivo") or None,
            ata_texto=request.form.get("ata_texto") or None,
            dados_json=raw, created_by=current_user.id)
        db.session.add(ata)
        db.session.commit()
        # cria as atividades marcadas (com a conferência do gestor)
        n = int(request.form.get("n_acoes") or 0)
        criadas = 0
        for i in range(n):
            if not request.form.get(f"criar_{i}"):
                continue
            titulo = (request.form.get(f"tarefa_{i}") or "").strip()
            if not titulo:
                continue
            db.session.add(Activity(
                title=titulo[:240], kind="spot", origin="ata",
                member_id=_int(request.form.get(f"member_{i}")),
                company_id=ata.company_id, status="pendente",
                priority=request.form.get(f"prio_{i}") or "media",
                due_date=_pdate(request.form.get(f"prazo_{i}")),
                description=f"Origem: ata “{ata.titulo}”"
                            f"{(' — ' + ata.data_reuniao.strftime('%d/%m/%Y')) if ata.data_reuniao else ''}",
                created_by=current_user.id))
            criadas += 1
        db.session.commit()
        log_audit(current_user.id, "ata_criada", "ata", str(ata.id))
        flash(f"Ata salva. {criadas} atividade(s) criada(s).", "success")
        return redirect(url_for("ata_detail", aid=ata.id))

    @app.route("/atas/importar", methods=["POST"])
    @team_required
    def atas_importar():
        """Importa VÁRIAS atas de uma vez (histórico) — só arquiva, sem criar tarefas."""
        try:
            arr = json.loads(_extrai_json(request.form.get("json")))
        except Exception as e:
            flash(f"JSON inválido: {e}", "danger")
            return redirect(url_for("atas"))
        if isinstance(arr, dict):
            arr = [arr]
        n = 0
        for dados in arr:
            if not isinstance(dados, dict):
                continue
            meta = dados.get("meta") or {}
            tipo = meta.get("tipo") if meta.get("tipo") in ("resultado", "geral") else "geral"
            comp = _match_company(meta.get("empresa"))
            db.session.add(AtaReuniao(
                tipo=tipo, titulo=(meta.get("titulo") or "Ata importada")[:240],
                data_reuniao=_pdate(meta.get("data_reuniao")),
                company_id=comp.id if (tipo == "resultado" and comp) else None,
                comp_ym=_norm_ym(meta.get("competencia")) if tipo == "resultado" else None,
                resumo=dados.get("resumo_executivo") or None,
                dados_json=json.dumps(dados, ensure_ascii=False),
                created_by=current_user.id))
            n += 1
        db.session.commit()
        flash(f"{n} ata(s) importada(s) para o histórico.", "success")
        return redirect(url_for("atas"))

    @app.route("/ata/<int:aid>")
    @team_required
    def ata_detail(aid):
        ata = db.session.get(AtaReuniao, aid) or abort(404)
        return render_template("team/ata_detail.html", ata=ata,
                               mm={m.id: m for m in TeamMember.query.all()})

    @app.route("/ata/<int:aid>/excluir", methods=["POST"])
    @team_required
    def ata_excluir(aid):
        ata = db.session.get(AtaReuniao, aid) or abort(404)
        db.session.delete(ata)
        db.session.commit()
        flash("Ata excluída.", "success")
        return redirect(url_for("atas"))

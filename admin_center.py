# -*- coding: utf-8 -*-
"""Centro de Administracao — cadastro de todos os dados-mestre do portal.

Reune num so lugar o que antes estava disperso (ou so existia via seed):
  Organizacao      -> Empresas e Usuarios (CRUD completo)
  Time             -> Membros e Carteira (empresa x pessoa x entregas)
  Fechamento       -> atalho para /gestao (competencias, prazos, regua)
  Template & regras-> versao do template oficial + manifesto + tolerancias padrao

Acessivel por ADMINISTRADOR e CONTROLADORIA (mesma regra da /gestao).
"""
import os
import shutil
from datetime import datetime
from functools import wraps

from flask import (render_template, request, redirect, url_for, flash, abort)
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename

from config import Config
from models import (db, User, Company, Competency,
                    get_setting, set_setting, log_audit)
from team.models import TeamMember, CompanyAssignment
from team.models_workflow import Segment, Panel
from team.models import Indicator

ROLE_LABELS = [("gestor", "Gestor"), ("senior", "Analista Sênior"),
               ("pleno", "Analista Pleno"), ("especialista", "Especialista")]
DELIVERABLES = ["Painel", "Endividamento", "Consolidação", "Auxiliares"]
FLOWS = ["Próprio", "Terceiro", "Verificar"]
RESPONSIBILITIES = ["J&F", "Terceiro"]
DEFAULT_PASSWORD = "jfsa@2026T"


def _int(v):
    try:
        return int(v) if v not in (None, "", "None") else None
    except (ValueError, TypeError):
        return None


def _float(v):
    try:
        return float(str(v).replace(",", ".")) if v not in (None, "") else None
    except (ValueError, TypeError):
        return None


def _bool(v):
    return str(v).lower() in ("1", "true", "on", "yes", "sim")


def _whats(v):
    """Normaliza o WhatsApp digitado para E.164 (vazio -> None)."""
    from team.alerts import normaliza_whatsapp
    return normaliza_whatsapp(v)


def register_admin_routes(app):

    def admin_required(f):
        """Administrador OU controladoria (mesma regra da Gestao de Fechamento)."""
        @wraps(f)
        @login_required
        def wrap(*a, **k):
            if not current_user.is_controladoria:
                abort(403)
            return f(*a, **k)
        return wrap

    # ==================================================================
    # PAGINA PRINCIPAL
    # ==================================================================
    @app.route("/admin")
    @admin_required
    def admin():
        companies = Company.query.order_by(Company.name).all()
        users = User.query.order_by(User.role, User.display_name).all()
        members = TeamMember.query.order_by(TeamMember.sort_order,
                                            TeamMember.name).all()
        assignments = (CompanyAssignment.query.join(Company)
                       .order_by(CompanyAssignment.seat, Company.name).all())
        comps = Competency.query.order_by(Competency.year.desc(),
                                          Competency.month.desc()).all()
        # empresas ainda sem linha na carteira (o furo que travava a operacao)
        assigned = {a.company_id for a in assignments}
        orphan_companies = [c for c in companies if c.id not in assigned and c.active]
        # modulo de Consolidacao removido nesta versao — sem template/manifesto
        tpl_info = None
        man = None
        seg_objs = (Segment.query.order_by(Segment.sort_order, Segment.name).all())
        segments = [s.name for s in seg_objs if s.active]
        panels = Panel.query.order_by(Panel.sort_order, Panel.name).all()
        from team.models import IndicatorDef
        defs = (IndicatorDef.query.filter_by(active=True)
                .order_by(IndicatorDef.sort_order, IndicatorDef.title).all())
        return render_template(
            "admin.html", companies=companies, users=users, members=members,
            defs=defs,
            assignments=assignments, competencies=comps,
            orphan_companies=orphan_companies, tpl=tpl_info, man=man,
            role_labels=ROLE_LABELS, deliverables=DELIVERABLES, flows=FLOWS,
            responsibilities=RESPONSIBILITIES, segments=segments, seg_objs=seg_objs,
            panels=panels,
            default_tol_rel=get_setting("default_tol_rel", Config.DEFAULT_TOL_REL),
            default_tol_abs=get_setting("default_tol_abs", Config.DEFAULT_TOL_ABS),
            default_password=DEFAULT_PASSWORD, tab=request.args.get("tab", "time"),
            mod_consolidacao=str(get_setting("mod_consolidacao", "0")) == "1",
            competencia_atual_id=(int(get_setting("competencia_atual_id"))
                                  if get_setting("competencia_atual_id") else None),
            sched=_scheduler_info())

    def _scheduler_info():
        import scheduler as sched
        return {
            "status": sched.status(),
            "enabled": str(get_setting("scheduler_enabled", "1")) == "1",
            "hour": int(get_setting("scheduler_hour", 8) or 8),
            "weekday": int(get_setting("digest_weekday", 0) or 0),
            "last_alertas": get_setting("last_alertas"),
            "last_cobrancas": get_setting("last_cobrancas"),
            "last_resumo": get_setting("last_resumo"),
        }

    def _back(tab):
        return redirect(url_for("admin", tab=tab))

    # ==================================================================
    # TIME — MEMBROS
    # ==================================================================
    @app.route("/admin/member", methods=["POST"])
    @admin_required
    def admin_member_create():
        name = (request.form.get("name") or "").strip()
        if not name:
            flash("Informe o nome do membro.", "danger")
            return _back("time")
        m = TeamMember(
            name=name,
            role_label=request.form.get("role_label") or "especialista",
            user_id=_int(request.form.get("user_id")),
            panel_id=_int(request.form.get("panel_id")),
            is_manager=_bool(request.form.get("is_manager")),
            color=request.form.get("color") or "#1d5da8",
            whatsapp=_whats(request.form.get("whatsapp")),
            sort_order=_int(request.form.get("sort_order")) or 100,
            active=True)
        db.session.add(m)
        db.session.commit()
        log_audit(current_user.id, "membro_criado", "team_member", name)
        flash(f"Membro '{name}' cadastrado.", "success")
        return _back("time")

    @app.route("/admin/member/<int:mid>", methods=["POST"])
    @admin_required
    def admin_member_update(mid):
        m = db.session.get(TeamMember, mid) or abort(404)
        m.name = (request.form.get("name") or m.name).strip()
        m.role_label = request.form.get("role_label") or m.role_label
        m.user_id = _int(request.form.get("user_id"))
        m.panel_id = _int(request.form.get("panel_id"))
        m.is_manager = _bool(request.form.get("is_manager"))
        m.color = request.form.get("color") or m.color
        if "whatsapp" in request.form:
            m.whatsapp = _whats(request.form.get("whatsapp"))
        m.sort_order = _int(request.form.get("sort_order")) or m.sort_order
        m.active = _bool(request.form.get("active"))
        db.session.commit()
        log_audit(current_user.id, "membro_editado", "team_member", m.name)
        flash(f"Membro '{m.name}' atualizado.", "success")
        return _back("time")

    @app.route("/admin/member/<int:mid>/excluir", methods=["POST"])
    @admin_required
    def admin_member_delete(mid):
        from team.models import Activity, Indicator
        m = db.session.get(TeamMember, mid) or abort(404)
        n_assign = CompanyAssignment.query.filter_by(member_id=mid).count()
        n_act = Activity.query.filter_by(member_id=mid).count()
        if n_assign or n_act:
            # tem histórico -> não apaga; desvincula da carteira e INATIVA.
            # inativo some do painel do dia / indicadores / seletores, mas
            # continua no cadastro e nas atividades já concluídas.
            if n_assign:
                CompanyAssignment.query.filter_by(member_id=mid).update({"member_id": None})
            m.active = False
            db.session.commit()
            partes = []
            if n_act:
                partes.append(f"{n_act} atividade(s)")
            if n_assign:
                partes.append(f"{n_assign} empresa(s) na carteira")
            flash(f"'{m.name}' tem histórico ({', '.join(partes)}), então foi "
                  f"INATIVADO em vez de excluído — some do Painel do Dia e dos "
                  f"Indicadores, mas fica no cadastro e nas atividades concluídas. "
                  f"Para reativar, marque 'Ativo'.", "warning")
        else:
            name = m.name
            # sem histórico: exclusão definitiva (limpa painel pessoal órfão)
            Indicator.query.filter_by(member_id=mid).delete()
            db.session.delete(m)
            db.session.commit()
            flash(f"Membro '{name}' excluído definitivamente.", "success")
        log_audit(current_user.id, "membro_removido", "team_member", str(mid))
        return _back("time")

    # ==================================================================
    # TIME — CARTEIRA (CompanyAssignment)
    # ==================================================================
    def _apply_assignment(a, form):
        a.member_id = _int(form.get("member_id"))
        a.seat = _int(form.get("seat"))
        a.segment = (form.get("segment") or "").strip() or None
        a.flow = form.get("flow") or None
        a.responsibility = form.get("responsibility") or None
        a.deliverables = form.getlist("deliverables")
        a.load_real = _int(form.get("load_real")) or 0
        a.load_ideal = _int(form.get("load_ideal")) or 0
        a.production = _int(form.get("production")) or 0
        a.note = (form.get("note") or "").strip() or None

    @app.route("/admin/assignment", methods=["POST"])
    @admin_required
    def admin_assignment_create():
        cid = _int(request.form.get("company_id"))
        if not cid:
            flash("Selecione a empresa.", "danger")
            return _back("carteira")
        if CompanyAssignment.query.filter_by(company_id=cid).first():
            flash("Esta empresa já está na carteira.", "warning")
            return _back("carteira")
        a = CompanyAssignment(company_id=cid)
        _apply_assignment(a, request.form)
        db.session.add(a)
        db.session.commit()
        log_audit(current_user.id, "carteira_criada", "assignment", str(cid))
        flash(f"{a.company.name} incluída na carteira.", "success")
        return _back("carteira")

    @app.route("/admin/assignment/<int:aid>", methods=["POST"])
    @admin_required
    def admin_assignment_update(aid):
        a = db.session.get(CompanyAssignment, aid) or abort(404)
        _apply_assignment(a, request.form)
        db.session.commit()
        log_audit(current_user.id, "carteira_editada", "assignment", str(aid))
        flash(f"Carteira de {a.company.name} atualizada.", "success")
        return _back("carteira")

    @app.route("/admin/carteira/salvar", methods=["POST"])
    @admin_required
    def admin_carteira_salvar():
        """Salva a tabela inteira de uma vez.

        Antes cada linha era um formulario proprio: quem editava varias e
        clicava em 'Salvar' numa delas perdia as outras sem aviso.
        """
        alterados = 0
        for a in CompanyAssignment.query.all():
            pref = f"a{a.id}_"
            if not any(k.startswith(pref) for k in request.form):
                continue
            antes = (a.member_id, a.seat, a.segment, a.flow, a.responsibility,
                     tuple(a.deliverables), a.load_real, a.load_ideal)
            a.member_id = _int(request.form.get(pref + "member_id"))
            a.seat = _int(request.form.get(pref + "seat"))
            a.segment = (request.form.get(pref + "segment") or "").strip() or None
            a.flow = request.form.get(pref + "flow") or None
            a.responsibility = request.form.get(pref + "responsibility") or None
            a.deliverables = request.form.getlist(pref + "deliverables")
            a.load_real = _int(request.form.get(pref + "load_real")) or 0
            a.load_ideal = _int(request.form.get(pref + "load_ideal")) or 0
            depois = (a.member_id, a.seat, a.segment, a.flow, a.responsibility,
                      tuple(a.deliverables), a.load_real, a.load_ideal)
            if antes != depois:
                alterados += 1
        db.session.commit()
        log_audit(current_user.id, "carteira_lote_salva", "assignment",
                  f"{alterados} alteradas")
        flash(f"{alterados} linha(s) da carteira salva(s)." if alterados
              else "Nenhuma alteração para salvar.", "success" if alterados else "info")
        return _back("carteira")

    @app.route("/admin/assignment/<int:aid>/excluir", methods=["POST"])
    @admin_required
    def admin_assignment_delete(aid):
        a = db.session.get(CompanyAssignment, aid) or abort(404)
        nome = a.company.name
        db.session.delete(a)
        db.session.commit()
        log_audit(current_user.id, "carteira_removida", "assignment", str(aid))
        flash(f"{nome} removida da carteira.", "success")
        return _back("carteira")

    @app.route("/admin/assignment/criar-faltantes", methods=["POST"])
    @admin_required
    def admin_assignment_fill():
        """Cria a linha de carteira para toda empresa ativa que ainda nao tem.

        Fecha o furo: empresa criada no admin passa a aparecer na carteira do time.
        """
        assigned = {a.company_id for a in CompanyAssignment.query.all()}
        n = 0
        for c in Company.query.filter_by(active=True).all():
            if c.id not in assigned:
                db.session.add(CompanyAssignment(company_id=c.id, load_ideal=4))
                n += 1
        db.session.commit()
        log_audit(current_user.id, "carteira_faltantes", "assignment", str(n))
        flash(f"{n} empresa(s) incluída(s) na carteira (sem responsável definido).",
              "success")
        return _back("carteira")

    # ==================================================================
    # ORGANIZACAO — EMPRESAS
    # ==================================================================
    @app.route("/admin/company", methods=["POST"])
    @admin_required
    def admin_company_create():
        code = (request.form.get("code") or "").strip().upper()
        name = (request.form.get("name") or "").strip()
        if not code or not name:
            flash("Código e nome são obrigatórios.", "danger")
            return _back("org")
        if Company.query.filter_by(code=code).first():
            flash(f"Já existe empresa com o código {code}.", "warning")
            return _back("org")
        c = Company(code=code, name=name,
                    canonical_label=(request.form.get("canonical_label") or name).strip(),
                    tol_rel=_float(request.form.get("tol_rel")),
                    tol_abs=_float(request.form.get("tol_abs")),
                    active=True)
        db.session.add(c)
        db.session.commit()
        # ja entra na carteira do time (evita empresa "invisivel" para o time)
        if _bool(request.form.get("add_to_carteira")):
            db.session.add(CompanyAssignment(company_id=c.id, load_ideal=4))
            db.session.commit()
        log_audit(current_user.id, "empresa_criada", "company", code)
        flash(f"Empresa '{name}' cadastrada.", "success")
        return _back("org")

    @app.route("/admin/company/<int:cid>", methods=["POST"])
    @admin_required
    def admin_company_update(cid):
        c = db.session.get(Company, cid) or abort(404)
        code = (request.form.get("code") or c.code).strip().upper()
        dup = Company.query.filter(Company.code == code, Company.id != cid).first()
        if dup:
            flash(f"O código {code} já pertence a outra empresa.", "danger")
            return _back("org")
        c.code = code
        c.name = (request.form.get("name") or c.name).strip()
        c.canonical_label = (request.form.get("canonical_label") or "").strip() or c.name
        c.tol_rel = _float(request.form.get("tol_rel"))
        c.tol_abs = _float(request.form.get("tol_abs"))
        c.active = _bool(request.form.get("active"))
        db.session.commit()
        log_audit(current_user.id, "empresa_editada", "company", c.code)
        flash(f"Empresa '{c.name}' atualizada.", "success")
        return _back("org")

    @app.route("/admin/company/<int:cid>/excluir", methods=["POST"])
    @admin_required
    def admin_company_delete(cid):
        c = db.session.get(Company, cid) or abort(404)
        n_sub = Submission.query.filter_by(company_id=cid).count()
        if n_sub:
            c.active = False
            db.session.commit()
            flash(f"'{c.name}' tem {n_sub} envio(s) no histórico — foi inativada "
                  f"em vez de excluída.", "warning")
        else:
            CompanyAssignment.query.filter_by(company_id=cid).delete()
            User.query.filter_by(company_id=cid).update({"active": False})
            nome = c.name
            db.session.delete(c)
            db.session.commit()
            flash(f"Empresa '{nome}' excluída.", "success")
        log_audit(current_user.id, "empresa_removida", "company", str(cid))
        return _back("org")

    # ==================================================================
    # ORGANIZACAO — USUARIOS
    # ==================================================================
    @app.route("/admin/user", methods=["POST"])
    @admin_required
    def admin_user_create():
        email = (request.form.get("email") or "").strip().lower()
        name = (request.form.get("display_name") or "").strip()
        role = request.form.get("role") or "empresa"
        if not email or not name:
            flash("Nome e e-mail são obrigatórios.", "danger")
            return _back("org")
        if User.query.filter_by(email=email).first():
            flash(f"Já existe usuário com o e-mail {email}.", "warning")
            return _back("org")
        pw = request.form.get("password") or DEFAULT_PASSWORD
        u = User(email=email, display_name=name, role=role,
                 company_id=_int(request.form.get("company_id")),
                 must_change_password=True, active=True)
        u.set_password(pw)
        db.session.add(u)
        db.session.commit()
        log_audit(current_user.id, "usuario_criado", "user", email)
        flash(f"Usuário {email} criado (senha inicial: {pw}).", "success")
        return _back("org")

    @app.route("/admin/user/<int:uid>", methods=["POST"])
    @admin_required
    def admin_user_update(uid):
        u = db.session.get(User, uid) or abort(404)
        email = (request.form.get("email") or u.email).strip().lower()
        dup = User.query.filter(User.email == email, User.id != uid).first()
        if dup:
            flash(f"O e-mail {email} já pertence a outro usuário.", "danger")
            return _back("org")
        # nao permite remover o proprio acesso de admin (evita lockout)
        new_role = request.form.get("role") or u.role
        if u.id == current_user.id and new_role not in ("admin", "controladoria"):
            flash("Você não pode rebaixar o próprio perfil.", "danger")
            return _back("org")
        u.email = email
        u.display_name = (request.form.get("display_name") or u.display_name).strip()
        u.role = new_role
        u.company_id = _int(request.form.get("company_id"))
        active = _bool(request.form.get("active"))
        if u.id == current_user.id and not active:
            flash("Você não pode inativar o próprio usuário.", "danger")
            return _back("org")
        u.active = active
        db.session.commit()
        log_audit(current_user.id, "usuario_editado", "user", u.email)
        flash(f"Usuário {u.email} atualizado.", "success")
        return _back("org")

    @app.route("/admin/user/<int:uid>/senha", methods=["POST"])
    @admin_required
    def admin_user_reset_password(uid):
        u = db.session.get(User, uid) or abort(404)
        pw = request.form.get("password") or DEFAULT_PASSWORD
        u.set_password(pw)
        u.must_change_password = True
        db.session.commit()
        log_audit(current_user.id, "senha_resetada", "user", u.email)
        flash(f"Senha de {u.email} redefinida para '{pw}' (troca no próximo acesso).",
              "success")
        return _back("org")

    # ==================================================================
    # PAINEIS (cadastro: pessoal ou de equipe)
    # ==================================================================
    @app.route("/admin/painel", methods=["POST"])
    @admin_required
    def admin_painel_create():
        nome = (request.form.get("name") or "").strip()
        if not nome:
            flash("Informe o nome do painel.", "danger")
            return _back("time")
        kind = request.form.get("kind") or "pessoal"
        owner = _int(request.form.get("owner_member_id")) if kind == "pessoal" else None
        maior = db.session.query(db.func.max(Panel.sort_order)).scalar() or 100
        db.session.add(Panel(name=nome, kind=kind, owner_member_id=owner,
                             color=request.form.get("color") or "#1d5da8",
                             sort_order=maior + 1))
        db.session.commit()
        log_audit(current_user.id, "painel_criado", "panel", nome)
        flash(f"Painel “{nome}” criado.", "success")
        return _back("time")

    @app.route("/admin/painel/<int:pid>", methods=["POST"])
    @admin_required
    def admin_painel_update(pid):
        p = db.session.get(Panel, pid) or abort(404)
        p.name = (request.form.get("name") or p.name).strip()
        p.kind = request.form.get("kind") or p.kind
        p.owner_member_id = (_int(request.form.get("owner_member_id"))
                             if p.kind == "pessoal" else None)
        p.color = request.form.get("color") or p.color
        p.active = _bool(request.form.get("active"))
        p.sort_order = _int(request.form.get("sort_order")) or p.sort_order
        db.session.commit()
        log_audit(current_user.id, "painel_editado", "panel", p.name)
        flash(f"Painel “{p.name}” atualizado.", "success")
        return _back("time")

    @app.route("/admin/painel/<int:pid>/excluir", methods=["POST"])
    @admin_required
    def admin_painel_delete(pid):
        p = db.session.get(Panel, pid) or abort(404)
        n_seg = TeamMember.query.filter_by(panel_id=pid).count()
        n_ind = Indicator.query.filter_by(panel_id=pid).count()
        if n_seg or n_ind:
            p.active = False
            db.session.commit()
            flash(f"“{p.name}” está em uso ({n_seg} membro[s], {n_ind} meta[s]) — "
                  f"foi inativado em vez de excluído.", "warning")
        else:
            nome = p.name
            db.session.delete(p)
            db.session.commit()
            flash(f"Painel “{nome}” excluído.", "success")
        return _back("time")

    # ==================================================================
    # SEGMENTOS (cadastro editavel)
    # ==================================================================
    @app.route("/admin/segmento", methods=["POST"])
    @admin_required
    def admin_segmento_create():
        nome = (request.form.get("name") or "").strip()
        if not nome:
            flash("Informe o nome do segmento.", "danger")
            return _back("org")
        if Segment.query.filter(db.func.lower(Segment.name) == nome.lower()).first():
            flash("Esse segmento já existe.", "warning")
            return _back("org")
        maior = db.session.query(db.func.max(Segment.sort_order)).scalar() or 100
        db.session.add(Segment(name=nome, sort_order=maior + 1))
        db.session.commit()
        log_audit(current_user.id, "segmento_criado", "segment", nome)
        flash(f"Segmento “{nome}” cadastrado.", "success")
        return _back("org")

    @app.route("/admin/segmento/<int:sid>", methods=["POST"])
    @admin_required
    def admin_segmento_update(sid):
        seg = db.session.get(Segment, sid) or abort(404)
        novo = (request.form.get("name") or seg.name).strip()
        dup = Segment.query.filter(db.func.lower(Segment.name) == novo.lower(),
                                   Segment.id != sid).first()
        if dup:
            flash("Já existe outro segmento com esse nome.", "danger")
            return _back("org")
        antigo = seg.name
        seg.name = novo
        seg.active = _bool(request.form.get("active"))
        seg.sort_order = _int(request.form.get("sort_order")) or seg.sort_order
        # renomear propaga para as entidades que usavam o valor antigo
        if antigo != novo:
            CompanyAssignment.query.filter_by(segment=antigo).update(
                {"segment": novo})
        db.session.commit()
        log_audit(current_user.id, "segmento_editado", "segment", novo)
        flash(f"Segmento atualizado para “{novo}”.", "success")
        return _back("org")

    @app.route("/admin/segmento/<int:sid>/excluir", methods=["POST"])
    @admin_required
    def admin_segmento_delete(sid):
        seg = db.session.get(Segment, sid) or abort(404)
        em_uso = CompanyAssignment.query.filter_by(segment=seg.name).count()
        if em_uso:
            seg.active = False
            db.session.commit()
            flash(f"“{seg.name}” está em uso por {em_uso} entidade(s) — foi inativado "
                  f"em vez de excluído.", "warning")
        else:
            nome = seg.name
            db.session.delete(seg)
            db.session.commit()
            flash(f"Segmento “{nome}” excluído.", "success")
        return _back("org")

    # ==================================================================
    # SISTEMA (agendador, competencias)
    # ==================================================================
    @app.route("/admin/agendador", methods=["POST"])
    @admin_required
    def admin_scheduler():
        set_setting("scheduler_enabled",
                    "1" if _bool(request.form.get("scheduler_enabled")) else "0")
        hora = _int(request.form.get("scheduler_hour"))
        if hora is not None and 0 <= hora <= 23:
            set_setting("scheduler_hour", hora)
        dia = _int(request.form.get("digest_weekday"))
        if dia is not None and 0 <= dia <= 6:
            set_setting("digest_weekday", dia)
        log_audit(current_user.id, "agendador_config", "setting", "")
        flash("Configuração do agendador salva.", "success")
        return _back("sistema")

    @app.route("/admin/competencia", methods=["POST"])
    @admin_required
    def admin_competencia_create():
        from engine.calendar_br import deadline_for_competency
        year = _int(request.form.get("year"))
        month = _int(request.form.get("month"))
        if not year or not month or not (1 <= month <= 12):
            flash("Ano/mês inválidos.", "danger")
            return _back("sistema")
        if Competency.query.filter_by(year=year, month=month).first():
            flash("Essa competência já existe.", "warning")
            return _back("sistema")
        nth = int(get_setting("closing_nth_bday", "5") or 5)
        try:
            dl = deadline_for_competency(year, month, nth=nth)
        except Exception:
            dl = None
        db.session.add(Competency(year=year, month=month, deadline=dl,
                                  status="aberta"))
        db.session.commit()
        flash("Competência criada.", "success")
        return _back("sistema")

    @app.route("/admin/competencia-atual", methods=["POST"])
    @admin_required
    def admin_competencia_atual():
        cid = _int(request.form.get("competencia_atual_id"))
        if cid and db.session.get(Competency, cid):
            set_setting("competencia_atual_id", cid)
            c = db.session.get(Competency, cid)
            flash(f"Competência atual definida: {c.label}.", "success")
        else:
            set_setting("competencia_atual_id", "")
            flash("Competência atual limpa (usa a aberta mais recente).", "info")
        return _back("sistema")

    @app.route("/admin/competencia/<int:cid>/status", methods=["POST"])
    @admin_required
    def admin_competencia_status(cid):
        c = db.session.get(Competency, cid) or abort(404)
        novo = (request.form.get("status") or "").strip()
        if novo in ("aberta", "fechada", "planejada"):
            c.status = novo
            c.closed_at = datetime.utcnow() if novo == "fechada" else None
            db.session.commit()
            flash(f"Competência {c.label} agora está {novo}.", "success")
        return _back("sistema")

    @app.route("/admin/agendador/rodar", methods=["POST"])
    @admin_required
    def admin_scheduler_run():
        import scheduler as sched
        res = sched.run_daily_tasks(app, force=True)
        flash(f"Tarefas executadas agora — resumo: "
              f"{'sim' if res.get('resumo') else 'não'}.", "success")
        return _back("sistema")

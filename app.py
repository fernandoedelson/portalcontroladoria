# -*- coding: utf-8 -*-
"""Portal de Gestao da Controladoria J&F — aplicacao Flask (versao enxuta).

Somente o modulo de Gestao do Time (atividades, projetos, indicadores,
capacidade, ferias, carteira, alertas, atas, tarefas e notas). O modulo de
Consolidacao foi removido desta versao.
"""
from datetime import datetime
from functools import wraps

from flask import (Flask, render_template, request, redirect, url_for, flash,
                   abort, jsonify)
from flask_login import (LoginManager, login_user, logout_user, login_required,
                         current_user)
from flask_wtf import CSRFProtect

from config import Config
from models import (db, User, Company, Notification, get_setting,
                    log_audit, notify, current_competency)

# Modulo de Gestao do Time — importa os modelos para que db.create_all()
# registre as tabelas, e as rotas para registro.
import team.models  # noqa: F401
import team.models_workflow  # noqa: F401
from team.models_workflow import ensure_segments_defaults, ensure_panels_defaults
from team.models import ensure_alert_defaults
from team.routes import register_team_routes
from admin_center import register_admin_routes
from workflow_routes import register_workflow_routes
import scheduler
from icons import register_icons

login_manager = LoginManager()
csrf = CSRFProtect()


def create_app(config=Config):
    app = Flask(__name__)
    app.config.from_object(config)
    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)
    login_manager.login_view = "login"

    with app.app_context():
        db.create_all()
        for fn, nome in ((ensure_alert_defaults, "alertas"),
                         (ensure_segments_defaults, "segmentos"),
                         (ensure_panels_defaults, "paineis")):
            try:
                fn()
            except Exception as e:
                app.logger.warning("Nao foi possivel semear %s: %s", nome, e)

    register_icons(app)
    register_routes(app)
    register_team_routes(app)
    register_admin_routes(app)
    register_workflow_routes(app)
    scheduler.start(app)
    return app


@login_manager.user_loader
def load_user(uid):
    return db.session.get(User, int(uid))


# --------------------------------------------------------------------------
# Decorators de perfil
# --------------------------------------------------------------------------
def controladoria_required(f):
    @wraps(f)
    @login_required
    def wrap(*a, **k):
        if not current_user.is_controladoria:
            abort(403)
        return f(*a, **k)
    return wrap


def admin_required(f):
    @wraps(f)
    @login_required
    def wrap(*a, **k):
        if not current_user.is_admin:
            abort(403)
        return f(*a, **k)
    return wrap


def consolidacao_ativa():
    """Mantida por compatibilidade dos templates; nesta versao e sempre False."""
    return False


def register_routes(app):

    @app.context_processor
    def inject_globals():
        unread = 0
        if current_user.is_authenticated:
            unread = Notification.query.filter_by(
                user_id=current_user.id, is_read=False).count()
        return {"APP_NAME": Config.APP_NAME, "ORG": Config.ORG,
                "unread_notifications": unread, "app_version": "1.7.0",
                "consolidacao_ativa": False, "now": datetime.utcnow()}

    # ---------------- Auth ----------------
    @app.route("/login", methods=["GET", "POST"])
    def login():
        if current_user.is_authenticated:
            return redirect(url_for("index"))
        if request.method == "POST":
            email = (request.form.get("email") or "").strip().lower()
            pw = request.form.get("password") or ""
            user = User.query.filter_by(email=email).first()
            if user and user.active and user.check_password(pw):
                login_user(user)
                user.last_login = datetime.utcnow()
                db.session.commit()
                log_audit(user.id, "login", "user", email)
                if user.must_change_password:
                    return redirect(url_for("change_password"))
                return redirect(url_for("index"))
            flash("Credenciais invalidas ou usuario inativo.", "danger")
        return render_template("login.html")

    @app.route("/logout")
    @login_required
    def logout():
        log_audit(current_user.id, "logout", "user", current_user.email)
        logout_user()
        return redirect(url_for("login"))

    @app.route("/change-password", methods=["GET", "POST"])
    @login_required
    def change_password():
        if request.method == "POST":
            cur = request.form.get("current") or ""
            new = request.form.get("new") or ""
            conf = request.form.get("confirm") or ""
            if not current_user.check_password(cur):
                flash("Senha atual incorreta.", "danger")
            elif len(new) < 8:
                flash("A nova senha deve ter ao menos 8 caracteres.", "danger")
            elif new != conf:
                flash("A confirmacao nao confere.", "danger")
            else:
                current_user.set_password(new)
                current_user.must_change_password = False
                db.session.commit()
                flash("Senha alterada com sucesso.", "success")
                return redirect(url_for("index"))
        return render_template("change_password.html")

    # ---------------- Home ----------------
    @app.route("/")
    @login_required
    def index():
        if current_user.is_team:
            return redirect(url_for("team_hoje"))
        abort(403)

    @app.route("/ajuda")
    @login_required
    def ajuda():
        return render_template("ajuda.html")

    # ---------------- Busca global ----------------
    @app.route("/buscar")
    @login_required
    def buscar():
        q = (request.args.get("q") or "").strip()
        grupos = []
        if len(q) >= 2 and current_user.is_controladoria:
            like = f"%{q}%"
            from team.models import Activity, Project, Indicator, TeamMember
            comps = (Company.query
                     .filter(db.or_(Company.name.ilike(like),
                                    Company.code.ilike(like)))
                     .order_by(Company.name).limit(20).all())
            grupos.append(("Empresas", [
                {"titulo": c.name, "sub": c.code + ("" if c.active else " · inativa"),
                 "url": url_for("admin", tab="org")} for c in comps]))

            acts = (Activity.query.filter(Activity.title.ilike(like))
                    .order_by(Activity.due_date).limit(25).all())
            grupos.append(("Atividades", [
                {"titulo": a.title,
                 "sub": ((a.member.name + " · ") if a.member else "")
                        + (a.due_date.strftime("%d/%m/%Y") if a.due_date else "sem prazo"),
                 "url": url_for("team_atividade", aid=a.id)} for a in acts]))

            projs = (Project.query.filter(Project.name.ilike(like))
                     .order_by(Project.name).limit(15).all())
            grupos.append(("Projetos", [
                {"titulo": p.name, "sub": p.status,
                 "url": url_for("team_projeto", pid=p.id)} for p in projs]))

            inds = (Indicator.query.filter(Indicator.title.ilike(like))
                    .limit(15).all())
            grupos.append(("Indicadores", [
                {"titulo": i.title, "sub": i.member.name if i.member else "",
                 "url": url_for("team_indicador", iid=i.id)} for i in inds]))

            pess = (TeamMember.query.filter(TeamMember.name.ilike(like))
                    .limit(10).all())
            grupos.append(("Pessoas", [
                {"titulo": m.name, "sub": "",
                 "url": url_for("team_atividades", member_id=m.id)} for m in pess]))

        grupos = [(nome, itens) for nome, itens in grupos if itens]
        total = sum(len(itens) for _n, itens in grupos)
        return render_template("busca.html", q=q, grupos=grupos, total=total)

    # ---------------- API (notificacoes) ----------------
    @app.route("/api/notifications")
    @login_required
    def api_notifications():
        limit = int(request.args.get("limit", 20))
        ns = (Notification.query.filter_by(user_id=current_user.id)
              .order_by(Notification.created_at.desc()).limit(limit).all())
        return jsonify(notifications=[{
            "id": n.id, "title": n.title, "message": n.message, "type": n.kind,
            "url": n.url, "is_read": n.is_read,
            "created_at": n.created_at.isoformat()} for n in ns])

    @app.route("/api/notifications/<int:nid>/read", methods=["POST"])
    @login_required
    def api_notif_read(nid):
        n = db.session.get(Notification, nid)
        if n and n.user_id == current_user.id:
            n.is_read = True
            db.session.commit()
        return jsonify(ok=True)

    @app.route("/api/notifications/read-all", methods=["POST"])
    @login_required
    def api_notif_read_all():
        Notification.query.filter_by(user_id=current_user.id, is_read=False).update(
            {"is_read": True})
        db.session.commit()
        return jsonify(ok=True)

    @app.errorhandler(403)
    def forbidden(e):
        return render_template("error.html", code=403,
                               msg="Voce nao tem permissao para acessar esta pagina."), 403

    @app.errorhandler(404)
    def notfound(e):
        return render_template("error.html", code=404,
                               msg="Pagina nao encontrada."), 404

    # helper usado pelas rotas do time/workflow
    def _current_competency():
        return current_competency()

    app._current_competency = _current_competency


app = create_app()

if __name__ == "__main__":
    app.run(debug=True, port=5001)

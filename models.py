# -*- coding: utf-8 -*-
"""Modelos de dados do Portal de Consolidacao."""
import json
from datetime import datetime

from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()


# --------------------------------------------------------------------------
# Usuarios, perfis e empresas
# --------------------------------------------------------------------------
class User(UserMixin, db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(160), unique=True, nullable=False, index=True)
    display_name = db.Column(db.String(160), nullable=False)
    password_hash = db.Column(db.String(255))
    # perfis: 'admin', 'controladoria', 'empresa'
    role = db.Column(db.String(30), nullable=False, default="empresa")
    company_id = db.Column(db.Integer, db.ForeignKey("companies.id"), nullable=True)
    active = db.Column(db.Boolean, default=True)
    must_change_password = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login = db.Column(db.DateTime)

    company = db.relationship("Company", backref="users")

    def set_password(self, pw):
        self.password_hash = generate_password_hash(pw)

    def check_password(self, pw):
        return bool(self.password_hash) and check_password_hash(self.password_hash, pw)

    @property
    def is_admin(self):
        return self.role == "admin"

    @property
    def is_controladoria(self):
        return self.role in ("admin", "controladoria")

    @property
    def is_profissional(self):
        # acessa o modulo do time, mas so enxerga o proprio painel
        return self.role == "profissional"

    @property
    def is_team(self):
        # quem pode ver o modulo de Gestao da Area (com ou sem escopo)
        return self.role in ("admin", "controladoria", "profissional")

    @property
    def is_empresa(self):
        return self.role == "empresa"


class Company(db.Model):
    __tablename__ = "companies"
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(40), unique=True, nullable=False, index=True)
    name = db.Column(db.String(160), nullable=False)
    # rotulo exato usado no template/consolidacao (para matching de contraparte)
    canonical_label = db.Column(db.String(160))
    active = db.Column(db.Boolean, default=True)
    # tolerancia especifica (override); None => usa default por porte
    tol_rel = db.Column(db.Float)   # relativa (fracao)
    tol_abs = db.Column(db.Float)   # piso absoluto R$
    # ROL LTM aproximada (base da tolerancia relativa) - alimentada pelo aceite
    rol_ltm = db.Column(db.Float, default=0.0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


# --------------------------------------------------------------------------
# Competencias (meses de fechamento) e calendario
# --------------------------------------------------------------------------
class Competency(db.Model):
    __tablename__ = "competencies"
    id = db.Column(db.Integer, primary_key=True)
    year = db.Column(db.Integer, nullable=False)
    month = db.Column(db.Integer, nullable=False)   # 1..12
    deadline = db.Column(db.Date)                    # 5o dia util
    status = db.Column(db.String(20), default="aberta")  # aberta | fechada
    closed_at = db.Column(db.DateTime)
    __table_args__ = (db.UniqueConstraint("year", "month", name="uq_comp_ym"),)

    @property
    def label(self):
        meses = ["", "Jan", "Fev", "Mar", "Abr", "Mai", "Jun",
                 "Jul", "Ago", "Set", "Out", "Nov", "Dez"]
        return f"{meses[self.month]}/{self.year}"

    @property
    def key(self):
        return f"{self.year:04d}-{self.month:02d}"


def current_competency():
    """A competência que a ferramenta considera 'a atual' (o mês vigente).

    Definida explicitamente pelo gestor em Administração › Sistema
    (setting 'competencia_atual_id'). Sem definição, cai na aberta mais
    recente e, por fim, na mais recente cadastrada.
    """
    cid = get_setting("competencia_atual_id")
    if cid:
        try:
            c = Competency.query.get(int(cid))
            if c:
                return c
        except (ValueError, TypeError):
            pass
    return (Competency.query.filter_by(status="aberta")
            .order_by(Competency.year.desc(), Competency.month.desc()).first()
            or Competency.query.order_by(Competency.year.desc(),
                                         Competency.month.desc()).first())


# --------------------------------------------------------------------------
# Submissoes (upload versionado) e resultados de validacao
# --------------------------------------------------------------------------
class Submission(db.Model):
    __tablename__ = "submissions"
    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("companies.id"), nullable=False)
    competency_id = db.Column(db.Integer, db.ForeignKey("competencies.id"), nullable=False)
    version = db.Column(db.Integer, nullable=False, default=1)
    filename = db.Column(db.String(255))
    stored_path = db.Column(db.String(400))
    file_hash = db.Column(db.String(64))
    vba_hash = db.Column(db.String(64))
    # status: 'validando','aprovado','reprovado','aprovado_com_excecao'
    status = db.Column(db.String(30), default="validando")
    n_errors = db.Column(db.Integer, default=0)
    n_warnings = db.Column(db.Integer, default=0)
    result_json = db.Column(db.Text)                # relatorio completo
    # snapshot dos meses realizados (para deteccao de restatement)
    realized_snapshot = db.Column(db.Text)
    submitted_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    submitted_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_current = db.Column(db.Boolean, default=True)

    company = db.relationship("Company")
    competency = db.relationship("Competency")

    def result(self):
        try:
            return json.loads(self.result_json) if self.result_json else {}
        except Exception:
            return {}


class ValidationException(db.Model):
    """Justificativa/excecao quando a validacao reprova mas o numero esta correto."""
    __tablename__ = "exceptions"
    id = db.Column(db.Integer, primary_key=True)
    submission_id = db.Column(db.Integer, db.ForeignKey("submissions.id"), nullable=False)
    rule_code = db.Column(db.String(60))
    category = db.Column(db.String(40))    # arredondamento|transito_ic|nao_recorrente|outro
    justification = db.Column(db.Text)
    requested_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    requested_at = db.Column(db.DateTime, default=datetime.utcnow)
    status = db.Column(db.String(20), default="pendente")  # pendente|aprovada|recusada
    decided_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    decided_at = db.Column(db.DateTime)
    decision_note = db.Column(db.Text)

    submission = db.relationship("Submission", backref="exceptions")


# --------------------------------------------------------------------------
# Conta corrente intercompany (declaracoes bilaterais)
# --------------------------------------------------------------------------
class IntercompanyDeclaration(db.Model):
    __tablename__ = "ic_declarations"
    id = db.Column(db.Integer, primary_key=True)
    competency_id = db.Column(db.Integer, db.ForeignKey("competencies.id"), nullable=False)
    reporter_id = db.Column(db.Integer, db.ForeignKey("companies.id"), nullable=False)
    counterparty_id = db.Column(db.Integer, db.ForeignKey("companies.id"), nullable=False)
    # 'saldo' (posicao fim do mes) ou 'fluxo' (movimento no mes) ou 'juros'
    kind = db.Column(db.String(20), default="saldo")
    # convencao: valor positivo = a reportar TEM A RECEBER da contraparte;
    #            valor negativo = a reportar DEVE / enviou recursos a contraparte
    amount = db.Column(db.Float, default=0.0)
    note = db.Column(db.String(255))
    reported_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    reported_at = db.Column(db.DateTime, default=datetime.utcnow)

    competency = db.relationship("Competency")
    reporter = db.relationship("Company", foreign_keys=[reporter_id])
    counterparty = db.relationship("Company", foreign_keys=[counterparty_id])


class IntercompanyPendency(db.Model):
    """Divergencia bilateral apurada pela controladoria."""
    __tablename__ = "ic_pendencies"
    id = db.Column(db.Integer, primary_key=True)
    competency_id = db.Column(db.Integer, db.ForeignKey("competencies.id"), nullable=False)
    company_a_id = db.Column(db.Integer, db.ForeignKey("companies.id"), nullable=False)
    company_b_id = db.Column(db.Integer, db.ForeignKey("companies.id"), nullable=False)
    kind = db.Column(db.String(20), default="saldo")
    amount_a = db.Column(db.Float, default=0.0)   # o que A declara sobre B
    amount_b = db.Column(db.Float, default=0.0)   # o que B declara sobre A
    diff = db.Column(db.Float, default=0.0)
    status = db.Column(db.String(20), default="aberta")  # aberta|em_transito|cobrada|resolvida
    note = db.Column(db.String(255))
    updated_at = db.Column(db.DateTime, default=datetime.utcnow)

    competency = db.relationship("Competency")
    company_a = db.relationship("Company", foreign_keys=[company_a_id])
    company_b = db.relationship("Company", foreign_keys=[company_b_id])


# --------------------------------------------------------------------------
# Notificacoes e auditoria
# --------------------------------------------------------------------------
class Notification(db.Model):
    __tablename__ = "notifications"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    title = db.Column(db.String(160))
    message = db.Column(db.String(400))
    kind = db.Column(db.String(40))
    url = db.Column(db.String(200))
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Setting(db.Model):
    """Configuracoes globais (chave-valor) do fechamento."""
    __tablename__ = "settings"
    key = db.Column(db.String(60), primary_key=True)
    value = db.Column(db.String(400))


def get_setting(key, default=None):
    s = db.session.get(Setting, key)
    return s.value if s else default


def set_setting(key, value):
    s = db.session.get(Setting, key)
    if s:
        s.value = str(value)
    else:
        db.session.add(Setting(key=key, value=str(value)))
    db.session.commit()


class AuditLog(db.Model):
    __tablename__ = "audit_log"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    action = db.Column(db.String(80))
    entity = db.Column(db.String(80))
    detail = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


def log_audit(user_id, action, entity="", detail=""):
    try:
        db.session.add(AuditLog(user_id=user_id, action=action, entity=entity, detail=detail))
        db.session.commit()
    except Exception:
        db.session.rollback()


def notify(user_id, title, message, kind="info", url=None):
    db.session.add(Notification(user_id=user_id, title=title, message=message,
                                kind=kind, url=url))
    db.session.commit()

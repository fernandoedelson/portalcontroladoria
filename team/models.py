# -*- coding: utf-8 -*-
"""Modelos do modulo de Gestao do Time (compartilha o db do Portal de Consolidacao)."""
import fuso
import json
from datetime import datetime, date

from models import db


# --------------------------------------------------------------------------
# Time da controladoria
# --------------------------------------------------------------------------
class TeamMember(db.Model):
    """Pessoa do time da controladoria (executor de atividades).

    Desacoplado de User: nem todo membro tem login definido ainda (3 pessoas
    a definir). member.user_id liga ao login quando existir.
    """
    __tablename__ = "team_members"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(160), nullable=False)
    # 'gestor' | 'senior' | 'pleno' | 'especialista'
    role_label = db.Column(db.String(30), default="especialista")
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    # membros cujo painel "sobe" para outro (ex.: 3 novos seguem o painel do senior)
    follows_member_id = db.Column(db.Integer, db.ForeignKey("team_members.id"), nullable=True)
    # painel que o membro segue (substitui follows; ver team.models_workflow.Panel)
    panel_id = db.Column(db.Integer, db.ForeignKey("panels.id"), nullable=True)
    is_manager = db.Column(db.Boolean, default=False)   # pilota os paineis
    color = db.Column(db.String(9), default="#1d5da8")
    whatsapp = db.Column(db.String(30))   # E.164 (+55DDD...) p/ alertas via Twilio
    active = db.Column(db.Boolean, default=True)
    sort_order = db.Column(db.Integer, default=100)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User", backref="team_member", foreign_keys=[user_id])
    follows = db.relationship("TeamMember", remote_side=[id],
                              foreign_keys=[follows_member_id], backref="followers")
    panel = db.relationship("Panel", foreign_keys=[panel_id])

    @property
    def role_pt(self):
        return {"gestor": "Gestor", "senior": "Analista Sênior",
                "pleno": "Analista Pleno", "especialista": "Especialista"}.get(
            self.role_label, self.role_label)

    @property
    def initials(self):
        parts = [p for p in (self.name or "").split() if p]
        if not parts:
            return "?"
        if len(parts) == 1:
            return parts[0][:2].upper()
        return (parts[0][0] + parts[-1][0]).upper()


# --------------------------------------------------------------------------
# Carteira: alocacao empresa x pessoa x entregas (o "pilotar paineis")
# --------------------------------------------------------------------------
class Cluster(db.Model):
    """Agrupamento de entidades para atribuir pessoas em bloco.

    Quantidade e composicao sao livres (tela Administracao > Entidades).
    O responsavel do cluster vale para todas as entidades dele, exceto as
    marcadas como excecao (responsavel escolhido a mao na linha da entidade).
    """
    __tablename__ = "clusters"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    sort_order = db.Column(db.Integer, default=0)
    member_id = db.Column(db.Integer, db.ForeignKey("team_members.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    member = db.relationship("TeamMember")


class CompanyAssignment(db.Model):
    """Quem cuida de qual empresa e quais entregas ela exige.

    seat = numero do 'Funcionario' na planilha de origem (1..6). O vinculo
    seat -> membro e configuravel na tela de Carteira (mapeamento nao definido).
    """
    __tablename__ = "company_assignments"
    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("companies.id"), nullable=False)
    member_id = db.Column(db.Integer, db.ForeignKey("team_members.id"), nullable=True)
    seat = db.Column(db.Integer)                 # 'Funcionario' de origem (1..6)
    segment = db.Column(db.String(60))           # Energia, Agro, Financeiro...
    flow = db.Column(db.String(30))              # Proprio | Terceiro | Verificar
    responsibility = db.Column(db.String(30))    # J&F | Terceiro
    # entregas exigidas (Painel, Endividamento, Consolidacao, Auxiliares)
    deliverables_json = db.Column(db.Text, default="[]")
    load_real = db.Column(db.Integer, default=0)     # 'Suportes'
    load_ideal = db.Column(db.Integer, default=4)    # 'Suporte Ideal'
    production = db.Column(db.Integer, default=0)     # 'Producao'
    note = db.Column(db.String(255))
    cluster_id = db.Column(db.Integer, db.ForeignKey("clusters.id"), nullable=True)
    member_excecao = db.Column(db.Boolean, default=False)   # responsavel fora do cluster
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    company = db.relationship("Company")
    cluster = db.relationship("Cluster", backref="assignments")

    def segue_cluster(self):
        """Responsavel = o do cluster (quando o cluster tem um e a linha nao e excecao)."""
        if self.cluster and self.cluster.member_id and not self.member_excecao:
            self.member_id = self.cluster.member_id
    member = db.relationship("TeamMember", backref="assignments")

    @property
    def deliverables(self):
        try:
            return json.loads(self.deliverables_json or "[]")
        except Exception:
            return []

    @deliverables.setter
    def deliverables(self, value):
        self.deliverables_json = json.dumps(list(value or []), ensure_ascii=False)


# --------------------------------------------------------------------------
# Projetos e marcos
# --------------------------------------------------------------------------
class Project(db.Model):
    __tablename__ = "projects"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(160), nullable=False)
    code = db.Column(db.String(40))
    # 'planejado' | 'ativo' | 'pausado' | 'concluido' | 'cancelado'
    status = db.Column(db.String(20), default="ativo")
    owner_member_id = db.Column(db.Integer, db.ForeignKey("team_members.id"), nullable=True)
    # gestor do projeto (quem aprova/superviona); pode diferir do líder (owner)
    manager_member_id = db.Column(db.Integer, db.ForeignKey("team_members.id"), nullable=True)
    description = db.Column(db.Text)
    start_date = db.Column(db.Date)
    target_date = db.Column(db.Date)
    color = db.Column(db.String(9), default="#1d5da8")    # azul da paleta
    confidential = db.Column(db.Boolean, default=False)   # nome nao vai a canais externos
    sort_order = db.Column(db.Integer, default=100)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    owner = db.relationship("TeamMember", foreign_keys=[owner_member_id])
    manager = db.relationship("TeamMember", foreign_keys=[manager_member_id])

    @property
    def status_pt(self):
        return {"planejado": "Planejado", "ativo": "Ativo", "pausado": "Pausado",
                "concluido": "Concluído", "cancelado": "Cancelado"}.get(
            self.status, self.status)


class Milestone(db.Model):
    __tablename__ = "milestones"
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    due_date = db.Column(db.Date)
    done = db.Column(db.Boolean, default=False)
    done_at = db.Column(db.DateTime)
    sort_order = db.Column(db.Integer, default=100)

    project = db.relationship("Project", backref=db.backref(
        "milestones", cascade="all, delete-orphan", order_by="Milestone.sort_order"))


# --------------------------------------------------------------------------
# Atividade: o motor unico
# --------------------------------------------------------------------------
KINDS = ("fechamento", "recorrente", "transversal", "spot", "projeto", "indicador")
STATUSES = ("pendente", "em_andamento", "concluida", "bloqueada", "cancelada")
PRIORITIES = ("baixa", "media", "alta", "critica")


class Activity(db.Model):
    __tablename__ = "activities"
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(240), nullable=False)
    description = db.Column(db.Text)
    kind = db.Column(db.String(20), default="spot", index=True)
    origin = db.Column(db.String(20), default="manual")   # manual | template | indicador

    member_id = db.Column(db.Integer, db.ForeignKey("team_members.id"), nullable=True, index=True)
    company_id = db.Column(db.Integer, db.ForeignKey("companies.id"), nullable=True)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=True)
    competency_id = db.Column(db.Integer, db.ForeignKey("competencies.id"), nullable=True, index=True)
    template_id = db.Column(db.Integer, db.ForeignKey("closing_template_items.id"), nullable=True)

    status = db.Column(db.String(20), default="pendente", index=True)
    priority = db.Column(db.String(10), default="media")

    start_date = db.Column(db.Date)
    due_date = db.Column(db.Date, index=True)
    due_provisional = db.Column(db.Boolean, default=False)   # prazo ainda depende de insumo
    # regra de prazo (para atividades geradas por template)
    due_rule_json = db.Column(db.Text)

    done_at = db.Column(db.DateTime)
    done_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    auto_metric = db.Column(db.Boolean, default=False)   # concluir/on-time derivado do fechamento

    # "Combinada nova data": o prazo original CONTINUA valendo (a atividade segue
    # atrasada no app), mas as cobranças por e-mail/WhatsApp/push param até a data
    # combinada. Serve para dependência de terceiro sem encher a caixa de entrada.
    nova_data = db.Column(db.Date)
    nova_data_motivo = db.Column(db.String(240))
    nova_data_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    nova_data_at = db.Column(db.DateTime)

    sort_order = db.Column(db.Integer, default=100)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    member = db.relationship("TeamMember", backref="activities")
    company = db.relationship("Company")
    project = db.relationship("Project", backref="activities")
    competency = db.relationship("Competency")

    @property
    def due_rule(self):
        try:
            return json.loads(self.due_rule_json) if self.due_rule_json else {}
        except Exception:
            return {}

    @due_rule.setter
    def due_rule(self, value):
        self.due_rule_json = json.dumps(value or {}, ensure_ascii=False)

    @property
    def is_done(self):
        return self.status == "concluida" or self.done_at is not None

    @property
    def is_open(self):
        return self.status in ("pendente", "em_andamento", "bloqueada")

    def is_overdue(self, ref=None):
        ref = ref or fuso.hoje()
        return bool(self.due_date and self.is_open and not self.due_provisional
                    and self.due_date < ref)

    def is_due_today(self, ref=None):
        ref = ref or fuso.hoje()
        return bool(self.due_date and self.is_open and self.due_date == ref)

    def effective_status(self, ref=None):
        """Status para exibicao: cruza status manual com o vencimento."""
        if self.status in ("concluida", "cancelada", "bloqueada"):
            return self.status
        if self.due_provisional:
            return "aguardando"
        if self.is_overdue(ref):
            return "atrasada"
        if self.is_due_today(ref):
            return "vence_hoje"
        return self.status  # pendente | em_andamento

    def cobranca_pausada(self, ref=None):
        """Tem nova data combinada ainda no futuro? Então não cobra por fora do app."""
        if not self.nova_data or self.status in ("concluida", "cancelada"):
            return False
        return self.nova_data >= (ref or fuso.hoje())

    def nova_data_cumprida(self):
        """None = ainda aberta; True/False = concluída dentro ou fora da nova data."""
        if not self.nova_data or self.status != "concluida":
            return None
        fim = (fuso.local(self.done_at).date() if self.done_at else fuso.hoje())
        return fim <= self.nova_data

    def days_to_due(self, ref=None):
        ref = ref or fuso.hoje()
        return (self.due_date - ref).days if self.due_date else None

    def kind_pt(self):
        return {"fechamento": "Fechamento", "recorrente": "Recorrente",
                "transversal": "Transversal", "spot": "Spot",
                "projeto": "Projeto", "indicador": "Indicador"}.get(self.kind, self.kind)


# --------------------------------------------------------------------------
# Agenda de fechamento: template que gera as atividades do mes
# --------------------------------------------------------------------------
class ClosingTemplateItem(db.Model):
    __tablename__ = "closing_template_items"
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(240), nullable=False)
    kind = db.Column(db.String(20), default="fechamento")
    member_id = db.Column(db.Integer, db.ForeignKey("team_members.id"), nullable=True)
    company_id = db.Column(db.Integer, db.ForeignKey("companies.id"), nullable=True)
    # empresas específicas (uma ou várias): lista de ids em JSON. Itens antigos
    # guardavam uma só em company_id — a propriedade company_ids cobre os dois.
    company_ids_json = db.Column(db.Text)
    deliverable = db.Column(db.String(60))   # Painel, Consolidacao, Custos, Release...
    priority = db.Column(db.String(10), default="media")
    # True: replica a atividade para cada empresa da carteira (responsavel = o da empresa).
    # False: uma unica atividade geral, com member_id fixo abaixo.
    per_company = db.Column(db.Boolean, default=False)
    # macro-atividade ligada a um selo de entrega das Entidades (Painel,
    # Endividamento, Consolidação): gera uma atividade por entidade com o selo,
    # para o responsável dela. Não tem escopo nem responsável próprios.
    macro = db.Column(db.String(30))

    # base do prazo: 'deadline' (5o DU), 'insumo' (chegada das ancoras), 'fixed_bd'
    due_base = db.Column(db.String(20), default="deadline")
    due_offset = db.Column(db.Integer, default=0)   # dias uteis (D+2 => 2; D-2 => -2)
    # empresas cuja chegada dispara o relogio (para due_base='insumo')
    insumo_codes_json = db.Column(db.Text, default="[]")
    auto_metric = db.Column(db.Boolean, default=False)

    active = db.Column(db.Boolean, default=True)
    sort_order = db.Column(db.Integer, default=100)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    member = db.relationship("TeamMember")
    company = db.relationship("Company")

    @property
    def company_ids(self):
        try:
            ids = [int(x) for x in json.loads(self.company_ids_json or "[]")]
        except Exception:
            ids = []
        if not ids and self.company_id:
            ids = [self.company_id]
        return ids

    @company_ids.setter
    def company_ids(self, value):
        ids = []
        for v in value or []:
            try:
                v = int(v)
            except (TypeError, ValueError):
                continue
            if v not in ids:
                ids.append(v)
        self.company_ids_json = json.dumps(ids) if ids else None
        self.company_id = ids[0] if len(ids) == 1 else None

    @property
    def escopo_label(self):
        """Texto do escopo: 'Todas as empresas', nomes das empresas ou '—'."""
        if self.per_company:
            return "Todas as empresas"
        ids = self.company_ids
        if not ids:
            return "—"
        from models import Company
        nomes = [c.name for c in Company.query.filter(Company.id.in_(ids)).order_by(Company.name)]
        return ", ".join(nomes) if len(nomes) <= 3 else f"{len(nomes)} empresas: " + ", ".join(nomes[:3]) + "…"

    @property
    def escopo(self):
        """'todas' | 'empresas' | 'geral' — como a tela mostra o item."""
        if self.per_company:
            return "todas"
        return "empresas" if self.company_ids else "geral"

    @property
    def insumo_codes(self):
        try:
            return json.loads(self.insumo_codes_json or "[]")
        except Exception:
            return []

    @insumo_codes.setter
    def insumo_codes(self, value):
        self.insumo_codes_json = json.dumps(list(value or []), ensure_ascii=False)

    @property
    def du_efetivo(self):
        """Nº do dia útil do mês seguinte em que a atividade vence."""
        return self.due_offset if self.due_offset is not None else 5

    @property
    def due_label(self):
        if self.due_base == "fixed_bd":
            return f"{self.du_efetivo}º DU"
        base = {"deadline": "5º DU", "insumo": "insumo"}.get(self.due_base, self.due_base)
        if self.due_offset == 0:
            return base
        sign = "+" if self.due_offset > 0 else "−"
        return f"{base} {sign}{abs(self.due_offset)} DU"


# --------------------------------------------------------------------------
# Indicadores / Metas
# --------------------------------------------------------------------------
class IndicatorDef(db.Model):
    """Catálogo de indicadores — a DEFINIÇÃO reutilizável.

    Um mesmo indicador do catálogo pode ser usado em vários painéis, cada um
    com a sua própria meta (ver Indicator.target_label). A definição guarda o
    que não muda entre painéis: nome, dimensão e como avaliar.
    """
    __tablename__ = "indicator_defs"
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(240), nullable=False)
    dimension = db.Column(db.String(40), default="RESULTADO")
    # como avaliar: 'prazo_du' | 'contagem' | 'data_marco' | 'projeto' | 'manual'
    target_type = db.Column(db.String(20), default="manual")
    unit = db.Column(db.String(30))                    # ex.: "dias úteis", "%", "un"
    rational = db.Column(db.Text)                      # racional padrão (herdável)
    auto_source = db.Column(db.String(60))
    active = db.Column(db.Boolean, default=True)
    sort_order = db.Column(db.Integer, default=100)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    @property
    def target_type_pt(self):
        return {"prazo_du": "Prazo (dia útil)", "contagem": "Contagem",
                "data_marco": "Data / marco", "projeto": "Projeto",
                "manual": "Manual"}.get(self.target_type, self.target_type)

    @property
    def usos(self):
        return Indicator.query.filter_by(indicator_def_id=self.id).count()


class Indicator(db.Model):
    __tablename__ = "indicators"
    id = db.Column(db.Integer, primary_key=True)
    # ligação ao catálogo (a definição reutilizável); a meta é por painel, aqui
    indicator_def_id = db.Column(db.Integer, db.ForeignKey("indicator_defs.id"),
                                 nullable=True, index=True)
    member_id = db.Column(db.Integer, db.ForeignKey("team_members.id"), nullable=True)
    panel_id = db.Column(db.Integer, db.ForeignKey("panels.id"), nullable=True, index=True)
    seq = db.Column(db.Integer)                # numero da meta (1..N)
    title = db.Column(db.String(240), nullable=False)
    dimension = db.Column(db.String(40), default="RESULTADO")
    target_label = db.Column(db.String(60))    # "D+2", "5", "OUT/26"
    weight = db.Column(db.Float)               # peso (%) do indicador NESTE painel
    # do formulário de contratação de metas: unidade, sentido e escala
    unit = db.Column(db.String(30))            # "Dia útil", "Mês", "%", "Prazo"
    direction = db.Column(db.String(10))       # 'menor' (menor melhor) | 'maior'
    scale_min = db.Column(db.String(30))       # mínimo aceitável (80%)
    scale_obj = db.Column(db.String(30))       # objetivo (100%)
    scale_sup = db.Column(db.String(30))       # superado (>=120%)
    # como avaliar: 'prazo_du' (D+x), 'contagem', 'data_marco', 'projeto', 'manual'
    target_type = db.Column(db.String(20), default="manual")
    rational = db.Column(db.Text)
    # fonte automatica opcional: chave que o motor sabe calcular
    auto_source = db.Column(db.String(60))
    year = db.Column(db.Integer, default=2026)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=True)
    active = db.Column(db.Boolean, default=True)
    sort_order = db.Column(db.Integer, default=100)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    member = db.relationship("TeamMember", backref="indicators")
    project = db.relationship("Project")
    panel = db.relationship("Panel")
    definition = db.relationship("IndicatorDef")


    @property
    def escala_curta(self):
        """['NOV','OUT','SET'] — tira o /26 quando os três valores são do mesmo
        ano (fica mais limpo). Se a escala cruza anos, mantém o /26 e o /27."""
        vals = [self.scale_min, self.scale_obj, self.scale_sup]
        anos = {v.rsplit("/", 1)[1] for v in vals if v and "/" in v}
        if len(anos) == 1 and all((v is None) or ("/" in v) for v in vals):
            vals = [(v.rsplit("/", 1)[0] if v else v) for v in vals]
        return [v or "—" for v in vals]


class IndicatorResult(db.Model):
    __tablename__ = "indicator_results"
    id = db.Column(db.Integer, primary_key=True)
    indicator_id = db.Column(db.Integer, db.ForeignKey("indicators.id"), nullable=False)
    competency_id = db.Column(db.Integer, db.ForeignKey("competencies.id"), nullable=True)
    period_label = db.Column(db.String(20))     # "Jun/2026" ou "2026"
    # 'superado' | 'atingido' | 'parcial' | 'nao_atingido' | 'na'
    outcome = db.Column(db.String(20), default="na")
    value_label = db.Column(db.String(60))      # ex.: "D+2 (entregue em D+1)"
    computed = db.Column(db.Boolean, default=False)   # True = derivado do motor
    # evidencia (anexo simples, com quem/quando gravados)
    evidence_path = db.Column(db.String(400))
    evidence_name = db.Column(db.String(255))
    evidence_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    evidence_at = db.Column(db.DateTime)
    note = db.Column(db.Text)
    recorded_at = db.Column(db.DateTime, default=datetime.utcnow)
    recorded_by = db.Column(db.Integer, db.ForeignKey("users.id"))

    indicator = db.relationship("Indicator", backref=db.backref(
        "results", cascade="all, delete-orphan"))
    competency = db.relationship("Competency")


# --------------------------------------------------------------------------
# Alertas: matriz evento x canal + log de envios
# --------------------------------------------------------------------------
# eventos suportados pelo motor de alertas
ALERT_EVENTS = [
    ("lembrete_previo", "Lembrete antes do prazo"),
    ("vence_hoje", "Atividade vence hoje"),
    ("atraso", "Atraso — escalada ao gestor"),
    ("resumo_diario", "Resumo diário do time (manhã)"),
    ("indicador_sem_evidencia", "Indicador sem evidência no fechamento"),
]


class AlertChannelSetting(db.Model):
    """Uma linha por evento: quais canais estao ligados (matriz evento x canal)."""
    __tablename__ = "alert_channel_settings"
    event_key = db.Column(db.String(40), primary_key=True)
    email = db.Column(db.Boolean, default=False)
    whatsapp = db.Column(db.Boolean, default=False)
    painel = db.Column(db.Boolean, default=True)
    push = db.Column(db.Boolean, default=True)      # notificacao no celular/computador
    escalate_manager = db.Column(db.Boolean, default=False)
    lead_days = db.Column(db.Integer, default=1)   # p/ lembrete_previo: dias uteis antes

    @property
    def label(self):
        return dict(ALERT_EVENTS).get(self.event_key, self.event_key)


class AlertLog(db.Model):
    __tablename__ = "alert_log"
    id = db.Column(db.Integer, primary_key=True)
    event_key = db.Column(db.String(40), index=True)
    channel = db.Column(db.String(20))          # email | whatsapp | painel | push
    member_id = db.Column(db.Integer, db.ForeignKey("team_members.id"), nullable=True)
    activity_id = db.Column(db.Integer, db.ForeignKey("activities.id"), nullable=True)
    dedup_key = db.Column(db.String(120), index=True)   # idempotencia por dia/evento/alvo
    subject = db.Column(db.String(200))
    body = db.Column(db.Text)
    status = db.Column(db.String(20), default="enviado")   # enviado | falha | simulado
    error = db.Column(db.String(300))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


# --------------------------------------------------------------------------
# Helpers de bootstrap
# --------------------------------------------------------------------------
def ensure_alert_defaults():
    """Garante uma linha por evento na matriz (idempotente)."""
    defaults = {
        "lembrete_previo": dict(email=True, whatsapp=False, painel=True,
                                escalate_manager=False, lead_days=1),
        "vence_hoje": dict(email=True, whatsapp=True, painel=True,
                           escalate_manager=False, lead_days=0),
        "atraso": dict(email=True, whatsapp=True, painel=True,
                       escalate_manager=True, lead_days=0),
        "resumo_diario": dict(email=True, whatsapp=False, painel=False, lead_days=0),
        "indicador_sem_evidencia": dict(email=True, whatsapp=False, painel=True,
                                        lead_days=0),
    }
    changed = False
    for key, _ in ALERT_EVENTS:
        row = db.session.get(AlertChannelSetting, key)
        if not row:
            d = defaults.get(key, {})
            db.session.add(AlertChannelSetting(event_key=key, **d))
            changed = True
    if changed:
        db.session.commit()


# Divisao inicial combinada com o gestor (21/09/2026). Depois disso e livre na tela.
CLUSTERS_INICIAIS = [
    ("Energia", ["AMBAR_GER", "AMBAR_DIS", "FLUXUS", "GLOBE", "GREENCARGO", "LOGAS", "MGAS"]),
    ("Financeiro e Tecnologia", ["ORIGINAL", "ORIGINAL_CORRETORA", "PICPAY", "KOVR",
                                 "J_F_PARTICIPACOES", "INOVTI", "LIONX", "PHIZ"]),
    ("Holdings e Investimentos", ["J_F_CAYMAN", "VLMB", "VNMB", "VVMB", "JJMB", "WWMB", "ZMF",
                                  "J_F_INTERNATIONAL", "LAGUZ", "J_F_LUX", "FAZENDA_LUTA",
                                  "MAIS55", "JF_URB", "MUNDO_NOVO"]),
    ("Mineração, Bens de Consumo, Serviços e Diversos",
     ["LHG", "FLORA", "AERONAVE", "SAK_SEGURANCA", "SAK_SERVICOS", "CANAL", "ARAGUAIA",
      "ARROSSENSAL", "VB_AGRO", "INSTITUTO_J_F"]),
    ("Grandes Operações", ["JBS", "JF_SA", "ELDORADO"]),
]


def ensure_clusters_defaults():
    """Semeia os clusters uma unica vez (se o gestor apagar tudo, nao volta)."""
    from models import Company, get_setting, set_setting
    if get_setting("clusters_semeados") == "1" or Cluster.query.count():
        return 0
    por_codigo = {c.code: c.id for c in Company.query.all()}
    linhas = {a.company_id: a for a in CompanyAssignment.query.all()}
    n = 0
    for i, (nome, codigos) in enumerate(CLUSTERS_INICIAIS, start=1):
        cl = Cluster(name=nome, sort_order=i)
        db.session.add(cl)
        db.session.flush()
        for cod in codigos:
            a = linhas.get(por_codigo.get(cod))
            if a and a.cluster_id is None:
                a.cluster_id = cl.id
                n += 1
    db.session.commit()
    set_setting("clusters_semeados", "1")
    return n


# selo da entidade -> título da macro-atividade no cronograma
MACROS = [("Painel", "Painel"), ("Endividamento", "Fluxo de Endividamento"),
          ("Consolidação", "Consolidação")]


def ensure_macros():
    """Garante as três macro-atividades no cronograma (idempotente)."""
    existentes = {it.macro for it in ClosingTemplateItem.query.filter(
        ClosingTemplateItem.macro.isnot(None)).all()}
    n = 0
    for i, (selo, titulo) in enumerate(MACROS, start=1):
        if selo in existentes:
            continue
        db.session.add(ClosingTemplateItem(
            title=titulo, macro=selo, kind="fechamento", priority="alta",
            due_base="fixed_bd", due_offset=5, active=True, sort_order=i))
        n += 1
    if n:
        db.session.commit()
    return n


def normaliza_prazos_cronograma():
    """Todo item passa a valer como 'Nº dia útil do mês seguinte' (uma vez).

    Antes havia três bases (5º DU ±n, insumo ±n, DU fixo) e a coluna do
    cronograma mostrava o ±n, o que confundia. Agora o número da coluna É o
    dia útil. Itens com número menor que 1 (ex.: '5º DU −1') viram o dia útil
    correspondente (4).
    """
    n = 0
    for it in ClosingTemplateItem.query.filter(
            db.or_(ClosingTemplateItem.due_base != "fixed_bd",
                   ClosingTemplateItem.due_offset < 1)).all():
        off = it.due_offset if it.due_offset is not None else 5
        if off < 1:                       # '5º DU −1' -> 4º DU
            off = max(5 + off, 1)
        it.due_base, it.due_offset = "fixed_bd", off
        n += 1
    if n:
        db.session.commit()
    return n

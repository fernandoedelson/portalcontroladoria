# -*- coding: utf-8 -*-
"""Modelos que tiram atrito do dia a dia do time.

Notas e anexos na atividade (o trabalho passa a ser discutido no portal),
ausencias (o mapa de capacidade so e honesto se souber quem esta fora) e a
serie historica do fechamento (o que transforma foto em argumento).
"""
import fuso
import json
from datetime import datetime, date

from models import db


class ActivityNote(db.Model):
    """Andamento da atividade: comentario livre ou registro de bloqueio."""
    __tablename__ = "activity_notes"
    id = db.Column(db.Integer, primary_key=True)
    activity_id = db.Column(db.Integer, db.ForeignKey("activities.id"),
                            nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    # 'comentario' | 'bloqueio' | 'desbloqueio' | 'sistema'
    kind = db.Column(db.String(20), default="comentario")
    body = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    activity = db.relationship("Activity", backref=db.backref(
        "notes", cascade="all, delete-orphan",
        order_by="ActivityNote.created_at.desc()"))
    user = db.relationship("User")

    @property
    def icone(self):
        """Nome do ícone (icons.py) do tipo de nota."""
        return {"bloqueio": "block", "desbloqueio": "ok-circle",
                "sistema": "settings"}.get(self.kind, "message")


class ActivityFile(db.Model):
    """Anexo da atividade — a evidencia mora junto da tarefa."""
    __tablename__ = "activity_files"
    id = db.Column(db.Integer, primary_key=True)
    activity_id = db.Column(db.Integer, db.ForeignKey("activities.id"),
                            nullable=False, index=True)
    filename = db.Column(db.String(255), nullable=False)
    stored_path = db.Column(db.String(400), nullable=False)
    size_bytes = db.Column(db.Integer, default=0)
    uploaded_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)

    activity = db.relationship("Activity", backref=db.backref(
        "files", cascade="all, delete-orphan"))
    user = db.relationship("User")

    @property
    def size_label(self):
        b = self.size_bytes or 0
        if b < 1024:
            return f"{b} B"
        if b < 1024 * 1024:
            return f"{b / 1024:.0f} KB"
        return f"{b / 1024 / 1024:.1f} MB"


class Absence(db.Model):
    """Ferias/ausencia — o mapa de capacidade precisa saber quem esta fora."""
    __tablename__ = "absences"
    id = db.Column(db.Integer, primary_key=True)
    member_id = db.Column(db.Integer, db.ForeignKey("team_members.id"),
                          nullable=False, index=True)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    # 'ferias' | 'licenca' | 'treinamento' | 'outro'
    kind = db.Column(db.String(20), default="ferias")
    note = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    # fluxo de aprovação: 'solicitada' | 'aprovada' | 'recusada'
    status = db.Column(db.String(15), default="aprovada", index=True)
    requested_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    decided_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    decided_at = db.Column(db.DateTime)
    decision_note = db.Column(db.String(255))
    # confirmação depois: a pessoa realmente saiu? e voltou quando?
    saida_confirmada = db.Column(db.Boolean, default=False)
    real_return = db.Column(db.Date)
    # delegação durante a ausência (usada sobretudo quando um gestor sai):
    # quem APROVA e quem toca o ANDAMENTO da área no lugar da pessoa
    aprova_delegado_id = db.Column(db.Integer, db.ForeignKey("team_members.id"))
    andamento_delegado_id = db.Column(db.Integer, db.ForeignKey("team_members.id"))

    member = db.relationship("TeamMember", foreign_keys=[member_id], backref=db.backref(
        "absences", cascade="all, delete-orphan"))
    aprova_delegado = db.relationship("TeamMember", foreign_keys=[aprova_delegado_id])
    andamento_delegado = db.relationship("TeamMember", foreign_keys=[andamento_delegado_id])

    KINDS = [("ferias", "Férias"), ("licenca", "Licença"),
             ("treinamento", "Treinamento"), ("outro", "Outro")]
    STATUS_PT = {"solicitada": "Solicitada", "aprovada": "Aprovada",
                 "recusada": "Recusada"}

    @property
    def kind_pt(self):
        return dict(self.KINDS).get(self.kind, self.kind)

    @property
    def status_pt(self):
        return self.STATUS_PT.get(self.status, self.status)

    @property
    def dias(self):
        return (self.end_date - self.start_date).days + 1

    def cobre(self, d):
        # só conta como "fora" quando aprovada
        return self.status == "aprovada" and self.start_date <= d <= self.end_date

    @property
    def vigente(self):
        return self.cobre(fuso.hoje())

    @property
    def em_aberto_para_retorno(self):
        """Aprovada, já terminou e ainda não teve o retorno confirmado."""
        return (self.status == "aprovada" and not self.real_return
                and self.end_date <= fuso.hoje())


def absences_map(dias):
    """{member_id: {iso_do_dia: Absence}} para os dias informados."""
    if not dias:
        return {}
    ini, fim = min(dias), max(dias)
    out = {}
    q = Absence.query.filter(Absence.end_date >= ini,
                             Absence.start_date <= fim).all()
    for a in q:
        for d in dias:
            if a.cobre(d):
                out.setdefault(a.member_id, {})[d.isoformat()] = a
    return out


class UserNote(db.Model):
    """Bloco de notas pessoal — visivel APENAS para o proprio usuario.

    Nao e' compartilhado, nao aparece para a controladoria nem para o admin.
    Toda consulta deve filtrar por user_id do usuario da sessao.
    """
    __tablename__ = "user_notes"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"),
                        nullable=False, index=True)
    title = db.Column(db.String(200), default="")
    body = db.Column(db.Text, default="")
    pinned = db.Column(db.Boolean, default=False, index=True)
    color = db.Column(db.String(20), default="padrao")
    # escrita a mao (caneta): tracos vetoriais em JSON + miniatura PNG p/ a lista
    ink_json = db.Column(db.Text)
    ink_thumb = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow,
                           onupdate=datetime.utcnow)

    user = db.relationship("User")

    CORES = [("padrao", "Padrão"), ("amarelo", "Amarelo"), ("verde", "Verde"),
             ("azul", "Azul"), ("rosa", "Rosa")]

    @property
    def ink(self):
        """Pagina manuscrita: {'h': altura, 'strokes': [...]} (ou None)."""
        try:
            return json.loads(self.ink_json) if self.ink_json else None
        except Exception:
            return None

    @property
    def tem_tinta(self):
        """Ha escrita visivel? (tracos de borracha 'er' sozinhos nao contam)"""
        d = self.ink
        return bool(d and any(s.get("t") != "er" for s in d.get("strokes") or []))

    @property
    def titulo_exibicao(self):
        if (self.title or "").strip():
            return self.title.strip()
        primeira = (self.body or "").strip().split("\n")[0][:60]
        if not primeira and self.tem_tinta:
            return "Nota manuscrita"
        return primeira or "Sem título"

    @property
    def preview(self):
        corpo = (self.body or "").strip()
        linhas = [l for l in corpo.split("\n") if l.strip()]
        # pula a primeira linha quando ela ja virou o titulo
        if not (self.title or "").strip() and linhas:
            linhas = linhas[1:]
        return " ".join(linhas)[:110]

    @property
    def vazia(self):
        return (not (self.title or "").strip() and not (self.body or "").strip()
                and not self.tem_tinta)


class DigestSnapshot(db.Model):
    """Foto de cada Resumo do fechamento enviado — o push/e-mail aponta para ela."""
    __tablename__ = "digest_snapshots"
    id = db.Column(db.Integer, primary_key=True)
    texto = db.Column(db.Text, nullable=False)
    sent_by = db.Column(db.Integer, db.ForeignKey("users.id"))   # None = agendador
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    destinos = db.Column(db.Integer, default=0)
    email_ok = db.Column(db.Integer, default=0)
    email_falhas = db.Column(db.Text)                             # "fulano: motivo" por linha
    autor = db.relationship("User", foreign_keys=[sent_by])

    @property
    def quando(self):
        """Data/hora de Brasília para exibir (gravado em UTC)."""
        from fuso import local
        return local(self.created_at).strftime("%d/%m/%Y às %H:%M") if self.created_at else ""


class DefinitionList(db.Model):
    """Lista de Definicoes gerais — COMPARTILHADA: todo o time ve.

    Diferente das listas pessoais de tarefas: nao tem dono, nem vencimento, nem
    status. Guarda os combinados da area (padroes, criterios, prazos acordados)."""
    __tablename__ = "definition_lists"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False, default="Definições")
    sort_order = db.Column(db.Integer, default=100)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    itens = db.relationship("Definition", cascade="all, delete-orphan",
                            order_by="Definition.sort_order, Definition.id")


class Definition(db.Model):
    """Uma definicao (texto) dentro de uma lista compartilhada."""
    __tablename__ = "definitions"
    id = db.Column(db.Integer, primary_key=True)
    list_id = db.Column(db.Integer, db.ForeignKey("definition_lists.id"),
                        nullable=False, index=True)
    text = db.Column(db.Text, nullable=False)
    sort_order = db.Column(db.Integer, default=100)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    updated_at = db.Column(db.DateTime)

    lista = db.relationship("DefinitionList", overlaps="itens")
    autor = db.relationship("User", foreign_keys=[created_by])
    editor = db.relationship("User", foreign_keys=[updated_by])


class PushSubscription(db.Model):
    """Aparelho que ativou as notificacoes push (um por navegador/celular).

    `endpoint` e o endereco do servico de push do aparelho; p256dh/auth sao as
    chaves PUBLICAS dele para criptografar a mensagem (ver team/push.py)."""
    __tablename__ = "push_subscriptions"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    endpoint = db.Column(db.String(600), nullable=False, unique=True)
    p256dh = db.Column(db.String(200), nullable=False)
    auth = db.Column(db.String(60), nullable=False)
    aparelho = db.Column(db.String(160))          # resumo do navegador/sistema
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)
    ultimo_ok = db.Column(db.String(20))
    falhas = db.Column(db.Integer, default=0)

    user = db.relationship("User")


class TaskList(db.Model):
    """Lista de tarefas pessoal — gestao do dia de cada profissional.

    Privada: so o dono ve. Distinta das Activity (trabalho do time atribuido).
    """
    __tablename__ = "task_lists"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"),
                        nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False, default="Minhas tarefas")
    color = db.Column(db.String(20), default="padrao")
    sort_order = db.Column(db.Integer, default=100)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    tasks = db.relationship(
        "PersonalTask", cascade="all, delete-orphan",
        order_by="PersonalTask.done, PersonalTask.due_date.is_(None), "
                 "PersonalTask.due_date, PersonalTask.sort_order")


class PersonalTask(db.Model):
    """Item de uma lista pessoal, com vencimento e aviso opcional."""
    __tablename__ = "personal_tasks"
    id = db.Column(db.Integer, primary_key=True)
    list_id = db.Column(db.Integer, db.ForeignKey("task_lists.id"),
                        nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"),
                        nullable=False, index=True)
    title = db.Column(db.String(300), nullable=False)
    done = db.Column(db.Boolean, default=False, index=True)
    # 'nao_iniciado' | 'em_andamento' | 'concluido' (done fica em sincronia)
    status = db.Column(db.String(15), default="nao_iniciado", index=True)
    due_date = db.Column(db.Date, index=True)
    remind = db.Column(db.Boolean, default=False)   # avisar no vencimento
    remind_days = db.Column(db.Integer, default=0)  # antecedencia do aviso (dias corridos)
    reminded_on = db.Column(db.Date)                # ultimo dia que avisou
    priority = db.Column(db.String(10), default="media")
    done_at = db.Column(db.DateTime)
    sort_order = db.Column(db.Integer, default=100)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    lista = db.relationship("TaskList", overlaps="tasks")

    STATUSES = [("nao_iniciado", "Não iniciado"), ("em_andamento", "Em andamento"),
                ("concluido", "Concluído")]
    # antecedencia do aviso: (dias, rotulo)
    ANTECEDENCIAS = [(0, "No dia"), (1, "1 dia antes"), (2, "2 dias antes"),
                     (7, "1 semana antes")]

    @property
    def status_pt(self):
        return dict(self.STATUSES).get(self.status, self.status)

    @property
    def aviso_label(self):
        d = self.remind_days or 0
        return dict(self.ANTECEDENCIAS).get(d, f"{d} dias antes")

    def data_do_aviso(self):
        """Primeiro dia em que o aviso deve sair (vencimento - antecedencia)."""
        from datetime import timedelta
        if not self.due_date:
            return None
        return self.due_date - timedelta(days=self.remind_days or 0)

    @property
    def atrasada(self):
        return bool(self.due_date and self.status != "concluido"
                    and self.due_date < fuso.hoje())

    @property
    def vence_hoje(self):
        return bool(self.due_date and self.status != "concluido"
                    and self.due_date == fuso.hoje())

    @property
    def grupo(self):
        """Balde de exibição: atrasado vem antes de tudo."""
        if self.status == "concluido":
            return "concluido"
        if self.atrasada:
            return "atrasado"
        return self.status


class DeadlineRevision(db.Model):
    """Histórico de revisão de prazo (projeto ou marco), com justificativa.

    Em vez de editar a data e perder o rastro, cada mudança de prazo fica
    registrada aqui — para frente ou para trás, com o motivo e quem alterou.
    """
    __tablename__ = "deadline_revisions"
    id = db.Column(db.Integer, primary_key=True)
    entity_type = db.Column(db.String(20), nullable=False)   # 'project'|'milestone'|'activity'
    entity_id = db.Column(db.Integer, nullable=False, index=True)
    old_date = db.Column(db.Date)
    new_date = db.Column(db.Date)
    reason = db.Column(db.Text, nullable=False)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    # fluxo de aprovação do realinhamento (atividade/marco):
    # 'aplicada' (líder/gestor fez direto) | 'solicitada' | 'recusada'
    status = db.Column(db.String(15), default="aplicada", index=True)
    requested_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    approved_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    approved_at = db.Column(db.DateTime)
    decision_note = db.Column(db.String(255))

    user = db.relationship("User", foreign_keys=[created_by])

    @property
    def sentido(self):
        if not self.old_date or not self.new_date:
            return "definido"
        return "adiado" if self.new_date > self.old_date else "antecipado"

    @property
    def dias(self):
        if self.old_date and self.new_date:
            return abs((self.new_date - self.old_date).days)
        return None


class Panel(db.Model):
    """Painel = agrupamento de metas e da carga do time.

    'pessoal'  -> pertence a uma pessoa (o dono).
    'equipe'   -> compartilhado (ex.: Controladoria), sem dono unico.
    Membros seguem um painel; indicadores/metas pertencem a um painel.
    """
    __tablename__ = "panels"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    kind = db.Column(db.String(20), default="pessoal")   # pessoal | equipe
    owner_member_id = db.Column(db.Integer, db.ForeignKey("team_members.id"),
                                nullable=True)
    color = db.Column(db.String(9), default="#1d5da8")
    active = db.Column(db.Boolean, default=True)
    sort_order = db.Column(db.Integer, default=100)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    owner = db.relationship("TeamMember", foreign_keys=[owner_member_id])

    @property
    def kind_pt(self):
        return {"pessoal": "Pessoal", "equipe": "Equipe"}.get(self.kind, self.kind)

    @property
    def initials(self):
        parts = [p for p in (self.name or "").split() if p]
        if not parts:
            return "?"
        if len(parts) == 1:
            return parts[0][:2].upper()
        return (parts[0][0] + parts[-1][0]).upper()


def ensure_panels_defaults():
    """Garante um painel pessoal por membro, o painel Controladoria e o
    preenchimento de panel_id em membros e indicadores. Idempotente."""
    from team.models import TeamMember, Indicator
    # painel pessoal por membro
    donos = {p.owner_member_id for p in Panel.query.filter_by(kind="pessoal").all()}
    for m in TeamMember.query.all():
        if m.id not in donos:
            db.session.add(Panel(name=m.name, kind="pessoal", owner_member_id=m.id,
                                 color=m.color, sort_order=m.sort_order or 100))
    db.session.commit()
    # painel de equipe Controladoria
    if not Panel.query.filter_by(kind="equipe").first():
        db.session.add(Panel(name="Controladoria", kind="equipe",
                             color="#16324f", sort_order=1))
        db.session.commit()
    # mapa membro -> painel pessoal
    pessoal = {p.owner_member_id: p for p in Panel.query.filter_by(kind="pessoal").all()}
    # backfill do painel que cada membro segue
    for m in TeamMember.query.filter(TeamMember.panel_id.is_(None)).all():
        alvo = m.follows_member_id or m.id      # segue outro? usa o painel dele
        p = pessoal.get(alvo) or pessoal.get(m.id)
        if p:
            m.panel_id = p.id
    # backfill do painel de cada indicador (pelo dono)
    for i in Indicator.query.filter(Indicator.panel_id.is_(None)).all():
        p = pessoal.get(i.member_id)
        if p:
            i.panel_id = p.id
    db.session.commit()


class Segment(db.Model):
    """Segmento de negocio das entidades do grupo (Energia, Agro, ...).

    Cadastro editavel: alimenta o select de Segmento na tela de Entidades.
    """
    __tablename__ = "segments"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(60), unique=True, nullable=False)
    sort_order = db.Column(db.Integer, default=100)
    active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    @staticmethod
    def nomes_ativos():
        return [s.name for s in Segment.query.filter_by(active=True)
                .order_by(Segment.sort_order, Segment.name).all()]


def ensure_segments_defaults():
    """Popula a tabela de segmentos a partir dos que ja estao em uso na carteira.

    Idempotente: nao recria os que ja existem. Roda no start do app, entao os
    valores livres antigos (Energia, Agro...) viram cadastro editavel.
    """
    from team.models import CompanyAssignment
    existentes = {s.name for s in Segment.query.all()}
    usados = {a.segment.strip() for a in CompanyAssignment.query.all()
              if a.segment and a.segment.strip()}
    novos = sorted(usados - existentes)
    for i, nome in enumerate(novos):
        db.session.add(Segment(name=nome, sort_order=10 + i))
    if novos:
        db.session.commit()
    return len(novos)


class ClosingMetric(db.Model):
    """Foto do desempenho de uma competencia — vira serie historica.

    Gravada ao fechar a competencia (e recalculavel a qualquer momento).
    """
    __tablename__ = "closing_metrics"
    id = db.Column(db.Integer, primary_key=True)
    competency_id = db.Column(db.Integer, db.ForeignKey("competencies.id"),
                              nullable=False, unique=True)
    # cobertura
    n_empresas = db.Column(db.Integer, default=0)
    n_aprovadas = db.Column(db.Integer, default=0)
    n_no_prazo = db.Column(db.Integer, default=0)
    # qualidade do envio
    n_envios = db.Column(db.Integer, default=0)          # total de versoes
    n_reprovacoes = db.Column(db.Integer, default=0)
    rodadas_media = db.Column(db.Float, default=0.0)     # versoes por empresa
    pct_primeira = db.Column(db.Float, default=0.0)      # % aprovadas na v1
    # tempo
    dias_ate_verde = db.Column(db.Float, default=0.0)    # media (prazo -> aprovacao)
    # execucao do time
    n_atividades = db.Column(db.Integer, default=0)
    n_atrasadas = db.Column(db.Integer, default=0)
    pct_no_prazo_time = db.Column(db.Float, default=0.0)
    # excecoes e intercompany
    n_excecoes = db.Column(db.Integer, default=0)
    n_pend_ic = db.Column(db.Integer, default=0)
    computed_at = db.Column(db.DateTime, default=datetime.utcnow)

    competency = db.relationship("Competency")


class AtaReuniao(db.Model):
    """Ata estruturada de reunião (vinda do prompt sobre a transcrição do Teams).

    Guarda o qualitativo por empresa×competência (reuniões de resultado) — o
    lastro que, no futuro, alimenta o release. As ações viram Activity após a
    conferência do gestor. `dados_json` guarda o JSON bruto do prompt.
    """
    __tablename__ = "atas_reuniao"
    id = db.Column(db.Integer, primary_key=True)
    tipo = db.Column(db.String(15), default="geral")          # resultado | geral
    titulo = db.Column(db.String(240), nullable=False)
    data_reuniao = db.Column(db.Date, index=True)
    company_id = db.Column(db.Integer, db.ForeignKey("companies.id"),
                           nullable=True, index=True)
    comp_ym = db.Column(db.String(7), index=True)             # "AAAA-MM" (competência)
    resumo = db.Column(db.Text)
    ata_texto = db.Column(db.Text)                            # markdown humano (opcional)
    dados_json = db.Column(db.Text)                           # JSON estruturado bruto
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    company = db.relationship("Company")

    @property
    def dados(self):
        try:
            return json.loads(self.dados_json or "{}")
        except Exception:
            return {}

    @property
    def qualitativo(self):
        return self.dados.get("qualitativo") or {}

    @property
    def decisoes(self):
        return self.dados.get("decisoes") or []

    @property
    def acoes(self):
        return self.dados.get("acoes") or []

    @property
    def temas(self):
        return (self.qualitativo or {}).get("temas") or []

    @property
    def comp_label(self):
        if not self.comp_ym:
            return "—"
        try:
            y, m = self.comp_ym.split("-")
            meses = ["", "Jan", "Fev", "Mar", "Abr", "Mai", "Jun",
                     "Jul", "Ago", "Set", "Out", "Nov", "Dez"]
            return f"{meses[int(m)]}/{y}"
        except Exception:
            return self.comp_ym

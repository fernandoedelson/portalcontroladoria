def sub(s, old, new, count=1):
    assert old in s, old[:80]
    return s.replace(old, new, count)

# ------------------------------------------------ modelos: agenda por responsavel
p = 'team/models_workflow.py'
s = open(p, encoding='utf-8').read()
s = sub(s, '''    weekday = db.Column(db.Integer, nullable=False)        # 0 = segunda
    inicio = db.Column(db.String(5), nullable=False)       # 'HH:MM'
    fim = db.Column(db.String(5), nullable=False)
    active = db.Column(db.Boolean, default=True)''', '''    member_id = db.Column(db.Integer, db.ForeignKey("team_members.id"), nullable=True, index=True)   # vazio = agenda geral
    weekday = db.Column(db.Integer, nullable=False)        # 0 = segunda
    inicio = db.Column(db.String(5), nullable=False)       # 'HH:MM'
    fim = db.Column(db.String(5), nullable=False)
    active = db.Column(db.Boolean, default=True)''')
s = sub(s, '''    inicio = db.Column(db.DateTime, nullable=False)        # horário de Brasília (sem fuso)
    fim = db.Column(db.DateTime, nullable=False)
    motivo = db.Column(db.String(160))''', '''    member_id = db.Column(db.Integer, db.ForeignKey("team_members.id"), nullable=True, index=True)   # vazio = vale para todos
    inicio = db.Column(db.DateTime, nullable=False)        # horário de Brasília (sem fuso)
    fim = db.Column(db.DateTime, nullable=False)
    motivo = db.Column(db.String(160))''')
s = sub(s, '''    link_id = db.Column(db.Integer, db.ForeignKey("agenda_links.id"), nullable=True)
    company_id = db.Column(db.Integer, db.ForeignKey("companies.id"), nullable=True)
    nome = db.Column(db.String(120), nullable=False)''', '''    link_id = db.Column(db.Integer, db.ForeignKey("agenda_links.id"), nullable=True)
    company_id = db.Column(db.Integer, db.ForeignKey("companies.id"), nullable=True)
    member_id = db.Column(db.Integer, db.ForeignKey("team_members.id"), nullable=True, index=True)   # de quem é a agenda
    nome = db.Column(db.String(120), nullable=False)''')
s = sub(s, '''    company = db.relationship("Company")
EOF_MARK''', '') if False else s
s = sub(s, '''    criada_em = db.Column(db.DateTime, default=datetime.utcnow)
    company = db.relationship("Company")''', '''    criada_em = db.Column(db.DateTime, default=datetime.utcnow)
    company = db.relationship("Company")
    member = db.relationship("TeamMember", foreign_keys=[member_id])''')
open(p, 'w', encoding='utf-8').write(s)

p = 'app.py'
s = open(p, encoding='utf-8').read()
s = sub(s, '''    ("companies", "grupo_report", "VARCHAR(20)"),
]''', '''    ("companies", "grupo_report", "VARCHAR(20)"),
    ("agenda_janelas", "member_id", "INTEGER"),
    ("agenda_bloqueios", "member_id", "INTEGER"),
    ("agenda_reunioes", "member_id", "INTEGER"),
]''')
open(p, 'w', encoding='utf-8').write(s)

# ------------------------------------------------ logica
p = 'team/agenda.py'
s = open(p, encoding='utf-8').read()
a = s.index('def horarios_livres(ref=None):')
b = s.index('# ---------------------------------------------------------------- convite .ics')
s = s[:a] + '''def membro_da_empresa(company_id):
    """Responsável da Controladoria pela empresa (carteira) ou None."""
    from team.models import CompanyAssignment
    if not company_id:
        return None
    a = CompanyAssignment.query.filter_by(company_id=company_id).first()
    return a.member if a and a.member and a.member.active else None


def dia_reuniao(company_id, comp=None):
    """Dia da reunião da empresa: SEMPRE o dia útil seguinte ao prazo de entrega dela
    (prazo no 5º d.u. -> reunião no 6º). Usa o calendário de feriados do portal."""
    from models import current_competency
    from team.avisos_empresas import prazo_da_empresa
    from team.engine import add_business_days
    comp = comp or current_competency()
    prazo, _du = prazo_da_empresa(company_id, comp)
    return add_business_days(prazo, 1) if prazo else None


def horarios_livres(member_id=None, company_id=None, ref=None):
    """{date: [datetime,...]} dos horários livres da agenda de UM responsável.

    `member_id=None` = agenda geral. Com `company_id`, só o dia da reunião daquela
    empresa (dia útil seguinte ao prazo). Reuniões de responsáveis diferentes podem
    se sobrepor; para o mesmo responsável, não."""
    ref = ref or fuso.agora()
    dur = timedelta(minutes=cfg_int("ag_duracao", 30))
    minimo = ref + timedelta(hours=cfg_int("ag_antecedencia_h", 24))
    limite = ref.date() + timedelta(days=cfg_int("ag_janela_dias", 30))
    janelas = {}
    for j in AgendaJanela.query.filter(AgendaJanela.active.is_(True),
                                       AgendaJanela.member_id == member_id).all():
        janelas.setdefault(j.weekday, []).append((_hm(j.inicio), _hm(j.fim)))
    if not janelas:
        return {}
    ocupados = [(r.inicio, r.fim) for r in Reuniao.query.filter(
        Reuniao.status == "marcada", Reuniao.fim > ref, Reuniao.member_id == member_id).all()]
    ocupados += [(b.inicio, b.fim) for b in AgendaBloqueio.query.filter(
        AgendaBloqueio.fim > ref,
        db.or_(AgendaBloqueio.member_id.is_(None), AgendaBloqueio.member_id == member_id)).all()]
    so_dia = dia_reuniao(company_id) if company_id else None
    if company_id and not so_dia:
        return {}
    out = {}
    d = ref.date()
    while d <= limite:
        if (so_dia is None or d == so_dia) and d.weekday() in janelas and fuso.dia_util(d):
            for ini, fim in janelas[d.weekday()]:
                cur = datetime.combine(d, ini)
                fim_dt = datetime.combine(d, fim)
                while cur + dur <= fim_dt:
                    if cur >= minimo and not any(cur < o_fim and cur + dur > o_ini
                                                 for o_ini, o_fim in ocupados):
                        out.setdefault(d, []).append(cur)
                    cur += dur
        d += timedelta(days=1)
    return out


def livres_do_link(link):
    """Horários livres para quem abriu este link: a agenda do responsável da empresa."""
    mem = membro_da_empresa(link.company_id)
    return horarios_livres(mem.id if mem else None, link.company_id), mem


''' + s[b:]

s = sub(s, '''def participantes_da_empresa(company_id):''', '''def participantes_da_empresa(company_id, membro=None):''')
s = sub(s, '''        a = CompanyAssignment.query.filter_by(company_id=company_id).first()
        if a and a.member and a.member.user and a.member.user.email:
            emails.append(a.member.user.email)''', '''        if membro is None:
            a = CompanyAssignment.query.filter_by(company_id=company_id).first()
            membro = a.member if a else None
    if membro and membro.user and membro.user.email:
        emails.append(membro.user.email)''')
s = sub(s, '''    for e in [r.email] + participantes_da_empresa(r.company_id) + participantes_fixos():''',
        '''    for e in [r.email] + participantes_da_empresa(r.company_id, r.member) + participantes_fixos():''')
s = sub(s, '''    livres = horarios_livres()
    if inicio not in livres.get(inicio.date(), []):''', '''    livres, mem = livres_do_link(link)
    if inicio not in livres.get(inicio.date(), []):''')
s = sub(s, '''    r = Reuniao(link_id=link.id, company_id=link.company_id, nome=nome[:120]''',
        '''    r = Reuniao(link_id=link.id, company_id=link.company_id, member_id=(mem.id if mem else None),
                nome=nome[:120]''')
open(p, 'w', encoding='utf-8').write(s)
print('ok parte 1')

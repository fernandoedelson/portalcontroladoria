# -*- coding: utf-8 -*-
"""Comunicação do portal: listas de destinatários, régua por pessoa e e-mail diário único.

• Listas por tipo (protocolo, resumo semanal, sistema): o administrador não recebe
  e-mail por ser administrador — o que ele recebia passa por estas listas.
• Régua: cada pessoa liga/desliga, só para si, cada etapa de aviso.
• E-mail diário: tudo o que a pessoa tem a saber no dia vai em UMA mensagem.
"""
import fuso
from datetime import datetime

from models import db, User, get_setting, set_setting
from team.models_workflow import ListaEmail, PreferenciaAviso

TIPOS_LISTA = [
    ("protocolo", "Protocolo de envio às empresas",
     "Comprovante de cada rodada de avisos de prazo enviados às empresas."),
    ("resumo", "Resumo semanal do fechamento",
     "Situação do cronograma de fechamento (toda semana)."),
    ("report", "Report do fechamento (quem já enviou)",
     "Quadro de envio dos 3 documentos por empresa — do time até a diretoria."),
    ("agenda", "Reuniões agendadas (participantes fixos)",
     "Quem entra em todo convite de reunião marcado pelas empresas."),
    ("sistema", "Avisos do sistema",
     "Só quando algo dá errado: e-mail do dia, report ou avisos às empresas que falharam, ou falha no ciclo diário (no máximo 1 aviso por tipo por dia)."),
]

# Etapas da régua: (chave, título, quando acontece, quem costuma receber)
ETAPAS = [
    ("lembrete_previo", "Lembrete antes do prazo",
     "Alguns dias úteis antes de uma atividade minha vencer.", "todos"),
    ("vence_hoje", "Vence hoje",
     "No dia em que uma atividade minha vence.", "todos"),
    ("atraso", "Atraso",
     "Enquanto uma atividade minha estiver atrasada.", "todos"),
    ("escalada_atraso", "Atrasos do time (escalada)",
     "Atividades atrasadas de outras pessoas, que sobem até mim.", "gestão"),
    ("tarefa_pessoal", "Minhas tarefas pessoais",
     "Vencimento das tarefas da minha lista pessoal.", "todos"),
]
CHAVES_ETAPAS = {k for k, *_ in ETAPAS}


# -------------------------------------------------------------------- listas
def _limpa(email):
    return (email or "").strip().lower()


def emails_da_lista(tipo):
    return [x.email for x in ListaEmail.query.filter_by(tipo=tipo, active=True)
            .order_by(ListaEmail.id).all() if x.email]


def semeia_listas():
    """1x: copia para as listas quem recebia antes (controladoria, sem o administrador)."""
    if get_setting("listas_email_semeadas") == "1":
        return 0
    ctrl = [u.email for u in User.query.filter_by(role="controladoria", active=True).all()
            if u.email]
    manual = [e.strip() for e in (get_setting("aviso_emp_copia") or "").replace(";", ",").split(",")
              if "@" in e]
    admins = {_limpa(u.email) for u in User.query.filter_by(role="admin").all()}
    n = 0
    for tipo in ("protocolo", "resumo"):
        base = manual if (tipo == "protocolo" and manual) else ctrl
        vistos = set()
        for e in base:
            k = _limpa(e)
            if not k or k in vistos or k in admins:
                continue
            vistos.add(k)
            db.session.add(ListaEmail(tipo=tipo, email=k))
            n += 1
    set_setting("listas_email_semeadas", "1")
    db.session.commit()
    return n


# ------------------------------------------------------------------- régua
def etapas_desligadas(user_id):
    return {p.etapa for p in PreferenciaAviso.query.filter_by(user_id=user_id).all()}


def quer(user_id, etapa):
    """True se a pessoa não desligou a etapa (sem login vinculado: sempre True)."""
    if not user_id:
        return True
    return db.session.query(PreferenciaAviso.id).filter_by(
        user_id=user_id, etapa=etapa).first() is None


def salva_etapas(user_id, ligadas):
    ligadas = set(ligadas)
    atual = {p.etapa: p for p in PreferenciaAviso.query.filter_by(user_id=user_id).all()}
    for chave in CHAVES_ETAPAS:
        if chave in ligadas and chave in atual:
            db.session.delete(atual[chave])
        elif chave not in ligadas and chave not in atual:
            db.session.add(PreferenciaAviso(user_id=user_id, etapa=chave))
    db.session.commit()


# ------------------------------------------------------------- e-mail diário
SECOES = [
    ("atraso", "ATRASADAS"),
    ("vence_hoje", "VENCEM HOJE"),
    ("lembrete_previo", "PRÓXIMOS VENCIMENTOS"),
    ("escalada_atraso", "ATRASOS DO TIME"),
    ("tarefa_pessoal", "SUAS TAREFAS PESSOAIS"),
]


class Coletor:
    """Junta, por destinatário, tudo o que entra no e-mail do dia."""

    def __init__(self):
        self.por_email = {}            # email -> {"nome": str, "itens": [(etapa, linha, meta)]}

    def add(self, email, nome, etapa, linha, meta=None):
        e = _limpa(email)
        if not e:
            return
        d = self.por_email.setdefault(e, {"nome": nome or "", "itens": []})
        d["itens"].append((etapa, linha, meta))

    def __len__(self):
        return len(self.por_email)


def monta_corpo(nome, itens, portal_url):
    por = {}
    for etapa, linha, _m in itens:
        por.setdefault(etapa, []).append(linha)
    partes = [f"Olá{', ' + nome.split()[0] if nome else ''}. Este é o seu resumo do dia "
              f"({fuso.hoje().strftime('%d/%m/%Y')}) na Controladoria J&F.", ""]
    for chave, titulo in SECOES:
        if por.get(chave):
            partes.append(f"{titulo} ({len(por[chave])})")
            partes += [f"  • {l}" for l in por[chave]]
            partes.append("")
    partes.append(f"Abra o portal: {portal_url}/time")
    partes.append("Para escolher o que quer receber: " + portal_url + "/comunicacao")
    return "\n".join(partes)


def envia_coletor(coletor, portal_url, enviar, dry_run=False):
    """Manda UM e-mail por pessoa. `enviar(to, assunto, corpo)` -> (ok, erro).

    Retorna {email: (ok, erro, n_itens)}."""
    saida = {}
    for email, d in coletor.por_email.items():
        itens = d["itens"]
        if not itens:
            continue
        n = len(itens)
        atrasadas = sum(1 for e, *_ in itens if e == "atraso")
        assunto = (f"Controladoria J&F — {n} pendência(s) hoje"
                   + (f", {atrasadas} em atraso" if atrasadas else ""))
        if dry_run:
            saida[email] = (True, "simulado", n)
            continue
        ok, err = enviar(email, assunto, monta_corpo(d["nome"], itens, portal_url))
        saida[email] = (ok, err, n)
    return saida


# ------------------------------------------------------------ horário do envio
def hora_envio():
    """(hora, minuto) do disparo diário. `envio_diario_hora` 'HH:MM'; sem ele, cai no
    antigo `scheduler_hour`; padrão 08:00."""
    v = get_setting("envio_diario_hora")
    try:
        h, m = str(v).split(":")
        h, m = int(h), int(m)
        if 0 <= h <= 23 and 0 <= m <= 59:
            return h, m
    except (TypeError, ValueError):
        pass
    try:
        return max(0, min(23, int(get_setting("scheduler_hour", 8) or 8))), 0
    except (TypeError, ValueError):
        return 8, 0


def hora_envio_txt():
    h, m = hora_envio()
    return f"{h:02d}:{m:02d}"


def salva_hora_envio(txt):
    """Grava 'HH:MM' (valida). Mantém o antigo `scheduler_hour` em dia. Retorna bool."""
    try:
        h, m = str(txt).strip().split(":")
        h, m = int(h), int(m)
        assert 0 <= h <= 23 and 0 <= m <= 59
    except (ValueError, AssertionError):
        return False
    set_setting("envio_diario_hora", f"{h:02d}:{m:02d}")
    set_setting("scheduler_hour", str(h))
    db.session.commit()
    return True


# ------------------------------------------------------------ avisos do sistema
def avisa_sistema(tipo, assunto, detalhe):
    """Manda um aviso operacional à lista "Avisos do sistema" (no máximo 1 por tipo por
    dia, para uma falha repetida não virar enxurrada). Nunca levanta erro."""
    try:
        from team import alerts
        hoje = fuso.hoje().isoformat()
        chave = f"sistema_aviso_{tipo}"
        if get_setting(chave) == hoje:
            return False
        destinos = emails_da_lista("sistema")
        if not destinos:
            return False
        set_setting(chave, hoje)
        db.session.commit()
        corpo = (f"{assunto}\n\n{detalhe}\n\nPortal: {alerts.PORTAL_URL}\n"
                 "(Aviso automático do sistema; no máximo um por tipo por dia.)")
        for e in destinos:
            alerts.send_email(e, f"[Portal Controladoria] {assunto}", corpo, lista=True)
        return True
    except Exception:
        return False

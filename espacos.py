# -*- coding: utf-8 -*-
"""Espaços de trabalho: cada espaço é um banco SQLite próprio, dentro do mesmo serviço.

Serve para dar a outra área (por exemplo, o RH) o portal COMPLETO e funcional, mas com uma base
vazia e independente: o que ela cadastra, vê e recebe (e-mails, push, avisos) é só dela, e nada do
time da Controladoria aparece ali, nem o contrário.

Como funciona
  * O banco da Controladoria continua sendo o "principal" (o de sempre, sem nenhuma mudança).
  * Cada espaço é um arquivo `instance/ws_<nome>.db`, criado vazio com a estrutura completa.
  * Quem entra é levado ao espaço do seu e-mail: `session["ws"]` guarda o espaço e toda consulta
    daquela requisição vai para o banco dele (ver `RoutingSession`).
  * O agendador roda uma vez por espaço (cada um com as próprias configurações e listas).
  * Sem nenhum arquivo `ws_*.db`, nada muda: o portal se comporta exatamente como antes.
"""
import contextvars
import glob
import os
import re
import threading
import uuid
from contextlib import contextmanager

from flask_sqlalchemy.session import Session as _FSASession
from sqlalchemy import create_engine, text

from config import INSTANCE_DIR

PRINCIPAL = "principal"
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,30}$")
_ws = contextvars.ContextVar("espaco", default=PRINCIPAL)
_engines = {}
_lock = threading.Lock()


# ------------------------------------------------------------------ espaço corrente
def atual():
    """Espaço da requisição/rotina em andamento ('principal' se nenhum)."""
    return _ws.get()


def definir(slug):
    """Aponta a requisição para um espaço (usado no login e no gancho de cada requisição)."""
    _ws.set(slug or PRINCIPAL)


def _descarta_sessao():
    """Troca de espaço no meio de um mesmo contexto de aplicação: a sessão não pode carregar
    uma transação aberta no banco anterior."""
    try:
        from models import db
        db.session.remove()
    except RuntimeError:          # fora de contexto de aplicação: nada a descartar
        pass


@contextmanager
def espaco(slug):
    """Executa um trecho dentro de um espaço (agendador, push, testes) e volta ao anterior."""
    _descarta_sessao()
    token = _ws.set(slug or PRINCIPAL)
    try:
        yield
    finally:
        _descarta_sessao()
        _ws.reset(token)


# ------------------------------------------------------------------ arquivos e motores
def slug_valido(slug):
    return bool(slug) and bool(_SLUG_RE.match(slug)) and slug != PRINCIPAL


def caminho_db(slug):
    return os.path.join(INSTANCE_DIR, f"ws_{slug}.db")


def existe(slug):
    return slug_valido(slug) and os.path.exists(caminho_db(slug))


def slugs():
    """Espaços existentes (um por arquivo ws_*.db), em ordem alfabética."""
    nomes = []
    for p in glob.glob(os.path.join(INSTANCE_DIR, "ws_*.db")):
        base = os.path.basename(p)[3:-3]
        if slug_valido(base):
            nomes.append(base)
    return sorted(nomes)


def todos():
    """Principal primeiro, depois os espaços."""
    return [PRINCIPAL] + slugs()


def engine_do(slug):
    with _lock:
        eng = _engines.get(slug)
        if eng is None:
            eng = create_engine("sqlite:///" + caminho_db(slug).replace("\\", "/"),
                                connect_args={"check_same_thread": False, "timeout": 30})
            _engines[slug] = eng
        return eng


def _fecha_engine(slug):
    with _lock:
        eng = _engines.pop(slug, None)
    if eng is not None:
        eng.dispose()


# ------------------------------------------------------------------ roteamento da sessão
class RoutingSession(_FSASession):
    """Sessão do Flask-SQLAlchemy que escolhe o banco pelo espaço corrente."""

    def get_bind(self, mapper=None, clause=None, bind=None, **kwargs):
        slug = _ws.get()
        if slug != PRINCIPAL and bind is None:
            return engine_do(slug)
        return super().get_bind(mapper=mapper, clause=clause, bind=bind, **kwargs)


def espaco_id(slug):
    """Identidade desta 'encarnação' do espaço (muda quando ele é zerado ou recriado). A sessão guarda esse
    valor: um espaço zerado reaproveita os números de usuário, e a sessão antiga não pode virar outra pessoa."""
    try:
        with engine_do(slug).connect() as c:
            r = c.execute(text("SELECT value FROM settings WHERE key='espaco_id'")).first()
            return r[0] if r else None
    except Exception:
        return None


# ------------------------------------------------------------------ ganchos de requisição
def registra(app):
    from flask import session

    @app.before_request
    def _escolhe_espaco():
        slug = session.get("ws")
        if slug and (not existe(slug) or session.get("ws_id") != espaco_id(slug)):
            # O espaço foi removido, ou zerado/recriado, com a pessoa logada. O número do usuário na sessão pertencia
            # àquele banco: NÃO pode ser reaproveitado (apontaria para outra pessoa). Encerra a sessão.
            session.clear()
            slug = None
        definir(slug or PRINCIPAL)

    @app.teardown_request
    def _volta_ao_principal(_exc):
        _ws.set(PRINCIPAL)          # threads do servidor são reaproveitadas: nada vaza entre requisições

    @app.context_processor
    def _contexto_espaco():
        slug = atual()
        nome = None
        if slug != PRINCIPAL:
            try:
                from models import get_setting
                nome = get_setting("espaco_nome") or slug
            except Exception:
                nome = slug
        return {"ws_atual": slug, "ws_nome": nome}


# ------------------------------------------------------------------ login
def _tem_email(engine, email):
    try:
        with engine.connect() as c:
            return bool(c.execute(text("SELECT 1 FROM users WHERE lower(email)=:e LIMIT 1"), {"e": email}).first())
    except Exception:
        return False


def espaco_do_email(email):
    """Espaço onde o e-mail está cadastrado: None = principal. Procura no principal primeiro.
    Consulta cada banco direto pelo motor, sem trocar o espaço da requisição."""
    from models import db
    email = (email or "").strip().lower()
    if not email:
        return None
    if _tem_email(db.engine, email):
        return None
    for slug in slugs():
        if _tem_email(engine_do(slug), email):
            return slug
    return None


def email_em_uso(email, exceto_slug=None):
    """O e-mail já existe no principal ou em algum espaço? (cada e-mail vive em um lugar só).
    `exceto_slug`: o lugar de onde a pergunta vem (ele mesmo já valida a duplicidade)."""
    from models import db
    email = (email or "").strip().lower()
    if exceto_slug != PRINCIPAL and _tem_email(db.engine, email):
        return PRINCIPAL
    for slug in slugs():
        if slug != exceto_slug and _tem_email(engine_do(slug), email):
            return slug
    return None


# ------------------------------------------------------------------ ciclo de vida
def _prepara(app, slug):
    """Cria/atualiza a estrutura do espaço (idempotente). NÃO carrega nada da Controladoria: sem
    carteira, sem clusters, sem listas de e-mail e sem indicadores iniciais."""
    from models import db
    import team.models  # noqa: F401  (registra as tabelas)
    import team.models_workflow  # noqa: F401
    db.metadata.create_all(engine_do(slug))
    from app import _ensure_columns
    _ensure_columns(app, engine_do(slug))
    with espaco(slug):
        from team.models import ensure_alert_defaults, ensure_macros, normaliza_prazos_cronograma
        from team.models_workflow import ensure_segments_defaults, ensure_panels_defaults
        for fn in (ensure_alert_defaults, ensure_segments_defaults, ensure_panels_defaults,
                   ensure_macros, normaliza_prazos_cronograma):
            try:
                fn()
            except Exception as e:
                db.session.rollback()
                app.logger.warning("Espaço %s: não foi possível preparar %s: %s", slug, fn.__name__, e)


def _criar(app, slug, nome, admin_nome, admin_email, senha):
    """Cria o espaço com o primeiro usuário (administrador do espaço). Retorna (ok, mensagem)."""
    from models import db, User, set_setting
    slug = (slug or "").strip().lower()
    admin_email = (admin_email or "").strip().lower()
    if not slug_valido(slug):
        return False, "Nome interno inválido: use de 2 a 31 letras minúsculas, números, - ou _."
    if existe(slug):
        return False, "Já existe um espaço com esse nome interno."
    if "@" not in admin_email or "." not in admin_email.split("@")[-1]:
        return False, "Informe um e-mail válido para o primeiro usuário."
    if len(senha or "") < 8:
        return False, "A senha provisória precisa de ao menos 8 caracteres."
    onde = email_em_uso(admin_email)
    if onde:
        return False, "Esse e-mail já está cadastrado " + ("no portal principal." if onde == PRINCIPAL else f"no espaço {onde}.")
    try:
        prepara(app, slug)
        with espaco(slug):
            set_setting("espaco_nome", (nome or slug).strip()[:80])
            set_setting("espaco_id", uuid.uuid4().hex)
            u = User(email=admin_email, display_name=(admin_nome or admin_email).strip()[:160],
                     role="admin", active=True, must_change_password=True)
            u.set_password(senha)
            db.session.add(u)
            db.session.commit()
    except Exception as e:
        remover(slug)
        return False, f"Não consegui criar o espaço: {e}"
    return True, "Espaço criado."


def remover(slug):
    """Apaga o espaço (o arquivo do banco). Irreversível."""
    if not slug_valido(slug):
        return False
    _fecha_engine(slug)
    ok = False
    for suf in ("", "-wal", "-shm", "-journal"):
        p = caminho_db(slug) + suf
        if os.path.exists(p):
            try:
                os.remove(p)
                ok = ok or suf == ""
            except OSError:
                pass
    return ok


def resumo(slug):
    """Dados leves para a tela de administração: nome, usuários e atividades."""
    info = {"slug": slug, "nome": slug, "usuarios": 0, "atividades": 0, "tamanho_kb": 0}
    try:
        info["tamanho_kb"] = round(os.path.getsize(caminho_db(slug)) / 1024)
        with engine_do(slug).connect() as c:
            n = c.execute(text("SELECT value FROM settings WHERE key='espaco_nome'")).first()
            if n and n[0]:
                info["nome"] = n[0]
            info["usuarios"] = c.execute(text("SELECT COUNT(*) FROM users")).scalar() or 0
            info["atividades"] = c.execute(text("SELECT COUNT(*) FROM activities")).scalar() or 0
    except Exception:
        pass
    return info


def _zerar(app, slug, admin_nome, admin_email, senha):
    """Volta o espaço a uma base vazia, com um único usuário. Valida tudo antes de apagar."""
    if not existe(slug):
        return False, "Espaço não encontrado."
    admin_email = (admin_email or "").strip().lower()
    if "@" not in admin_email or "." not in admin_email.split("@")[-1]:
        return False, "Informe um e-mail válido para o usuário."
    if len(senha or "") < 8:
        return False, "A senha provisória precisa de ao menos 8 caracteres."
    onde = email_em_uso(admin_email, exceto_slug=slug)
    if onde:
        return False, "Esse e-mail já está cadastrado " + ("no portal principal." if onde == PRINCIPAL else f"no espaço {onde}.")
    nome = resumo(slug)["nome"]
    remover(slug)
    return criar(app, slug, nome, admin_nome, admin_email, senha)


def prepara(app, slug):
    with app.app_context():
        return _prepara(app, slug)


def criar(app, slug, nome, admin_nome, admin_email, senha):
    with app.app_context():
        return _criar(app, slug, nome, admin_nome, admin_email, senha)


def zerar(app, slug, admin_nome, admin_email, senha):
    with app.app_context():
        return _zerar(app, slug, admin_nome, admin_email, senha)

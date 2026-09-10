# -*- coding: utf-8 -*-
"""Notificacoes push (Web Push) — o portal avisando no celular/computador.

Padrao aberto, sem servico pago: o navegador de cada aparelho entrega um
"endereco" (endpoint) do servico de push dele (Google, Apple, Microsoft,
Mozilla). O portal assina o pedido com as chaves VAPID (RFC 8292) e manda o
conteudo criptografado ponta a ponta (RFC 8291, aes128gcm) — o servico de push
nao consegue ler a mensagem.

Implementado so com `cryptography` (sem pywebpush). Tudo que vira notificacao
no sininho (models.Notification) tambem sai como push para os aparelhos que a
pessoa ativou — ver `instala()`.
"""
import base64
import json
import os
import struct
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

TTL = 24 * 3600          # o servico de push guarda por ate 1 dia se o aparelho estiver off
AGRUPA_A_PARTIR = 3      # varios avisos juntos para a mesma pessoa viram um resumo


# --------------------------------------------------------------------------
# base64url
# --------------------------------------------------------------------------
def b64u(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def unb64u(s):
    s = (s or "").strip()
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


# --------------------------------------------------------------------------
# Chaves VAPID: env (se definidas) ou geradas uma vez e guardadas no banco
# --------------------------------------------------------------------------
def _chave_privada():
    from models import get_setting, set_setting
    raw = os.environ.get("VAPID_PRIVATE_KEY") or get_setting("vapid_private_key")
    if not raw:
        priv = ec.generate_private_key(ec.SECP256R1())
        raw = b64u(priv.private_numbers().private_value.to_bytes(32, "big"))
        set_setting("vapid_private_key", raw)
        return priv
    return ec.derive_private_key(int.from_bytes(unb64u(raw), "big"), ec.SECP256R1())


def chave_publica_b64():
    """applicationServerKey para o navegador (ponto nao comprimido, 65 bytes)."""
    pub = _chave_privada().public_key()
    return b64u(pub.public_bytes(serialization.Encoding.X962,
                                 serialization.PublicFormat.UncompressedPoint))


def _assunto():
    """'sub' do VAPID: contato do remetente (a Apple exige mailto: ou https:)."""
    if os.environ.get("VAPID_SUBJECT"):
        return os.environ["VAPID_SUBJECT"]
    url = os.environ.get("TEAM_PORTAL_URL", "")
    if url.startswith("https://"):
        return url
    return "mailto:" + (os.environ.get("SMTP_FROM") or os.environ.get("SMTP_USER")
                        or "controladoria@jfsa.com.br")


def cabecalho_vapid(endpoint, priv=None):
    """Authorization: vapid t=<JWT ES256>, k=<chave publica>  (RFC 8292)."""
    priv = priv or _chave_privada()
    u = urllib.parse.urlsplit(endpoint)
    head = b64u(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    claims = b64u(json.dumps({"aud": f"{u.scheme}://{u.netloc}",
                              "exp": int(time.time()) + 12 * 3600,
                              "sub": _assunto()}, separators=(",", ":")).encode())
    assinado = f"{head}.{claims}".encode()
    r, s = decode_dss_signature(priv.sign(assinado, ec.ECDSA(hashes.SHA256())))
    jwt = f"{head}.{claims}.{b64u(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"
    k = b64u(priv.public_key().public_bytes(serialization.Encoding.X962,
                                            serialization.PublicFormat.UncompressedPoint))
    return f"vapid t={jwt}, k={k}"


# --------------------------------------------------------------------------
# Criptografia do conteudo (RFC 8291 + RFC 8188, aes128gcm, 1 registro)
# --------------------------------------------------------------------------
def _hkdf(salt, ikm, info, n):
    return HKDF(algorithm=hashes.SHA256(), length=n, salt=salt, info=info).derive(ikm)


def criptografa(conteudo, p256dh_b64, auth_b64, _priv_servidor=None, _salt=None):
    ua_pub = unb64u(p256dh_b64)                      # chave publica do aparelho (65 bytes)
    auth = unb64u(auth_b64)                          # segredo de autenticacao (16 bytes)
    ua_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_pub)
    as_priv = _priv_servidor or ec.generate_private_key(ec.SECP256R1())   # efemera por mensagem
    as_pub = as_priv.public_key().public_bytes(serialization.Encoding.X962,
                                               serialization.PublicFormat.UncompressedPoint)
    segredo = as_priv.exchange(ec.ECDH(), ua_key)
    ikm = _hkdf(auth, segredo, b"WebPush: info\x00" + ua_pub + as_pub, 32)
    salt = _salt or os.urandom(16)
    cek = _hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    cifrado = AESGCM(cek).encrypt(nonce, conteudo + b"\x02", None)   # \x02 = ultimo registro
    rs = 4096
    return salt + struct.pack("!I", rs) + bytes([len(as_pub)]) + as_pub + cifrado


# --------------------------------------------------------------------------
# Envio
# --------------------------------------------------------------------------
def envia(assinatura, dados, urgencia="normal"):
    """Manda um push para UM aparelho. Retorna (ok, status, erro).

    status 404/410 = aparelho nao existe mais (a assinatura deve ser apagada)."""
    corpo = criptografa(json.dumps(dados, ensure_ascii=False).encode("utf-8"),
                        assinatura.p256dh, assinatura.auth)
    req = urllib.request.Request(assinatura.endpoint, data=corpo, method="POST")
    req.add_header("Authorization", cabecalho_vapid(assinatura.endpoint))
    req.add_header("Content-Encoding", "aes128gcm")
    req.add_header("Content-Type", "application/octet-stream")
    req.add_header("TTL", str(TTL))
    req.add_header("Urgency", urgencia)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return (200 <= resp.status < 300), resp.status, None
    except urllib.error.HTTPError as e:
        return False, e.code, e.read().decode(errors="ignore")[:200]
    except Exception as e:                                   # pragma: no cover (rede)
        return False, None, f"{type(e).__name__}: {e}"


def envia_para_usuario(user_id, titulo, corpo, url="/", tag=None):
    """Push para todos os aparelhos ativos da pessoa. Retorna (enviados, falhas)."""
    from models import db, Notification
    from team.models_workflow import PushSubscription
    subs = PushSubscription.query.filter_by(user_id=user_id).all()
    if not subs:
        return 0, 0
    pendentes = Notification.query.filter_by(user_id=user_id, is_read=False).count()
    dados = {"t": titulo[:120], "b": (corpo or "")[:300], "u": url or "/",
             "tag": tag, "n": pendentes}
    ok_n = falha_n = 0
    for s in subs:
        ok, status, _erro = envia(s, dados)
        if ok:
            ok_n += 1
            s.ultimo_ok = time.strftime("%Y-%m-%d %H:%M")
            s.falhas = 0
        elif status in (404, 410):              # aparelho desinstalou/expirou: limpa
            db.session.delete(s)
            falha_n += 1
        else:
            s.falhas = (s.falhas or 0) + 1
            falha_n += 1
    db.session.commit()
    return ok_n, falha_n


# --------------------------------------------------------------------------
# Gancho: toda Notification nova tambem vira push (depois do commit)
# --------------------------------------------------------------------------
def _envia_lote(app, fila):
    with app.app_context():
        por_usuario = {}
        for item in fila:
            por_usuario.setdefault(item["user_id"], []).append(item)
        for uid, itens in por_usuario.items():
            try:
                if len(itens) >= AGRUPA_A_PARTIR:          # nao metralhar o aparelho
                    titulos = "; ".join(i["title"] for i in itens[:3])
                    envia_para_usuario(uid, f"{len(itens)} novos avisos no portal",
                                       titulos + ("…" if len(itens) > 3 else ""),
                                       url="/time", tag="resumo")
                else:
                    for i in itens:
                        envia_para_usuario(uid, i["title"], i["message"], i["url"] or "/",
                                           tag=i["kind"])
            except Exception as e:                        # push nunca derruba o portal
                app.logger.warning("Falha ao enviar push para o usuario %s: %s", uid, e)


def enfileira(sessao, user_id, titulo, mensagem, url="/", tipo=None):
    """Poe um push na fila da sessao — sai depois do commit (com o agrupamento).

    Usado pela matriz de alertas, que decide o canal push evento a evento."""
    sessao.info.setdefault("push_fila", []).append({
        "user_id": user_id, "title": titulo or "Controladoria J&F",
        "message": mensagem or "", "url": url, "kind": tipo})


def tem_aparelho(user_id):
    from team.models_workflow import PushSubscription
    return PushSubscription.query.filter_by(user_id=user_id).first() is not None


def instala(app):
    """Liga o gancho Notification -> push. Envia em segundo plano (em testes, na hora).

    Notificacoes criadas pela matriz de alertas vem marcadas com `_sem_push`: ali
    quem decide o push e a coluna Push da matriz (ver team/alerts.py)."""
    from sqlalchemy import event
    from sqlalchemy.orm import Session, object_session
    from models import Notification

    @event.listens_for(Notification, "after_insert")
    def _enfileira(mapper, connection, alvo):
        if getattr(alvo, "_sem_push", False):
            return
        sess = object_session(alvo)
        if sess is not None:
            sess.info.setdefault("push_fila", []).append({
                "user_id": alvo.user_id, "title": alvo.title or "Controladoria J&F",
                "message": alvo.message or "", "url": alvo.url, "kind": alvo.kind})

    @event.listens_for(Session, "after_commit")
    def _dispara(sess):
        fila = sess.info.pop("push_fila", None)
        if not fila:
            return
        if app.config.get("TESTING") or os.environ.get("PUSH_SINCRONO") == "1":
            _envia_lote(app, fila)
        else:
            threading.Thread(target=_envia_lote, args=(app, fila), daemon=True).start()

    @event.listens_for(Session, "after_rollback")
    def _descarta(sess):
        sess.info.pop("push_fila", None)

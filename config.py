# -*- coding: utf-8 -*-
"""Configuracao do Portal de Consolidacao J&F."""
import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
# Raiz dos dados. Local: a propria pasta do projeto. Producao (Render): aponte
# PORTAL_DATA_DIR para o disco persistente (ex.: /var/data) para o banco SQLite
# e os uploads NAO serem apagados a cada deploy.
DATA_ROOT = os.environ.get("PORTAL_DATA_DIR", BASE_DIR)
INSTANCE_DIR = os.path.join(DATA_ROOT, "instance")
UPLOAD_DIR = os.path.join(DATA_ROOT, "uploads")
DATA_DIR = os.path.join(DATA_ROOT, "data")
os.makedirs(INSTANCE_DIR, exist_ok=True)
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)


class Config:
    APP_NAME = "Gestão Controladoria J&F S.A."
    ORG = "J&F Investimentos"
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-consolidacao-jf-troque-em-producao")
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", "sqlite:///" + os.path.join(INSTANCE_DIR, "portal.db")
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    MAX_CONTENT_LENGTH = 64 * 1024 * 1024  # 64 MB por upload
    UPLOAD_DIR = UPLOAD_DIR
    DATA_DIR = DATA_DIR
    ALLOWED_EXTENSIONS = {".xlsm", ".xlsx"}

    # Template oficial de referencia (fonte do manifesto)
    OFFICIAL_TEMPLATE = os.environ.get(
        "OFFICIAL_TEMPLATE",
        os.path.join(
            os.path.dirname(BASE_DIR),
            "Consolidação Mensal",
            "Consolidação Conglomerado JF - Eldorado - Jun.2026.xlsm",
        ),
    )
    MANIFEST_PATH = os.path.join(DATA_DIR, "manifest.json")

    # Tolerancia default (relativa a ROL LTM da empresa, com piso absoluto em R$)
    DEFAULT_TOL_REL = 0.0001   # 0,01%
    DEFAULT_TOL_ABS = 1000.0   # R$ 1 mil
    # Regras de identidade/aditividade (arquivo deveria fechar por construcao)
    STRUCT_TOL_ABS = 1.0       # R$ 1 (praticamente zero)

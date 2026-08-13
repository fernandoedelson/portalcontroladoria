# -*- coding: utf-8 -*-
"""Popula o portal enxuto: admin, controladoria, empresas (entidades) e competencias.

Sem submissoes/validacao/intercompany (modulo de Consolidacao removido). Depois
repovoa o modulo de Gestao do Time (idempotente) e garante os paineis.
"""
from datetime import datetime

from app import app
from models import db, User, Company, Competency, set_setting
from engine.calendar_br import deadline_for_competency

# entidades do grupo (cadastro-base)
COMPANIES = [
    ("ELDORADO", "Eldorado Brasil Celulose"),
    ("AMBAR_GER", "Âmbar Geração"),
    ("AMBAR_DIS", "Âmbar Distribuição"),
    ("LHG", "LHG Mining"),
    ("FLORA", "Flora Higiene e Beleza"),
    ("MGAS", "MGás"),
    ("JF_SA", "J&F S.A. (Holding)"),
    ("JF_URB", "J&F Urbanismo"),
]

ADMIN_EMAIL = "admin@jfsa.com.br"
ADMIN_PASS = "JF@Gestao2026!"
CTRL_EMAIL = "controladoria@jfsa.com.br"
CTRL_PASS = "JF@Controla2026!"


def seed():
    with app.app_context():
        db.drop_all()
        db.create_all()

        # entidades
        for code, name in COMPANIES:
            db.session.add(Company(code=code, name=name, canonical_label=name,
                                   active=True))
        db.session.commit()

        # usuarios de gestao
        admin = User(email=ADMIN_EMAIL, display_name="Administrador do Portal",
                     role="admin", must_change_password=False, active=True)
        admin.set_password(ADMIN_PASS)
        ctrl = User(email=CTRL_EMAIL, display_name="Controladoria J&F",
                    role="controladoria", must_change_password=False, active=True)
        ctrl.set_password(CTRL_PASS)
        db.session.add_all([admin, ctrl])
        db.session.commit()

        # competencias de 2026: jan-jul fechadas, ago aberta (a atual)
        atual = None
        for mo in range(1, 9):
            dl = deadline_for_competency(2026, mo, nth=5)
            status = "fechada" if mo < 8 else "aberta"
            cy = Competency(year=2026, month=mo, deadline=dl, status=status,
                            closed_at=datetime.utcnow() if status == "fechada" else None)
            db.session.add(cy)
            if status == "aberta":
                atual = cy
        db.session.commit()

        # configuracoes base
        set_setting("closing_nth_bday", 5)
        set_setting("reminder_offsets", "-2,-1,0,1")
        if atual:
            set_setting("competencia_atual_id", atual.id)

        print("Seed (gestao) concluido.")
        print(f"  Entidades: {len(COMPANIES)}  Usuarios: {User.query.count()}  "
              f"Competencias: {Competency.query.count()}")
        print("\n=== CREDENCIAIS ===")
        print(f"  ADMIN         : {ADMIN_EMAIL} / {ADMIN_PASS}")
        print(f"  CONTROLADORIA : {CTRL_EMAIL} / {CTRL_PASS}")


def seed_all(with_team=True):
    """Reset + repovoamento do modulo de Gestao do Time.

    seed() faz db.drop_all(); como o app registra as tabelas do time no mesmo
    metadata, o seed do time e re-executado logo em seguida (idempotente, sem
    drop). Use seed_all() — nao chame seed() sozinho.
    """
    seed()
    if with_team:
        try:
            from team.seed_team import seed as seed_team
            seed_team()
        except Exception as e:
            print(f"\n[aviso] Nao foi possivel repovoar o modulo do time: {e}")
            print("        Rode manualmente: python -m team.seed_team")
    try:
        from team.models_workflow import ensure_panels_defaults
        with app.app_context():
            ensure_panels_defaults()
    except Exception as e:
        print(f"[aviso] Nao foi possivel criar os paineis: {e}")


if __name__ == "__main__":
    import sys
    seed_all(with_team="--sem-time" not in sys.argv)

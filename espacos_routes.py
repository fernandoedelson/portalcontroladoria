# -*- coding: utf-8 -*-
"""Administração dos espaços de trabalho (só o administrador do portal principal).

Aqui se cria, zera e remove espaços. Não há tela para abrir o conteúdo de um espaço: quem administra
o principal não enxerga os dados da outra área, assim como ela não enxerga os do time da Controladoria.
"""
import re
import unicodedata
from functools import wraps

from flask import render_template, request, redirect, url_for, flash, abort
from flask_login import login_required, current_user

import espacos
from models import log_audit


def _slugify(txt):
    t = unicodedata.normalize("NFD", (txt or "").lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    t = re.sub(r"[^a-z0-9]+", "-", t).strip("-")
    return t[:30]


def register_espacos_routes(app):

    def admin_do_principal(f):
        @wraps(f)
        @login_required
        def wrap(*a, **k):
            if espacos.atual() != espacos.PRINCIPAL or not current_user.is_admin:
                abort(403)
            return f(*a, **k)
        return wrap

    @app.route("/admin/espacos")
    @admin_do_principal
    def admin_espacos():
        return render_template("admin_espacos.html",
                               lista=[espacos.resumo(s) for s in espacos.slugs()])

    @app.route("/admin/espacos/criar", methods=["POST"])
    @admin_do_principal
    def admin_espacos_criar():
        nome = (request.form.get("nome") or "").strip()
        slug = _slugify(request.form.get("slug") or nome)
        ok, msg = espacos.criar(app, slug, nome, request.form.get("admin_nome"),
                                request.form.get("admin_email"), request.form.get("senha") or "")
        flash(f"Espaço “{nome or slug}” criado. O primeiro usuário já pode entrar pelo endereço de sempre."
              if ok else msg, "success" if ok else "warning")
        if ok:
            log_audit(current_user.id, "espaco_criado", "espaco", slug)
        return redirect(url_for("admin_espacos"))

    @app.route("/admin/espacos/<slug>/zerar", methods=["POST"])
    @admin_do_principal
    def admin_espacos_zerar(slug):
        if not espacos.existe(slug):
            abort(404)
        ok, msg = espacos.zerar(app, slug, request.form.get("admin_nome"),
                                request.form.get("admin_email"), request.form.get("senha") or "")
        flash("Espaço zerado: voltou a uma base vazia, com um único usuário." if ok else msg,
              "success" if ok else "warning")
        if ok:
            log_audit(current_user.id, "espaco_zerado", "espaco", slug)
        return redirect(url_for("admin_espacos"))

    @app.route("/admin/espacos/<slug>/remover", methods=["POST"])
    @admin_do_principal
    def admin_espacos_remover(slug):
        if not espacos.existe(slug):
            abort(404)
        if (request.form.get("confirma") or "").strip().lower() != slug:
            flash("Para remover, digite o nome interno do espaço na confirmação.", "warning")
            return redirect(url_for("admin_espacos"))
        espacos.remover(slug)
        log_audit(current_user.id, "espaco_removido", "espaco", slug)
        flash(f"Espaço {slug} removido.", "success")
        return redirect(url_for("admin_espacos"))

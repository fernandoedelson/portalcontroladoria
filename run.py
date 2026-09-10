# -*- coding: utf-8 -*-
"""Servidor do Portal de Gestao da Controladoria (waitress).

Local: escuta em 127.0.0.1:5050 por padrao.
Producao (Render): defina HOST=0.0.0.0 e a porta vem de $PORT.
"""
import os
from waitress import serve
from app import app

HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "5050"))

if __name__ == "__main__":
    print()
    print("  PORTAL DE GESTAO DA CONTROLADORIA")
    print(f"  Servindo em  http://{HOST}:{PORT}")
    print("  Para parar: Ctrl+C nesta janela.")
    print()
    serve(app, host=HOST, port=PORT, threads=8)

# -*- coding: utf-8 -*-
"""Servidor local do Portal de Consolidacao (waitress)."""
from waitress import serve
from app import app

HOST = "127.0.0.1"
PORT = 5050

if __name__ == "__main__":
    print()
    print("  PORTAL DE CONSOLIDACAO")
    print(f"  Abra no navegador:  http://{HOST}:{PORT}")
    print("  Para parar: Ctrl+C nesta janela.")
    print()
    serve(app, host=HOST, port=PORT, threads=8)

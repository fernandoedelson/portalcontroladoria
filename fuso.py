# -*- coding: utf-8 -*-
"""Fuso horário do portal: tudo em horário de Brasília.

O servidor (Render) roda em UTC. Sem isto, `date.today()` virava o dia às 21h
de Brasília (o painel mostrava sexta-feira na quinta à noite), os atrasos
viravam 3 horas antes e o agendador "das 8h" rodava às 5h.

- O código usa `fuso.hoje()` / `fuso.agora()` (nunca `date.today()` /
  `datetime.now()`), que calculam no fuso de Brasília qualquer que seja o
  relógio do servidor. `ativa()` ainda põe o processo no fuso (TZ + tzset)
  como rede de segurança para bibliotecas de terceiros.
- Os registros continuam gravados em UTC (`datetime.utcnow()`); para exibir,
  os templates usam o filtro `|local`.
"""
import datetime as _dt
import os
import time
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

NOME = os.environ.get("PORTAL_FUSO", "America/Sao_Paulo")
FUSO = ZoneInfo(NOME)

DIAS = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira",
        "sexta-feira", "sábado", "domingo"]
MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho",
         "agosto", "setembro", "outubro", "novembro", "dezembro"]


def ativa():
    """Processo no horário de Brasília — só em sistemas POSIX (Linux do Render).
    No Windows o C runtime não entende "America/Sao_Paulo" em TZ e passaria a
    usar UTC, o contrário do desejado; lá o relógio da máquina já é o local."""
    if hasattr(time, "tzset"):
        os.environ["TZ"] = NOME
        time.tzset()


def agora():
    """Data e hora de Brasília, sem fuso anexado (compara com as do banco local)."""
    return datetime.now(FUSO).replace(tzinfo=None)


def hoje():
    return datetime.now(FUSO).date()


def local(dt):
    """Registro gravado em UTC (sem fuso) -> horário de Brasília, para exibir."""
    if dt is None or not isinstance(dt, _dt.datetime):
        return dt
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(FUSO).replace(tzinfo=None)


def data_extenso(d):
    """quinta-feira, 10/09/2026"""
    if d is None:
        return ""
    if isinstance(d, _dt.datetime):
        d = d.date()
    return f"{DIAS[d.weekday()]}, {d.strftime('%d/%m/%Y')}"


def dia_mes(d):
    """quinta-feira, 10 de setembro"""
    if d is None:
        return ""
    if isinstance(d, _dt.datetime):
        d = d.date()
    return f"{DIAS[d.weekday()]}, {d.day} de {MESES[d.month - 1]}"


def registra(app):
    app.jinja_env.filters["local"] = local
    app.jinja_env.filters["data_extenso"] = data_extenso
    app.jinja_env.filters["dia_mes"] = dia_mes

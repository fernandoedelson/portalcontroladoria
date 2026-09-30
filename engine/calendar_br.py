# -*- coding: utf-8 -*-
"""Calendario de dias uteis (feriados nacionais BR) e prazos de fechamento."""
import fuso
from datetime import date, timedelta

try:
    import holidays as _holidays
    _HAS = True
except Exception:
    _HAS = False


def feriados_nacionais(year):
    """{data: nome} dos feriados nacionais do ano (sem ajustes)."""
    if _HAS:
        return dict(_holidays.Brazil(years=year).items())
    return {}


_CACHE = {}


def _ajustes(year):
    """(incluir, ignorar) cadastrados em Feriados; sem banco/contexto, vazio."""
    try:
        from team.models_workflow import Feriado
        linhas = Feriado.query.filter(Feriado.data >= date(year, 1, 1),
                                      Feriado.data <= date(year, 12, 31)).all()
    except Exception:
        return None
    return ({f.data for f in linhas if f.tipo == "incluir"},
            {f.data for f in linhas if f.tipo == "ignorar"})


def _br_holidays(year):
    """Feriados que valem para o calendário: nacionais, menos os ignorados, mais os
    incluídos (municipais, Carnaval...). Memoizado; `invalida()` após editar."""
    if year not in _CACHE:
        aj = _ajustes(year)
        if aj is None:                 # sem banco/contexto: só os nacionais, sem memoizar
            return set(feriados_nacionais(year))
        inc, ign = aj
        _CACHE[year] = (set(feriados_nacionais(year)) - ign) | inc
    return set(_CACHE[year])


def invalida():
    """Esquece o que foi memoizado (chamar depois de mudar os feriados)."""
    _CACHE.clear()
    try:
        from team import engine
        engine._year_holidays.cache_clear()
    except Exception:
        pass


def business_days(year, month):
    """Lista ordenada de dias uteis (seg-sex, exceto feriados nacionais) do mes."""
    hol = _br_holidays(year)
    d = date(year, month, 1)
    days = []
    while d.month == month:
        if d.weekday() < 5 and d not in hol:
            days.append(d)
        d += timedelta(days=1)
    return days


def nth_business_day(year, month, n):
    """N-esimo dia util do mes SEGUINTE a competencia (padrao J&F: 5o DU)."""
    days = business_days(year, month)
    if not days:
        return None
    idx = min(max(n, 1), len(days)) - 1
    return days[idx]


def deadline_for_competency(comp_year, comp_month, nth=5):
    """Prazo de envio de uma competencia = nth dia util do mes seguinte."""
    y, mo = (comp_year + 1, 1) if comp_month == 12 else (comp_year, comp_month + 1)
    return nth_business_day(y, mo, nth)


def days_until(target, ref=None):
    ref = ref or fuso.hoje()
    return (target - ref).days if target else None

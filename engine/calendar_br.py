# -*- coding: utf-8 -*-
"""Calendario de dias uteis (feriados nacionais BR) e prazos de fechamento."""
from datetime import date, timedelta

try:
    import holidays as _holidays
    _HAS = True
except Exception:
    _HAS = False


def _br_holidays(year):
    if _HAS:
        return set(_holidays.Brazil(years=year).keys())
    return set()


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
    ref = ref or date.today()
    return (target - ref).days if target else None

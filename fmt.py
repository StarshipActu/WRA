"""Mise en forme des dates : « Du 18 nov. 12:00 au 18 nov. 18:00 UTC »."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import config

UTC = timezone.utc
LOCAL = ZoneInfo(config.LOCAL_TZ)
MONTHS_FR = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."]


def _d(dt: datetime) -> str:
    return f"{dt.day} {MONTHS_FR[dt.month - 1]}"


def _hm(dt: datetime) -> str:
    return dt.strftime("%H:%M")


def fmt_range(start: datetime | None, end: datetime | None, tz=UTC, label: str | None = None) -> str:
    """Renvoie « Le 18 nov. de 12:00 à 18:00 UTC » ou « Du ... au ... UTC »."""
    if not start and not end:
        return "Dates non précisées"
    if start and not end:
        s = start.astimezone(tz)
        return f"À partir du {_d(s)} {_hm(s)} {label or s.tzname()}"
    if end and not start:
        e = end.astimezone(tz)
        return f"Jusqu'au {_d(e)} {_hm(e)} {label or e.tzname()}"
    s, e = start.astimezone(tz), end.astimezone(tz)
    lab = label or s.tzname()
    if s.date() == e.date():
        return f"Le {_d(s)} de {_hm(s)} à {_hm(e)} {lab}"
    return f"Du {_d(s)} {_hm(s)} au {_d(e)} {_hm(e)} {lab}"


def fmt_both(start: datetime | None, end: datetime | None) -> str:
    """UTC sur la première ligne, heure locale (Lyon/Paris) sur la seconde."""
    utc = fmt_range(start, end, UTC, "UTC")
    if not start and not end:
        return utc
    return f"{utc}\n{fmt_range(start, end, LOCAL)} (heure de Paris)"

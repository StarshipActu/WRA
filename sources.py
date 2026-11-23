"""Récupération et analyse des avis : TFR (FAA), NAVAREA IV (NGA), NOTAM (API FAA, optionnel)."""
from __future__ import annotations

import hashlib
import logging
import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import aiohttp

import config

log = logging.getLogger("sources")
UTC = timezone.utc
HEADERS = {"User-Agent": "StarshipActuBot/1.0"}
TIMEOUT = aiohttp.ClientTimeout(total=30)

# Fuseau utilisé quand un TFR est publié en heure « Local »
STATE_TZ = {
    "TX": "America/Chicago", "FL": "America/New_York", "VA": "America/New_York",
    "CA": "America/Los_Angeles", "NM": "America/Denver", "UT": "America/Denver",
    "WA": "America/Los_Angeles", "HI": "Pacific/Honolulu", "AK": "America/Anchorage",
    "GA": "America/New_York", "SC": "America/New_York", "NC": "America/New_York",
    "AL": "America/Chicago", "LA": "America/Chicago", "MS": "America/Chicago",
}


# --------------------------------------------------------------------------- modèles
@dataclass
class Zone:
    name: str
    geom: str                 # "Polygon" | "LineString" | "Point"
    coords: list              # coordonnées GeoJSON [lon, lat]
    altitude: str = ""
    start: datetime | None = None
    end: datetime | None = None


@dataclass
class Alert:
    kind: str                 # "TFR" | "NOTAM" | "NAVAREA"
    uid: str
    version: str              # empreinte : change quand l'avis est modifié
    title: str
    category: str = ""
    description: str = ""
    url: str = ""
    start: datetime | None = None
    end: datetime | None = None
    zones: list[Zone] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.uid}"


# --------------------------------------------------------------------------- géométrie
R_NM = 3440.065


def _dest(lat: float, lon: float, brg: float, dist_nm: float) -> tuple[float, float]:
    d, b = dist_nm / R_NM, math.radians(brg)
    p1, l1 = math.radians(lat), math.radians(lon)
    p2 = math.asin(math.sin(p1) * math.cos(d) + math.cos(p1) * math.sin(d) * math.cos(b))
    l2 = l1 + math.atan2(math.sin(b) * math.sin(d) * math.cos(p1), math.cos(d) - math.sin(p1) * math.sin(p2))
    return math.degrees(p2), (math.degrees(l2) + 540) % 360 - 180


def _bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2, dl = math.radians(lat1), math.radians(lat2), math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def circle_ring(lat: float, lon: float, r_nm: float, n: int = 72) -> list:
    pts = [_dest(lat, lon, i * 360 / n, r_nm) for i in range(n)]
    ring = [[lo, la] for la, lo in pts]
    ring.append(ring[0])
    return ring


def _close(ring: list) -> list:
    return ring if ring[0] == ring[-1] else ring + [ring[0]]


def _sha(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8", "ignore")).hexdigest()[:12]


# --------------------------------------------------------------------------- HTTP
async def _get_json(session: aiohttp.ClientSession, url: str, **kw):
    async with session.get(url, headers={**HEADERS, **kw.pop("headers", {})}, timeout=TIMEOUT, **kw) as r:
        r.raise_for_status()
        return await r.json(content_type=None)


async def _get_text(session: aiohttp.ClientSession, url: str) -> str:
    async with session.get(url, headers=HEADERS, timeout=TIMEOUT) as r:
        r.raise_for_status()
        return await r.text()


# =========================================================================== TFR
def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _iter(root, name):
    return (e for e in root.iter() if _local(e.tag) == name)


def _text(root, name) -> str | None:
    for e in _iter(root, name):
        if e.text and e.text.strip():
            return e.text.strip()
    return None


def _dd(s: str | None) -> float | None:
    if not s:
        return None
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([NSEW])\s*", s)
    if m:
        v = float(m[1])
        return -v if m[2] in "SW" else v
    try:
        return float(s)
    except ValueError:
        return None


def _alt(val, uom, code) -> str:
    if val in (None, ""):
        return ""
    try:
        v = float(val)
    except ValueError:
        return str(val)
    if (uom or "").upper() == "FL":
        return f"FL{int(v):03d}"
    if v == 0:
        return "SFC"
    s = f"{int(v):,} ft".replace(",", " ")
    return f"{s} {code}" if code in ("MSL", "AGL") else s


def _radius_nm(val, uom) -> float:
    r = float(val)
    u = (uom or "NM").upper()
    return r / 1.852 if u == "KM" else r / 1.15078 if u == "SM" else r


def _ring_from_avx(avx: list[dict]) -> list:
    """Construit l'anneau d'un polygone, en interpolant les arcs (CWA/CCA)."""
    pts: list[tuple[float, float]] = []
    n = len(avx)
    for i, a in enumerate(avx):
        pts.append((a["lat"], a["lon"]))
        if a["type"] in ("CWA", "CCA") and a.get("clat") is not None and a.get("r") and n > 1:
            nxt = avx[(i + 1) % n]
            b1 = _bearing(a["clat"], a["clon"], a["lat"], a["lon"])
            b2 = _bearing(a["clat"], a["clon"], nxt["lat"], nxt["lon"])
            sweep = (b2 - b1) % 360 if a["type"] == "CWA" else -((b1 - b2) % 360)
            steps = max(2, int(abs(sweep) / 5))
            for k in range(1, steps):
                pts.append(_dest(a["clat"], a["clon"], b1 + sweep * k / steps, a["r"]))
    return _close([[lo, la] for la, lo in pts])


def _tfr_dt(s: str | None, tzcode: str, state: str) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", ""))
    except ValueError:
        return None
    if (tzcode or "UTC").upper() in ("UTC", "Z", "GMT"):
        return dt.replace(tzinfo=UTC)
    tz = ZoneInfo(STATE_TZ.get(state.upper(), "UTC"))
    return dt.replace(tzinfo=tz).astimezone(UTC)


def parse_tfr_xml(xml_text: str, state: str = "") -> dict:
    root = ET.fromstring(xml_text)
    tzcode = _text(root, "codeTimeZone") or "UTC"
    start = _tfr_dt(_text(root, "dateEffective"), tzcode, state)
    end = _tfr_dt(_text(root, "dateExpire"), tzcode, state)
    zones: list[Zone] = []
    for i, group in enumerate(_iter(root, "TFRAreaGroup")):
        meta = next(_iter(group, "aseTFRArea"), group)
        merged = next(_iter(group, "abdMergedArea"), None)
        if merged is None:
            continue
        avx = []
        for e in _iter(merged, "Avx"):
            d = {_local(c.tag): (c.text or "").strip() for c in e}
            lat, lon = _dd(d.get("geoLat")), _dd(d.get("geoLong"))
            if lat is None or lon is None:
                continue
            item = {"type": d.get("codeType", ""), "lat": lat, "lon": lon,
                    "clat": _dd(d.get("geoLatArc")), "clon": _dd(d.get("geoLongArc")), "r": None}
            if d.get("valRadiusArc"):
                try:
                    item["r"] = _radius_nm(d["valRadiusArc"], d.get("uomRadiusArc"))
                except ValueError:
                    pass
            avx.append(item)
        if not avx:
            continue
        if len(avx) == 1 and avx[0]["r"] and avx[0]["clat"] is not None:
            a = avx[0]
            ring = circle_ring(a["clat"], a["clon"], a["r"])
        elif len(avx) >= 3:
            ring = _ring_from_avx(avx)
        else:
            continue
        z_start = _tfr_dt(_text(meta, "dateEffective"), tzcode, state) or start
        z_end = _tfr_dt(_text(meta, "dateExpire"), tzcode, state) or end
        low = _alt(_text(meta, "valDistVerLower"), _text(meta, "uomDistVerLower"), _text(meta, "codeDistVerLower"))
        up = _alt(_text(meta, "valDistVerUpper"), _text(meta, "uomDistVerUpper"), _text(meta, "codeDistVerUpper"))
        alt = f"{low or 'SFC'} → {up}" if up else ""
        zones.append(Zone(_text(meta, "txtName") or f"Zone {i + 1}", "Polygon", [ring], alt, z_start, z_end))
    return {"start": start, "end": end, "purpose": _text(root, "txtDescrPurpose") or "", "zones": zones}


async def _tfr_list(session: aiohttp.ClientSession) -> list[dict]:
    last: Exception | None = None
    for url in ("https://tfr.faa.gov/tfrapi/getTfrList", "https://tfr.faa.gov/tfrapi/exportTfrList"):
        try:
            data = await _get_json(session, url)
            if isinstance(data, list) and data:
                return data
        except Exception as e:  # noqa: BLE001
            last = e
            log.warning("Liste TFR indisponible (%s) : %s", url, e)
    if last:
        raise last
    return []


def _tfr_wanted(item: dict) -> bool:
    if config.TFR_TYPES and (item.get("type") or "").upper() not in config.TFR_TYPES:
        return False
    if config.TFR_STATES and (item.get("state") or "").upper() not in config.TFR_STATES:
        return False
    # SPACE OPERATIONS : toujours gardé ; les autres types doivent citer un lieu surveillé
    if (item.get("type") or "").upper() != "SPACE OPERATIONS" and config.TFR_PLACES:
        desc = (item.get("description") or "").upper()
        return any(p in desc for p in config.TFR_PLACES)
    return True


async def fetch_tfr(session, known: dict, light: bool = False) -> list[Alert]:
    out: list[Alert] = []
    for item in await _tfr_list(session):
        if not _tfr_wanted(item):
            continue
        nid = item.get("notam_id") or item.get("gid")
        if not nid:
            continue
        desc = item.get("description", "")
        version = str(item.get("mod_abs_time") or _sha(desc))
        if known.get(f"TFR:{nid}") == version:
            continue
        a_, _, b_ = nid.partition("/")
        alert = Alert(
            kind="TFR", uid=nid, version=version,
            title=f"TFR {nid} · {', '.join(desc.split(',')[:2]).strip()}",
            category=item.get("type", ""), description=desc,
            url=f"https://tfr.faa.gov/tfr3/?page=detail_{a_}_{b_}",
        )
        if not light:
            try:
                xml = await _get_text(session, f"https://tfr.faa.gov/save_pages/detail_{a_}_{b_}.xml")
                d = parse_tfr_xml(xml, item.get("state", ""))
                alert.zones, alert.start, alert.end = d["zones"], d["start"], d["end"]
                if d["purpose"]:
                    alert.description = f"{desc}\n{d['purpose']}"
            except Exception as e:  # noqa: BLE001
                log.warning("Détail TFR %s indisponible : %s", nid, e)
        out.append(alert)
    return out


# =========================================================================== NAVAREA IV
_MON = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
_TOK = re.compile(r"\b(\d{2})(\d{2})(\d{2})Z(?:\s+(" + "|".join(_MON) + r")(?:\s+(\d{2,4}))?)?")
_LAT = r"(\d{1,2})-(\d{2}(?:\.\d+)?)(?:-(\d{2}(?:\.\d+)?))?([NS])"
_LON = r"(\d{1,3})-(\d{2}(?:\.\d+)?)(?:-(\d{2}(?:\.\d+)?))?([EW])"
_COORD = re.compile(rf"\b{_LAT}\s+{_LON}\b")
_CIRCLE = re.compile(
    r"(?:WITHIN\s+(\d+(?:\.\d+)?)\s*(?:NM|NAUTICAL\s+MILES?|MILES?)|(\d+(?:\.\d+)?)\s*(?:NM|NAUTICAL\s+MILES?)\s+RADIUS)"
)


def _mk(day, hh, mm, mon, year):
    try:
        return datetime(year, mon, day, hh, mm, tzinfo=UTC)
    except ValueError:
        return None


def parse_navarea_times(text: str, issue: datetime) -> tuple[datetime | None, datetime | None]:
    body = re.split(r"\bCANCEL\b", text.upper())[0]  # « CANCEL THIS MSG ... » n'est pas la fin de la zone
    toks = list(_TOK.finditer(body))
    if len(toks) < 2:
        return None, None

    def parts(m):
        return int(m[1]), int(m[2]), int(m[3]), (_MON.index(m[4]) + 1 if m[4] else None), (int(m[5]) if m[5] else None)

    a, b = parts(toks[0]), parts(toks[-1])
    y = b[4] or a[4] or issue.year
    y = y + 2000 if y < 100 else y
    mb = b[3] or a[3] or issue.month
    ma = a[3] or (mb if a[0] <= b[0] else (mb - 1 or 12))
    ya = y - 1 if ma > mb else y
    return _mk(a[0], a[1], a[2], ma, ya), _mk(b[0], b[1], b[2], mb, y)


def _points(chunk: str) -> list[tuple[float, float]]:
    pts = []
    for m in _COORD.finditer(chunk):
        lat = int(m[1]) + float(m[2]) / 60 + (float(m[3]) / 3600 if m[3] else 0)
        lon = int(m[5]) + float(m[6]) / 60 + (float(m[7]) / 3600 if m[7] else 0)
        pts.append((-lat if m[4] == "S" else lat, -lon if m[8] == "W" else lon))
    return pts


def _zone_from_points(name: str, pts: list, chunk: str, start, end) -> Zone | None:
    circ = _CIRCLE.search(chunk)
    if circ and len(pts) == 1:
        r = float(circ[1] or circ[2])
        return Zone(name, "Polygon", [circle_ring(pts[0][0], pts[0][1], r)], f"rayon {r:g} NM", start, end)
    if len(pts) >= 3:
        return Zone(name, "Polygon", [_close([[lo, la] for la, lo in pts])], "", start, end)
    if len(pts) == 2:
        return Zone(name, "LineString", [[lo, la] for la, lo in pts], "", start, end)
    if len(pts) == 1:
        return Zone(name, "Point", [pts[0][1], pts[0][0]], "", start, end)
    return None


def parse_navarea_zones(text: str, start, end) -> list[Zone]:
    t = text.upper().replace("\r", "")
    parts = re.split(r"(?m)^\s*([A-Z])\.\s", t)
    zones: list[Zone] = []
    if len(parts) > 1:
        for letter, chunk in zip(parts[1::2], parts[2::2]):
            z = _zone_from_points(f"Zone {letter}", _points(chunk), chunk, start, end)
            if z:
                zones.append(z)
    if not zones:
        z = _zone_from_points("Zone", _points(t), t, start, end)
        if z:
            zones.append(z)
    return zones


async def fetch_navarea(session, known: dict, light: bool = False) -> list[Alert]:
    params = {"navArea": "4", "status": "active", "output": "json"}
    data = None
    for base in ("https://msi.gs.mil/api/publications/broadcast-warn", "https://msi.nga.mil/api/publications/broadcast-warn"):
        try:
            data = await _get_json(session, base, params=params)
            break
        except Exception as e:  # noqa: BLE001
            log.warning("NAVAREA indisponible (%s) : %s", base, e)
    if not data:
        return []
    out: list[Alert] = []
    for w in data.get("broadcast-warn", []):
        text = w.get("text") or ""
        up = text.upper()
        if not config.NAVAREA_ALL and not any(k in up for k in config.NAVAREA_KEYWORDS):
            continue
        num, year = w.get("msgNumber"), w.get("msgYear")
        uid = f"{num}/{str(year)[-2:]}"
        version = _sha(text)
        if known.get(f"NAVAREA:{uid}") == version:
            continue
        issue_m = _TOK.search((w.get("issueDate") or "").upper())
        issue = datetime.now(UTC)
        if issue_m:
            mon = _MON.index(issue_m[4]) + 1 if issue_m[4] else issue.month
            y = int(issue_m[5]) if issue_m[5] else issue.year
            issue = _mk(int(issue_m[1]), int(issue_m[2]), int(issue_m[3]), mon, y + 2000 if y < 100 else y) or issue
        start, end = parse_navarea_times(text, issue)
        alert = Alert(
            kind="NAVAREA", uid=uid, version=version,
            title=f"NAVAREA IV {uid}" + (f" · sous-zone {w['subregion']}" if w.get("subregion") else ""),
            category="NOTMAR / NAVAREA IV", description=text.strip(),
            url="https://msi.nga.mil/NavWarnings", start=start, end=end,
        )
        if not light:
            alert.zones = parse_navarea_zones(text, start, end)
        out.append(alert)
    return out


# =========================================================================== NOTAM (optionnel)
_NOTAM_COORD = re.compile(r"(\d{2})(\d{2})(\d{2})?([NS])\s*(\d{3})(\d{2})(\d{2})?([EW])")


def _iso(s) -> datetime | None:
    if not s or str(s).upper().startswith("PERM"):
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


async def fetch_notam(session, known: dict, light: bool = False) -> list[Alert]:
    """NOTAM classiques via l'API FAA (clés requises). Désactivé sans FAA_CLIENT_ID/SECRET."""
    if not (config.FAA_CLIENT_ID and config.FAA_CLIENT_SECRET):
        return []
    out: dict[str, Alert] = {}
    for name, lat, lon, rad in config.WATCH_POINTS:
        params = {"locationLatitude": lat, "locationLongitude": lon, "locationRadius": rad, "pageSize": 100}
        headers = {"client_id": config.FAA_CLIENT_ID, "client_secret": config.FAA_CLIENT_SECRET}
        try:
            data = await _get_json(session, "https://external-api.faa.gov/notamapi/v1/notams", params=params, headers=headers)
        except Exception as e:  # noqa: BLE001
            log.warning("API NOTAM FAA indisponible (%s) : %s", name, e)
            continue
        for it in data.get("items", []):
            n = (it.get("properties", {}).get("coreNOTAMData", {}) or {}).get("notam", {}) or {}
            text = n.get("text") or ""
            if not any(k in text.upper() for k in config.NOTAM_KEYWORDS):
                continue
            uid = n.get("id") or n.get("number")
            if not uid or uid in out:
                continue
            start, end = _iso(n.get("effectiveStart")), _iso(n.get("effectiveEnd"))
            version = _sha(text + str(n.get("effectiveStart")) + str(n.get("effectiveEnd")))
            if known.get(f"NOTAM:{uid}") == version:
                continue
            alert = Alert("NOTAM", uid, version, f"NOTAM {n.get('number', uid)} · {n.get('location', name)}",
                          n.get("classification", ""), text.strip(), "https://notams.aim.faa.gov/notamSearch/",
                          start, end)
            if not light:
                geom = it.get("geometry") or {}
                gtype = geom.get("type")
                if gtype in ("Polygon", "LineString", "Point"):
                    alert.zones = [Zone("Zone NOTAM", gtype, geom["coordinates"], "", start, end)]
                else:
                    pts = [((int(m[1]) + int(m[2]) / 60 + (int(m[3]) / 3600 if m[3] else 0)) * (-1 if m[4] == "S" else 1),
                            (int(m[5]) + int(m[6]) / 60 + (int(m[7]) / 3600 if m[7] else 0)) * (-1 if m[8] == "W" else 1))
                           for m in _NOTAM_COORD.finditer(text.upper())]
                    z = _zone_from_points("Zone NOTAM", pts, text.upper(), start, end)
                    alert.zones = [z] if z else []
            out[uid] = alert
    return list(out.values())

"""Configuration centralisée (variables d'environnement / fichier .env)."""
import os
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # python-dotenv est optionnel
    pass


def _int(name: str, default: int = 0) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except ValueError:
        return default


def _csv(name: str, default: list[str]) -> list[str]:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return list(default)          # variable absente ou vide -> valeur par défaut
    if raw.strip() == "*":
        return []                     # "*" = pas de filtre (tout garder)
    return [x.strip().upper() for x in raw.split(",") if x.strip()]


# --- Discord -----------------------------------------------------------------
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")
_default_channel = _int("CHANNEL_ID")
CHANNELS = {
    "TFR": _int("CHANNEL_TFR") or _default_channel,
    "NOTAM": _int("CHANNEL_NOTAM") or _default_channel,
    "NAVAREA": _int("CHANNEL_NAVAREA") or _default_channel,
}

# --- Fonctionnement ----------------------------------------------------------
POLL_MINUTES = max(2, _int("POLL_MINUTES", 5))
# 1 = poste aussi tout ce qui est déjà actif au tout premier lancement
POST_EXISTING = os.getenv("POST_EXISTING_ON_FIRST_RUN", "0") == "1"
STATE_PATH = Path(os.getenv("STATE_FILE") or "state.json")
LOCAL_TZ = os.getenv("LOCAL_TZ") or "Europe/Paris"

# --- Filtres -----------------------------------------------------------------
# TFR : types FAA et états retenus (mettre * pour tout garder)
TFR_TYPES = _csv("TFR_TYPES", ["SPACE OPERATIONS", "HAZARDS"])
TFR_STATES = _csv("TFR_STATES", ["TX", "FL"])
# Les TFR HAZARDS ne sont gardés que si leur description contient un de ces lieux
# (le TFR 6/1666 de Starbase est classé HAZARDS, pas SPACE OPERATIONS). * = tous.
TFR_PLACES = _csv(
    "TFR_PLACES",
    ["BROWNSVILLE", "SOUTH PADRE", "BOCA CHICA", "PORT ISABEL", "CAPE CANAVERAL", "TITUSVILLE",
     "COCOA", "MERRITT ISLAND", "KENNEDY", "ORLANDO"],
)
# NAVAREA IV : mots-clés (NAVAREA_ALL=1 pour tout poster)
NAVAREA_ALL = os.getenv("NAVAREA_ALL", "0") == "1"
NAVAREA_KEYWORDS = _csv(
    "NAVAREA_KEYWORDS",
    ["ROCKET", "LAUNCH", "SPACE", "DEBRIS", "REENTRY", "RE-ENTRY", "SPACEX", "STARSHIP", "SPLASHDOWN"],
)
# NOTAM classiques (API FAA, clés gratuites sur https://api.faa.gov)
FAA_CLIENT_ID = os.getenv("FAA_CLIENT_ID", "")
FAA_CLIENT_SECRET = os.getenv("FAA_CLIENT_SECRET", "")
NOTAM_KEYWORDS = _csv(
    "NOTAM_KEYWORDS",
    ["ROCKET", "SPACE", "LAUNCH", "REENTRY", "DEBRIS", "SPACEX", "STARSHIP", "HAZARD"],
)

# (nom, latitude, longitude, rayon de recherche en NM) pour les NOTAM classiques
WATCH_POINTS = [
    ("Starbase", 25.9972, -97.1560, 40),
    ("Cap Canaveral / KSC", 28.5200, -80.6800, 40),
]
# Repères affichés sur la carte quand ils sont dans le cadre
REF_POINTS = [(n, lat, lon) for n, lat, lon, _ in WATCH_POINTS]

# --- Carte -------------------------------------------------------------------
# Valeurs possibles pour MAP_STYLE / MAP_STYLE_<TYPE> :
#   satellite       -> imagerie USGS (NAIP), domaine public, couvre les États-Unis (Texas, Floride)
#   satellite-esri  -> Esri World Imagery, couverture mondiale (vérifier les conditions d'utilisation d'Esri)
#   liberty | bright | positron | fiord -> styles vectoriels OpenFreeMap (fiord = sombre)
#   une URL de style MapLibre (https://...)
SATELLITE_SOURCES = {
    "satellite": {
        "tiles": "https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryOnly/MapServer/tile/{z}/{y}/{x}",
        "attribution": "Imagery: USGS The National Map, USDA NAIP",
        "maxzoom": 17,
    },
    "satellite-esri": {
        "tiles": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        "attribution": "Imagery: Esri, Maxar, Earthstar Geographics and the GIS User Community",
        "maxzoom": 17,
    },
}


def resolve_style(name: str):
    """Renvoie une URL de style ou un objet de style MapLibre (raster satellite)."""
    if name in SATELLITE_SOURCES:
        s = SATELLITE_SOURCES[name]
        return {
            "version": 8,
            "sources": {"sat": {"type": "raster", "tiles": [s["tiles"]], "tileSize": 256,
                                "maxzoom": s["maxzoom"], "attribution": s["attribution"]}},
            "layers": [{"id": "sat", "type": "raster", "source": "sat"}],
        }
    return name if name.startswith("http") else f"https://tiles.openfreemap.org/styles/{name}"


_base_style = os.getenv("MAP_STYLE") or "liberty"
MAP_STYLES = {
    "TFR": resolve_style(os.getenv("MAP_STYLE_TFR") or _base_style),
    "NOTAM": resolve_style(os.getenv("MAP_STYLE_NOTAM") or _base_style),
    # Si vous passez MAP_STYLE=satellite (USGS, États-Unis seulement), gardez un fond vectoriel
    # pour les zones maritimes, souvent hors couverture : MAP_STYLE_NAVAREA=liberty
    "NAVAREA": resolve_style(os.getenv("MAP_STYLE_NAVAREA") or (_base_style if _base_style not in SATELLITE_SOURCES else "liberty")),
}
MAP_WIDTH = _int("MAP_WIDTH", 1280)
MAP_HEIGHT = _int("MAP_HEIGHT", 720)

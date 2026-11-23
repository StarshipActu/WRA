"""Génère une carte PNG (fond OpenFreeMap + zones d'exclusion + légende datée) pour un avis."""
from __future__ import annotations

import json
import logging

from playwright.async_api import async_playwright

import config
from fmt import LOCAL, UTC, fmt_range
from sources import Alert

log = logging.getLogger("mapper")

PALETTE = {
    "TFR": ["#E24B4A", "#F0997B", "#D4537E", "#993C1D"],
    "NOTAM": ["#EF9F27", "#FAC775", "#BA7517", "#854F0B"],
    "NAVAREA": ["#7F77DD", "#AFA9EC", "#534AB7", "#3C3489"],
}
KIND_LABEL = {"TFR": "TFR", "NOTAM": "NOTAM", "NAVAREA": "NOTMAR · NAVAREA IV"}


def _all_points(zones) -> list[list[float]]:
    pts: list[list[float]] = []
    for z in zones:
        if z.geom == "Polygon":
            pts += z.coords[0]
        elif z.geom == "LineString":
            pts += z.coords
        else:
            pts.append(z.coords)
    return pts


def build_payload(alert: Alert) -> dict:
    colors = PALETTE[alert.kind]
    features, legend = [], []
    for i, z in enumerate(alert.zones):
        color = colors[i % len(colors)]
        features.append({
            "type": "Feature",
            "properties": {"color": color, "name": z.name},
            "geometry": {"type": z.geom, "coordinates": z.coords},
        })
        s, e = z.start or alert.start, z.end or alert.end
        dates = fmt_range(s, e, UTC, "UTC")
        if s or e:
            dates += f"  ·  {fmt_range(s, e, LOCAL)}"
        legend.append({"color": color, "name": z.name, "alt": z.altitude, "dates": dates})

    pts = _all_points(alert.zones)
    lons, lats = [p[0] for p in pts], [p[1] for p in pts]
    w, s_, e, n = min(lons), min(lats), max(lons), max(lats)
    if e - w < 0.05:
        w, e = w - 0.1, e + 0.1
    if n - s_ < 0.05:
        s_, n = s_ - 0.1, n + 0.1

    pad = 0.5 * max(e - w, n - s_)
    refs = [
        {"name": name, "lat": lat, "lon": lon}
        for name, lat, lon in config.REF_POINTS
        if w - pad <= lon <= e + pad and s_ - pad <= lat <= n + pad
    ]
    subtitle = alert.description.split("\n")[0][:140]
    return {
        "style": config.MAP_STYLES[alert.kind],
        "kind": alert.kind,
        "kindLabel": KIND_LABEL[alert.kind],
        "title": alert.title,
        "subtitle": subtitle,
        "features": features,
        "legend": legend,
        "refs": refs,
        "bounds": [[w, s_], [e, n]],
        "padBottom": 70 + 52 * len(legend),
        "badgeColor": colors[0],
    }


HTML = """<!doctype html><html><head><meta charset="utf-8">
<link href="https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.css" rel="stylesheet">
<script src="https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.js"></script>
<style>
html,body{margin:0;height:100%;font-family:Inter,Segoe UI,Roboto,Arial,sans-serif;background:#222}
#map{position:absolute;inset:0}
#head{position:absolute;top:16px;left:16px;right:16px;color:#fff;background:rgba(15,20,28,.82);padding:10px 14px;border-radius:10px}
.badge{display:inline-block;font-size:13px;font-weight:600;padding:2px 9px;border-radius:5px;margin-right:10px;color:#111}
.ttl{font-size:20px;font-weight:600}.sub{font-size:13px;opacity:.8;margin-top:3px}
#legend{position:absolute;left:16px;bottom:30px;max-width:78%;color:#fff;background:rgba(15,20,28,.86);padding:10px 14px;border-radius:10px}
.row{display:flex;gap:10px;align-items:flex-start;margin:5px 0}
.sw{width:16px;height:16px;border-radius:4px;margin-top:2px;flex:none;border:1.5px solid #fff}
.nm{font-size:14px;font-weight:600}.dt{font-size:13px;opacity:.85}
.ref{display:flex;flex-direction:column;align-items:center;color:#fff;font-size:12px;font-weight:600;text-shadow:0 0 3px #000,0 0 3px #000}
.ref i{width:10px;height:10px;border-radius:50%;background:#fff;border:2px solid #111}
.maplibregl-ctrl-attrib{font-size:12px}
</style></head><body><div id="map"></div><div id="head"></div><div id="legend"></div>
<script>
const D = __DATA__;
const el=(t,c,x)=>{const e=document.createElement(t);if(c)e.className=c;if(x!==undefined)e.textContent=x;return e;};
const badge=el('span','badge',D.kindLabel);badge.style.background=D.badgeColor;
const head=document.getElementById('head');head.append(badge,el('span','ttl',D.title));
if(D.subtitle)head.append(el('div','sub',D.subtitle));
const lg=document.getElementById('legend');
D.legend.forEach(r=>{const row=el('div','row');const sw=el('span','sw');sw.style.background=r.color;row.append(sw);
const t=el('div','');t.append(el('div','nm',r.name+(r.alt?'  ·  '+r.alt:'')),el('div','dt',r.dates));row.append(t);lg.append(row);});
const map=new maplibregl.Map({container:'map',style:D.style,bounds:D.bounds,
 fitBoundsOptions:{padding:{top:110,bottom:D.padBottom,left:60,right:60},maxZoom:14},
 attributionControl:{compact:false},fadeDuration:0,interactive:false});
map.on('error',e=>console.log('maperr',e&&e.error&&e.error.message));
map.on('load',()=>{
 map.addSource('z',{type:'geojson',data:{type:'FeatureCollection',features:D.features}});
 map.addLayer({id:'fill',type:'fill',source:'z',filter:['==',['geometry-type'],'Polygon'],paint:{'fill-color':['get','color'],'fill-opacity':0.38}});
 map.addLayer({id:'line',type:'line',source:'z',filter:['!=',['geometry-type'],'Point'],paint:{'line-color':['get','color'],'line-width':3}});
 map.addLayer({id:'pt',type:'circle',source:'z',filter:['==',['geometry-type'],'Point'],paint:{'circle-radius':8,'circle-color':['get','color'],'circle-stroke-color':'#fff','circle-stroke-width':2}});
 D.refs.forEach(r=>{const m=el('div','ref');m.append(el('i'),el('span','',r.name));new maplibregl.Marker({element:m}).setLngLat([r.lon,r.lat]).addTo(map);});
 map.once('idle',()=>{window.__ready=true;});
});
</script></body></html>"""


class MapRenderer:
    def __init__(self) -> None:
        self._pw = None
        self._browser = None

    async def start(self) -> None:
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(args=[
            "--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader",
            "--ignore-gpu-blocklist", "--enable-webgl",
        ])

    async def close(self) -> None:
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()

    async def render(self, alert: Alert) -> bytes:
        html = HTML.replace("__DATA__", json.dumps(build_payload(alert)).replace("</", "<\\/"))
        page = await self._browser.new_page(viewport={"width": config.MAP_WIDTH, "height": config.MAP_HEIGHT})
        try:
            page.on("console", lambda m: log.debug("browser: %s", m.text))
            await page.set_content(html, wait_until="load")
            await page.wait_for_function("window.__ready === true", timeout=45_000)
            await page.wait_for_timeout(600)
            return await page.screenshot(type="png")
        finally:
            await page.close()

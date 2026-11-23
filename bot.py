"""Bot Discord StarshipActu : une nouvelle carte à chaque nouveau TFR / NOTAM / NOTMAR."""
from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import sys
from datetime import datetime, timezone

import aiohttp
import discord
from discord.ext import tasks

import config
import sources
from fmt import fmt_both, fmt_range, UTC
from mapper import MapRenderer

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("bot")

COLORS = {"TFR": 0xE24B4A, "NOTAM": 0xEF9F27, "NAVAREA": 0x7F77DD}
SOURCES = {"TFR": "FAA · tfr.faa.gov", "NOTAM": "FAA · NOTAM API", "NAVAREA": "NGA · msi.nga.mil (NAVAREA IV)"}


def load_state() -> dict:
    try:
        return json.loads(config.STATE_PATH.read_text("utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state: dict) -> None:
    tmp = config.STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1), "utf-8")
    os.replace(tmp, config.STATE_PATH)


def build_embed(alert: sources.Alert, updated: bool) -> discord.Embed:
    prefix = "Mise à jour" if updated else "Nouveau"
    e = discord.Embed(
        title=f"{prefix} · {alert.title}"[:256],
        url=alert.url or None,
        color=COLORS[alert.kind],
        timestamp=datetime.now(timezone.utc),
    )
    if alert.category:
        e.set_author(name=alert.category)
    if alert.description:
        text = alert.description.strip()
        e.description = (f"```{text[:1500]}```" if alert.kind == "NAVAREA" else text[:1500])
    e.add_field(name="Période", value=fmt_both(alert.start, alert.end)[:1024], inline=False)
    if alert.zones:
        lines = []
        for z in alert.zones:
            line = f"• **{z.name}**" + (f" — {z.altitude}" if z.altitude else "")
            if (z.start, z.end) != (alert.start, alert.end) and (z.start or z.end):
                line += f"\n  {fmt_range(z.start, z.end, UTC, 'UTC')}"
            lines.append(line)
        e.add_field(name="Zones d'exclusion", value="\n".join(lines)[:1024], inline=False)
    else:
        e.add_field(name="Zones d'exclusion", value="Géométrie non disponible pour cet avis.", inline=False)
    e.set_footer(text=SOURCES[alert.kind])
    return e


class StarshipBot(discord.Client):
    def __init__(self, once: bool = False) -> None:
        super().__init__(intents=discord.Intents.default())
        self.once = once
        self.first_run = not config.STATE_PATH.exists()
        self.state = load_state()
        self.renderer = MapRenderer()
        self.session: aiohttp.ClientSession | None = None

    async def setup_hook(self) -> None:
        self.session = aiohttp.ClientSession()
        await self.renderer.start()
        if not self.once:
            self.poll.start()

    async def close(self) -> None:
        await self.renderer.close()
        if self.session:
            await self.session.close()
        await super().close()

    async def on_ready(self) -> None:
        log.info("Connecté en tant que %s", self.user)

    @tasks.loop(minutes=config.POLL_MINUTES)
    async def poll(self) -> None:
        await self.poll_once()

    async def poll_once(self) -> None:
        light = self.first_run and not config.POST_EXISTING
        alerts: list[sources.Alert] = []
        for fetch in (sources.fetch_tfr, sources.fetch_navarea, sources.fetch_notam):
            try:
                alerts += await fetch(self.session, self.state, light=light)
            except Exception:  # noqa: BLE001
                log.exception("Source en échec : %s", fetch.__name__)

        for alert in alerts:
            was_known = alert.key in self.state
            if not light:
                try:
                    await self.publish(alert, updated=was_known)
                except Exception:  # noqa: BLE001
                    log.exception("Envoi impossible pour %s (nouvel essai au prochain cycle)", alert.key)
                    continue
                await asyncio.sleep(2)
            self.state[alert.key] = alert.version
            save_state(self.state)

        if self.first_run:
            save_state(self.state)
            self.first_run = False
            log.info("Premier lancement : %d avis mémorisés sans publication.", len(alerts))

    @poll.before_loop
    async def _before_poll(self) -> None:
        await self.wait_until_ready()

    async def publish(self, alert: sources.Alert, updated: bool) -> None:
        channel_id = config.CHANNELS[alert.kind]
        channel = self.get_channel(channel_id) or await self.fetch_channel(channel_id)
        embed = build_embed(alert, updated)
        file = None
        if alert.zones:
            try:
                png = await self.renderer.render(alert)
                file = discord.File(io.BytesIO(png), filename="carte.png")
                embed.set_image(url="attachment://carte.png")
            except Exception:  # noqa: BLE001
                log.exception("Rendu de la carte impossible pour %s", alert.key)
        await channel.send(embed=embed, file=file)
        log.info("Publié : %s", alert.key)


async def run_once() -> None:
    """Un seul cycle (GitHub Actions) : connexion REST, vérification, publication, arrêt."""
    bot = StarshipBot(once=True)
    async with bot:
        await bot.login(config.DISCORD_TOKEN)
        await bot.poll_once()


def main() -> None:
    if not config.DISCORD_TOKEN:
        raise SystemExit("DISCORD_TOKEN manquant (voir .env.example)")
    if not any(config.CHANNELS.values()):
        raise SystemExit("Aucun salon configuré (CHANNEL_ID ou CHANNEL_TFR / CHANNEL_NOTAM / CHANNEL_NAVAREA)")
    if "--once" in sys.argv:
        asyncio.run(run_once())
        return
    StarshipBot().run(config.DISCORD_TOKEN, log_handler=None)


if __name__ == "__main__":
    main()

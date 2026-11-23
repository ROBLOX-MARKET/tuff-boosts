"""
Crypto Swaps — a non-custodial crypto swap bot for Discord.

How it stays non-custodial:
  * Every swap is created on ChangeNOW with the *user's own* wallet as the payout.
  * ChangeNOW gives a deposit address; the user sends their coin there directly.
  * ChangeNOW sends the swapped coin straight to the user's wallet.
  * This bot NEVER receives, holds, routes, or can touch anyone's funds.
    It only shows the swap details and tracks public stats.

All secrets come from environment variables (set them in your host panel, never in code):
  DISCORD_TOKEN      - your bot token (reset it if it ever leaks)
  CHANGENOW_API_KEY  - your ChangeNOW API key (get one free at changenow.io)
  GUILD_ID           - (optional) your server ID, for instant slash-command sync
"""

import asyncio
import json
import logging
import os
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
import discord
from discord import app_commands
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger("crypto-swaps")

TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
CN_KEY = os.getenv("CHANGENOW_API_KEY", "").strip()
GUILD_ID = os.getenv("GUILD_ID", "").strip()

if not TOKEN or TOKEN.startswith("PASTE"):
    raise SystemExit("Set DISCORD_TOKEN in your environment variables.")

BLUE = 0x19E0FF            # light-blue theme accent
STATS_FILE = BASE_DIR / "swaps.json"
CN_BASE = "https://api.changenow.io/v2"

# Coins offered. `cn` = ChangeNOW currency ticker, `cg` = CoinGecko id (for USD value).
# USDT defaults to TRC20 (cheap fees); change to "usdterc20" etc. if you prefer.
COINS = {
    "USDT": {"label": "USDT", "emoji": "\U0001FA99", "cn": "usdttrc20", "cg": "tether"},
    "BTC":  {"label": "BTC",  "emoji": "₿",      "cn": "btc",       "cg": "bitcoin"},
    "LTC":  {"label": "LTC",  "emoji": "Ł",      "cn": "ltc",       "cg": "litecoin"},
    "SOL":  {"label": "SOL",  "emoji": "◎",      "cn": "sol",       "cg": "solana"},
    "ETH":  {"label": "ETH",  "emoji": "Ξ",      "cn": "eth",       "cg": "ethereum"},
}


# ===========================================================================
# Stats storage (public, no funds — just counts/volume for the /swapstats panel)
# ===========================================================================

def load_stats() -> dict:
    try:
        return json.loads(STATS_FILE.read_text("utf-8"))
    except Exception:
        return {"count": 0, "volume_usd": 0.0, "biggest": None, "latest": None}


def save_stats(data: dict) -> None:
    tmp = STATS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), "utf-8")
    tmp.replace(STATS_FILE)


def record_swap(frm: str, to: str, amount: float, usd: float, user: str) -> dict:
    s = load_stats()
    s["count"] = int(s.get("count", 0)) + 1
    s["volume_usd"] = float(s.get("volume_usd", 0.0)) + (usd or 0.0)
    entry = {
        "from": frm, "to": to, "amount": amount, "usd": usd, "user": user,
        "at": datetime.now(timezone.utc).isoformat(),
    }
    s["latest"] = entry
    if not s.get("biggest") or (usd or 0) > (s["biggest"].get("usd") or 0):
        s["biggest"] = entry
    save_stats(s)
    return s


# ===========================================================================
# ChangeNOW (non-custodial swap provider) + CoinGecko (USD value)
# ===========================================================================

def cn_headers() -> dict:
    return {"x-changenow-api-key": CN_KEY, "Content-Type": "application/json"}


async def cn_estimate(session: aiohttp.ClientSession, frm: str, to: str, amount: float):
    """Return estimated receive amount, or None."""
    params = {
        "fromCurrency": frm, "toCurrency": to, "fromAmount": str(amount),
        "flow": "standard", "type": "direct",
    }
    url = f"{CN_BASE}/exchange/estimated-amount?" + urllib.parse.urlencode(params)
    async with session.get(url, headers=cn_headers()) as r:
        if r.status != 200:
            log.warning("estimate %s: %s", r.status, await r.text())
            return None
        data = await r.json()
        return data.get("toAmount")


async def cn_min(session: aiohttp.ClientSession, frm: str, to: str):
    params = {"fromCurrency": frm, "toCurrency": to, "flow": "standard", "type": "direct"}
    url = f"{CN_BASE}/exchange/min-amount?" + urllib.parse.urlencode(params)
    async with session.get(url, headers=cn_headers()) as r:
        if r.status != 200:
            return None
        return (await r.json()).get("minAmount")


async def cn_create(session, frm, to, amount, payout_address):
    """Create the exchange. Payout goes to the USER's address — never the bot's."""
    body = {
        "fromCurrency": frm, "toCurrency": to, "fromAmount": str(amount),
        "address": payout_address, "flow": "standard", "type": "direct",
    }
    url = f"{CN_BASE}/exchange"
    async with session.post(url, headers=cn_headers(), json=body) as r:
        text = await r.text()
        if r.status not in (200, 201):
            log.warning("create %s: %s", r.status, text)
            return None, text
        return json.loads(text), None


async def usd_value(session: aiohttp.ClientSession, coin_key: str, amount: float):
    """Best-effort USD value of `amount` of a coin, via CoinGecko. None if unavailable."""
    cg = COINS[coin_key]["cg"]
    url = f"https://api.coingecko.com/api/v3/simple/price?ids={cg}&vs_currencies=usd"
    try:
        async with session.get(url) as r:
            if r.status != 200:
                return None
            price = (await r.json()).get(cg, {}).get("usd")
            return round(float(price) * float(amount), 2) if price else None
    except Exception:
        return None


def qr_url(data: str) -> str:
    return "https://api.qrserver.com/v1/create-qr-code/?size=240x240&margin=10&data=" + \
        urllib.parse.quote(data)


# ===========================================================================
# Discord UI
# ===========================================================================

intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)


def coin_options(exclude: str | None = None):
    opts = []
    for key, c in COINS.items():
        if key == exclude:
            continue
        opts.append(discord.SelectOption(label=c["label"], value=key, emoji=None,
                                         description=f"Swap {'from' if exclude is None else 'to'} {c['label']}"))
    return opts


class SwapFlow(discord.ui.View):
    """Ephemeral, per-user. Pick from-coin, to-coin, then enter amount + payout address."""

    def __init__(self, author_id: int):
        super().__init__(timeout=300)
        self.author_id = author_id
        self.frm: str | None = None
        self.to: str | None = None
        self.add_item(self.FromSelect(self))
        self.add_item(self.ToSelect(self))
        self.enter = self.EnterButton(self)
        self.add_item(self.enter)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("This swap isn't yours — run `/cryptoswaps` to start your own.", ephemeral=True)
            return False
        return True

    class FromSelect(discord.ui.Select):
        def __init__(self, parent):
            self.parent_view = parent
            super().__init__(placeholder="Swap FROM…", options=coin_options(), row=0)

        async def callback(self, interaction: discord.Interaction):
            self.parent_view.frm = self.values[0]
            await interaction.response.defer()

    class ToSelect(discord.ui.Select):
        def __init__(self, parent):
            self.parent_view = parent
            super().__init__(placeholder="Receive (TO)…", options=coin_options(), row=1)

        async def callback(self, interaction: discord.Interaction):
            self.parent_view.to = self.values[0]
            await interaction.response.defer()

    class EnterButton(discord.ui.Button):
        def __init__(self, parent):
            self.parent_view = parent
            super().__init__(label="Enter amount →", style=discord.ButtonStyle.primary, row=2)

        async def callback(self, interaction: discord.Interaction):
            p = self.parent_view
            if not p.frm or not p.to:
                await interaction.response.send_message("Pick both coins first.", ephemeral=True)
                return
            if p.frm == p.to:
                await interaction.response.send_message("From and To can't be the same coin.", ephemeral=True)
                return
            await interaction.response.send_modal(AmountModal(p.frm, p.to))


class AmountModal(discord.ui.Modal, title="Crypto Swaps"):
    def __init__(self, frm: str, to: str):
        super().__init__()
        self.frm = frm
        self.to = to
        self.amount = discord.ui.TextInput(
            label=f"Amount of {COINS[frm]['label']} to swap",
            placeholder="e.g. 149", required=True, max_length=24)
        self.address = discord.ui.TextInput(
            label=f"YOUR {COINS[to]['label']} wallet (you receive here)",
            placeholder="Your own wallet address", required=True, max_length=120)
        self.add_item(self.amount)
        self.add_item(self.address)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            amount = float(str(self.amount.value).replace(",", "").strip())
            if amount <= 0:
                raise ValueError
        except ValueError:
            await interaction.followup.send("That amount isn't a valid number.", ephemeral=True)
            return

        payout = str(self.address.value).strip()
        frm_cn, to_cn = COINS[self.frm]["cn"], COINS[self.to]["cn"]

        async with aiohttp.ClientSession() as session:
            minv = await cn_min(session, frm_cn, to_cn)
            try:
                if minv is not None and amount < float(minv):
                    await interaction.followup.send(
                        f"Minimum for {COINS[self.frm]['label']} → {COINS[self.to]['label']} is **{minv} {COINS[self.frm]['label']}**.",
                        ephemeral=True)
                    return
            except (TypeError, ValueError):
                pass

            est = await cn_estimate(session, frm_cn, to_cn, amount)
            usd = await usd_value(session, self.frm, amount)
            view = ConfirmSwap(self.frm, self.to, amount, payout, est, usd)
            await interaction.followup.send(embed=view.quote_embed(), view=view, ephemeral=True)


class ConfirmSwap(discord.ui.View):
    def __init__(self, frm, to, amount, payout, est, usd):
        super().__init__(timeout=300)
        self.frm, self.to, self.amount = frm, to, amount
        self.payout, self.est, self.usd = payout, est, usd

    def quote_embed(self) -> discord.Embed:
        cf, ct = COINS[self.frm], COINS[self.to]
        e = discord.Embed(title="Confirm your swap", color=BLUE,
                          description="Funds flow **you → ChangeNOW → you**. The bot never holds them.")
        e.add_field(name="You send", value=f"{cf['emoji']} **{self.amount:g} {cf['label']}**")
        e.add_field(name="You receive (est.)",
                    value=f"{ct['emoji']} **≈ {self.est} {ct['label']}**" if self.est else "—")
        if self.usd:
            e.add_field(name="Value", value=f"≈ ${self.usd:,.2f}")
        e.add_field(name="Your payout wallet", value=f"`{self.payout}`", inline=False)
        e.set_footer(text="Non-custodial · Powered by ChangeNOW")
        return e

    @discord.ui.button(label="Confirm & get deposit address", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True, thinking=True)
        frm_cn, to_cn = COINS[self.frm]["cn"], COINS[self.to]["cn"]
        async with aiohttp.ClientSession() as session:
            tx, err = await cn_create(session, frm_cn, to_cn, self.amount, self.payout)
        if not tx:
            await interaction.followup.send(
                "Couldn't create the swap — double-check the wallet address is a valid "
                f"**{COINS[self.to]['label']}** address and try again.", ephemeral=True)
            return

        payin = tx.get("payinAddress") or "—"
        tx_id = tx.get("id") or "—"
        to_amount = tx.get("toAmount") or self.est

        cf, ct = COINS[self.frm], COINS[self.to]
        e = discord.Embed(title="\U0001F4B1 Crypto Swap — send your coins", color=BLUE,
                          description=f"Send exactly the amount below. Your **{ct['label']}** goes straight to your wallet.")
        e.add_field(name=f"① Send this {cf['label']}", value=f"**{self.amount:g} {cf['label']}**", inline=True)
        e.add_field(name="You'll receive", value=f"≈ {to_amount} {ct['label']}", inline=True)
        e.add_field(name=f"② Deposit address (send {cf['label']} here)", value=f"`{payin}`", inline=False)
        e.add_field(name="Your payout wallet", value=f"`{self.payout}`", inline=False)
        e.set_image(url=qr_url(payin))
        e.set_footer(text=f"Non-custodial · ChangeNOW · ID {tx_id}")
        await interaction.followup.send(embed=e, ephemeral=True)

        # record stats (public counts only — no funds involved)
        try:
            record_swap(self.frm, self.to, self.amount, self.usd or 0.0,
                        f"@{interaction.user.name}")
        except Exception as ex:
            log.warning("stats record failed: %s", ex)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Swap cancelled.", embed=None, view=None)


def panel_embed() -> discord.Embed:
    e = discord.Embed(
        title="\U0001F4B1 Crypto Swaps",
        color=BLUE,
        description=("Swap one coin for another in seconds — **your funds never touch the bot.**\n\n"
                     "Press **Start a Swap** below to begin."))
    e.add_field(name="\U0001F512 Non-custodial",
                value="You → ChangeNOW → you. The bot only shows the swap.", inline=False)
    e.add_field(name="Supported", value="  ".join(c["label"] for c in COINS.values()), inline=False)
    e.set_footer(text="Powered by ChangeNOW")
    return e


class PanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Start a Swap", style=discord.ButtonStyle.primary, emoji="\U0001F501",
                       custom_id="cryptoswaps:start")
    async def start(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "Pick the coins, then enter your amount + wallet.",
            view=SwapFlow(interaction.user.id), ephemeral=True)


# ===========================================================================
# Commands
# ===========================================================================

@tree.command(name="cryptoswaps", description="Open the Crypto Swaps panel.")
async def cmd_panel(interaction: discord.Interaction):
    await interaction.response.send_message(embed=panel_embed(), view=PanelView())


@tree.command(name="swapstats", description="Show all-time Crypto Swaps stats.")
async def cmd_stats(interaction: discord.Interaction):
    s = load_stats()
    e = discord.Embed(title="\U0001F4CA Crypto Swaps — Live Stats", color=BLUE,
                      description="All-time activity · updates after every swap")
    e.add_field(name="\U0001F501 Total swaps", value=f"**{s.get('count', 0):,}**")
    e.add_field(name="\U0001F4B0 Total volume ever", value=f"**${s.get('volume_usd', 0.0):,.2f}**")

    big = s.get("biggest")
    if big:
        bu = f"${big['usd']:,.2f}" if big.get("usd") else f"{big['amount']:g} {big['from']}"
        e.add_field(name="\U0001F3C6 Biggest swap",
                    value=f"**{bu}**\n{big['from']} → {big['to']} · {big.get('user','?')}", inline=False)
    latest = s.get("latest")
    if latest:
        when = latest.get("at", "")[:16].replace("T", " ")
        e.add_field(name="⚡ Latest swap",
                    value=f"{latest['amount']:g} {latest['from']} → {latest['to']} · {when} UTC", inline=False)
    e.set_footer(text="Non-custodial · Powered by ChangeNOW")
    await interaction.response.send_message(embed=e)


@client.event
async def on_ready():
    client.add_view(PanelView())  # keep the panel button alive across restarts
    try:
        if GUILD_ID.isdigit():
            g = discord.Object(id=int(GUILD_ID))
            tree.copy_global_to(guild=g)
            await tree.sync(guild=g)
            log.info("Synced commands to guild %s", GUILD_ID)
        else:
            await tree.sync()
            log.info("Synced commands globally (may take up to 1h to show).")
    except discord.Forbidden:
        await tree.sync()
        log.info("Guild sync forbidden; synced globally instead.")
    log.info("Logged in as %s", client.user)
    if not CN_KEY:
        log.warning("CHANGENOW_API_KEY is not set — swaps will fail until you add it.")


if __name__ == "__main__":
    client.run(TOKEN)

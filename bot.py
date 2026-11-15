"""Crypto wallet checker: shows your addresses and balances, and DMs you on every
incoming/outgoing transaction. Watches your OWN public addresses only (read-only).

Settings live in the .env file. Put your addresses in the WALLETS list below.
"""

import asyncio
import json
import logging
import os
from pathlib import Path

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
DATA_FILE = BASE_DIR / "seen.json"
load_dotenv(BASE_DIR / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("crypto-checker")

# ===========================================================================
# Settings (.env)
# ===========================================================================
TOKEN = os.getenv("BOT_TOKEN", "").strip()
OWNER_ID = int(os.getenv("OWNER_ID", "1492971645747593256") or "0")  # who gets the DM alerts
CHECKER_NAME = os.getenv("CHECKER_NAME", "Crypto Checker").strip() or "Crypto Checker"
ETHERSCAN_KEY = os.getenv("ETHERSCAN_KEY", "").strip()  # one free key covers ETH + BSC (send/receive alerts for tokens)
GREEN = 0x57F287
POLL_SECONDS = 60

# RPCs (public, no key needed)
ETH_RPC = os.getenv("ETH_RPC", "https://ethereum-rpc.publicnode.com").strip()
BSC_RPC = os.getenv("BSC_RPC", "https://bsc-rpc.publicnode.com").strip()
SOL_RPC = os.getenv("SOL_RPC", "https://solana-rpc.publicnode.com").strip()

# ===========================================================================
# Your wallets — fill in the "address" fields with your real addresses.
# chain: "ltc", "btc", "sol", "evm" (native ETH/BNB) or "token" (ERC-20 / BEP20)
# ===========================================================================
USDC_ETH = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"   # USDC contract on Ethereum (6 decimals)
USDT_BSC = "0x55d398326f99059fF775485246999027B3197955"   # USDT contract on BNB Smart Chain (18 decimals)

WALLETS = [
    {
        "symbol": "USDC", "name": "USDC", "network": "Ethereum", "coin": "usd-coin",
        "chain": "token", "evm": "eth", "contract": USDC_ETH, "decimals": 6,
        "address": "0x8B3549061FD38D34810971f8D8A33D62966cF614",
        "icon": "https://assets.coingecko.com/coins/images/6319/small/usdc.png",
        "explorer_addr": "https://etherscan.io/address/", "explorer_tx": "https://etherscan.io/tx/",
    },
    {
        "symbol": "USDT", "name": "USDT", "network": "BNB Smart Chain (BEP20)", "coin": "tether",
        "chain": "token", "evm": "bsc", "contract": USDT_BSC, "decimals": 18,
        "address": "0x8B3549061FD38D34810971f8D8A33D62966cF614",
        "icon": "https://assets.coingecko.com/coins/images/325/small/Tether.png",
        "explorer_addr": "https://bscscan.com/address/", "explorer_tx": "https://bscscan.com/tx/",
    },
    {
        "symbol": "LTC", "name": "Litecoin", "network": "Litecoin", "coin": "litecoin",
        "chain": "ltc", "decimals": 8,
        "address": "LTQsGfYiTR7wpfcuR8SsMxFqEbfp58s5o9",
        "icon": "https://assets.coingecko.com/coins/images/2/small/litecoin.png",
        "explorer_addr": "https://litecoinspace.org/address/", "explorer_tx": "https://litecoinspace.org/tx/",
    },
    {
        "symbol": "BTC", "name": "Bitcoin", "network": "Bitcoin", "coin": "bitcoin",
        "chain": "btc", "decimals": 8,
        "address": "bc1qvsdxjfrgaf8573ka9k748fmnk3uhjupp0s2hk9",
        "icon": "https://assets.coingecko.com/coins/images/1/small/bitcoin.png",
        "explorer_addr": "https://mempool.space/address/", "explorer_tx": "https://mempool.space/tx/",
    },
    {
        "symbol": "SOL", "name": "Solana", "network": "Solana", "coin": "solana",
        "chain": "sol", "decimals": 9,
        "address": "DDm9iM2Tqf5pmzhzK5tuCiM5KBrzZdv7BEsLur5rtkV6",
        "icon": "https://assets.coingecko.com/coins/images/4128/small/solana.png",
        "explorer_addr": "https://solscan.io/account/", "explorer_tx": "https://solscan.io/tx/",
    },
]


def active_wallets() -> list[dict]:
    return [w for w in WALLETS if w["address"] and not w["address"].startswith("PASTE_")]


# ===========================================================================
# Saved state (which transactions we've already alerted on)
# ===========================================================================
def load_seen() -> dict:
    try:
        return json.loads(DATA_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def save_seen(seen: dict) -> None:
    try:
        DATA_FILE.write_text(json.dumps(seen))
    except OSError:
        log.exception("Couldn't save seen.json")


# ===========================================================================
# HTTP helpers
# ===========================================================================
async def get_json(session, url, params=None, headers=None):
    try:
        async with session.get(url, params=params, headers=headers,
                               timeout=aiohttp.ClientTimeout(total=20)) as r:
            if r.status != 200:
                return None
            return await r.json(content_type=None)
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
        return None


async def rpc(session, url, method, params):
    try:
        async with session.post(url, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                                timeout=aiohttp.ClientTimeout(total=20)) as r:
            data = await r.json(content_type=None)
            return data.get("result")
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
        return None


def balanceof_data(addr: str) -> str:
    return "0x70a08231" + addr.lower().replace("0x", "").rjust(64, "0")


def rpc_url(evm: str) -> str:
    return BSC_RPC if evm == "bsc" else ETH_RPC


# ===========================================================================
# Prices (CoinGecko)
# ===========================================================================
_price_cache: dict[str, float] = {}


async def refresh_prices(session) -> None:
    ids = ",".join({w["coin"] for w in active_wallets()})
    if not ids:
        return
    data = await get_json(session, "https://api.coingecko.com/api/v3/simple/price",
                          params={"ids": ids, "vs_currencies": "usd"})
    if isinstance(data, dict):
        for coin, d in data.items():
            if isinstance(d, dict) and "usd" in d:
                _price_cache[coin] = float(d["usd"])


def price_of(w: dict) -> float | None:
    return _price_cache.get(w["coin"])


def usd(amount: float, w: dict) -> str:
    p = price_of(w)
    return f"${amount * p:,.2f}" if p is not None else "$—"


# ===========================================================================
# Balances
# ===========================================================================
async def get_balance(session, w: dict) -> float | None:
    try:
        if w["chain"] == "ltc":
            d = await get_json(session, f"https://api.blockcypher.com/v1/ltc/main/addrs/{w['address']}/balance")
            return (d.get("final_balance", 0) / 1e8) if d else None
        if w["chain"] == "btc":
            d = await get_json(session, f"https://api.blockcypher.com/v1/btc/main/addrs/{w['address']}/balance")
            return (d.get("final_balance", 0) / 1e8) if d else None
        if w["chain"] == "sol":
            res = await rpc(session, SOL_RPC, "getBalance", [w["address"]])
            return (res.get("value", 0) / 1e9) if isinstance(res, dict) else None
        if w["chain"] == "evm":
            res = await rpc(session, rpc_url(w["evm"]), "eth_getBalance", [w["address"], "latest"])
            return int(res, 16) / 1e18 if res else None
        if w["chain"] == "token":
            res = await rpc(session, rpc_url(w["evm"]), "eth_call",
                            [{"to": w["contract"], "data": balanceof_data(w["address"])}, "latest"])
            return int(res, 16) / (10 ** w["decimals"]) if res and res != "0x" else 0.0
    except (ValueError, TypeError, KeyError):
        return None
    return None


# ===========================================================================
# Transactions — normalized to:
#   {hash, direction, amount, fee, fee_rate, confirmations, block, to}
# ===========================================================================
async def get_txs(session, w: dict) -> list[dict] | None:
    if w["chain"] in ("ltc", "btc"):
        return await _utxo_txs(session, w)
    if w["chain"] == "sol":
        return await _sol_txs(session, w)
    if w["chain"] == "token" and ETHERSCAN_KEY:
        return await _token_txs(session, w)
    return []  # tokens without an Etherscan key: balances only, no tx alerts


async def _utxo_txs(session, w: dict) -> list[dict] | None:
    net = "ltc" if w["chain"] == "ltc" else "btc"
    me = w["address"].lower()
    d = await get_json(session, f"https://api.blockcypher.com/v1/{net}/main/addrs/{w['address']}/full",
                       params={"limit": 10})
    if not d or "txs" not in d:
        return None
    out = []
    for tx in d["txs"]:
        confs = tx.get("confirmations", 0)
        inputs = [a.lower() for i in tx.get("inputs", []) for a in (i.get("addresses") or [])]
        sent = me in inputs
        outs = tx.get("outputs", [])
        if sent:
            amount = sum(o.get("value", 0) for o in outs if me not in [a.lower() for a in (o.get("addresses") or [])])
            to = next((a for o in outs for a in (o.get("addresses") or []) if a.lower() != me), "—")
        else:
            amount = sum(o.get("value", 0) for o in outs if me in [a.lower() for a in (o.get("addresses") or [])])
            to = w["address"]
        fees = tx.get("fees", 0)
        vsize = tx.get("vsize") or tx.get("size") or 0
        unit = "lit" if net == "ltc" else "sat"
        out.append({
            "hash": tx.get("hash", ""), "direction": "sent" if sent else "received",
            "amount": amount / 1e8, "fee": fees / 1e8,
            "fee_rate": f"{fees / vsize:.2f} {unit}/vB" if vsize else None,
            "confirmations": confs, "block": tx.get("block_height"), "to": to,
        })
    return out


async def _sol_txs(session, w: dict) -> list[dict] | None:
    sigs = await rpc(session, SOL_RPC, "getSignaturesForAddress", [w["address"], {"limit": 10}])
    if not isinstance(sigs, list):
        return None
    out = []
    for s in sigs:
        if s.get("err") is not None:
            continue
        confirmed = s.get("confirmationStatus") in ("confirmed", "finalized")
        tx = await rpc(session, SOL_RPC, "getTransaction",
                       [s["signature"], {"maxSupportedTransactionVersion": 0, "encoding": "jsonParsed"}])
        amount, direction, fee = 0.0, "received", 0.0
        if isinstance(tx, dict) and tx.get("meta"):
            meta = tx["meta"]
            fee = meta.get("fee", 0) / 1e9
            keys = [k.get("pubkey") for k in tx["transaction"]["message"]["accountKeys"]]
            if w["address"] in keys:
                i = keys.index(w["address"])
                delta = (meta["postBalances"][i] - meta["preBalances"][i]) / 1e9
                direction = "sent" if delta < 0 else "received"
                amount = abs(delta)
        out.append({
            "hash": s["signature"], "direction": direction, "amount": amount, "fee": fee,
            "fee_rate": None, "confirmations": 1 if confirmed else 0, "block": s.get("slot"), "to": "—",
        })
    return out


async def _token_txs(session, w: dict) -> list[dict] | None:
    chainid = 56 if w["evm"] == "bsc" else 1
    me = w["address"].lower()
    d = await get_json(session, "https://api.etherscan.io/v2/api", params={
        "chainid": chainid, "module": "account", "action": "tokentx",
        "address": w["address"], "contractaddress": w["contract"],
        "sort": "desc", "page": 1, "offset": 10, "apikey": ETHERSCAN_KEY,
    })
    if not d or not isinstance(d.get("result"), list):
        return None
    out = []
    for tx in d["result"]:
        sent = tx.get("from", "").lower() == me
        dec = int(tx.get("tokenDecimal", w["decimals"]) or w["decimals"])
        out.append({
            "hash": tx.get("hash", ""), "direction": "sent" if sent else "received",
            "amount": int(tx.get("value", 0)) / (10 ** dec), "fee": 0.0, "fee_rate": None,
            "confirmations": int(tx.get("confirmations", 0) or 0),
            "block": tx.get("blockNumber"), "to": tx.get("to") if sent else w["address"],
        })
    return out


# ===========================================================================
# Embeds
# ===========================================================================
def short(text: str, head: int = 8, tail: int = 8) -> str:
    text = str(text or "")
    return text if len(text) <= head + tail + 1 else f"{text[:head]}…{text[-tail:]}"


def tx_embed(w: dict, tx: dict, balance: float | None) -> discord.Embed:
    sent = tx["direction"] == "sent"
    badge = "SENT" if sent else "RECEIVED"
    sym = w["symbol"]
    embed = discord.Embed(title="Transaction Confirmed", colour=GREEN,
                          description=("📤 Outgoing · confirmed" if sent else "📥 Incoming · confirmed"))
    embed.set_author(name=f"{w['name']} • {badge}", icon_url=w.get("icon"))
    embed.add_field(name="Amount", value=f"{tx['amount']:.8f} {sym}\n{usd(tx['amount'], w)}", inline=True)
    embed.add_field(name="Network fee", value=f"{tx['fee']:.8f} {sym}", inline=True)
    embed.add_field(name="Fee rate", value=tx["fee_rate"] or "—", inline=True)
    embed.add_field(name="To", value=f"`{short(tx['to'])}`", inline=True)
    embed.add_field(name="Confirmations", value=str(tx["confirmations"]), inline=True)
    embed.add_field(name="Block", value=f"{tx['block']:,}" if tx.get("block") else "—", inline=True)
    embed.add_field(name="Transaction", value=f"`{short(tx['hash'], 10, 10)}`", inline=False)
    if balance is not None:
        embed.add_field(name=f"Balance Now · {w['name']}", value=f"{balance:.8f} {sym} · {usd(balance, w)}", inline=False)
    embed.set_footer(text=f"{sym} · {CHECKER_NAME}")
    return embed


class LinkView(discord.ui.View):
    def __init__(self, url: str):
        super().__init__()
        self.add_item(discord.ui.Button(label="View transaction", emoji="🔗", style=discord.ButtonStyle.link, url=url))


async def addresses_embed() -> discord.Embed:
    embed = discord.Embed(colour=GREEN)
    embed.set_author(name=f"{CHECKER_NAME} · Payment Methods")
    for w in active_wallets():
        embed.add_field(name=f"{w['symbol']} — {w['network']}", value=f"`{w['address']}`", inline=False)
    if not active_wallets():
        embed.description = "No addresses set yet."
    return embed


class CopyView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)
        for w in active_wallets():
            btn = discord.ui.Button(label=f"Copy {w['symbol']}", style=discord.ButtonStyle.secondary)
            btn.callback = self._copy(w)
            self.add_item(btn)

    def _copy(self, w: dict):
        async def cb(interaction: discord.Interaction):
            await interaction.response.send_message(f"**{w['symbol']} ({w['network']})**\n```\n{w['address']}\n```",
                                                    ephemeral=True)
        return cb


async def balances_embed(session) -> discord.Embed:
    embed = discord.Embed(colour=GREEN)
    embed.set_author(name=f"{CHECKER_NAME} · Balances")
    await refresh_prices(session)
    total = 0.0
    for w in active_wallets():
        bal = await get_balance(session, w)
        if bal is None:
            embed.add_field(name=f"{w['symbol']} — {w['network']}", value="Couldn't fetch", inline=False)
            continue
        p = price_of(w)
        if p is not None:
            total += bal * p
        embed.add_field(name=f"{w['symbol']} — {w['network']}",
                        value=f"{bal:.8f} {w['symbol']} · {usd(bal, w)}", inline=False)
    embed.add_field(name="Estimated total", value=f"${total:,.2f}", inline=False)
    embed.set_footer(text=f"{CHECKER_NAME} · live USD via CoinGecko")
    return embed


class RefreshView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Refresh", emoji="🔄", style=discord.ButtonStyle.secondary, custom_id="crypto:refresh")
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        await interaction.edit_original_response(embed=await balances_embed(bot.session), view=RefreshView())


# ===========================================================================
# Bot
# ===========================================================================
class CryptoBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=discord.Intents.default(), help_command=None)
        self.session: aiohttp.ClientSession | None = None

    async def setup_hook(self):
        self.session = aiohttp.ClientSession()
        self.add_view(RefreshView())
        await self.tree.sync()
        self.loop.create_task(watcher())

    async def on_ready(self):
        log.info("Logged in as %s — watching %d wallet(s)", self.user, len(active_wallets()))


bot = CryptoBot()


@bot.tree.command(name="addresses", description="Show the crypto payment addresses")
async def addresses(interaction: discord.Interaction):
    await interaction.response.send_message(embed=await addresses_embed(), view=CopyView())


@bot.tree.command(name="balance", description="Show wallet balances")
async def balance(interaction: discord.Interaction):
    if OWNER_ID and interaction.user.id != OWNER_ID:
        return await interaction.response.send_message("Only the owner can check balances.", ephemeral=True)
    await interaction.response.defer()
    await interaction.followup.send(embed=await balances_embed(bot.session), view=RefreshView())


# ===========================================================================
# Watcher — DMs the owner on new confirmed transactions
# ===========================================================================
async def watcher():
    await bot.wait_until_ready()
    seen = load_seen()
    owner = None
    try:
        owner = await bot.fetch_user(OWNER_ID) if OWNER_ID else None
    except discord.HTTPException:
        log.error("Couldn't find the owner user %s to DM.", OWNER_ID)

    while not bot.is_closed():
        try:
            await refresh_prices(bot.session)
            for w in active_wallets():
                key = f"{w['chain']}:{w['address']}"
                first_run = key not in seen
                known = set(seen.get(key, []))
                txs = await get_txs(bot.session, w)
                if txs is None:
                    continue
                confirmed = [t for t in txs if t["confirmations"] >= 1 and t["hash"]]
                new = [t for t in confirmed if t["hash"] not in known]
                if first_run:
                    # Seed silently so we don't DM the whole history on startup.
                    seen[key] = [t["hash"] for t in confirmed]
                    save_seen(seen)
                    continue
                for t in reversed(new):  # oldest first
                    known.add(t["hash"])
                    if owner:
                        bal = await get_balance(bot.session, w)
                        url = w["explorer_tx"] + t["hash"]
                        try:
                            await owner.send(embed=tx_embed(w, t, bal), view=LinkView(url))
                        except discord.HTTPException:
                            log.exception("Couldn't DM the owner.")
                if new:
                    seen[key] = list(known)[-50:]
                    save_seen(seen)
        except Exception:
            log.exception("Watcher loop error")
        await asyncio.sleep(POLL_SECONDS)


if not TOKEN:
    raise SystemExit("Missing BOT_TOKEN in the .env file.")
bot.run(TOKEN, log_handler=None)

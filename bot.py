"""
M&E Lean Bot — moderation, vouches, giveaways, embeds, channel nuke, server build.
Lightweight (no image rendering) so it runs comfortably under 100 MB RAM.

Environment variables (set on your host, never in code/chat):
  BOT_TOKEN   - your bot token (reset it if it ever leaks)   [required]
  GUILD_ID    - your server ID (OPTIONAL now — the bot auto-syncs to every
                server it's in, so you don't even need this)
"""

import asyncio
import html as _html
import io
import json
import os
import random
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

BASE = Path(__file__).resolve().parent
load_dotenv(BASE / ".env")

TOKEN = os.getenv("BOT_TOKEN", "").strip()
GUILD_ID = os.getenv("GUILD_ID", "").strip()
if not TOKEN or TOKEN.startswith("PASTE"):
    raise SystemExit("Set BOT_TOKEN in your environment variables.")
GUILD_ID = int(GUILD_ID) if GUILD_ID.isdigit() else None  # optional: just makes sync instant

ACCENT = 0x8A4DFF
GOOD = 0x37D98A
BAD = 0xFF5D7A
WARN = 0xFFCE4D
DATA_FILE = BASE / "data.json"
BANNER_FILE = BASE / "banner.gif"

# Ticket staff role — the ONLY role that gets pinged on a ticket and can close it.
# Override by setting TICKET_ROLE in env, else this default.
TICKET_ROLE_DEFAULT = 1575014415458111551

# Vouch channel — where vouches post and the sticky "how to vouch" message lives.
VOUCH_CHANNEL_DEFAULT = 1575014425948070069

# Payment methods for purchase tickets: (name, custom-emoji name, fee text or None)
PAY_METHODS = [
    ("USDT", "sb_usdt", "$1"),
    ("BTC",  "sb_btc",  "$1"),
    ("LTC",  "sb_ltc",  None),
]
PAY_FEE = {name: fee for name, _e, fee in PAY_METHODS}

# Custom emojis the bot uploads to the server (files live in ./emojis/<name>.png)
EMOJI_FILES = ["sb_usdt", "sb_btc", "sb_ltc", "sb_ticket", "sb_boost",
               "sb_check", "sb_star", "sb_arrow"]
EMOJI = {}  # name -> discord.Emoji (filled at runtime by ensure_emojis)


def em(name, fallback=""):
    e = EMOJI.get(name)
    return str(e) if e else fallback


def pay_label(name):
    fee = PAY_FEE.get(name)
    return f"{name} — **+{fee} fee**" if fee else f"{name} — **no fee**"


# ----------------------------------------------------------------- data store
def _load():
    try:
        return json.loads(DATA_FILE.read_text("utf-8"))
    except Exception:
        return {}


class Store:
    def __init__(self):
        d = _load()
        self.config = d.get("config", {})       # modlog_channel, vouch_channel, log_channel
        self.cases = d.get("cases", {})          # user_id -> [ {type, reason, mod, at} ]
        v = d.get("vouches", [])                 # list of {order, stars, text, by, by_id, at}
        self.vouches = v if isinstance(v, list) else []   # migrate old per-seller dict -> fresh list
        self.giveaways = d.get("giveaways", {})  # message_id -> {...}
        self.msgs = d.get("msgs", {})            # guild_id -> user_id -> {"YYYY-MM-DD": count}
        self.invites = d.get("invites", {})      # guild_id -> inviter_id -> count
        self.tickets = d.get("tickets", {})      # channel_id -> {opener, kind, pay, at}

    def save(self):
        tmp = DATA_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps({
            "config": self.config, "cases": self.cases,
            "vouches": self.vouches, "giveaways": self.giveaways,
            "msgs": self.msgs, "invites": self.invites,
            "tickets": self.tickets}, indent=2), "utf-8")
        tmp.replace(DATA_FILE)


store = Store()


def now():
    return datetime.now(timezone.utc)


def add_case(uid, ctype, reason, mod):
    store.cases.setdefault(str(uid), []).append(
        {"type": ctype, "reason": reason or "—", "mod": str(mod), "at": now().isoformat()})
    store.save()


def parse_duration(text):
    """'10m' '2h' '1d' '30s' -> seconds, or None."""
    m = re.fullmatch(r"\s*(\d+)\s*([smhd])\s*", (text or "").lower())
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2)
    return n * {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]


# ---- message counting (per-user daily buckets) -------------------------------
def bump_message(guild_id, user_id):
    day = now().strftime("%Y-%m-%d")
    g = store.msgs.setdefault(str(guild_id), {})
    u = g.setdefault(str(user_id), {})
    u[day] = u.get(day, 0) + 1
    # keep it light: drop buckets older than ~400 days
    if len(u) > 420:
        for k in sorted(u)[:-400]:
            u.pop(k, None)


def msg_count(guild_id, user_id, period):
    """period: 'day' | 'week' | 'month' | 'all'"""
    u = store.msgs.get(str(guild_id), {}).get(str(user_id), {})
    if not u:
        return 0
    if period == "all":
        return sum(u.values())
    days = {"day": 1, "week": 7, "month": 30}.get(period, 1)
    cutoff = (now() - timedelta(days=days - 1)).date()
    total = 0
    for k, v in u.items():
        try:
            if datetime.strptime(k, "%Y-%m-%d").date() >= cutoff:
                total += v
        except ValueError:
            pass
    return total


def invite_count(guild_id, user_id):
    return store.invites.get(str(guild_id), {}).get(str(user_id), 0)


PERIOD_LABEL = {"day": "today", "week": "this week", "month": "this month", "all": "all-time"}


def requirement_lines(gw):
    """Human-readable list of a giveaway's entry requirements (for the embed)."""
    b = em("sb_arrow", "›")
    out = []
    if gw.get("req_role"):
        out.append(f"{b} Must have <@&{gw['req_role']}>")
    if gw.get("req_invites"):
        out.append(f"{b} **{gw['req_invites']}+** invites")
    if gw.get("req_msgs"):
        out.append(f"{b} **{gw['req_msgs']}+** messages {PERIOD_LABEL.get(gw.get('req_period','all'))}")
    if gw.get("req_age_days"):
        out.append(f"{b} Account **{gw['req_age_days']}+** days old")
    return out


def check_requirements(gw, member):
    """Return None if member qualifies, else a short reason string."""
    gid = member.guild.id
    if gw.get("req_role") and not any(r.id == gw["req_role"] for r in member.roles):
        return f"You need the <@&{gw['req_role']}> role to enter."
    if gw.get("req_invites"):
        have = invite_count(gid, member.id)
        if have < gw["req_invites"]:
            return f"You need **{gw['req_invites']}** invites to enter (you have **{have}**)."
    if gw.get("req_msgs"):
        period = gw.get("req_period", "all")
        have = msg_count(gid, member.id, period)
        if have < gw["req_msgs"]:
            return (f"You need **{gw['req_msgs']}** messages {PERIOD_LABEL.get(period)} "
                    f"to enter (you have **{have}**).")
    if gw.get("req_age_days"):
        age = (now() - member.created_at).days
        if age < gw["req_age_days"]:
            return f"Your account must be **{gw['req_age_days']}+** days old (it's **{age}**)."
    return None


def ticket_role_id(guild):
    env = os.getenv("TICKET_ROLE", "").strip()
    if env.isdigit():
        return int(env)
    cfg = store.config.get("ticket_role")
    return int(cfg) if cfg else TICKET_ROLE_DEFAULT


# ----------------------------------------------------------------- bot
intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)


def log_channel(guild, key="modlog_channel"):
    cid = store.config.get(key)
    if cid:
        ch = guild.get_channel(int(cid))
        if ch:
            return ch
    # fallback: find by name
    names = {"modlog_channel": "mod-logs", "msglog_channel": "message-logs",
             "log_channel": "join-leave", "vouchlog_channel": "vouch-logs",
             "gwlog_channel": "giveaway-logs", "orders_channel": "orders",
             "vouch_channel": "vouches"}
    want = names.get(key, "")
    return next((c for c in guild.text_channels if c.name.endswith(want)), None) if want else None


def is_admin():
    def pred(i: discord.Interaction):
        return bool(i.user.guild_permissions.administrator)
    return app_commands.check(pred)


async def modlog(guild, title, desc, colour=ACCENT):
    ch = log_channel(guild, "modlog_channel")
    if ch:
        try:
            await ch.send(embed=discord.Embed(title=title, description=desc, colour=colour, timestamp=now()))
        except Exception:
            pass


async def log_to(guild, key, title, desc, colour=ACCENT):
    """Send a log embed to one of the named log channels (orders / vouch-logs / etc.)."""
    ch = log_channel(guild, key)
    if ch:
        try:
            await ch.send(embed=discord.Embed(title=title, description=desc, colour=colour, timestamp=now()))
        except Exception:
            pass


# The private, staff-only channels the bot keeps around for logging.
LOG_CHANNELS = ["message-logs", "mod-logs", "join-leave", "vouch-logs",
                "giveaway-logs", "orders"]


async def ensure_log_channels(guild):
    """Create any missing log channels as PRIVATE (staff + bot only)."""
    role = guild.get_role(ticket_role_id(guild))
    ow = {guild.default_role: discord.PermissionOverwrite(view_channel=False),
          guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True)}
    if role:
        ow[role] = discord.PermissionOverwrite(view_channel=True, send_messages=False,
                                               read_message_history=True)
    cat = discord.utils.get(guild.categories, name="🔒 Logs")
    for nm in LOG_CHANNELS:
        if any(c.name.endswith(nm) for c in guild.text_channels):
            continue
        try:
            if cat is None:
                cat = await guild.create_category("🔒 Logs", overwrites=ow)
            await guild.create_text_channel(nm, category=cat, overwrites=ow)
            print(f"📁 created private #{nm}")
        except Exception as ex:
            print(f"ensure channel {nm}: {ex}")


# ====================================================================== SETUP
setup_group = app_commands.Group(name="setup", description="Configure the bot",
                                 default_permissions=discord.Permissions(administrator=True))


@setup_group.command(name="channels", description="Point the bot's log channels wherever you want")
@app_commands.describe(modlog="Mod actions", messagelog="Message edits/deletes",
                       joinleave="Joins/leaves", vouches="Where vouches post",
                       vouchlog="Vouch log (staff)", giveawaylog="Giveaway log (staff)",
                       orders="Order log (staff)")
async def setup_channels(i: discord.Interaction, modlog: discord.TextChannel = None,
                         messagelog: discord.TextChannel = None, joinleave: discord.TextChannel = None,
                         vouches: discord.TextChannel = None, vouchlog: discord.TextChannel = None,
                         giveawaylog: discord.TextChannel = None, orders: discord.TextChannel = None):
    if modlog:
        store.config["modlog_channel"] = modlog.id
    if messagelog:
        store.config["msglog_channel"] = messagelog.id
    if joinleave:
        store.config["log_channel"] = joinleave.id
    if vouches:
        store.config["vouch_channel"] = vouches.id
    if vouchlog:
        store.config["vouchlog_channel"] = vouchlog.id
    if giveawaylog:
        store.config["gwlog_channel"] = giveawaylog.id
    if orders:
        store.config["orders_channel"] = orders.id
    store.save()
    await i.response.send_message("✅ Saved channel settings.", ephemeral=True)


@setup_group.command(name="makechannel", description="📁 Create a channel as the bot")
@app_commands.describe(name="Channel name", private="Only staff + you can see it?",
                       category="Put it under this category (optional)")
async def setup_makechannel(i: discord.Interaction, name: str, private: bool = False,
                            category: discord.CategoryChannel = None):
    guild = i.guild
    ow = {}
    if private:
        role = guild.get_role(ticket_role_id(guild))
        ow = {guild.default_role: discord.PermissionOverwrite(view_channel=False),
              guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True),
              i.user: discord.PermissionOverwrite(view_channel=True, send_messages=True)}
        if role:
            ow[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
    try:
        ch = await guild.create_text_channel(name, category=category, overwrites=ow)
    except discord.Forbidden:
        return await i.response.send_message(
            "❌ I need the **Manage Channels** permission (give me Administrator).", ephemeral=True)
    await i.response.send_message(
        f"{em('sb_check','✅')} Created {ch.mention}" + (" 🔒 (private)" if private else ""),
        ephemeral=True)


# ================================================================= MODERATION
@bot.tree.command(name="warn", description="⚠️ Warn a member")
@is_admin()
@app_commands.describe(member="Who to warn", reason="Why")
async def warn(i: discord.Interaction, member: discord.Member, reason: str = None):
    add_case(member.id, "warn", reason, i.user)
    await i.response.send_message(f"⚠️ Warned {member.mention}. Reason: **{reason or '—'}**")
    await modlog(i.guild, "⚠️ Warn", f"{member.mention} by {i.user.mention}\nReason: {reason or '—'}", WARN)
    try:
        await member.send(f"You were warned in **{i.guild.name}**: {reason or '—'}")
    except Exception:
        pass


@bot.tree.command(name="mute", description="🔇 Timeout a member (e.g. 10m, 2h, 1d)")
@is_admin()
@app_commands.describe(member="Who", duration="How long: 10m, 2h, 1d", reason="Why")
async def mute(i: discord.Interaction, member: discord.Member, duration: str, reason: str = None):
    secs = parse_duration(duration)
    if not secs or secs > 2419200:
        return await i.response.send_message("Use a time like `10m`, `2h`, `1d` (max 28d).", ephemeral=True)
    try:
        await member.timeout(timedelta(seconds=secs), reason=reason)
    except discord.Forbidden:
        return await i.response.send_message("I can't timeout them — my role needs to be above theirs.", ephemeral=True)
    add_case(member.id, "mute", f"{duration} · {reason or '—'}", i.user)
    await i.response.send_message(f"🔇 Muted {member.mention} for **{duration}**.")
    await modlog(i.guild, "🔇 Mute", f"{member.mention} for {duration} by {i.user.mention}\nReason: {reason or '—'}", WARN)


@bot.tree.command(name="unmute", description="🔊 Remove a member's timeout")
@is_admin()
async def unmute(i: discord.Interaction, member: discord.Member):
    try:
        await member.timeout(None)
    except discord.Forbidden:
        return await i.response.send_message("I can't do that — check my role position.", ephemeral=True)
    await i.response.send_message(f"🔊 Unmuted {member.mention}.")
    await modlog(i.guild, "🔊 Unmute", f"{member.mention} by {i.user.mention}", GOOD)


@bot.tree.command(name="kick", description="👢 Kick a member")
@is_admin()
async def kick(i: discord.Interaction, member: discord.Member, reason: str = None):
    try:
        await member.kick(reason=reason)
    except discord.Forbidden:
        return await i.response.send_message("I can't kick them — check my role position.", ephemeral=True)
    add_case(member.id, "kick", reason, i.user)
    await i.response.send_message(f"👢 Kicked {member.mention}. Reason: **{reason or '—'}**")
    await modlog(i.guild, "👢 Kick", f"{member} by {i.user.mention}\nReason: {reason or '—'}", BAD)


@bot.tree.command(name="ban", description="🔨 Ban a member")
@is_admin()
async def ban(i: discord.Interaction, member: discord.Member, reason: str = None):
    try:
        await member.ban(reason=reason, delete_message_days=1)
    except discord.Forbidden:
        return await i.response.send_message("I can't ban them — check my role position.", ephemeral=True)
    add_case(member.id, "ban", reason, i.user)
    await i.response.send_message(f"🔨 Banned {member.mention}. Reason: **{reason or '—'}**")
    await modlog(i.guild, "🔨 Ban", f"{member} by {i.user.mention}\nReason: {reason or '—'}", BAD)


@bot.tree.command(name="unban", description="🕊️ Unban someone by user ID")
@is_admin()
async def unban(i: discord.Interaction, user_id: str, reason: str = None):
    if not user_id.isdigit():
        return await i.response.send_message("Give a numeric user ID.", ephemeral=True)
    try:
        await i.guild.unban(discord.Object(id=int(user_id)), reason=reason)
    except discord.NotFound:
        return await i.response.send_message("That user isn't banned.", ephemeral=True)
    await i.response.send_message(f"🕊️ Unbanned `{user_id}`.")
    await modlog(i.guild, "🕊️ Unban", f"`{user_id}` by {i.user.mention}", GOOD)


@bot.tree.command(name="purge", description="🗑️ Delete recent messages in this channel")
@is_admin()
@app_commands.describe(amount="How many (1-100)")
async def purge(i: discord.Interaction, amount: int):
    amount = max(1, min(100, amount))
    await i.response.defer(ephemeral=True)
    deleted = await i.channel.purge(limit=amount)
    await i.followup.send(f"🗑️ Deleted {len(deleted)} messages.", ephemeral=True)
    await modlog(i.guild, "🗑️ Purge", f"{len(deleted)} msgs in {i.channel.mention} by {i.user.mention}")


@bot.tree.command(name="lock", description="🔒 Stop everyone talking in this channel")
@is_admin()
async def lock(i: discord.Interaction):
    ow = i.channel.overwrites_for(i.guild.default_role)
    ow.send_messages = False
    await i.channel.set_permissions(i.guild.default_role, overwrite=ow)
    await i.response.send_message("🔒 Channel locked.")


@bot.tree.command(name="unlock", description="🔓 Let everyone talk again")
@is_admin()
async def unlock(i: discord.Interaction):
    ow = i.channel.overwrites_for(i.guild.default_role)
    ow.send_messages = None
    await i.channel.set_permissions(i.guild.default_role, overwrite=ow)
    await i.response.send_message("🔓 Channel unlocked.")


@bot.tree.command(name="slowmode", description="🐢 Set slowmode seconds (0 = off)")
@is_admin()
async def slowmode(i: discord.Interaction, seconds: int):
    seconds = max(0, min(21600, seconds))
    await i.channel.edit(slowmode_delay=seconds)
    await i.response.send_message(f"🐢 Slowmode set to **{seconds}s**." if seconds else "🐢 Slowmode off.")


@bot.tree.command(name="modlogs", description="📋 See a member's warns, mutes, kicks, bans")
@is_admin()
async def modlogs(i: discord.Interaction, member: discord.Member):
    cases = store.cases.get(str(member.id), [])
    if not cases:
        return await i.response.send_message(f"{member.mention} has a clean record. ✅", ephemeral=True)
    e = discord.Embed(title=f"Record · {member}", colour=ACCENT)
    for c in cases[-15:]:
        when = c["at"][:16].replace("T", " ")
        e.add_field(name=f"{c['type'].upper()} · {when}", value=f"{c['reason']} — by {c['mod']}", inline=False)
    await i.response.send_message(embed=e, ephemeral=True)


@bot.tree.command(name="clearlogs", description="🧹 Wipe a member's record (admin)")
@is_admin()
async def clearlogs(i: discord.Interaction, member: discord.Member):
    store.cases.pop(str(member.id), None)
    store.save()
    await i.response.send_message(f"🧹 Cleared {member.mention}'s record.", ephemeral=True)


@bot.tree.command(name="nuke", description="💥 Wipe this channel (clone + delete all messages)")
@is_admin()
async def nuke(i: discord.Interaction):
    ch = i.channel
    await i.response.send_message("💥 Nuking this channel…", ephemeral=True)
    try:
        new = await ch.clone(reason=f"Nuke by {i.user}")
        await new.edit(position=ch.position)
        await ch.delete(reason=f"Nuke by {i.user}")
        await new.send(f"💥 Channel nuked by {i.user.mention}.")
        await modlog(i.guild, "💥 Nuke", f"#{new.name} by {i.user.mention}", BAD)
    except discord.Forbidden:
        await i.followup.send("I need **Manage Channels** for that.", ephemeral=True)


# ===================================================================== VOUCHES
def vouch_channel_id():
    cfg = store.config.get("vouch_channel_id") or store.config.get("vouch_channel")
    return int(cfg) if cfg else VOUCH_CHANNEL_DEFAULT


def vouch_stats():
    n = len(store.vouches)
    avg = (sum(v["stars"] for v in store.vouches) / n) if n else 0
    return n, avg


def vouch_sticky_embed():
    n, avg = vouch_stats()
    stars = "⭐" * round(avg) if n else "—"
    arr = em("sb_arrow", "›")
    e = discord.Embed(
        title=f"{em('sb_star', '⭐')}  How to leave a vouch",
        colour=ACCENT,
        description=("Bought from us? Drop a vouch! 💜\n\n"
                     "Use **`/vouch`** and fill in:\n"
                     f"{arr} **order** — your order ID\n"
                     f"{arr} **rating** — 1 to 5 ⭐\n"
                     f"{arr} **comment** — a few words about your experience"))
    e.add_field(name="📊 Total vouches", value=f"**{n}**", inline=True)
    e.add_field(name="⭐ Average rating", value=(f"**{avg:.1f} / 5**  {stars}" if n else "—"), inline=True)
    if (BASE / "emojis" / "sb_star.png").exists():
        e.set_thumbnail(url="attachment://logo.png")
    e.set_footer(text="Simple Boosts · Thank you for your support 💜")
    return e


async def update_vouch_sticky(guild):
    """Keep one 'how to vouch' + count message pinned to the bottom of the vouch channel."""
    ch = guild.get_channel(vouch_channel_id())
    if ch is None:
        return
    old_id = store.config.get("vouch_sticky_id")
    if old_id:
        try:
            old = await ch.fetch_message(int(old_id))
            await old.delete()
        except Exception:
            pass
    try:
        star = BASE / "emojis" / "sb_star.png"
        kwargs = {"embed": vouch_sticky_embed()}
        if star.exists():
            kwargs["file"] = discord.File(star, filename="logo.png")
        msg = await ch.send(**kwargs)
        store.config["vouch_sticky_id"] = msg.id
        store.save()
    except Exception as ex:
        print("vouch sticky:", ex)


@bot.tree.command(name="vouch", description="⭐ Leave a vouch for your order")
@app_commands.describe(order="Your order ID", rating="1-5 stars", comment="How was it?")
async def vouch(i: discord.Interaction, order: str, rating: int, comment: str):
    rating = max(1, min(5, rating))
    rec = {"order": order, "stars": rating, "text": comment,
           "by": str(i.user), "by_id": i.user.id, "at": now().isoformat()}
    store.vouches.append(rec)
    store.save()
    num = len(store.vouches)
    e = discord.Embed(description=f"{'⭐' * rating}\n\n> {comment}", colour=ACCENT, timestamp=now())
    e.set_author(name=f"{i.user.display_name} vouched", icon_url=i.user.display_avatar.url)
    e.add_field(name="Order", value=f"`{order}`", inline=True)
    e.add_field(name="Rating", value=f"{rating}/5", inline=True)
    e.set_footer(text=f"Vouch #{num} · Simple Boosts")
    await log_to(i.guild, "vouchlog_channel", f"⭐ New vouch #{num}",
                 f"By {i.user.mention} (`{i.user}`)\nOrder: `{order}` · {rating}/5\n> {comment}")
    ch = i.guild.get_channel(vouch_channel_id())
    if ch:
        await ch.send(embed=e)
        await update_vouch_sticky(i.guild)   # bump the sticky back to the bottom
        chk = em("sb_check", "✅")
        if ch.id != i.channel.id:
            await i.response.send_message(f"{chk} Thanks! Your vouch is posted in {ch.mention} 💜",
                                          ephemeral=True)
        else:
            await i.response.send_message(f"{chk} Thanks for your vouch! 💜", ephemeral=True)
    else:
        await i.response.send_message(embed=e)


@bot.tree.command(name="vouches", description="🔎 See the total vouch count & rating")
async def vouches(i: discord.Interaction):
    n, avg = vouch_stats()
    if not n:
        return await i.response.send_message("No vouches yet — be the first! ⭐", ephemeral=True)
    e = discord.Embed(title="⭐ Vouches", colour=ACCENT,
                      description=f"**{n}** total vouches · average **{avg:.1f} / 5**")
    for v in store.vouches[-8:][::-1]:
        e.add_field(name=f"{'⭐' * v['stars']} · {v.get('by','?')}",
                    value=f"`{v.get('order','—')}` — {v['text'][:150]}", inline=False)
    await i.response.send_message(embed=e, ephemeral=True)


@bot.tree.command(name="delvouch", description="🗑️ Delete a vouch by its number (admin)")
@app_commands.describe(number="The vouch number (see the footer on the vouch message)")
@is_admin()
async def delvouch(i: discord.Interaction, number: int):
    if number < 1 or number > len(store.vouches):
        return await i.response.send_message("No vouch with that number.", ephemeral=True)
    removed = store.vouches.pop(number - 1)
    store.save()
    await update_vouch_sticky(i.guild)
    await log_to(i.guild, "vouchlog_channel", "🗑️ Vouch deleted",
                 f"Vouch #{number} (order `{removed.get('order','—')}`) removed by {i.user.mention}", BAD)
    await i.response.send_message(f"🗑️ Deleted vouch #{number}.", ephemeral=True)


# =================================================================== /embed
class EmbedModal(discord.ui.Modal, title="🎨 Create an embed"):
    heading = discord.ui.TextInput(label="Title", required=False, max_length=256)
    body = discord.ui.TextInput(label="Description", style=discord.TextStyle.paragraph, required=False, max_length=4000)
    colour = discord.ui.TextInput(label="Colour hex (e.g. #8A4DFF)", required=False, max_length=7)
    image = discord.ui.TextInput(label="Image URL (optional)", required=False)
    footer = discord.ui.TextInput(label="Footer (optional)", required=False, max_length=2048)

    def __init__(self, channel, use_banner):
        super().__init__()
        self.channel = channel
        self.use_banner = use_banner

    async def on_submit(self, i: discord.Interaction):
        if not self.heading.value and not self.body.value:
            return await i.response.send_message("Add a title or description.", ephemeral=True)
        try:
            col = discord.Colour.from_str(self.colour.value.strip()) if self.colour.value.strip() else discord.Colour(ACCENT)
        except ValueError:
            return await i.response.send_message("Colour must be hex like `#8A4DFF`.", ephemeral=True)
        e = discord.Embed(title=self.heading.value or None, description=self.body.value or None, colour=col)
        if self.footer.value:
            e.set_footer(text=self.footer.value)
        file = None
        banner = BASE / "banner.gif"
        if self.image.value.strip().startswith("http"):
            e.set_image(url=self.image.value.strip())
        elif self.use_banner and banner.exists():
            file = discord.File(banner, filename="banner.gif")
            e.set_image(url="attachment://banner.gif")
        await i.response.defer(ephemeral=True)
        await self.channel.send(embed=e, file=file) if file else await self.channel.send(embed=e)
        await i.followup.send(f"✅ Sent to {self.channel.mention}.", ephemeral=True)


embed_group = app_commands.Group(name="embed", description="Embeds",
                                 default_permissions=discord.Permissions(administrator=True))


@embed_group.command(name="create", description="🎨 Make a custom embed")
@app_commands.describe(channel="Where to send (default: here)", banner="Add the Simple Boosts banner")
async def embed_create(i: discord.Interaction, channel: discord.TextChannel = None, banner: bool = False):
    await i.response.send_modal(EmbedModal(channel or i.channel, banner))


# ================================================================= GIVEAWAYS
class GiveawayView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Enter", emoji="🎉", style=discord.ButtonStyle.success, custom_id="gw:enter")
    async def enter(self, i: discord.Interaction, b: discord.ui.Button):
        gw = store.giveaways.get(str(i.message.id))
        if not gw or gw.get("ended"):
            return await i.response.send_message("This giveaway is over.", ephemeral=True)
        ent = gw.setdefault("entrants", [])
        if i.user.id in ent:
            ent.remove(i.user.id)
            store.save()
            try:
                await i.message.edit(embed=gw_embed(gw))
            except Exception:
                pass
            return await i.response.send_message("👋 You left the giveaway.", ephemeral=True)
        # requirement gate
        reason = check_requirements(gw, i.user)
        if reason:
            return await i.response.send_message(f"🔒 {reason}", ephemeral=True)
        ent.append(i.user.id)
        store.save()
        try:
            await i.message.edit(embed=gw_embed(gw))
        except Exception:
            pass
        await i.response.send_message("✅ You're entered! Good luck 🍀", ephemeral=True)

    @discord.ui.button(label="Entries", emoji="👥", style=discord.ButtonStyle.secondary, custom_id="gw:count")
    async def count(self, i: discord.Interaction, b: discord.ui.Button):
        gw = store.giveaways.get(str(i.message.id))
        n = len(gw.get("entrants", [])) if gw else 0
        you = "✅ You're in." if gw and i.user.id in gw.get("entrants", []) else "❌ You're not entered."
        await i.response.send_message(f"👥 **{n}** entered so far.\n{you}", ephemeral=True)


def gw_embed(gw):
    end = datetime.fromisoformat(gw["end"])
    ended = gw.get("ended")
    ts = int(end.timestamp())
    boost = em("sb_boost", "🎉")
    star = em("sb_star", "⭐")
    n = len(gw.get("entrants", []))
    if ended:
        desc = (f"{star}  **This giveaway has ended!**\n"
                f"Thanks to everyone who entered — winners are below. 💜")
    else:
        desc = (f"{boost}  **GIVEAWAY**  {boost}\n\n"
                f"Press **🎉 Enter** below for your chance to win!\n"
                f"Ends <t:{ts}:R>  •  <t:{ts}:f>")
    e = discord.Embed(
        title=f"🎁  {gw['prize']}",
        colour=0x5A2DA6 if ended else ACCENT,
        description=desc)
    e.add_field(name="🏆 Winners", value=f"**{gw['winners']}**", inline=True)
    e.add_field(name="👥 Entries", value=f"**{n}**", inline=True)
    e.add_field(name="🎀 Host", value=f"<@{gw['host_id']}>", inline=True)
    reqs = requirement_lines(gw)
    if reqs:
        e.add_field(name="📋 Requirements to enter", value="\n".join(reqs), inline=False)
    if (BASE / "emojis" / "sb_boost.png").exists():
        e.set_thumbnail(url="attachment://logo.png")
    e.set_footer(text="Simple Boosts  •  Good luck! 🍀")
    e.timestamp = end
    return e


def _gw_logo():
    p = BASE / "emojis" / "sb_boost.png"
    return discord.File(p, filename="logo.png") if p.exists() else None


giveaway_group = app_commands.Group(name="giveaway", description="Giveaways",
                                    default_permissions=discord.Permissions(manage_guild=True))


@giveaway_group.command(name="create", description="🎉 Start a giveaway (with optional entry requirements)")
@app_commands.describe(
    duration="e.g. 1h, 2d, 30m",
    winners="How many winners",
    prize="What they win",
    required_role="Entrants must have this role",
    required_invites="Minimum invites needed to enter",
    required_messages="Minimum messages needed to enter",
    message_period="Over what period the messages must be sent",
    min_account_age_days="Minimum account age in days (anti-alt)",
)
@app_commands.choices(message_period=[
    app_commands.Choice(name="Today", value="day"),
    app_commands.Choice(name="This week", value="week"),
    app_commands.Choice(name="This month", value="month"),
    app_commands.Choice(name="All time", value="all"),
])
async def gw_create(i: discord.Interaction, duration: str, winners: int, prize: str,
                    required_role: discord.Role = None,
                    required_invites: int = 0,
                    required_messages: int = 0,
                    message_period: app_commands.Choice[str] = None,
                    min_account_age_days: int = 0):
    secs = parse_duration(duration)
    if not secs:
        return await i.response.send_message("Duration like `1h`, `2d`, `30m`.", ephemeral=True)
    winners = max(1, min(20, winners))
    end = now() + timedelta(seconds=secs)
    gw = {"prize": prize, "winners": winners, "end": end.isoformat(),
          "host": str(i.user), "host_id": i.user.id, "channel": i.channel.id,
          "entrants": [], "ended": False,
          "req_role": required_role.id if required_role else None,
          "req_invites": max(0, required_invites),
          "req_msgs": max(0, required_messages),
          "req_period": (message_period.value if message_period else "all"),
          "req_age_days": max(0, min_account_age_days)}
    await i.response.send_message(f"{em('sb_check','✅')} Giveaway started!", ephemeral=True)
    logo = _gw_logo()
    kwargs = {"embed": gw_embed(gw), "view": GiveawayView()}
    if logo:
        kwargs["file"] = logo
    msg = await i.channel.send(**kwargs)
    store.giveaways[str(msg.id)] = gw
    store.save()
    reqs = requirement_lines(gw)
    await log_to(i.guild, "gwlog_channel", "🎉 Giveaway started",
                 f"**Prize:** {prize}\n**Winners:** {winners}\n**Ends:** <t:{int(end.timestamp())}:R>\n"
                 f"**Host:** {i.user.mention}\n**Channel:** {i.channel.mention}"
                 + (("\n**Requirements:**\n" + "\n".join(reqs)) if reqs else ""))


def _pick_winners(gw, guild):
    """Pick winners from entrants who still qualify (role may have changed)."""
    ent = list(gw.get("entrants", []))
    eligible = []
    for uid in ent:
        m = guild.get_member(uid)
        if m is None:               # left the server
            continue
        if check_requirements(gw, m) is None:
            eligible.append(uid)
    pool = eligible or ent          # fall back to raw entrants if we can't resolve members
    return random.sample(pool, min(gw["winners"], len(pool))) if pool else []


async def end_giveaway(msg_id, gw, guild):
    gw["ended"] = True
    store.save()
    ch = guild.get_channel(int(gw["channel"]))
    if not ch:
        return
    ent = gw.get("entrants", [])
    try:
        msg = await ch.fetch_message(int(msg_id))
    except Exception:
        msg = None
    link = msg.jump_url if msg else ""
    if not ent:
        await ch.send(f"🎉 Giveaway for **{gw['prize']}** ended — no entries 😔")
        await log_to(guild, "gwlog_channel", "🎉 Giveaway ended",
                     f"**Prize:** {gw['prize']}\nNo entries.")
        if msg:
            try:
                await msg.edit(embed=gw_embed(gw), view=None)
            except Exception:
                pass
        return
    winners = _pick_winners(gw, guild)
    if not winners:
        await ch.send(f"🎉 **{gw['prize']}** ended — no eligible entrants met the requirements 😔")
        await log_to(guild, "gwlog_channel", "🎉 Giveaway ended",
                     f"**Prize:** {gw['prize']}\n{len(ent)} entries — none eligible.")
    else:
        mentions = ", ".join(f"<@{w}>" for w in winners)
        gw["last_winners"] = winners
        store.save()
        boost = em("sb_boost", "🎉")
        e = discord.Embed(title=f"{boost}  GIVEAWAY ENDED", colour=0x5A2DA6,
                          description=f"🎁 **{gw['prize']}**\n\n🏆 **Winner(s):** {mentions}\n\nCongratulations! 🥳")
        if link:
            e.add_field(name="Giveaway", value=f"[Jump to message]({link})")
        e.set_footer(text=f"{len(ent)} total entries • Simple Boosts")
        logo = _gw_logo()
        if logo:
            e.set_thumbnail(url="attachment://logo.png")
            await ch.send(content=mentions, embed=e, file=logo)
        else:
            await ch.send(content=mentions, embed=e)
        await log_to(guild, "gwlog_channel", "🎉 Giveaway ended",
                     f"**Prize:** {gw['prize']}\n**Winner(s):** {mentions}\n**Entries:** {len(ent)}")
    if msg:
        try:
            await msg.edit(embed=gw_embed(gw), view=None)
        except Exception:
            pass


@giveaway_group.command(name="end", description="⏹️ End a giveaway now (paste its message ID)")
@app_commands.describe(message_id="Right-click the giveaway message → Copy Message ID")
async def gw_end(i: discord.Interaction, message_id: str):
    gw = store.giveaways.get(message_id)
    if not gw or gw.get("ended"):
        return await i.response.send_message("No active giveaway with that ID.", ephemeral=True)
    await i.response.send_message("⏹️ Ending it now.", ephemeral=True)
    await end_giveaway(message_id, gw, i.guild)


@giveaway_group.command(name="reroll", description="🔄 Reroll an ended giveaway")
async def gw_reroll(i: discord.Interaction, message_id: str):
    gw = store.giveaways.get(message_id)
    if not gw:
        return await i.response.send_message("No giveaway with that ID.", ephemeral=True)
    ent = gw.get("entrants", [])
    if not ent:
        return await i.response.send_message("No entries to reroll.", ephemeral=True)
    w = _pick_winners({**gw, "winners": 1}, i.guild)
    if not w:
        return await i.response.send_message("No eligible entrant to reroll.", ephemeral=True)
    await i.response.send_message(f"🔄 New winner of **{gw['prize']}**: <@{w[0]}> 🥳")
    await log_to(i.guild, "gwlog_channel", "🔄 Giveaway reroll",
                 f"**Prize:** {gw['prize']}\n**New winner:** <@{w[0]}>\n**By:** {i.user.mention}")


async def giveaway_loop():
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            for mid, gw in list(store.giveaways.items()):
                if not gw.get("ended") and now() >= datetime.fromisoformat(gw["end"]):
                    ch = bot.get_channel(int(gw["channel"]))
                    if ch:
                        await end_giveaway(mid, gw, ch.guild)
        except Exception as ex:
            print("giveaway loop:", ex)
        await asyncio.sleep(20)


# ============================================================= BUILD SERVER
BAR = "⠀┃⠀"
BS_ROLES = [("Owner", 0xFF3D9A), ("Admin", 0x19E0FF), ("Staff", 0x37D98A),
            ("Customer", 0xFFCE4D), ("Booster", 0xF47FFF), ("Member", 0x8498A9)]
BS_STAFF = {"Owner", "Admin", "Staff"}
BS_PLAN = [
    {"cat": "📌 Important", "private": False, "channels": [
        ("📜", "rules"), ("📢", "announcements"), ("🌐", "website"),
        ("⭐", "vouches"), ("✈️", "telegram"), ("🎫", "support")]},
    {"cat": "💬 Community", "private": False, "channels": [("💬", "general"), ("🎁", "giveaways")]},
    {"cat": "🔒 Logs", "private": True, "channels": [
        ("📝", "message-logs"), ("🛡️", "mod-logs"), ("🚪", "join-leave")]},
]


async def _build(guild):
    made = {"roles": 0, "cats": 0, "channels": 0}
    roles = {}
    for name, color in BS_ROLES:
        r = discord.utils.get(guild.roles, name=name)
        if not r:
            r = await guild.create_role(name=name, colour=discord.Colour(color), reason="buildserver")
            made["roles"] += 1
        roles[name] = r
    for block in BS_PLAN:
        ow = None
        if block["private"]:
            ow = {guild.default_role: discord.PermissionOverwrite(view_channel=False),
                  guild.me: discord.PermissionOverwrite(view_channel=True)}
            for rn in BS_STAFF:
                if roles.get(rn):
                    ow[roles[rn]] = discord.PermissionOverwrite(view_channel=True)
        cat = discord.utils.get(guild.categories, name=block["cat"])
        if not cat:
            cat = await guild.create_category(block["cat"], overwrites=ow or {}, reason="buildserver")
            made["cats"] += 1
        for emoji, nm in block["channels"]:
            full = f"{emoji}{BAR}{nm}".lower()
            if discord.utils.get(guild.text_channels, name=full):
                continue
            await guild.create_text_channel(f"{emoji}{BAR}{nm}", category=cat, overwrites=ow or {}, reason="buildserver")
            made["channels"] += 1
    return made


class BuildConfirm(discord.ui.View):
    def __init__(self, uid):
        super().__init__(timeout=60)
        self.uid = uid

    async def interaction_check(self, i):
        return i.user.id == self.uid

    @discord.ui.button(label="Build it", style=discord.ButtonStyle.success)
    async def go(self, i, b):
        await i.response.edit_message(content="⚙️ Building…", view=None)
        try:
            m = await _build(i.guild)
        except discord.Forbidden:
            return await i.edit_original_response(content="❌ I need **Administrator**, with my role near the top.")
        await i.edit_original_response(
            content=f"✅ Done — {m['cats']} categories, {m['channels']} channels, {m['roles']} roles. Logs are staff-only.")

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def no(self, i, b):
        await i.response.edit_message(content="Cancelled.", view=None)


@bot.tree.command(name="buildserver", description="🏗️ Build the whole server: channels, roles, private logs")
@is_admin()
async def buildserver(i: discord.Interaction):
    total = sum(len(b["channels"]) for b in BS_PLAN)
    await i.response.send_message(
        f"This makes **{len(BS_PLAN)}** categories, **{total}** channels and **{len(BS_ROLES)}** roles "
        "(skips existing). Build it?", view=BuildConfirm(i.user.id), ephemeral=True)


# ==================================================================== TICKETS
TICKET_CAT = "🎫 Tickets"
TRANSCRIPT_NAME = "📑┃transcripts"

# Ticket categories for the panel dropdown:
#   (label, description, emoji-name-or-unicode, kind)
TICKET_CATEGORIES = [
    ("Purchase Boosts", "Order boosts — only if the website is down", "sb_boost", "buy"),
    ("Support", "Order issues, questions, or anything else", "💬", "support"),
]


async def ensure_emojis(guild):
    """Upload the bot's custom emojis to the server if they're not there yet."""
    existing = {e.name: e for e in guild.emojis}
    for name in EMOJI_FILES:
        if name in existing:
            EMOJI[name] = existing[name]
            continue
        p = BASE / "emojis" / f"{name}.png"
        if not p.exists():
            continue
        try:
            EMOJI[name] = await guild.create_custom_emoji(
                name=name, image=p.read_bytes(), reason="Simple Boosts branding")
        except Exception as ex:
            print(f"emoji {name}: {ex}")


def _emoji_for(token):
    """token is a custom-emoji name we uploaded, or a plain unicode emoji."""
    return EMOJI.get(token, token)


def _ticket_overwrites(guild, opener):
    role = guild.get_role(ticket_role_id(guild))
    ow = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True,
                                              manage_channels=True, read_message_history=True),
        opener: discord.PermissionOverwrite(view_channel=True, send_messages=True,
                                            attach_files=True, embed_links=True,
                                            read_message_history=True),
    }
    if role:
        ow[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True,
                                               read_message_history=True)
    return ow


async def _transcript_channel(guild):
    ch = discord.utils.find(lambda c: c.name.endswith("transcripts"), guild.text_channels)
    if ch:
        return ch
    role = guild.get_role(ticket_role_id(guild))
    ow = {guild.default_role: discord.PermissionOverwrite(view_channel=False),
          guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True)}
    if role:
        ow[role] = discord.PermissionOverwrite(view_channel=True, send_messages=False)
    cat = discord.utils.get(guild.categories, name=TICKET_CAT)
    try:
        return await guild.create_text_channel(TRANSCRIPT_NAME, overwrites=ow, category=cat)
    except Exception:
        return None


def _render_transcript(channel, messages, closer):
    opened = ""
    meta = store.tickets.get(str(channel.id), {})
    if meta.get("at"):
        opened = meta["at"][:16].replace("T", " ")
    rows = []
    for m in messages:
        av = m.author.display_avatar.url
        name = _html.escape(m.author.display_name)
        uname = _html.escape(str(m.author))
        ts = m.created_at.strftime("%d %b %Y · %H:%M")
        content = _html.escape(m.content or "")
        # resolve user mentions to @name
        for u in m.mentions:
            content = content.replace(f"&lt;@{u.id}&gt;", f"@{_html.escape(u.display_name)}")
            content = content.replace(f"&lt;@!{u.id}&gt;", f"@{_html.escape(u.display_name)}")
        content = content.replace("\n", "<br>")
        atts = ""
        for a in m.attachments:
            if a.content_type and a.content_type.startswith("image"):
                atts += f'<a href="{_html.escape(a.url)}" target="_blank"><img class="att-img" src="{_html.escape(a.url)}"></a>'
            else:
                atts += f'<div class="att"><a href="{_html.escape(a.url)}" target="_blank">📎 {_html.escape(a.filename)}</a></div>'
        embs = ""
        for eb in m.embeds:
            et = _html.escape(eb.title or "")
            ed = _html.escape(eb.description or "").replace("\n", "<br>")
            col = f"#{eb.colour.value:06x}" if eb.colour else "#8A4DFF"
            embs += (f'<div class="embed" style="border-left:4px solid {col}">'
                     f'{"<div class=et>"+et+"</div>" if et else ""}'
                     f'{"<div class=ed>"+ed+"</div>" if ed else ""}</div>')
        if not content and not atts and not embs:
            content = '<span class="sys">(no text)</span>'
        rows.append(f'''<div class="msg"><img class="av" src="{av}" loading="lazy">
<div class="body"><div class="head"><span class="name">{name}</span>
<span class="uname">{uname}</span><span class="ts">{ts}</span></div>
<div class="content">{content}{atts}{embs}</div></div></div>''')
    body = "\n".join(rows) or '<div class="sys">No messages.</div>'
    title = _html.escape(channel.name)
    return f'''<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Transcript · {title}</title>
<style>
:root{{color-scheme:dark}}
*{{box-sizing:border-box}}
body{{margin:0;background:#313338;color:#dbdee1;font:15px/1.45 "gg sans","Segoe UI",Roboto,Helvetica,Arial,sans-serif}}
.top{{position:sticky;top:0;background:#2b2d31;border-bottom:1px solid #1f2023;padding:16px 22px;z-index:5}}
.top h1{{margin:0;font-size:18px;color:#fff}}
.top .sub{{color:#949ba4;font-size:13px;margin-top:4px}}
.wrap{{max-width:900px;margin:0 auto;padding:18px 22px 60px}}
.msg{{display:flex;gap:16px;padding:8px 0;margin-top:10px}}
.av{{width:42px;height:42px;border-radius:50%;flex:0 0 42px;background:#1e1f22}}
.body{{min-width:0;flex:1}}
.head{{display:flex;align-items:baseline;gap:8px;flex-wrap:wrap}}
.name{{color:#fff;font-weight:600}}
.uname{{color:#949ba4;font-size:12px}}
.ts{{color:#949ba4;font-size:12px}}
.content{{color:#dbdee1;word-wrap:break-word;white-space:normal}}
.att{{margin-top:6px}}
.att a{{color:#00a8fc;text-decoration:none}}
.att-img{{max-width:360px;max-height:300px;border-radius:8px;margin-top:6px;display:block}}
.embed{{margin-top:6px;background:#2b2d31;border-radius:4px;padding:8px 12px;max-width:460px}}
.embed .et{{color:#fff;font-weight:600}}
.embed .ed{{color:#dbdee1;font-size:14px;margin-top:2px}}
.sys{{color:#949ba4;font-style:italic}}
a{{color:#00a8fc}}
</style></head><body>
<div class="top"><h1>#{title}</h1>
<div class="sub">{len(messages)} messages · opened {opened} · closed by {_html.escape(str(closer))} · {now().strftime("%d %b %Y · %H:%M")} UTC</div></div>
<div class="wrap">{body}</div></body></html>'''


async def close_ticket(channel, closer):
    try:
        messages = [m async for m in channel.history(limit=None, oldest_first=True)]
    except Exception:
        messages = []
    html_doc = _render_transcript(channel, messages, closer)
    meta = store.tickets.get(str(channel.id), {})
    opener_id = meta.get("opener")
    tch = await _transcript_channel(channel.guild)
    if tch:
        fobj = discord.File(io.BytesIO(html_doc.encode("utf-8")),
                            filename=f"{channel.name}.html")
        e = discord.Embed(title="📑 Ticket transcript", colour=ACCENT,
                          description=f"**Ticket:** #{channel.name}\n"
                                      f"**Opened by:** <@{opener_id}>\n"
                                      f"**Closed by:** {closer.mention}\n"
                                      f"**Messages:** {len(messages)}")
        if meta.get("kind"):
            e.add_field(name="Type", value=("🛒 Purchase" if meta["kind"] == "buy" else "💬 Support"))
        if meta.get("pay"):
            e.add_field(name="Payment", value=meta["pay"])
        e.timestamp = now()
        try:
            await tch.send(embed=e, file=fobj)
        except Exception as ex:
            print("transcript send:", ex)
    if str(channel.id) in store.tickets:
        store.tickets[str(channel.id)]["closed"] = True
        store.save()
    await asyncio.sleep(2)
    try:
        await channel.delete(reason=f"Ticket closed by {closer}")
    except Exception:
        pass


async def open_ticket(i, kind, pay=None, invoice=None):
    guild = i.guild
    if guild is None:
        return await i.response.send_message("Use this in a server.", ephemeral=True)
    # channel creation can take a moment — ack the interaction first
    if not i.response.is_done():
        await i.response.defer(ephemeral=True)
    for cid, meta in list(store.tickets.items()):
        if meta.get("opener") == i.user.id and not meta.get("closed"):
            ch = guild.get_channel(int(cid))
            if ch:
                return await i.followup.send(
                    f"⚠️ You already have an open ticket: {ch.mention}", ephemeral=True)
    cat = discord.utils.get(guild.categories, name=TICKET_CAT)
    if not cat:
        try:
            cat = await guild.create_category(TICKET_CAT)
        except Exception:
            cat = None
    label = {"buy": "boosts", "support": "support"}.get(kind, "ticket")
    counters = store.config.setdefault("ticket_counters", {})
    num = counters.get(kind, 0) + 1
    chname = f"{label}-{num:04d}"   # e.g. boosts-0001 / support-0001
    try:
        ch = await guild.create_text_channel(
            chname, category=cat, overwrites=_ticket_overwrites(guild, i.user))
    except discord.Forbidden:
        return await i.followup.send(
            "❌ I need the **Manage Channels** permission (give me Administrator).", ephemeral=True)
    counters[kind] = num   # only bump the counter once the channel actually exists
    store.tickets[str(ch.id)] = {"opener": i.user.id, "kind": kind, "pay": pay,
                                 "invoice": invoice, "at": now().isoformat(), "closed": False}
    store.save()
    role = guild.get_role(ticket_role_id(guild))
    ping = role.mention if role else ""
    if kind == "buy":
        fee = PAY_FEE.get(pay)
        pe = em(f"sb_{pay.lower()}") if pay else ""
        e = discord.Embed(
            title=f"{em('sb_boost', '🛒')}  Purchase Boosts",
            colour=ACCENT,
            description=("Thanks for your order! A staff member will be with you shortly.\n\n"
                         "**Please send:**\n"
                         "› What you want to buy\n"
                         "› How many / how long\n"
                         "› Your server invite (if boosting a server)"))
        e.add_field(name="Payment", value=f"{pe} **{pay}**".strip(), inline=True)
        e.add_field(name="Fee", value=(f"+{fee}" if fee else "No fee"), inline=True)
    else:
        e = discord.Embed(
            title="💬  Support",
            colour=ACCENT,
            description=("Thanks for reaching out! Describe your issue in as much detail as you "
                        "can and a staff member will help you out.\n\n"
                        "📦 **Having an issue with an order?** Please include your **order ID** "
                        "so we can find it quickly."))
    e.add_field(name="Opened by", value=i.user.mention, inline=True)
    e.set_footer(text="Simple Boosts • Only staff can close this ticket")
    await ch.send(content=f"{ping} {i.user.mention}".strip(),
                  embed=e, view=TicketCloseView(),
                  allowed_mentions=discord.AllowedMentions(roles=True, users=True))
    if kind == "buy":
        fee = PAY_FEE.get(pay)
        await log_to(guild, "orders_channel", "🛒 New order",
                     f"{i.user.mention} opened {ch.mention}\n"
                     f"Payment: **{pay}**" + (f" (+{fee})" if fee else " (no fee)"))
    await i.followup.send(f"{em('sb_check','✅')} Ticket created: {ch.mention}", ephemeral=True)


class PaymentSelect(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)
        opts = []
        for name, ename, fee in PAY_METHODS:
            opts.append(discord.SelectOption(
                label=name, value=name,
                description=(f"+{fee} fee" if fee else "No fee"),
                emoji=_emoji_for(ename)))
        self.sel = discord.ui.Select(placeholder="Choose your payment method…", options=opts)
        self.sel.callback = self._picked
        self.add_item(self.sel)

    async def _picked(self, i: discord.Interaction):
        await open_ticket(i, "buy", pay=self.sel.values[0])


class TicketPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        opts = []
        for label, desc, etok, kind in TICKET_CATEGORIES:
            opts.append(discord.SelectOption(label=label, description=desc, value=kind,
                                             emoji=_emoji_for(etok)))
        self.sel = discord.ui.Select(placeholder="Choose a ticket category…", options=opts,
                                     custom_id="tkt:category", min_values=1, max_values=1)
        self.sel.callback = self._picked
        self.add_item(self.sel)

    async def _picked(self, i: discord.Interaction):
        kind = self.sel.values[0]
        if kind == "buy":
            await i.response.send_message(
                "⚠️ **Only open a purchase ticket if the website is down.**\n"
                "Otherwise please order on the website.\n\nSelect how you'd like to pay:",
                view=PaymentSelect(), ephemeral=True)
        else:
            await open_ticket(i, "support")


class TicketCloseView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Close Ticket", emoji="🔒",
                       style=discord.ButtonStyle.danger, custom_id="tkt:close")
    async def close(self, i: discord.Interaction, b: discord.ui.Button):
        role_id = ticket_role_id(i.guild)
        if not any(r.id == role_id for r in i.user.roles):
            return await i.response.send_message(
                "🔒 Only staff can close this ticket.", ephemeral=True)
        await i.response.send_message("📦 Saving transcript & closing in a moment…", ephemeral=True)
        await close_ticket(i.channel, i.user)


ticket_group = app_commands.Group(name="ticket", description="Ticket system",
                                  default_permissions=discord.Permissions(manage_guild=True))


@ticket_group.command(name="panel", description="🎫 Post the ticket panel in this channel")
@app_commands.describe(banner="Show the Simple Boosts banner on top (default: yes)")
async def ticket_panel(i: discord.Interaction, banner: bool = True):
    await i.response.defer(ephemeral=True)
    await ensure_emojis(i.guild)
    tick = em("sb_ticket", "🎫")
    arr = em("sb_arrow", "›")
    paytxt = "   ".join(f"{em('sb_'+n.lower())} **{n}**" + (f" `+{f}`" if f else " `free`")
                        for n, _e, f in PAY_METHODS)
    body = discord.Embed(
        colour=ACCENT,
        title=f"{tick}  Simple Boosts Support",
        description="**Your #1 leading boost provider**\n"
                    "Open a ticket below and the right team will take care of you. 💜")
    body.add_field(
        name=f"{em('sb_boost','💬')}  How can we help?",
        value="Pick the category that fits your request from the menu below.",
        inline=False)
    body.add_field(
        name="📌  Before you open a ticket",
        value=(f"{arr} Read the server **rules** first.\n"
               f"{arr} Pick the **category** that matches your request.\n"
               f"{arr} Issue with an order? Have your **order ID** ready."),
        inline=False)
    body.add_field(
        name=f"{em('sb_usdt','💳')}  Payments we accept",
        value=f"{paytxt}\n*LTC is free · USDT & BTC have a $1 fee.*",
        inline=False)
    body.set_footer(text="Simple Boosts · Your #1 leading boost provider")

    logo = None
    if (BASE / "emojis" / "sb_boost.png").exists():
        logo = discord.File(BASE / "emojis" / "sb_boost.png", filename="logo.png")
        body.set_thumbnail(url="attachment://logo.png")

    embeds = [body]
    files = [f for f in [logo] if f]
    if banner and BANNER_FILE.exists():
        files.append(discord.File(BANNER_FILE, filename="banner.gif"))
        top = discord.Embed(colour=ACCENT)
        top.set_image(url="attachment://banner.gif")
        embeds = [top, body]   # banner renders ABOVE the content embed
    kwargs = {"embeds": embeds, "view": TicketPanelView()}
    if files:
        kwargs["files"] = files
    await i.channel.send(**kwargs)
    await i.followup.send(f"{em('sb_check','✅')} Panel posted.", ephemeral=True)


@ticket_group.command(name="setrole", description="🎫 Set which role is pinged & can close tickets")
async def ticket_setrole(i: discord.Interaction, role: discord.Role):
    store.config["ticket_role"] = role.id
    store.save()
    await i.response.send_message(f"✅ Ticket staff role set to {role.mention}.", ephemeral=True)


# ===================================================================== HELP
@bot.tree.command(name="help", description="📖 List everything the bot can do")
async def help_cmd(i: discord.Interaction):
    e = discord.Embed(title="📖 Commands", colour=ACCENT)
    e.add_field(name="🔨 Moderation",
                value="`/warn` `/mute` `/unmute` `/kick` `/ban` `/unban` `/purge` `/lock` `/unlock` `/slowmode` `/modlogs` `/clearlogs` `/nuke`", inline=False)
    e.add_field(name="⭐ Vouches", value="`/vouch order rating comment` `/vouches` `/delvouch`", inline=False)
    e.add_field(name="🎉 Giveaways", value="`/giveaway create` `/giveaway end` `/giveaway reroll`\n*requirements: role · invites · messages · account age*", inline=False)
    e.add_field(name="🎫 Tickets", value="`/ticket panel` `/ticket setrole`", inline=False)
    e.add_field(name="🛠️ Setup & build", value="`/setup channels` `/setup makechannel` `/buildserver` `/embed create`", inline=False)
    await i.response.send_message(embed=e, ephemeral=True)


# ==================================================================== EVENTS
@bot.event
async def on_message_delete(m):
    if not m.guild or m.author.bot:
        return
    ch = log_channel(m.guild, "msglog_channel")   # message logs, NOT mod-logs
    if ch:
        e = discord.Embed(description=(m.content or "*no text*")[:3000], colour=BAD, timestamp=now())
        e.set_author(name=f"Message deleted · {m.author}", icon_url=m.author.display_avatar.url)
        e.add_field(name="Channel", value=m.channel.mention)
        try:
            await ch.send(embed=e)
        except Exception:
            pass


@bot.event
async def on_message_edit(before, after):
    if not before.guild or before.author.bot or before.content == after.content:
        return
    ch = log_channel(before.guild, "msglog_channel")
    if ch:
        e = discord.Embed(colour=WARN, timestamp=now())
        e.set_author(name=f"Message edited · {before.author}", icon_url=before.author.display_avatar.url)
        e.add_field(name="Before", value=(before.content or "—")[:1000], inline=False)
        e.add_field(name="After", value=(after.content or "—")[:1000], inline=False)
        e.add_field(name="Channel", value=before.channel.mention, inline=False)
        try:
            await ch.send(embed=e)
        except Exception:
            pass


@bot.event
async def on_message(message):
    # count messages for giveaway requirements, then let commands run
    if message.guild and not message.author.bot:
        try:
            bump_message(message.guild.id, message.author.id)
        except Exception:
            pass
        # keep the "how to vouch" sticky pinned to the bottom of the vouch channel
        if message.channel.id == vouch_channel_id():
            try:
                await update_vouch_sticky(message.guild)
            except Exception:
                pass
    await bot.process_commands(message)


# -------- invite tracking (attribute joins to the inviter) --------
invite_cache = {}  # guild_id -> {code: uses}


async def refresh_invites(guild):
    try:
        invite_cache[guild.id] = {inv.code: inv.uses for inv in await guild.invites()}
    except Exception:
        invite_cache[guild.id] = invite_cache.get(guild.id, {})


@bot.event
async def on_invite_create(invite):
    await refresh_invites(invite.guild)


@bot.event
async def on_invite_delete(invite):
    await refresh_invites(invite.guild)


@bot.event
async def on_member_join(member):
    # figure out who invited them, bump that inviter's count
    try:
        before = invite_cache.get(member.guild.id, {})
        after = {inv.code: inv.uses for inv in await member.guild.invites()}
        inviter_id = None
        for code, uses in after.items():
            if uses > before.get(code, 0):
                inv = discord.utils.get(await member.guild.invites(), code=code)
                if inv and inv.inviter:
                    inviter_id = inv.inviter.id
                break
        invite_cache[member.guild.id] = after
        if inviter_id and inviter_id != member.id:
            g = store.invites.setdefault(str(member.guild.id), {})
            g[str(inviter_id)] = g.get(str(inviter_id), 0) + 1
            store.save()
    except Exception:
        pass

    ch = log_channel(member.guild, "log_channel")
    if ch:
        try:
            await ch.send(embed=discord.Embed(description=f"📥 {member.mention} **joined** ({member})",
                                              colour=GOOD, timestamp=now()))
        except Exception:
            pass


@bot.event
async def on_member_remove(member):
    ch = log_channel(member.guild, "log_channel")
    if ch:
        try:
            await ch.send(embed=discord.Embed(description=f"📤 **{member}** left", colour=BAD, timestamp=now()))
        except Exception:
            pass


@bot.event
async def setup_hook():
    bot.tree.add_command(setup_group)
    bot.tree.add_command(embed_group)
    bot.tree.add_command(giveaway_group)
    bot.tree.add_command(ticket_group)
    bot.add_view(GiveawayView())
    bot.add_view(TicketPanelView())
    bot.add_view(TicketCloseView())
    bot.loop.create_task(giveaway_loop())


_synced = False


@bot.event
async def on_ready():
    global _synced
    print(f"Logged in as {bot.user} (id {bot.user.id})")
    if not bot.guilds:
        print("⚠️  I'm not in ANY server yet. Invite me with the link, "
              "then I'll sync automatically.")
    else:
        print(f"I'm in {len(bot.guilds)} server(s):")
        for g in bot.guilds:
            print(f"   • {g.name}  (id {g.id})")
    for g in bot.guilds:
        await refresh_invites(g)
        await ensure_emojis(g)
        await ensure_log_channels(g)
        await update_vouch_sticky(g)
    if _synced:
        return
    _synced = True
    # Sync slash commands to EVERY server the bot is in — so the commands
    # show up wherever it actually landed, no matter the GUILD_ID.
    for g in bot.guilds:
        try:
            bot.tree.copy_global_to(guild=g)
            await bot.tree.sync(guild=g)
            print(f"✅ Commands synced to {g.name}")
        except Exception as e:
            print(f"❌ Couldn't sync to {g.name}: {e}")


@bot.event
async def on_guild_join(guild):
    # The moment someone adds the bot to a server, sync commands there.
    print(f"➕ Joined {guild.name} (id {guild.id}) — syncing commands…")
    try:
        bot.tree.copy_global_to(guild=guild)
        await bot.tree.sync(guild=guild)
        print(f"✅ Commands synced to {guild.name}")
    except Exception as e:
        print(f"❌ Couldn't sync to {guild.name}: {e}")
    try:
        await ensure_emojis(guild)
    except Exception:
        pass


if __name__ == "__main__":
    bot.run(TOKEN)

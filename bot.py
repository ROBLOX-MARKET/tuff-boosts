"""
M&E Lean Bot — moderation, vouches, giveaways, embeds, channel nuke, server build.
Lightweight (no image rendering) so it runs comfortably under 100 MB RAM.

Environment variables (set on your host, never in code/chat):
  BOT_TOKEN   - your bot token (reset it if it ever leaks)   [required]
  GUILD_ID    - your server ID (OPTIONAL now — the bot auto-syncs to every
                server it's in, so you don't even need this)
"""

import asyncio
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
        self.vouches = d.get("vouches", {})      # seller_id -> [ {stars, text, by, at} ]
        self.giveaways = d.get("giveaways", {})  # message_id -> {...}

    def save(self):
        tmp = DATA_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps({
            "config": self.config, "cases": self.cases,
            "vouches": self.vouches, "giveaways": self.giveaways}, indent=2), "utf-8")
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
    names = {"modlog_channel": "mod-logs", "log_channel": "join-leave", "vouch_channel": "vouches"}
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


# ====================================================================== SETUP
setup_group = app_commands.Group(name="setup", description="Configure the bot",
                                 default_permissions=discord.Permissions(administrator=True))


@setup_group.command(name="channels", description="Set the mod-log, vouch and join/leave channels")
@app_commands.describe(modlog="Where mod actions are logged", vouches="Where vouches post",
                       joinleave="Where joins/leaves are logged")
async def setup_channels(i: discord.Interaction, modlog: discord.TextChannel = None,
                         vouches: discord.TextChannel = None, joinleave: discord.TextChannel = None):
    if modlog:
        store.config["modlog_channel"] = modlog.id
    if vouches:
        store.config["vouch_channel"] = vouches.id
    if joinleave:
        store.config["log_channel"] = joinleave.id
    store.save()
    await i.response.send_message("✅ Saved channel settings.", ephemeral=True)


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
@bot.tree.command(name="vouch", description="⭐ Vouch for someone you bought from")
@app_commands.describe(seller="Who you're vouching for", stars="1-5", comment="Your review")
async def vouch(i: discord.Interaction, seller: discord.Member, stars: int, comment: str):
    if seller.id == i.user.id:
        return await i.response.send_message("You can't vouch for yourself.", ephemeral=True)
    stars = max(1, min(5, stars))
    store.vouches.setdefault(str(seller.id), []).append(
        {"stars": stars, "text": comment, "by": str(i.user), "at": now().isoformat()})
    store.save()
    e = discord.Embed(description=f"{'⭐' * stars}\n\n> {comment}", colour=WARN, timestamp=now())
    e.set_author(name=f"Vouch for {seller.display_name}", icon_url=seller.display_avatar.url)
    e.set_footer(text=f"by {i.user}")
    ch = log_channel(i.guild, "vouch_channel")
    if ch and ch.id != i.channel.id:
        await ch.send(embed=e)
        await i.response.send_message(f"✅ Vouch posted in {ch.mention}!", ephemeral=True)
    else:
        await i.response.send_message(embed=e)


@bot.tree.command(name="vouches", description="🔎 See someone's vouches")
async def vouches(i: discord.Interaction, seller: discord.Member):
    vs = store.vouches.get(str(seller.id), [])
    if not vs:
        return await i.response.send_message(f"{seller.mention} has no vouches yet.", ephemeral=True)
    avg = sum(v["stars"] for v in vs) / len(vs)
    e = discord.Embed(title=f"{seller.display_name}'s vouches",
                      description=f"**{len(vs)}** vouches · avg {avg:.1f}⭐", colour=WARN)
    for v in vs[-10:]:
        e.add_field(name=f"{'⭐' * v['stars']} · {v['by']}", value=v["text"][:200], inline=False)
    await i.response.send_message(embed=e)


@bot.tree.command(name="delvouch", description="🗑️ Delete someone's vouches (admin)")
@is_admin()
async def delvouch(i: discord.Interaction, seller: discord.Member):
    store.vouches.pop(str(seller.id), None)
    store.save()
    await i.response.send_message(f"🗑️ Cleared {seller.mention}'s vouches.", ephemeral=True)


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

    @discord.ui.button(label="🎉 Enter", style=discord.ButtonStyle.primary, custom_id="gw:enter")
    async def enter(self, i: discord.Interaction, b: discord.ui.Button):
        gw = store.giveaways.get(str(i.message.id))
        if not gw or gw.get("ended"):
            return await i.response.send_message("This giveaway is over.", ephemeral=True)
        ent = gw.setdefault("entrants", [])
        if i.user.id in ent:
            ent.remove(i.user.id)
            store.save()
            return await i.response.send_message("You left the giveaway.", ephemeral=True)
        ent.append(i.user.id)
        store.save()
        await i.response.send_message("✅ You're entered! Good luck 🍀", ephemeral=True)


def gw_embed(gw):
    end = datetime.fromisoformat(gw["end"])
    e = discord.Embed(title=f"🎉 {gw['prize']}", colour=ACCENT,
                      description=(f"Click **Enter** below!\n\n"
                                  f"Ends: <t:{int(end.timestamp())}:R>\n"
                                  f"Winners: **{gw['winners']}**\n"
                                  f"Entries: **{len(gw.get('entrants', []))}**"))
    e.set_footer(text=f"Hosted by {gw['host']}")
    return e


giveaway_group = app_commands.Group(name="giveaway", description="Giveaways",
                                    default_permissions=discord.Permissions(manage_guild=True))


@giveaway_group.command(name="create", description="🎉 Start a giveaway")
@app_commands.describe(duration="e.g. 1h, 2d, 30m", winners="How many winners", prize="What they win")
async def gw_create(i: discord.Interaction, duration: str, winners: int, prize: str):
    secs = parse_duration(duration)
    if not secs:
        return await i.response.send_message("Duration like `1h`, `2d`, `30m`.", ephemeral=True)
    winners = max(1, min(20, winners))
    end = now() + timedelta(seconds=secs)
    gw = {"prize": prize, "winners": winners, "end": end.isoformat(),
          "host": str(i.user), "host_id": i.user.id, "channel": i.channel.id,
          "entrants": [], "ended": False}
    await i.response.send_message("🎉 Giveaway started!", ephemeral=True)
    msg = await i.channel.send(embed=gw_embed(gw), view=GiveawayView())
    store.giveaways[str(msg.id)] = gw
    store.save()


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
    if not ent:
        if msg:
            await ch.send(f"🎉 Giveaway for **{gw['prize']}** ended — no entries 😔")
        return
    winners = random.sample(ent, min(gw["winners"], len(ent)))
    mentions = ", ".join(f"<@{w}>" for w in winners)
    await ch.send(f"🎉 **GIVEAWAY ENDED** — **{gw['prize']}**\nWinner(s): {mentions}\nCongrats! 🥳")
    if msg:
        try:
            e = msg.embeds[0]
            e.description = f"🎉 Ended!\nWinner(s): {mentions}\nEntries: **{len(ent)}**"
            await msg.edit(embed=e, view=None)
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
    w = random.choice(ent)
    await i.response.send_message(f"🔄 New winner of **{gw['prize']}**: <@{w}> 🥳")


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


# ===================================================================== HELP
@bot.tree.command(name="help", description="📖 List everything the bot can do")
async def help_cmd(i: discord.Interaction):
    e = discord.Embed(title="📖 Commands", colour=ACCENT)
    e.add_field(name="🔨 Moderation",
                value="`/warn` `/mute` `/unmute` `/kick` `/ban` `/unban` `/purge` `/lock` `/unlock` `/slowmode` `/modlogs` `/clearlogs` `/nuke`", inline=False)
    e.add_field(name="⭐ Vouches", value="`/vouch` `/vouches` `/delvouch`", inline=False)
    e.add_field(name="🎉 Giveaways", value="`/giveaway create` `/giveaway end` `/giveaway reroll`", inline=False)
    e.add_field(name="🛠️ Setup & build", value="`/setup channels` `/buildserver` `/embed create`", inline=False)
    await i.response.send_message(embed=e, ephemeral=True)


# ==================================================================== EVENTS
@bot.event
async def on_message_delete(m):
    if not m.guild or m.author.bot:
        return
    ch = log_channel(m.guild, "modlog_channel")
    if ch:
        e = discord.Embed(description=(m.content or "*no text*")[:3000], colour=BAD, timestamp=now())
        e.set_author(name=f"Message deleted · {m.author}", icon_url=m.author.display_avatar.url)
        e.add_field(name="Channel", value=m.channel.mention)
        try:
            await ch.send(embed=e)
        except Exception:
            pass


@bot.event
async def on_member_join(member):
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
    bot.add_view(GiveawayView())
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


if __name__ == "__main__":
    bot.run(TOKEN)

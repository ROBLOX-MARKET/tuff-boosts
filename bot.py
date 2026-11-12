"""M&E's Products bot: moderation + logs, tickets, sales, welcome, embeds and giveaways.

All settings live in the .env file, so you should not need to edit this file.
Saved data goes in mod_data.json next to this file.
"""

import asyncio
import io
import json
import logging
import os
import random
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
DATA_FILE = BASE_DIR / "mod_data.json"
load_dotenv(BASE_DIR / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("me-bot")


# ===========================================================================
# Settings (.env)
# ===========================================================================


def setting(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def id_setting(name: str, required: bool = False) -> int | None:
    value = setting(name)
    if not value or value.startswith("PASTE_"):
        if required:
            raise SystemExit(f"Missing setting: fill in {name} in your .env file.")
        return None
    if not value.isdigit():
        raise SystemExit(f"{name} in your .env file must be a Discord ID (numbers only).")
    return int(value)


BOT_TOKEN = setting("BOT_TOKEN")
if not BOT_TOKEN or BOT_TOKEN.startswith("PASTE_"):
    raise SystemExit("Missing setting: fill in BOT_TOKEN in your .env file.")

GUILD_ID = id_setting("GUILD_ID", required=True)
# Channels and roles are set inside Discord with /setup. These .env names still work as a fallback.
ENV_IDS = {
    "staff_role": id_setting("STAFF_ROLE_ID"),
    "ticket_category": id_setting("TICKET_CATEGORY_ID") or 1570362185182482462,
    "transcript_channel": id_setting("TRANSCRIPT_CHANNEL_ID"),
    "mod_log_channel": id_setting("MOD_LOG_CHANNEL_ID"),
    "sales_channel": id_setting("SALES_CHANNEL_ID"),
    "welcome_channel": id_setting("WELCOME_CHANNEL_ID"),
    "auto_role": id_setting("AUTO_ROLE_ID"),
    "rules_channel": id_setting("RULES_CHANNEL_ID"),
}
BANNER_FILE = BASE_DIR / "banner.gif"  # put your banner next to bot.py with this name

STORE_NAME = setting("STORE_NAME", "M&E's Products") or "M&E's Products"
STORE_TAGLINE = setting("STORE_TAGLINE")
BANNER_URL = setting("BANNER_URL")
WELCOME_BANNER_URL = setting("WELCOME_BANNER_URL")
WELCOME_MESSAGE = setting("WELCOME_MESSAGE").replace("\\n", "\n")
try:
    COLOUR = discord.Colour.from_str(setting("ACCENT_COLOUR", "#5BC8F5") or "#5BC8F5")
except ValueError:
    COLOUR = discord.Colour.from_str("#5BC8F5")


# ===========================================================================
# Saved data
# ===========================================================================


class Store:
    def __init__(self, path: Path):
        self.path = path
        self.dirty = False
        self.data = {
            "ticket_count": 0,
            "case_count": 0,
            "sale_count": 0,
            "modlogs": {},      # user_id -> [ {case, action, reason, mod, time, duration} ]
            "sales": [],        # [ {id, product, price, buyer, seller, date, time} ]
            "messages": {},     # user_id -> messages sent (for giveaway requirements)
            "invites": {},      # inviter_id -> invites
            "invited_by": {},   # member_id -> inviter_id
            "giveaways": {},    # message_id -> giveaway
            "panels": {},       # message_id -> banner reference
        }
        if path.exists():
            try:
                self.data.update(json.loads(path.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                log.exception("mod_data.json couldn't be read, starting fresh.")

    def save(self) -> None:
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, self.path)
        self.dirty = False

    def next(self, key: str) -> int:
        self.data[key] = self.data.get(key, 0) + 1
        self.save()
        return self.data[key]


store = Store(DATA_FILE)


def cfg(name: str) -> int | None:
    value = store.data.get("config", {}).get(name)
    return value if value else ENV_IDS.get(name)


# ===========================================================================
# Helpers
# ===========================================================================


def now() -> datetime:
    return datetime.now(timezone.utc)


def ts(moment: datetime | float, style: str = "f") -> str:
    value = moment.timestamp() if isinstance(moment, datetime) else moment
    return f"<t:{int(value)}:{style}>"


def is_admin(member: discord.Member) -> bool:
    return member.guild_permissions.administrator


def is_staff(member: discord.Member) -> bool:
    staff = cfg("staff_role")
    return is_admin(member) or (staff is not None and any(r.id == staff for r in member.roles))


def admin_only():
    async def predicate(interaction: discord.Interaction) -> bool:
        if isinstance(interaction.user, discord.Member) and is_admin(interaction.user):
            return True
        raise app_commands.CheckFailure("Only admins can use this command.")
    return app_commands.check(predicate)


def staff_only():
    async def predicate(interaction: discord.Interaction) -> bool:
        if isinstance(interaction.user, discord.Member) and is_staff(interaction.user):
            return True
        raise app_commands.CheckFailure("Only staff can use this command.")
    return app_commands.check(predicate)


DURATION_RE = re.compile(r"(\d+)\s*(w|d|h|m|s)", re.I)
UNITS = {"w": 604800, "d": 86400, "h": 3600, "m": 60, "s": 1}


def parse_duration(text: str) -> timedelta | None:
    text = (text or "").strip().lower().replace(" ", "")
    parts = DURATION_RE.findall(text)
    if not parts or "".join(a + b for a, b in parts) != text:
        return None
    seconds = sum(int(n) * UNITS[u] for n, u in parts)
    return timedelta(seconds=seconds) if seconds > 0 else None


def nice_duration(delta: timedelta) -> str:
    seconds = int(delta.total_seconds())
    out = []
    for name, size in (("w", 604800), ("d", 86400), ("h", 3600), ("m", 60), ("s", 1)):
        if seconds >= size:
            out.append(f"{seconds // size}{name}")
            seconds %= size
    return " ".join(out) or "0s"


def clip(text: str, limit: int = 1000) -> str:
    text = str(text or "")
    return text if len(text) <= limit else text[: limit - 1] + "…"


async def reply(interaction: discord.Interaction, content: str = None, *, ephemeral: bool = True, **kwargs) -> None:
    if interaction.response.is_done():
        await interaction.followup.send(content, ephemeral=ephemeral, **kwargs)
    else:
        await interaction.response.send_message(content, ephemeral=ephemeral, **kwargs)


# ===========================================================================
# Bot
# ===========================================================================


class MEBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True          # welcome, auto role, invite tracking
        intents.message_content = True  # ticket transcripts
        intents.invites = True          # invite tracking
        super().__init__(command_prefix=commands.when_mentioned, intents=intents)
        self.invite_cache: dict[str, int] = {}
        self.vanity_cache: int | None = None

    async def setup_hook(self) -> None:
        self.add_view(TicketPanelView())
        self.add_view(TicketControlsView())
        self.add_view(GiveawayView())
        self.tree.add_command(embed_group)
        self.tree.add_command(giveaway_group)
        guild = discord.Object(id=GUILD_ID)
        self.tree.copy_global_to(guild=guild)
        try:
            await self.tree.sync(guild=guild)
        except discord.Forbidden:
            log.error(
                "I can't add my slash commands to server %s. Make sure the bot is in that server and was "
                "invited with the 'applications.commands' scope, then restart.", GUILD_ID
            )
        self.loop.create_task(save_loop())
        self.loop.create_task(giveaway_loop())

    async def on_ready(self) -> None:
        log.info("Logged in as %s", self.user)
        await cache_invites()

    async def log_to(self, channel_id: int | None, embed: discord.Embed, file: discord.File | None = None) -> None:
        if not channel_id:
            return
        channel = self.get_channel(channel_id)
        if channel is None:
            log.error("Log channel %s wasn't found.", channel_id)
            return
        try:
            await channel.send(embed=embed, file=file, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            log.exception("Couldn't send to log channel %s.", channel_id)


bot = MEBot()


async def save_loop() -> None:
    while True:
        await asyncio.sleep(20)
        if store.dirty:
            try:
                store.save()
            except OSError:
                log.exception("Couldn't save mod_data.json.")


@bot.tree.error
async def on_tree_error(interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
    if isinstance(error, app_commands.CheckFailure):
        message = str(error) or "You can't use this command."
    elif isinstance(error, app_commands.CommandOnCooldown):
        message = f"Slow down! Try again in {error.retry_after:.0f}s."
    else:
        log.exception("Command error", exc_info=error)
        message = "⚠️ Something went wrong. Check my permissions and try again."
    try:
        await reply(interaction, message)
    except discord.HTTPException:
        pass


# ===========================================================================
# Moderation
# ===========================================================================

ACTION_STYLE = {
    "warn": ("⚠️", "Warn", discord.Colour.gold()),
    "mute": ("🔇", "Mute", discord.Colour.orange()),
    "unmute": ("🔊", "Unmute", discord.Colour.green()),
    "kick": ("👢", "Kick", discord.Colour.orange()),
    "ban": ("🔨", "Ban", discord.Colour.red()),
    "unban": ("🕊️", "Unban", discord.Colour.green()),
}


def check_target(actor: discord.Member, target: discord.Member) -> str | None:
    guild = actor.guild
    if target.id == actor.id:
        return "❌ You can't do that to yourself."
    if target.id == guild.owner_id:
        return "❌ You can't do that to the server owner."
    if target.id == guild.me.id:
        return "❌ I can't do that to myself."
    if actor.id != guild.owner_id and target.top_role >= actor.top_role:
        return "❌ That member's role is the same as or higher than yours."
    if target.top_role >= guild.me.top_role:
        return "❌ That member's role is higher than mine. Move my role up in Server Settings → Roles."
    return None


async def record_action(
    guild: discord.Guild,
    user: discord.abc.User,
    action: str,
    moderator: discord.abc.User,
    reason: str,
    duration: str | None = None,
) -> int:
    case = store.next("case_count")
    entry = {
        "case": case,
        "action": action,
        "reason": reason,
        "mod": moderator.id,
        "time": now().timestamp(),
        "duration": duration,
    }
    store.data["modlogs"].setdefault(str(user.id), []).append(entry)
    store.save()

    emoji, label, colour = ACTION_STYLE[action]
    embed = discord.Embed(title=f"{emoji} {label} | Case #{case}", colour=colour, timestamp=now())
    embed.set_thumbnail(url=user.display_avatar.url)
    embed.add_field(name="👤 User", value=f"{user.mention}\n`{user.id}`", inline=True)
    embed.add_field(name="🛡️ Moderator", value=moderator.mention, inline=True)
    if duration:
        embed.add_field(name="⏱️ Duration", value=duration, inline=True)
    embed.add_field(name="📝 Reason", value=clip(reason), inline=False)
    await bot.log_to(cfg("mod_log_channel"), embed)
    return case


async def dm_user(user: discord.abc.User, guild: discord.Guild, action: str, reason: str, duration: str | None = None) -> None:
    emoji, label, colour = ACTION_STYLE[action]
    embed = discord.Embed(title=f"{emoji} You received a {label.lower()} in {guild.name}", colour=colour, timestamp=now())
    if duration:
        embed.add_field(name="⏱️ Duration", value=duration, inline=True)
    embed.add_field(name="📝 Reason", value=clip(reason), inline=False)
    try:
        await user.send(embed=embed)
    except (discord.Forbidden, discord.HTTPException):
        pass


def action_done(action: str, user: discord.abc.User, reason: str, case: int, duration: str | None = None) -> discord.Embed:
    emoji, label, colour = ACTION_STYLE[action]
    past = {"warn": "warned", "mute": "muted", "unmute": "unmuted", "kick": "kicked", "ban": "banned", "unban": "unbanned"}[action]
    embed = discord.Embed(description=f"{emoji} **{user}** has been {past}.", colour=colour)
    if duration:
        embed.add_field(name="⏱️ Duration", value=duration, inline=True)
    embed.add_field(name="📝 Reason", value=clip(reason, 500), inline=True)
    embed.set_footer(text=f"Case #{case}")
    return embed


@bot.tree.command(name="warn", description="⚠️ Warn a member")
@app_commands.default_permissions(moderate_members=True)
@app_commands.checks.has_permissions(moderate_members=True)
@app_commands.describe(member="Who to warn", reason="Why")
async def warn(interaction: discord.Interaction, member: discord.Member, reason: str) -> None:
    if problem := check_target(interaction.user, member):
        return await reply(interaction, problem)
    await dm_user(member, interaction.guild, "warn", reason)
    case = await record_action(interaction.guild, member, "warn", interaction.user, reason)
    await reply(interaction, embed=action_done("warn", member, reason, case), ephemeral=False)


@bot.tree.command(name="mute", description="🔇 Mute (time out) a member, e.g. 10m, 2h, 1d")
@app_commands.default_permissions(moderate_members=True)
@app_commands.checks.has_permissions(moderate_members=True)
@app_commands.describe(member="Who to mute", duration="How long, e.g. 10m, 2h, 1d (max 28d)", reason="Why")
async def mute(interaction: discord.Interaction, member: discord.Member, duration: str, reason: str = "No reason given") -> None:
    if problem := check_target(interaction.user, member):
        return await reply(interaction, problem)
    length = parse_duration(duration)
    if length is None:
        return await reply(interaction, "❌ Use a time like `10m`, `2h`, `1d` or `1d12h`.")
    if length > timedelta(days=28):
        return await reply(interaction, "❌ Discord only allows mutes up to 28 days.")
    await member.timeout(length, reason=f"{interaction.user}: {reason}")
    shown = nice_duration(length)
    await dm_user(member, interaction.guild, "mute", reason, shown)
    case = await record_action(interaction.guild, member, "mute", interaction.user, reason, shown)
    await reply(interaction, embed=action_done("mute", member, reason, case, shown), ephemeral=False)


@bot.tree.command(name="unmute", description="🔊 Unmute a member")
@app_commands.default_permissions(moderate_members=True)
@app_commands.checks.has_permissions(moderate_members=True)
async def unmute(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason given") -> None:
    if not member.is_timed_out():
        return await reply(interaction, "❌ That member isn't muted.")
    await member.timeout(None, reason=f"{interaction.user}: {reason}")
    case = await record_action(interaction.guild, member, "unmute", interaction.user, reason)
    await reply(interaction, embed=action_done("unmute", member, reason, case), ephemeral=False)


@bot.tree.command(name="kick", description="👢 Kick a member")
@app_commands.default_permissions(kick_members=True)
@app_commands.checks.has_permissions(kick_members=True)
async def kick(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason given") -> None:
    if problem := check_target(interaction.user, member):
        return await reply(interaction, problem)
    await dm_user(member, interaction.guild, "kick", reason)
    await member.kick(reason=f"{interaction.user}: {reason}")
    case = await record_action(interaction.guild, member, "kick", interaction.user, reason)
    await reply(interaction, embed=action_done("kick", member, reason, case), ephemeral=False)


@bot.tree.command(name="ban", description="🔨 Ban a member (works on people who already left too)")
@app_commands.default_permissions(ban_members=True)
@app_commands.checks.has_permissions(ban_members=True)
@app_commands.describe(user="Who to ban", reason="Why", delete_days="Delete their messages from the last 0-7 days")
async def ban(
    interaction: discord.Interaction,
    user: discord.User,
    reason: str = "No reason given",
    delete_days: app_commands.Range[int, 0, 7] = 0,
) -> None:
    member = interaction.guild.get_member(user.id)
    if member is not None:
        if problem := check_target(interaction.user, member):
            return await reply(interaction, problem)
        await dm_user(member, interaction.guild, "ban", reason)
    await interaction.guild.ban(user, reason=f"{interaction.user}: {reason}", delete_message_seconds=delete_days * 86400)
    case = await record_action(interaction.guild, user, "ban", interaction.user, reason)
    await reply(interaction, embed=action_done("ban", user, reason, case), ephemeral=False)


@bot.tree.command(name="unban", description="🕊️ Unban someone by their user ID")
@app_commands.default_permissions(ban_members=True)
@app_commands.checks.has_permissions(ban_members=True)
@app_commands.describe(user_id="Their Discord user ID")
async def unban(interaction: discord.Interaction, user_id: str, reason: str = "No reason given") -> None:
    if not user_id.isdigit():
        return await reply(interaction, "❌ That isn't a user ID.")
    try:
        user = await bot.fetch_user(int(user_id))
        await interaction.guild.unban(user, reason=f"{interaction.user}: {reason}")
    except discord.NotFound:
        return await reply(interaction, "❌ That user isn't banned (or doesn't exist).")
    case = await record_action(interaction.guild, user, "unban", interaction.user, reason)
    await reply(interaction, embed=action_done("unban", user, reason, case), ephemeral=False)


@bot.tree.command(name="logs", description="📋 See someone's warns, mutes, kicks and bans")
@app_commands.default_permissions(moderate_members=True)
@app_commands.checks.has_permissions(moderate_members=True)
async def logs(interaction: discord.Interaction, user: discord.User) -> None:
    entries = store.data["modlogs"].get(str(user.id), [])
    counts = {key: sum(1 for e in entries if e["action"] == key) for key in ACTION_STYLE}
    embed = discord.Embed(title="📋 Moderation history", colour=COLOUR, timestamp=now())
    embed.set_author(name=f"{user} ({user.id})", icon_url=user.display_avatar.url)
    embed.set_thumbnail(url=user.display_avatar.url)
    embed.add_field(name="⚠️ Warns", value=f"**{counts['warn']}**", inline=True)
    embed.add_field(name="🔇 Mutes", value=f"**{counts['mute']}**", inline=True)
    embed.add_field(name="👢 Kicks", value=f"**{counts['kick']}**", inline=True)
    embed.add_field(name="🔨 Bans", value=f"**{counts['ban']}**", inline=True)
    embed.add_field(name="📊 Total cases", value=f"**{len(entries)}**", inline=True)
    if entries:
        lines = []
        for e in reversed(entries[-10:]):
            emoji, label, _ = ACTION_STYLE[e["action"]]
            extra = f" ({e['duration']})" if e.get("duration") else ""
            lines.append(f"`#{e['case']}` {emoji} **{label}**{extra} • <@{e['mod']}> • {ts(e['time'], 'R')}\n└ {clip(e['reason'], 80)}")
        embed.add_field(name="🕐 Latest cases", value=clip("\n".join(lines), 1024), inline=False)
    else:
        embed.description = "✨ Clean record, no cases yet."
    await reply(interaction, embed=embed)


@bot.tree.command(name="clearlogs", description="🧹 Wipe someone's moderation history (admin)")
@app_commands.default_permissions(administrator=True)
@admin_only()
async def clearlogs(interaction: discord.Interaction, user: discord.User) -> None:
    removed = len(store.data["modlogs"].pop(str(user.id), []))
    store.save()
    await reply(interaction, f"🧹 Cleared **{removed}** case(s) for {user.mention}.")


@bot.tree.command(name="purge", description="🗑️ Delete recent messages in this channel")
@app_commands.default_permissions(manage_messages=True)
@app_commands.checks.has_permissions(manage_messages=True)
@app_commands.describe(amount="How many messages to check (1-500)", user="Only delete this person's messages")
async def purge(interaction: discord.Interaction, amount: app_commands.Range[int, 1, 500], user: discord.User = None) -> None:
    await interaction.response.defer(ephemeral=True)
    check = (lambda m: m.author.id == user.id) if user else (lambda m: True)
    deleted = await interaction.channel.purge(limit=amount, check=check, reason=f"Purge by {interaction.user}")
    await interaction.followup.send(f"🗑️ Deleted **{len(deleted)}** message(s).", ephemeral=True)


@bot.tree.command(name="lock", description="🔒 Stop everyone from talking in this channel")
@app_commands.default_permissions(manage_channels=True)
@app_commands.checks.has_permissions(manage_channels=True)
async def lock(interaction: discord.Interaction, reason: str = "No reason given") -> None:
    overwrite = interaction.channel.overwrites_for(interaction.guild.default_role)
    overwrite.send_messages = False
    await interaction.channel.set_permissions(interaction.guild.default_role, overwrite=overwrite, reason=reason)
    embed = discord.Embed(description=f"🔒 This channel has been locked.\n📝 {clip(reason, 300)}", colour=discord.Colour.red())
    await reply(interaction, embed=embed, ephemeral=False)


@bot.tree.command(name="unlock", description="🔓 Let everyone talk in this channel again")
@app_commands.default_permissions(manage_channels=True)
@app_commands.checks.has_permissions(manage_channels=True)
async def unlock(interaction: discord.Interaction) -> None:
    overwrite = interaction.channel.overwrites_for(interaction.guild.default_role)
    overwrite.send_messages = None
    await interaction.channel.set_permissions(interaction.guild.default_role, overwrite=overwrite)
    await reply(interaction, embed=discord.Embed(description="🔓 This channel has been unlocked.", colour=discord.Colour.green()), ephemeral=False)


@bot.tree.command(name="slowmode", description="🐢 Set slowmode for this channel (0 turns it off)")
@app_commands.default_permissions(manage_channels=True)
@app_commands.checks.has_permissions(manage_channels=True)
@app_commands.describe(seconds="Seconds between messages (0-21600)")
async def slowmode(interaction: discord.Interaction, seconds: app_commands.Range[int, 0, 21600]) -> None:
    await interaction.channel.edit(slowmode_delay=seconds)
    text = "🐢 Slowmode is off." if seconds == 0 else f"🐢 Slowmode set to **{seconds}s**."
    await reply(interaction, text, ephemeral=False)


# ===========================================================================
# Tickets
# ===========================================================================

# key: (title, emoji, channel name, button label)
TICKET_TYPES = {
    "support": ("Support Ticket", "🎫", "support-ticket", "Ticket"),
    "discord": ("Discord Purchase", "💎", "discord-purchase", "Discord Products"),
    "server": ("Server Design", "🏗️", "server-design", "Server Build"),
    "ingame": ("In-Game Purchase", "🎮", "ingame-purchase", "In-Game Products"),
    "other": ("Purchase", "🛒", "purchase", "Other"),
}
PURCHASE_KEYS = ["discord", "server", "ingame", "other"]

# Role pinged (and let in) for each kind of ticket.
TICKET_PING_ROLES = {
    "support": 1570359979909849119,
    "discord": 1570359829057380352,
    "server": 1570360413336375356,
    "ingame": 1570360571625345166,
    "other": 1570360571625345166,
}


def topic_for(user_id: int, key: str) -> str:
    return f"ticket|{user_id}|{key}"


def read_topic(topic: str | None) -> tuple[int, str] | None:
    parts = (topic or "").split("|")
    if len(parts) != 3 or parts[0] != "ticket" or not parts[1].isdigit():
        return None
    return int(parts[1]), parts[2]


def build_panel(banner: str | None) -> "TicketPanelView":
    return TicketPanelView(banner)


class TicketSelect(discord.ui.Select):
    def __init__(self):
        super().__init__(
            custom_id="me:ticket_select",
            placeholder="📂 Choose an option...",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(label="Ticket", value="support", emoji="🎫", description="Help, questions or problems"),
                discord.SelectOption(label="Purchase", value="purchase", emoji="🛒", description="Buy Discord products, server builds and more"),
            ],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        choice = self.values[0]
        if choice == "purchase":
            embed = discord.Embed(
                title="🛒 What would you like to purchase?",
                description="Pick a category below and we'll open a ticket for you.",
                colour=COLOUR,
            )
            await interaction.response.send_message(embed=embed, view=PurchaseChoiceView(), ephemeral=True)
        else:
            await interaction.response.send_modal(SupportModal())

        # Reset the dropdown so the same option can be picked again.
        banner = store.data["panels"].get(str(interaction.message.id), BANNER_URL or None)
        try:
            await interaction.message.edit(view=build_panel(banner))
        except discord.HTTPException:
            log.exception("Couldn't reset the ticket panel.")


class SupportModal(discord.ui.Modal, title="🎫 Support Ticket"):
    issue = discord.ui.TextInput(
        label="What's the issue?",
        style=discord.TextStyle.paragraph,
        placeholder="Explain what's wrong or what you need help with",
        max_length=1000,
    )
    extra = discord.ui.TextInput(
        label="Anything else we should know?",
        style=discord.TextStyle.paragraph,
        placeholder="Order ID, screenshots you'll send, anything else (optional)",
        required=False,
        max_length=1000,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        answers = [("❓ What's the issue?", self.issue.value)]
        if self.extra.value.strip():
            answers.append(("📝 Anything else", self.extra.value))
        message = await open_ticket(interaction.guild, interaction.user, "support", answers)
        await interaction.followup.send(message, ephemeral=True)


class TicketPanelView(discord.ui.LayoutView):
    def __init__(self, banner: str | None = None):
        super().__init__(timeout=None)
        if banner is None:
            banner = BANNER_URL or None
        parts: list[discord.ui.Item] = []
        if banner:
            parts.append(discord.ui.MediaGallery(discord.MediaGalleryItem(media=banner)))

        heading = f"# 🎫 {STORE_NAME} Support"
        if STORE_TAGLINE:
            heading += f"\n-# {STORE_TAGLINE}"
        parts.append(discord.ui.TextDisplay(heading))
        parts.append(discord.ui.Separator())

        rules = f" in <#{cfg('rules_channel')}>" if cfg("rules_channel") else ""
        parts.append(discord.ui.TextDisplay(
            "### 📌 Before you open a ticket\n"
            "> 🚫 **No troll tickets.** Joke or empty tickets get closed and may lead to a punishment.\n"
            "> ⏳ **Be patient.** Staff will reply as soon as they can, please don't ping them.\n"
            "> 📝 **Be clear.** Say exactly what you need in your first message.\n"
            "> 💳 **Buying?** Have your payment method ready to speed things up.\n"
            "> 🤝 **Be respectful** to staff at all times.\n"
            f"> 📜 Make sure you've read the rules{rules}."
        ))
        parts.append(discord.ui.Separator())
        parts.append(discord.ui.TextDisplay(
            "### 🧭 How can we help?\n"
            "🎫 **Ticket** • help, questions or problems\n"
            "🛒 **Purchase** • Discord products, server builds, in-game products and more"
        ))
        row = discord.ui.ActionRow()
        row.add_item(TicketSelect())
        parts.append(row)
        footer = f"-# 💙 {STORE_NAME}" + (f" • {STORE_TAGLINE}" if STORE_TAGLINE else "")
        parts.append(discord.ui.TextDisplay(footer))
        self.add_item(discord.ui.Container(*parts, accent_color=COLOUR))


class PurchaseButton(discord.ui.Button):
    def __init__(self, key: str):
        title, emoji, _, label = TICKET_TYPES[key]
        super().__init__(label=label, emoji=emoji, style=discord.ButtonStyle.secondary if key == "other" else discord.ButtonStyle.primary)
        self.key = key

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(content="⏳ Opening your ticket...", embed=None, view=None)
        message = await open_ticket(interaction.guild, interaction.user, self.key)
        await interaction.edit_original_response(content=message)


class PurchaseChoiceView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)
        for key in PURCHASE_KEYS:
            self.add_item(PurchaseButton(key))


ticket_lock = asyncio.Lock()
closing: set[int] = set()


async def open_ticket(guild: discord.Guild, member: discord.Member, key: str, answers: list | None = None) -> str:
    title, emoji, slug, _ = TICKET_TYPES[key]
    staff_role = guild.get_role(cfg("staff_role")) if cfg("staff_role") else None
    ping_role = guild.get_role(TICKET_PING_ROLES.get(key, 0)) or staff_role
    category = guild.get_channel(cfg("ticket_category")) if cfg("ticket_category") else None
    if not isinstance(category, discord.CategoryChannel):
        category = None  # no category set: tickets go at the top of the channel list

    can_talk = discord.PermissionOverwrite(
        view_channel=True, send_messages=True, read_message_history=True, attach_files=True, embed_links=True
    )
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.me: discord.PermissionOverwrite(
            view_channel=True, send_messages=True, read_message_history=True,
            attach_files=True, embed_links=True, manage_channels=True,
        ),
        member: can_talk,
    }
    for role in (staff_role, ping_role):
        if role is not None:
            overwrites[role] = can_talk

    async with ticket_lock:
        topic = topic_for(member.id, key)
        existing = next((c for c in guild.text_channels if c.topic == topic), None)
        if existing:
            return f"❗ You already have one open: {existing.mention}"
        number = store.next("ticket_count")
        try:
            channel = await guild.create_text_channel(
                f"{slug}-{number:04d}",
                category=category,
                overwrites=overwrites,
                topic=topic,
                reason=f"{title} ticket opened by {member}",
            )
        except discord.Forbidden:
            return "⚠️ I don't have permission to make ticket channels. Please tell an admin."

    embed = discord.Embed(
        title=f"{emoji} {title} #{number:04d}",
        description=(
            f"👋 Hey {member.mention}, thanks for opening a ticket!\n\n"
            + ("🛒 Tell us **what you'd like to buy**, how many, and how you'd like to pay.\n"
               if key != "support" else "✅ Thanks for the details, our support team has been notified.\n")
            + "⏳ A member of staff will be with you shortly, please be patient."
        ),
        colour=COLOUR,
        timestamp=now(),
    )
    for question, answer in answers or []:
        embed.add_field(name=question, value=clip(answer, 1024), inline=False)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text=f"{STORE_NAME} • Opened by {member}")
    await channel.send(
        content=member.mention + (f" {ping_role.mention}" if ping_role else ""),
        embed=embed,
        view=TicketControlsView(),
        allowed_mentions=discord.AllowedMentions(users=[member], roles=[ping_role] if ping_role else []),
    )
    return f"✅ Your ticket is ready: {channel.mention}"


class TicketControlsView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Claim", emoji="🙋", style=discord.ButtonStyle.success, custom_id="me:ticket_claim")
    async def claim(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not is_staff(interaction.user):
            return await reply(interaction, "❌ Only staff can claim tickets.")
        view = TicketControlsView()
        view.claim.disabled = True
        view.claim.label = f"Claimed by {interaction.user.display_name}"[:80]
        await interaction.response.edit_message(view=view)
        await interaction.followup.send(
            embed=discord.Embed(description=f"🙋 {interaction.user.mention} will be handling this ticket.", colour=COLOUR)
        )

    @discord.ui.button(label="Close", emoji="🔒", style=discord.ButtonStyle.danger, custom_id="me:ticket_close")
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        info = read_topic(getattr(interaction.channel, "topic", None))
        if info is None:
            return await reply(interaction, "❌ This isn't a ticket.")
        owner_id, key = info
        if not (is_staff(interaction.user) or interaction.user.id == owner_id):
            return await reply(interaction, "❌ You can't close this ticket.")
        channel = interaction.channel
        if channel.id in closing:
            return await reply(interaction, "⏳ Already closing.")
        closing.add(channel.id)
        await interaction.response.send_message(
            embed=discord.Embed(description="🔒 Saving the transcript and closing this ticket in 5 seconds...", colour=discord.Colour.red())
        )
        try:
            await send_transcript(channel, interaction.user, owner_id, key)
        except Exception:
            log.exception("Transcript failed for %s", channel.name)
        await asyncio.sleep(5)
        try:
            await channel.delete(reason=f"Ticket closed by {interaction.user}")
        except discord.HTTPException:
            closing.discard(channel.id)


async def send_transcript(channel: discord.TextChannel, closed_by: discord.Member, owner_id: int, key: str) -> None:
    lines = [f"Transcript of #{channel.name} ({STORE_NAME})", f"Closed by {closed_by} at {now():%Y-%m-%d %H:%M UTC}", "=" * 60, ""]
    count = 0
    async for m in channel.history(limit=2000, oldest_first=True):
        count += 1
        text = m.content
        for e in m.embeds:
            text += f"\n  [embed] {e.title or ''} {e.description or ''}".rstrip()
        for a in m.attachments:
            text += f"\n  [file] {a.url}"
        lines.append(f"[{m.created_at:%Y-%m-%d %H:%M}] {m.author}: {text}")
    data = "\n".join(lines).encode("utf-8")

    title, emoji, _, _ = TICKET_TYPES.get(key, ("Ticket", "🎫", "", ""))
    embed = discord.Embed(title=f"📄 Ticket closed • {channel.name}", colour=COLOUR, timestamp=now())
    embed.add_field(name="📂 Type", value=f"{emoji} {title}", inline=True)
    embed.add_field(name="👤 Opened by", value=f"<@{owner_id}>", inline=True)
    embed.add_field(name="🔒 Closed by", value=closed_by.mention, inline=True)
    embed.add_field(name="💬 Messages", value=str(count), inline=True)
    await bot.log_to(cfg("transcript_channel"), embed, discord.File(io.BytesIO(data), filename=f"{channel.name}.txt"))

    owner = channel.guild.get_member(owner_id)
    if owner:
        try:
            await owner.send(
                embed=discord.Embed(description=f"🔒 Your ticket **{channel.name}** in **{channel.guild.name}** was closed. Here's a copy.", colour=COLOUR),
                file=discord.File(io.BytesIO(data), filename=f"{channel.name}.txt"),
            )
        except (discord.Forbidden, discord.HTTPException):
            pass


@bot.tree.command(name="ticketpanel", description="🎫 Post the ticket panel in this channel")
@app_commands.default_permissions(administrator=True)
@admin_only()
@app_commands.describe(banner="Banner image for the top (optional, otherwise banner.gif next to the bot is used)")
async def ticketpanel(interaction: discord.Interaction, banner: discord.Attachment = None) -> None:
    await interaction.response.defer(ephemeral=True)
    if banner is not None:
        if not (banner.content_type or "").startswith("image/"):
            return await interaction.followup.send("❌ The banner has to be an image.", ephemeral=True)
        ext = (banner.filename.rsplit(".", 1)[-1] or "png").lower()
        name = f"banner.{ext}"
        file = await banner.to_file(filename=name)
        ref = f"attachment://{name}"
        message = await interaction.channel.send(view=build_panel(ref), file=file)
    elif BANNER_URL:
        ref = BANNER_URL
        message = await interaction.channel.send(view=build_panel(ref))
    elif BANNER_FILE.exists():
        ref = f"attachment://{BANNER_FILE.name}"
        message = await interaction.channel.send(view=build_panel(ref), file=discord.File(BANNER_FILE))
    else:
        ref = ""
        message = await interaction.channel.send(view=build_panel(None))
    store.data["panels"][str(message.id)] = ref or None
    store.save()
    await interaction.followup.send("✅ Ticket panel posted.", ephemeral=True)


@bot.tree.command(name="add", description="➕ Add someone to this ticket")
@staff_only()
async def ticket_add(interaction: discord.Interaction, member: discord.Member) -> None:
    if read_topic(getattr(interaction.channel, "topic", None)) is None:
        return await reply(interaction, "❌ Use this inside a ticket.")
    await interaction.channel.set_permissions(member, view_channel=True, send_messages=True, read_message_history=True, attach_files=True)
    await reply(interaction, f"➕ Added {member.mention} to the ticket.", ephemeral=False)


@bot.tree.command(name="remove", description="➖ Remove someone from this ticket")
@staff_only()
async def ticket_remove(interaction: discord.Interaction, member: discord.Member) -> None:
    if read_topic(getattr(interaction.channel, "topic", None)) is None:
        return await reply(interaction, "❌ Use this inside a ticket.")
    await interaction.channel.set_permissions(member, overwrite=None)
    await reply(interaction, f"➖ Removed {member.mention} from the ticket.", ephemeral=False)


# ===========================================================================
# Sales
# ===========================================================================

MONEY_RE = re.compile(r"-?\d+(?:[.,]\d+)?")


def money_value(price: str) -> float | None:
    match = MONEY_RE.search(price.replace(",", ""))
    return float(match.group()) if match else None


@bot.tree.command(name="sale", description="💰 Log a sale (admin)")
@app_commands.default_permissions(administrator=True)
@admin_only()
@app_commands.describe(
    user="Who it was sold to",
    product="What was sold",
    price="Price, e.g. £10 or $15.50",
    date="Date of the sale (leave empty for today)",
)
async def sale(interaction: discord.Interaction, user: discord.Member, product: str, price: str, date: str = None) -> None:
    sale_id = store.next("sale_count")
    date = (date or "").strip() or now().strftime("%d %B %Y")
    store.data["sales"].append({
        "id": sale_id, "product": product, "price": price, "buyer": user.id,
        "seller": interaction.user.id, "date": date, "time": now().timestamp(),
    })
    store.save()

    embed = discord.Embed(title=f"💰 New Sale • #{sale_id:04d}", colour=discord.Colour.green(), timestamp=now())
    embed.set_thumbnail(url=user.display_avatar.url)
    embed.add_field(name="📦 Product", value=clip(product, 200), inline=False)
    embed.add_field(name="💵 Price", value=clip(price, 100), inline=True)
    embed.add_field(name="📅 Date", value=clip(date, 100), inline=True)
    embed.add_field(name="🙋 Sold to", value=user.mention, inline=True)
    embed.add_field(name="🛡️ Sold by", value=interaction.user.mention, inline=True)
    embed.set_footer(text=f"{STORE_NAME} • Thank you for your purchase! 💙")

    target = (bot.get_channel(cfg("sales_channel")) if cfg("sales_channel") else None) or interaction.channel
    await target.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
    where = f" in {target.mention}" if target.id != interaction.channel.id else ""
    await reply(interaction, f"✅ Sale #{sale_id:04d} logged{where}.")


@bot.tree.command(name="sales", description="📊 See sales totals (admin)")
@app_commands.default_permissions(administrator=True)
@admin_only()
@app_commands.describe(user="Only show sales to this person")
async def sales(interaction: discord.Interaction, user: discord.User = None) -> None:
    rows = [s for s in store.data["sales"] if user is None or s["buyer"] == user.id]
    total = sum(v for v in (money_value(s["price"]) for s in rows) if v is not None)
    embed = discord.Embed(title="📊 Sales" + (f" to {user}" if user else ""), colour=COLOUR, timestamp=now())
    embed.add_field(name="🧾 Sales", value=f"**{len(rows)}**", inline=True)
    embed.add_field(name="💵 Total", value=f"**{total:,.2f}**", inline=True)
    if rows:
        lines = [f"`#{s['id']:04d}` **{clip(s['product'], 40)}** • {clip(s['price'], 20)} • <@{s['buyer']}> • {clip(s['date'], 20)}"
                 for s in reversed(rows[-10:])]
        embed.add_field(name="🕐 Latest", value=clip("\n".join(lines), 1024), inline=False)
    await reply(interaction, embed=embed)


# ===========================================================================
# Invites + welcome
# ===========================================================================


async def vanity_uses(guild: discord.Guild) -> int | None:
    if "VANITY_URL" not in guild.features:
        return None
    try:
        invite = await guild.vanity_invite()
        return invite.uses if invite else None
    except discord.HTTPException:
        return None


async def cache_invites() -> None:
    guild = bot.get_guild(GUILD_ID)
    if guild is None:
        return
    try:
        bot.invite_cache = {i.code: i.uses or 0 for i in await guild.invites()}
    except discord.Forbidden:
        log.warning("I need the Manage Server permission to track invites.")
    bot.vanity_cache = await vanity_uses(guild)


@bot.event
async def on_invite_create(invite: discord.Invite) -> None:
    bot.invite_cache[invite.code] = invite.uses or 0


@bot.event
async def on_invite_delete(invite: discord.Invite) -> None:
    bot.invite_cache.pop(invite.code, None)


async def find_inviter(guild: discord.Guild) -> tuple[discord.abc.User | None, str]:
    """Return (inviter, how) where how is 'invite', 'vanity' or 'unknown'."""
    inviter = None
    try:
        current = await guild.invites()
        for inv in current:
            if (inv.uses or 0) > bot.invite_cache.get(inv.code, 0):
                inviter = inv.inviter
                break
        bot.invite_cache = {i.code: i.uses or 0 for i in current}
    except discord.Forbidden:
        pass
    if inviter is not None:
        return inviter, "invite"
    uses = await vanity_uses(guild)
    old = getattr(bot, "vanity_cache", None)
    bot.vanity_cache = uses
    if uses is not None and old is not None and uses > old:
        return None, "vanity"
    return None, "unknown"


@bot.event
async def on_member_join(member: discord.Member) -> None:
    if member.guild.id != GUILD_ID:
        return
    inviter, how = await find_inviter(member.guild)
    if inviter and inviter.id != member.id:
        key = str(inviter.id)
        store.data["invites"][key] = store.data["invites"].get(key, 0) + 1
        store.data["invited_by"][str(member.id)] = inviter.id
        store.save()

    if cfg("auto_role") and not member.bot:
        role = member.guild.get_role(cfg("auto_role"))
        if role:
            try:
                await member.add_roles(role, reason="Auto role")
            except discord.HTTPException:
                log.exception("Couldn't give the auto role.")

    channel = bot.get_channel(cfg("welcome_channel")) if cfg("welcome_channel") else None
    if channel is None:
        return
    try:
        await send_welcome(channel, member, inviter, how)
    except discord.HTTPException:
        log.exception("Couldn't send the welcome message.")


async def send_welcome(channel: discord.abc.Messageable, member: discord.Member, inviter, how: str, test: bool = False) -> None:
    if how == "invite" and inviter is not None:
        joined = f"📨 You were invited by {inviter.mention} (**{store.data['invites'].get(str(inviter.id), 0)}** invites)"
    elif how == "vanity":
        code = member.guild.vanity_url_code
        joined = "🔗 You joined through the server vanity link" + (f" (discord.gg/{code})" if code else "")
    else:
        joined = "📨 We couldn't tell which invite you used"
    embed = discord.Embed(
        description=(
            f"## 👋 Welcome, {member.mention}!\n"
            f"🎉 We are now at **{member.guild.member_count:,}** members!\n"
            f"{joined}"
        ),
        colour=COLOUR,
        timestamp=now(),
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text=f"{STORE_NAME}" + (" • test" if test else ""), icon_url=member.guild.icon.url if member.guild.icon else None)
    file = None
    if WELCOME_BANNER_URL:
        embed.set_image(url=WELCOME_BANNER_URL)
    elif BANNER_FILE.exists():
        file = discord.File(BANNER_FILE)
        embed.set_image(url=f"attachment://{BANNER_FILE.name}")
    kwargs = {"embed": embed, "allowed_mentions": discord.AllowedMentions(users=[member] if not test else [])}
    if not test:
        kwargs["content"] = member.mention
    if file:
        kwargs["file"] = file
    await channel.send(**kwargs)


@bot.event
async def on_member_remove(member: discord.Member) -> None:
    inviter_id = store.data["invited_by"].pop(str(member.id), None)
    if inviter_id is not None:
        key = str(inviter_id)
        store.data["invites"][key] = max(0, store.data["invites"].get(key, 0) - 1)
        store.save()


@bot.event
async def on_message(message: discord.Message) -> None:
    if message.author.bot or message.guild is None or message.guild.id != GUILD_ID:
        return
    key = str(message.author.id)
    store.data["messages"][key] = store.data["messages"].get(key, 0) + 1
    store.dirty = True


@bot.tree.command(name="invites", description="📨 See how many people someone has invited")
async def invites(interaction: discord.Interaction, user: discord.User = None) -> None:
    user = user or interaction.user
    count = store.data["invites"].get(str(user.id), 0)
    msgs = store.data["messages"].get(str(user.id), 0)
    embed = discord.Embed(colour=COLOUR)
    embed.set_author(name=str(user), icon_url=user.display_avatar.url)
    embed.add_field(name="📨 Invites", value=f"**{count}**", inline=True)
    embed.add_field(name="💬 Messages", value=f"**{msgs:,}**", inline=True)
    await reply(interaction, embed=embed, ephemeral=False)


@bot.tree.command(name="welcometest", description="👋 Preview the welcome message on yourself (admin)")
@app_commands.default_permissions(administrator=True)
@admin_only()
async def welcometest(interaction: discord.Interaction) -> None:
    channel = bot.get_channel(cfg("welcome_channel")) if cfg("welcome_channel") else None
    if channel is None:
        return await reply(interaction, "❌ Set the welcome channel first with `/setup welcome_channel:`.")
    await interaction.response.defer(ephemeral=True)
    await send_welcome(channel, interaction.user, interaction.user, "invite", test=True)
    await interaction.followup.send(f"👋 Test welcome sent to {channel.mention}.", ephemeral=True)


# ===========================================================================
# /setup
# ===========================================================================

SETUP_LABELS = {
    "welcome_channel": ("👋 Welcome channel", "#"),
    "mod_log_channel": ("🛡️ Mod log channel", "#"),
    "transcript_channel": ("📄 Transcript channel", "#"),
    "sales_channel": ("💰 Sales channel", "#"),
    "rules_channel": ("📜 Rules channel", "#"),
    "ticket_category": ("📂 Ticket category", "#"),
    "staff_role": ("🛡️ Staff role", "@&"),
    "auto_role": ("🎭 Auto role", "@&"),
}


@bot.tree.command(name="setup", description="⚙️ Pick the bot's channels and roles (admin)")
@app_commands.default_permissions(administrator=True)
@admin_only()
@app_commands.describe(
    welcome_channel="Where welcome messages go",
    mod_log_channel="Where warns, mutes, kicks and bans get logged",
    transcript_channel="Where closed ticket transcripts go",
    sales_channel="Where /sale posts go",
    rules_channel="Your rules channel (linked on the ticket panel)",
    ticket_category="Category new tickets are made in",
    staff_role="Role that can see and handle tickets (admins always can)",
    auto_role="Role new members get automatically",
)
async def setup(
    interaction: discord.Interaction,
    welcome_channel: discord.TextChannel = None,
    mod_log_channel: discord.TextChannel = None,
    transcript_channel: discord.TextChannel = None,
    sales_channel: discord.TextChannel = None,
    rules_channel: discord.TextChannel = None,
    ticket_category: discord.CategoryChannel = None,
    staff_role: discord.Role = None,
    auto_role: discord.Role = None,
) -> None:
    config = store.data.setdefault("config", {})
    for name, value in (
        ("welcome_channel", welcome_channel), ("mod_log_channel", mod_log_channel),
        ("transcript_channel", transcript_channel), ("sales_channel", sales_channel),
        ("rules_channel", rules_channel), ("ticket_category", ticket_category),
        ("staff_role", staff_role), ("auto_role", auto_role),
    ):
        if value is not None:
            config[name] = value.id
    store.save()

    lines = []
    for name, (label, prefix) in SETUP_LABELS.items():
        value = cfg(name)
        lines.append(f"{label}: " + (f"<{prefix}{value}>" if value else "`not set`"))
    embed = discord.Embed(title="⚙️ Bot setup", description="\n".join(lines), colour=COLOUR)
    embed.set_footer(text="Run /setup again any time to change something.")
    await reply(interaction, embed=embed)


# ===========================================================================
# /embed create
# ===========================================================================

embed_group = app_commands.Group(
    name="embed", description="Make embeds", default_permissions=discord.Permissions(administrator=True)
)


class EmbedModal(discord.ui.Modal, title="🎨 Create an embed"):
    heading = discord.ui.TextInput(label="Title", required=False, max_length=256)
    body = discord.ui.TextInput(label="Description", style=discord.TextStyle.paragraph, required=False, max_length=4000)
    colour = discord.ui.TextInput(label="Colour (hex, e.g. #5BC8F5)", required=False, max_length=7, placeholder="#5BC8F5")
    image = discord.ui.TextInput(label="Image link (optional)", required=False, placeholder="https://...")
    footer = discord.ui.TextInput(label="Footer (optional)", required=False, max_length=2048)

    def __init__(self, channel: discord.abc.Messageable, attachment: discord.Attachment | None):
        super().__init__()
        self.channel = channel
        self.attachment = attachment

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not self.heading.value and not self.body.value:
            return await reply(interaction, "❌ Add a title or a description.")
        try:
            colour = discord.Colour.from_str(self.colour.value.strip()) if self.colour.value.strip() else COLOUR
        except ValueError:
            return await reply(interaction, "❌ That colour isn't a hex code like `#5BC8F5`.")
        embed = discord.Embed(title=self.heading.value or None, description=self.body.value or None, colour=colour)
        if self.footer.value:
            embed.set_footer(text=self.footer.value)
        file = None
        if self.attachment is not None:
            file = await self.attachment.to_file(filename=f"image.{self.attachment.filename.rsplit('.', 1)[-1]}")
            embed.set_image(url=f"attachment://{file.filename}")
        elif self.image.value.strip().startswith("http"):
            embed.set_image(url=self.image.value.strip())
        await interaction.response.defer(ephemeral=True)
        if file:
            await self.channel.send(embed=embed, file=file)
        else:
            await self.channel.send(embed=embed)
        await interaction.followup.send(f"✅ Embed sent to {self.channel.mention}.", ephemeral=True)


@embed_group.command(name="create", description="🎨 Make a custom embed")
@admin_only()
@app_commands.describe(channel="Where to send it (default: here)", image="Image to show in the embed (optional)")
async def embed_create(interaction: discord.Interaction, channel: discord.TextChannel = None, image: discord.Attachment = None) -> None:
    await interaction.response.send_modal(EmbedModal(channel or interaction.channel, image))


# ===========================================================================
# Giveaways
# ===========================================================================

giveaway_group = app_commands.Group(
    name="giveaway", description="Giveaways", default_permissions=discord.Permissions(manage_guild=True)
)
pending_edits: set[str] = set()


def giveaway_embed(gw: dict, image_url: str | None = None) -> discord.Embed:
    ended = gw.get("ended", False)
    entrants = gw.get("entrants", {})
    total_entries = sum(entrants.values())
    lines = [
        f"## 🎁 {gw['prize']}",
        "",
        (f"⏰ **Ended:** {ts(gw['ends_at'], 'R')}" if ended else f"⏰ **Ends:** {ts(gw['ends_at'], 'R')} ({ts(gw['ends_at'], 'f')})"),
        f"🏆 **Winners:** {gw['winners']}",
        f"👑 **Hosted by:** <@{gw['host']}>",
        f"👥 **Entrants:** {len(entrants)} • 🎟️ **Entries:** {total_entries}",
    ]
    if gw.get("description"):
        lines += ["", gw["description"]]

    req = []
    if gw.get("required_role"):
        req.append(f"🎭 Have the <@&{gw['required_role']}> role")
    if gw.get("min_invites"):
        req.append(f"📨 At least **{gw['min_invites']}** invite(s)")
    if gw.get("min_messages"):
        req.append(f"💬 At least **{gw['min_messages']}** message(s)")
    if req:
        lines += ["", "### 📋 Requirements", *[f"> {r}" for r in req]]
        if gw.get("bypass_role"):
            lines.append(f"> ⚡ <@&{gw['bypass_role']}> skips all requirements")
    if gw.get("bonus_role") and gw.get("bonus_entries"):
        lines += ["", "### ✨ Extra entries", f"> <@&{gw['bonus_role']}> gets **+{gw['bonus_entries']}** entries"]

    if ended:
        winners = gw.get("winner_ids", [])
        lines += ["", "### 🎊 Winners", " ".join(f"<@{w}>" for w in winners) if winners else "No valid entries 😔"]

    embed = discord.Embed(
        title="🎉 GIVEAWAY ENDED 🎉" if ended else "🎉 GIVEAWAY 🎉",
        description="\n".join(lines),
        colour=discord.Colour.dark_grey() if ended else COLOUR,
        timestamp=datetime.fromtimestamp(gw["ends_at"], timezone.utc),
    )
    if image_url:
        embed.set_image(url=image_url)
    embed.set_footer(text=f"{STORE_NAME} • {'Ended' if ended else 'Click 🎉 to enter • Ends'}")
    return embed


class GiveawayView(discord.ui.View):
    def __init__(self, ended: bool = False):
        super().__init__(timeout=None)
        if ended:
            self.enter.disabled = True
            self.enter.label = "Ended"

    @discord.ui.button(label="Enter", emoji="🎉", style=discord.ButtonStyle.primary, custom_id="me:giveaway_enter")
    async def enter(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        gw = store.data["giveaways"].get(str(interaction.message.id))
        if gw is None or gw.get("ended"):
            return await reply(interaction, "❌ This giveaway has ended.")
        member = interaction.user
        uid = str(member.id)

        if uid in gw["entrants"]:
            return await reply(
                interaction,
                f"✅ You're already in with **{gw['entrants'][uid]}** entr{'y' if gw['entrants'][uid] == 1 else 'ies'}. Want to leave?",
                view=LeaveGiveawayView(str(interaction.message.id)),
            )

        role_ids = {r.id for r in member.roles}
        if not (gw.get("bypass_role") and gw["bypass_role"] in role_ids):
            missing = []
            if gw.get("required_role") and gw["required_role"] not in role_ids:
                missing.append(f"🎭 You need the <@&{gw['required_role']}> role.")
            invites_now = store.data["invites"].get(uid, 0)
            if gw.get("min_invites") and invites_now < gw["min_invites"]:
                missing.append(f"📨 You need **{gw['min_invites']}** invites (you have **{invites_now}**).")
            msgs_now = store.data["messages"].get(uid, 0)
            if gw.get("min_messages") and msgs_now < gw["min_messages"]:
                missing.append(f"💬 You need **{gw['min_messages']}** messages (you have **{msgs_now}**).")
            if missing:
                embed = discord.Embed(title="❌ You can't enter yet", description="\n".join(missing), colour=discord.Colour.red())
                return await reply(interaction, embed=embed)

        entries = 1
        if gw.get("bonus_role") and gw["bonus_role"] in role_ids:
            entries += gw.get("bonus_entries", 0)
        gw["entrants"][uid] = entries
        store.save()
        bonus = f" (✨ +{entries - 1} bonus)" if entries > 1 else ""
        await reply(interaction, f"🎉 You're in with **{entries}** entr{'y' if entries == 1 else 'ies'}{bonus}. Good luck! 🍀")
        schedule_giveaway_edit(str(interaction.message.id))


class LeaveGiveawayView(discord.ui.View):
    def __init__(self, message_id: str):
        super().__init__(timeout=120)
        self.message_id = message_id

    @discord.ui.button(label="Leave giveaway", emoji="🚪", style=discord.ButtonStyle.danger)
    async def leave(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        gw = store.data["giveaways"].get(self.message_id)
        if gw and not gw.get("ended"):
            gw["entrants"].pop(str(interaction.user.id), None)
            store.save()
            schedule_giveaway_edit(self.message_id)
        await interaction.response.edit_message(content="🚪 You left the giveaway.", view=None)


def schedule_giveaway_edit(message_id: str) -> None:
    """Update the entry count a few seconds later, so a rush of entries is one edit."""
    if message_id in pending_edits:
        return
    pending_edits.add(message_id)

    async def run():
        await asyncio.sleep(4)
        pending_edits.discard(message_id)
        await refresh_giveaway(message_id)

    asyncio.create_task(run())


async def refresh_giveaway(message_id: str) -> discord.Message | None:
    gw = store.data["giveaways"].get(message_id)
    if gw is None:
        return None
    channel = bot.get_channel(gw["channel"])
    if channel is None:
        return None
    try:
        message = await channel.fetch_message(int(message_id))
        image = message.embeds[0].image.url if message.embeds and message.embeds[0].image else None
        await message.edit(embed=giveaway_embed(gw, image), view=GiveawayView(gw.get("ended", False)))
        return message
    except discord.HTTPException:
        log.exception("Couldn't update giveaway %s", message_id)
        return None


def pick_winners(gw: dict, guild: discord.Guild, count: int, exclude: set[str] = frozenset()) -> list[int]:
    pool = {uid: n for uid, n in gw["entrants"].items() if uid not in exclude and guild.get_member(int(uid))}
    winners = []
    while pool and len(winners) < count:
        uid = random.choices(list(pool), weights=list(pool.values()))[0]
        winners.append(int(uid))
        pool.pop(uid)
    return winners


async def end_giveaway(message_id: str) -> None:
    gw = store.data["giveaways"].get(message_id)
    if gw is None or gw.get("ended"):
        return
    guild = bot.get_guild(GUILD_ID)
    gw["ended"] = True
    gw["ends_at"] = min(gw["ends_at"], now().timestamp())
    gw["winner_ids"] = pick_winners(gw, guild, gw["winners"]) if guild else []
    store.save()
    message = await refresh_giveaway(message_id)
    channel = bot.get_channel(gw["channel"])
    if channel is None:
        return
    if gw["winner_ids"]:
        mentions = " ".join(f"<@{w}>" for w in gw["winner_ids"])
        embed = discord.Embed(
            description=f"🎊 Congratulations {mentions}!\nYou won **{gw['prize']}** 🎁\n\n🎫 Open a ticket to claim your prize.",
            colour=discord.Colour.gold(),
        )
        await channel.send(content=mentions, embed=embed, reference=message,
                           allowed_mentions=discord.AllowedMentions(users=True))
    else:
        await channel.send(embed=discord.Embed(description=f"😔 Nobody entered the giveaway for **{gw['prize']}**.", colour=discord.Colour.dark_grey()),
                           reference=message)


async def giveaway_loop() -> None:
    await bot.wait_until_ready()
    while not bot.is_closed():
        for message_id, gw in list(store.data["giveaways"].items()):
            if not gw.get("ended") and gw["ends_at"] <= now().timestamp():
                try:
                    await end_giveaway(message_id)
                except Exception:
                    log.exception("Couldn't end giveaway %s", message_id)
        await asyncio.sleep(10)


@giveaway_group.command(name="create", description="🎉 Start a giveaway")
@admin_only()
@app_commands.describe(
    prize="What they win",
    duration="How long, e.g. 30m, 2h, 1d, 1w",
    winners="How many winners",
    channel="Where to post it (default: here)",
    description="Extra info to show (optional)",
    required_role="Role people need to enter",
    min_invites="Invites people need to enter",
    min_messages="Messages people need to enter",
    bypass_role="Role that skips all requirements",
    bonus_role="Role that gets extra entries",
    bonus_entries="How many extra entries the bonus role gets",
    image="Picture to show on the giveaway (optional)",
)
async def giveaway_create(
    interaction: discord.Interaction,
    prize: str,
    duration: str,
    winners: app_commands.Range[int, 1, 50] = 1,
    channel: discord.TextChannel = None,
    description: str = None,
    required_role: discord.Role = None,
    min_invites: app_commands.Range[int, 0, 10000] = 0,
    min_messages: app_commands.Range[int, 0, 1000000] = 0,
    bypass_role: discord.Role = None,
    bonus_role: discord.Role = None,
    bonus_entries: app_commands.Range[int, 1, 100] = 1,
    image: discord.Attachment = None,
) -> None:
    length = parse_duration(duration)
    if length is None:
        return await reply(interaction, "❌ Use a time like `30m`, `2h`, `1d` or `1w`.")
    await interaction.response.defer(ephemeral=True)
    channel = channel or interaction.channel
    gw = {
        "channel": channel.id,
        "prize": clip(prize, 200),
        "description": clip(description, 1000) if description else None,
        "winners": winners,
        "host": interaction.user.id,
        "ends_at": (now() + length).timestamp(),
        "required_role": required_role.id if required_role else None,
        "min_invites": min_invites,
        "min_messages": min_messages,
        "bypass_role": bypass_role.id if bypass_role else None,
        "bonus_role": bonus_role.id if bonus_role else None,
        "bonus_entries": bonus_entries if bonus_role else 0,
        "entrants": {},
        "ended": False,
    }
    file = None
    image_ref = None
    if image is not None and (image.content_type or "").startswith("image/"):
        file = await image.to_file(filename=f"giveaway.{image.filename.rsplit('.', 1)[-1].lower()}")
        image_ref = f"attachment://{file.filename}"
    kwargs = {"embed": giveaway_embed(gw, image_ref), "view": GiveawayView()}
    if file:
        kwargs["file"] = file
    message = await channel.send(**kwargs)
    store.data["giveaways"][str(message.id)] = gw
    store.save()
    await interaction.followup.send(f"🎉 Giveaway started in {channel.mention}! Message ID: `{message.id}`", ephemeral=True)


@giveaway_group.command(name="end", description="⏹️ End a giveaway now")
@admin_only()
@app_commands.describe(message_id="The giveaway's message ID")
async def giveaway_end(interaction: discord.Interaction, message_id: str) -> None:
    gw = store.data["giveaways"].get(message_id.strip())
    if gw is None:
        return await reply(interaction, "❌ I couldn't find that giveaway.")
    if gw.get("ended"):
        return await reply(interaction, "❌ That giveaway already ended. Use `/giveaway reroll` instead.")
    await interaction.response.defer(ephemeral=True)
    await end_giveaway(message_id.strip())
    await interaction.followup.send("⏹️ Giveaway ended.", ephemeral=True)


@giveaway_group.command(name="reroll", description="🔄 Pick new winner(s) for an ended giveaway")
@admin_only()
@app_commands.describe(message_id="The giveaway's message ID", winners="How many new winners")
async def giveaway_reroll(interaction: discord.Interaction, message_id: str, winners: app_commands.Range[int, 1, 50] = 1) -> None:
    gw = store.data["giveaways"].get(message_id.strip())
    if gw is None or not gw.get("ended"):
        return await reply(interaction, "❌ That giveaway hasn't ended (or doesn't exist).")
    new = pick_winners(gw, interaction.guild, winners, exclude={str(w) for w in gw.get("winner_ids", [])})
    if not new:
        return await reply(interaction, "😔 There's nobody left to pick.")
    channel = bot.get_channel(gw["channel"])
    mentions = " ".join(f"<@{w}>" for w in new)
    await channel.send(
        content=mentions,
        embed=discord.Embed(description=f"🔄 New winner{'s' if len(new) > 1 else ''}: {mentions}!\nYou won **{gw['prize']}** 🎁", colour=discord.Colour.gold()),
        allowed_mentions=discord.AllowedMentions(users=True),
    )
    await reply(interaction, "🔄 Rerolled.")


# ===========================================================================

if __name__ == "__main__":
    bot.run(BOT_TOKEN, log_handler=None)

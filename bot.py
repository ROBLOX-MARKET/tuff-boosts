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

import aiohttp
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
        super().__init__(command_prefix=commands.when_mentioned_or("!"), case_insensitive=True, intents=intents, help_command=None)
        self.invite_cache: dict[str, int] = {}
        self.vanity_cache: int | None = None

    async def setup_hook(self) -> None:
        self.add_view(TicketPanelView())
        self.add_view(TicketControlsView())
        self.add_view(GiveawayView())
        self.tree.add_command(embed_group)
        self.tree.add_command(giveaway_group)
        self.tree.add_command(joinping_group)
        self.tree.add_command(autorole_group)
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
        for guild in list(self.guilds):
            await leave_if_not_ours(guild)
        await cache_invites()
        await upload_chrome_emojis()
        await set_avatar()

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


async def leave_if_not_ours(guild: discord.Guild) -> None:
    """This bot only works in your server, so it leaves anywhere else straight away."""
    if guild.id != GUILD_ID:
        log.warning("Leaving server I'm not allowed in: %s (%s)", guild.name, guild.id)
        try:
            await guild.leave()
        except discord.HTTPException:
            log.exception("Couldn't leave %s", guild.id)


@bot.event
async def on_guild_join(guild: discord.Guild) -> None:
    await leave_if_not_ours(guild)


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
    await reply(interaction, embed=logs_embed(user))


def logs_embed(user: discord.abc.User) -> discord.Embed:
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
    return embed


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
# ! prefix commands (same as the slash ones)
# ===========================================================================


@bot.event
async def on_command_error(ctx: commands.Context, error: commands.CommandError) -> None:
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.MissingPermissions):
        msg = "❌ You don't have permission to use that."
    elif isinstance(error, (commands.MissingRequiredArgument, commands.BadArgument, commands.UserNotFound, commands.MemberNotFound)):
        usage = PREFIX_USAGE.get(ctx.command.name if ctx.command else "", "")
        msg = f"❌ Usage: `{usage}`" if usage else "❌ That's not quite right, check the command and try again."
    elif isinstance(error, commands.CommandInvokeError) and isinstance(error.original, discord.Forbidden):
        msg = "❌ I don't have permission to do that. Move my role higher or give me the permission."
    else:
        log.exception("Prefix command error", exc_info=error)
        msg = "⚠️ Something went wrong."
    await ctx.reply(msg, mention_author=False)


PREFIX_USAGE = {
    "warn": "!warn @user reason",
    "mute": "!mute @user 10m reason",
    "unmute": "!unmute @user [reason]",
    "kick": "!kick @user [reason]",
    "ban": "!ban @user [reason]",
    "unban": "!unban userID [reason]",
    "logs": "!logs @user",
    "purge": "!purge 50 [@user]",
    "slowmode": "!slowmode 10",
    "steal": "!steal :emoji: [:emoji2:] (or reply to a message with !steal)",
}


@bot.command(name="warn")
@commands.has_permissions(moderate_members=True)
async def p_warn(ctx: commands.Context, member: discord.Member, *, reason: str) -> None:
    if problem := check_target(ctx.author, member):
        return await ctx.reply(problem, mention_author=False)
    await dm_user(member, ctx.guild, "warn", reason)
    case = await record_action(ctx.guild, member, "warn", ctx.author, reason)
    await ctx.send(embed=action_done("warn", member, reason, case))


@bot.command(name="mute", aliases=["timeout"])
@commands.has_permissions(moderate_members=True)
async def p_mute(ctx: commands.Context, member: discord.Member, duration: str, *, reason: str = "No reason given") -> None:
    if problem := check_target(ctx.author, member):
        return await ctx.reply(problem, mention_author=False)
    length = parse_duration(duration)
    if length is None:
        return await ctx.reply("❌ Use a time like `10m`, `2h`, `1d` or `1d12h`.", mention_author=False)
    if length > timedelta(days=28):
        return await ctx.reply("❌ Discord only allows mutes up to 28 days.", mention_author=False)
    await member.timeout(length, reason=f"{ctx.author}: {reason}")
    shown = nice_duration(length)
    await dm_user(member, ctx.guild, "mute", reason, shown)
    case = await record_action(ctx.guild, member, "mute", ctx.author, reason, shown)
    await ctx.send(embed=action_done("mute", member, reason, case, shown))


@bot.command(name="unmute")
@commands.has_permissions(moderate_members=True)
async def p_unmute(ctx: commands.Context, member: discord.Member, *, reason: str = "No reason given") -> None:
    if not member.is_timed_out():
        return await ctx.reply("❌ That member isn't muted.", mention_author=False)
    await member.timeout(None, reason=f"{ctx.author}: {reason}")
    case = await record_action(ctx.guild, member, "unmute", ctx.author, reason)
    await ctx.send(embed=action_done("unmute", member, reason, case))


@bot.command(name="kick")
@commands.has_permissions(kick_members=True)
async def p_kick(ctx: commands.Context, member: discord.Member, *, reason: str = "No reason given") -> None:
    if problem := check_target(ctx.author, member):
        return await ctx.reply(problem, mention_author=False)
    await dm_user(member, ctx.guild, "kick", reason)
    await member.kick(reason=f"{ctx.author}: {reason}")
    case = await record_action(ctx.guild, member, "kick", ctx.author, reason)
    await ctx.send(embed=action_done("kick", member, reason, case))


@bot.command(name="ban")
@commands.has_permissions(ban_members=True)
async def p_ban(ctx: commands.Context, user: discord.User, *, reason: str = "No reason given") -> None:
    member = ctx.guild.get_member(user.id)
    if member is not None:
        if problem := check_target(ctx.author, member):
            return await ctx.reply(problem, mention_author=False)
        await dm_user(member, ctx.guild, "ban", reason)
    await ctx.guild.ban(user, reason=f"{ctx.author}: {reason}", delete_message_seconds=0)
    case = await record_action(ctx.guild, user, "ban", ctx.author, reason)
    await ctx.send(embed=action_done("ban", user, reason, case))


@bot.command(name="unban")
@commands.has_permissions(ban_members=True)
async def p_unban(ctx: commands.Context, user_id: int, *, reason: str = "No reason given") -> None:
    try:
        user = await bot.fetch_user(user_id)
        await ctx.guild.unban(user, reason=f"{ctx.author}: {reason}")
    except discord.NotFound:
        return await ctx.reply("❌ That user isn't banned (or doesn't exist).", mention_author=False)
    case = await record_action(ctx.guild, user, "unban", ctx.author, reason)
    await ctx.send(embed=action_done("unban", user, reason, case))


@bot.command(name="logs", aliases=["history", "cases"])
@commands.has_permissions(moderate_members=True)
async def p_logs(ctx: commands.Context, user: discord.User) -> None:
    await ctx.send(embed=logs_embed(user))


@bot.command(name="purge", aliases=["clear"])
@commands.has_permissions(manage_messages=True)
async def p_purge(ctx: commands.Context, amount: int, user: discord.User = None) -> None:
    amount = max(1, min(amount, 500))
    check = (lambda m: m.author.id == user.id) if user else (lambda m: True)
    await ctx.message.delete()
    deleted = await ctx.channel.purge(limit=amount, check=check, reason=f"Purge by {ctx.author}")
    await ctx.send(f"🗑️ Deleted **{len(deleted)}** message(s).", delete_after=5)


@bot.command(name="lock")
@commands.has_permissions(manage_channels=True)
async def p_lock(ctx: commands.Context, *, reason: str = "No reason given") -> None:
    overwrite = ctx.channel.overwrites_for(ctx.guild.default_role)
    overwrite.send_messages = False
    await ctx.channel.set_permissions(ctx.guild.default_role, overwrite=overwrite, reason=reason)
    await ctx.send(embed=discord.Embed(description=f"🔒 This channel has been locked.\n📝 {clip(reason, 300)}", colour=discord.Colour.red()))


@bot.command(name="unlock")
@commands.has_permissions(manage_channels=True)
async def p_unlock(ctx: commands.Context) -> None:
    overwrite = ctx.channel.overwrites_for(ctx.guild.default_role)
    overwrite.send_messages = None
    await ctx.channel.set_permissions(ctx.guild.default_role, overwrite=overwrite)
    await ctx.send(embed=discord.Embed(description="🔓 This channel has been unlocked.", colour=discord.Colour.green()))


@bot.command(name="slowmode")
@commands.has_permissions(manage_channels=True)
async def p_slowmode(ctx: commands.Context, seconds: int) -> None:
    seconds = max(0, min(seconds, 21600))
    await ctx.channel.edit(slowmode_delay=seconds)
    await ctx.send("🐢 Slowmode is off." if seconds == 0 else f"🐢 Slowmode set to **{seconds}s**.")


# ===========================================================================
# Steal emojis (/steal and !steal)
# ===========================================================================

EMOJI_RE = re.compile(r"<(a?):([A-Za-z0-9_]{2,32}):(\d{15,25})>")


async def steal_emojis(guild: discord.Guild, text: str, by: discord.abc.User, name: str | None = None) -> discord.Embed:
    found = list(dict.fromkeys(EMOJI_RE.findall(text or "")))[:10]
    if not found:
        return discord.Embed(description="❌ I couldn't find any custom emojis there. Use a custom emoji like `:pepe:` from another server.", colour=discord.Colour.red())

    added, failed = [], []
    async with aiohttp.ClientSession() as session:
        for animated, emoji_name, emoji_id in found:
            final_name = (name if name and len(found) == 1 else emoji_name)[:32]
            url = f"https://cdn.discordapp.com/emojis/{emoji_id}.{'gif' if animated else 'png'}"
            try:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        raise ValueError("download failed")
                    data = await resp.read()
                emoji = await guild.create_custom_emoji(name=final_name, image=data, reason=f"Stolen by {by}")
                added.append(str(emoji))
            except discord.Forbidden:
                return discord.Embed(description="❌ I need the **Manage Expressions** permission to add emojis.", colour=discord.Colour.red())
            except discord.HTTPException as exc:
                reason = "server emoji slots are full" if exc.code == 30008 else "Discord refused it"
                failed.append(f"`{emoji_name}` ({reason})")
            except (aiohttp.ClientError, ValueError):
                failed.append(f"`{emoji_name}` (couldn't download)")

    embed = discord.Embed(title="🥷 Emoji steal", colour=discord.Colour.green() if added else discord.Colour.red())
    if added:
        embed.add_field(name=f"✅ Added ({len(added)})", value=" ".join(added), inline=False)
    if failed:
        embed.add_field(name=f"❌ Failed ({len(failed)})", value="\n".join(failed), inline=False)
    return embed


@bot.tree.command(name="steal", description="🥷 Add emojis from other servers to this one")
@app_commands.default_permissions(manage_expressions=True)
@app_commands.checks.has_permissions(manage_expressions=True)
@app_commands.describe(emojis="Paste one or more custom emojis", name="New name (only when stealing one emoji)")
async def steal_slash(interaction: discord.Interaction, emojis: str, name: str = None) -> None:
    await interaction.response.defer()
    await interaction.followup.send(embed=await steal_emojis(interaction.guild, emojis, interaction.user, name))


@bot.command(name="steal", aliases=["stealemoji", "addemoji"])
@commands.has_permissions(manage_expressions=True)
async def steal_prefix(ctx: commands.Context, *, emojis: str = "") -> None:
    text = emojis
    if not EMOJI_RE.search(text) and ctx.message.reference:
        ref = ctx.message.reference.resolved
        if ref is None and ctx.message.reference.message_id:
            ref = await ctx.channel.fetch_message(ctx.message.reference.message_id)
        text = getattr(ref, "content", "") or ""
    if not EMOJI_RE.search(text):
        return await ctx.reply(f"❌ Usage: `{PREFIX_USAGE['steal']}`", mention_author=False)
    async with ctx.typing():
        await ctx.send(embed=await steal_emojis(ctx.guild, text, ctx.author))


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


# The bot's own chrome emoji set (emojis/ folder) replaces these server emojis when it's uploaded.
CHROME_EMOJIS = {
    "ticket": "me_ticket", "question_mark_glowing": "me_question", "buy": "me_buy",
    "open_file_folder": "me_folder", "5647vslbank": "me_bank", "ez_timer": "me_timer",
    "respect": "me_respect", "rules": "me_rules", "no": "me_no",
}
EMOJI_DIR = BASE_DIR / "emojis"


def server_emoji(name: str, fallback: str):
    """The chrome emoji if uploaded, else your server's emoji by name, else the normal emoji."""
    guild = bot.get_guild(GUILD_ID)
    if guild is not None:
        for wanted in (CHROME_EMOJIS.get(name.lower()), name):
            if not wanted:
                continue
            for emoji in guild.emojis:
                if emoji.name.lower() == wanted.lower():
                    return emoji
    return fallback


async def set_avatar() -> None:
    """Set the bot's profile picture from avatar.gif (or avatar.png) once, and again if the file changes."""
    import hashlib
    for path in (BASE_DIR / "avatar.gif", BASE_DIR / "avatar.png"):
        if not path.exists():
            continue
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if store.data.get("avatar_hash") == digest:
            return
        try:
            await bot.user.edit(avatar=data)
            store.data["avatar_hash"] = digest
            store.save()
            log.info("Profile picture set from %s", path.name)
            return
        except discord.HTTPException as exc:
            log.error("Couldn't set the profile picture from %s: %s", path.name, exc)


async def upload_chrome_emojis() -> None:
    """Add any emoji from the emojis/ folder that the server doesn't have yet."""
    guild = bot.get_guild(GUILD_ID)
    if guild is None or not EMOJI_DIR.exists():
        return
    have = {e.name.lower() for e in guild.emojis}
    for path in sorted(EMOJI_DIR.glob("*.png")):
        if path.stem.lower() in have:
            continue
        try:
            await guild.create_custom_emoji(name=path.stem, image=path.read_bytes(), reason="M&E chrome emoji set")
            log.info("Added emoji %s", path.stem)
        except discord.HTTPException as exc:
            log.error("Couldn't add emoji %s: %s", path.stem, exc)
            if exc.code == 30008:  # emoji slots full
                break


def E(name: str, fallback: str) -> str:
    return str(server_emoji(name, fallback))


class TicketSelect(discord.ui.Select):
    def __init__(self):
        super().__init__(
            custom_id="me:ticket_select",
            placeholder="📂 Choose an option...",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(label="Ticket", value="support", emoji=server_emoji("Ticket", "🎫"), description="Help, questions or problems"),
                discord.SelectOption(label="Purchase", value="purchase", emoji=server_emoji("buy", "🛒"), description="Buy Discord products, server builds and more"),
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

        heading = f"# {E('Ticket', '🎫')} {STORE_NAME} Support"
        if STORE_TAGLINE:
            heading += f"\n-# {STORE_TAGLINE}"
        parts.append(discord.ui.TextDisplay(heading))
        parts.append(discord.ui.Separator())

        rules = f" in <#{cfg('rules_channel')}>" if cfg("rules_channel") else ""
        parts.append(discord.ui.TextDisplay(
            f"### {E('Rules', '📌')} Before you open a ticket\n"
            f"> {E('No', '🚫')} **No troll tickets.** Joke or empty tickets get closed and may lead to a punishment.\n"
            f"> {E('ez_timer', '⏳')} **Be patient.** Staff will reply as soon as they can, please don't ping them.\n"
            f"> {E('Question_Mark_Glowing', '📝')} **Be clear.** Say exactly what you need in your first message.\n"
            f"> {E('5647vslbank', '💳')} **Buying?** Have your payment method ready to speed things up.\n"
            f"> {E('respect', '🤝')} **Be respectful** to staff at all times.\n"
            f"> {E('Rules', '📜')} Make sure you've read the rules{rules}."
        ))
        parts.append(discord.ui.Separator())
        parts.append(discord.ui.TextDisplay(
            f"### {E('open_file_folder', '📂')} How can we help?\n"
            f"{E('Ticket', '🎫')} **Ticket** • help, questions or problems\n"
            f"{E('buy', '🛒')} **Purchase** • Discord products, server builds, in-game products and more"
        ))
        row = discord.ui.ActionRow()
        row.add_item(TicketSelect())
        parts.append(row)
        footer = f"-# {E('respect', '🤍')} {STORE_NAME}" + (f" • {STORE_TAGLINE}" if STORE_TAGLINE else "")
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

    if not member.bot:
        role_ids = set(store.data.get("config", {}).get("auto_roles", []))
        if cfg("auto_role"):
            role_ids.add(cfg("auto_role"))
        roles = [r for r in (member.guild.get_role(i) for i in role_ids) if r is not None]
        if roles:
            try:
                await member.add_roles(*roles, reason="Auto role")
            except discord.HTTPException:
                log.exception("Couldn't give the auto role(s).")

        # Ping on join: mention them in each chosen channel, then delete it straight away.
        for channel_id in store.data.get("config", {}).get("join_ping_channels", []):
            channel = bot.get_channel(channel_id)
            if channel is None:
                continue
            try:
                await channel.send(member.mention, delete_after=1.5, allowed_mentions=discord.AllowedMentions(users=[member]))
            except discord.HTTPException:
                log.exception("Couldn't send the join ping in %s.", channel_id)

    channel = bot.get_channel(cfg("welcome_channel")) if cfg("welcome_channel") else None
    if channel is None:
        return
    try:
        await send_welcome(channel, member, inviter, how)
    except discord.HTTPException:
        log.exception("Couldn't send the welcome message.")


WELCOME_CHANNELS = [1570050759913902170, 1570045204570837082, 1570045690028097556]


def ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n:,}{suffix}"


async def send_welcome(channel: discord.abc.Messageable, member: discord.Member, inviter, how: str, test: bool = False) -> None:
    guild = member.guild
    if how == "invite" and inviter is not None:
        joined = f"{E('ticket', '📨')} Invited by {inviter.mention} (**{store.data['invites'].get(str(inviter.id), 0)}** invites)"
    elif how == "vanity":
        code = guild.vanity_url_code
        joined = f"{E('ticket', '🔗')} Joined through the server vanity link" + (f" (discord.gg/{code})" if code else "")
    else:
        joined = ""

    branches = []
    for i, channel_id in enumerate(WELCOME_CHANNELS):
        branch = "╰" if i == len(WELCOME_CHANNELS) - 1 else "├"
        branches.append(f"> {branch} <#{channel_id}>")

    lines = [
        f"# {E('respect', '👋')} Welcome to {STORE_NAME}",
        "**Explore our server to discover our products and services. "
        "Check out the channels below to get started.**",
        "",
        *branches,
        "",
    ]
    if joined:
        lines += [joined, ""]
    lines.append(f"{E('respect', '🤍')} We hope you enjoy your experience with us!")

    embed = discord.Embed(description="\n".join(lines), colour=COLOUR, timestamp=now())
    embed.set_author(name=f"{member.display_name} has joined {STORE_NAME}!", icon_url=member.display_avatar.url)
    embed.set_footer(
        text=f"You are our {ordinal(guild.member_count or 0)} to join!" + (" • test" if test else ""),
        icon_url=guild.icon.url if guild.icon else None,
    )
    file = None
    if WELCOME_BANNER_URL:
        embed.set_image(url=WELCOME_BANNER_URL)
    elif BANNER_FILE.exists():
        file = discord.File(BANNER_FILE)
        embed.set_image(url=f"attachment://{BANNER_FILE.name}")
    kwargs = {"embed": embed, "allowed_mentions": discord.AllowedMentions(users=[member] if not test else [])}
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
    await bot.process_commands(message)


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
    "vouch_channel": ("⭐ Vouch channel", "#"),
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
    vouch_channel="Where /vouch posts go (otherwise the channel it's used in)",
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
    vouch_channel: discord.TextChannel = None,
) -> None:
    config = store.data.setdefault("config", {})
    for name, value in (
        ("welcome_channel", welcome_channel), ("mod_log_channel", mod_log_channel),
        ("transcript_channel", transcript_channel), ("sales_channel", sales_channel),
        ("rules_channel", rules_channel), ("ticket_category", ticket_category),
        ("staff_role", staff_role), ("auto_role", auto_role), ("vouch_channel", vouch_channel),
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
# Ping on join + auto roles
# ===========================================================================

joinping_group = app_commands.Group(
    name="joinping", description="Ping new members in channels, then delete the ping",
    default_permissions=discord.Permissions(administrator=True),
)
autorole_group = app_commands.Group(
    name="autorole", description="Roles new members get automatically",
    default_permissions=discord.Permissions(administrator=True),
)


def config_list(name: str) -> list[int]:
    return store.data.setdefault("config", {}).setdefault(name, [])


@joinping_group.command(name="add", description="📣 Ping new members in this channel (the ping deletes itself)")
@admin_only()
async def joinping_add(interaction: discord.Interaction, channel: discord.TextChannel) -> None:
    channels = config_list("join_ping_channels")
    if channel.id not in channels:
        channels.append(channel.id)
        store.save()
    await reply(interaction, f"📣 New members will get a quick ghost ping in {channel.mention}.")


@joinping_group.command(name="remove", description="🔕 Stop pinging new members in this channel")
@admin_only()
async def joinping_remove(interaction: discord.Interaction, channel: discord.TextChannel) -> None:
    channels = config_list("join_ping_channels")
    if channel.id in channels:
        channels.remove(channel.id)
        store.save()
    await reply(interaction, f"🔕 Removed {channel.mention} from join pings.")


@joinping_group.command(name="list", description="📋 See which channels ping new members")
@admin_only()
async def joinping_list(interaction: discord.Interaction) -> None:
    channels = config_list("join_ping_channels")
    text = "\n".join(f"📣 <#{c}>" for c in channels) or "No channels yet. Use `/joinping add`."
    await reply(interaction, embed=discord.Embed(title="📣 Join ping channels", description=text, colour=COLOUR))


@autorole_group.command(name="add", description="🎭 Give this role to everyone who joins")
@admin_only()
async def autorole_add(interaction: discord.Interaction, role: discord.Role) -> None:
    if role >= interaction.guild.me.top_role:
        return await reply(interaction, "❌ That role is above mine, so I can't give it. Move my role higher first.")
    if role.managed or role.is_default():
        return await reply(interaction, "❌ That role can't be given out.")
    roles = config_list("auto_roles")
    if role.id not in roles:
        roles.append(role.id)
        store.save()
    await reply(interaction, f"🎭 New members will now get {role.mention}.")


@autorole_group.command(name="remove", description="➖ Stop giving this role to new members")
@admin_only()
async def autorole_remove(interaction: discord.Interaction, role: discord.Role) -> None:
    roles = config_list("auto_roles")
    if role.id in roles:
        roles.remove(role.id)
    if store.data.get("config", {}).get("auto_role") == role.id:
        store.data["config"]["auto_role"] = None
    store.save()
    await reply(interaction, f"➖ New members won't get {role.mention} any more.")


@autorole_group.command(name="list", description="📋 See which roles new members get")
@admin_only()
async def autorole_list(interaction: discord.Interaction) -> None:
    ids = list(config_list("auto_roles"))
    if cfg("auto_role") and cfg("auto_role") not in ids:
        ids.append(cfg("auto_role"))
    text = "\n".join(f"🎭 <@&{r}>" for r in ids) or "No auto roles yet. Use `/autorole add`."
    await reply(interaction, embed=discord.Embed(title="🎭 Auto roles", description=text, colour=COLOUR))



# ===========================================================================
# Vouches
# ===========================================================================

STAR = "⭐"
PER_PAGE = 5


def stars(n: int) -> str:
    return STAR * n + "☆" * (5 - n)


def vouches_for(user_id: int) -> list[dict]:
    return [v for v in store.data.setdefault("vouches", []) if v["for"] == user_id]


@bot.tree.command(name="vouch", description="⭐ Vouch for someone you bought from")
@app_commands.describe(
    user="Who you're vouching for",
    product="What you bought",
    price="How much you paid, e.g. £10",
    rating="Your rating out of 5",
    comment="Anything else to say (optional)",
)
@app_commands.choices(rating=[app_commands.Choice(name=f"{stars(i)}  {i}/5", value=i) for i in range(5, 0, -1)])
@app_commands.checks.cooldown(1, 30)
async def vouch_cmd(
    interaction: discord.Interaction,
    user: discord.Member,
    product: app_commands.Range[str, 1, 150],
    price: app_commands.Range[str, 1, 50],
    rating: app_commands.Choice[int],
    comment: app_commands.Range[str, 1, 500] = None,
) -> None:
    if user.id == interaction.user.id:
        return await reply(interaction, "❌ You can't vouch for yourself.")
    if user.bot:
        return await reply(interaction, "❌ You can't vouch for a bot.")

    store.data.setdefault("vouches", [])
    number = store.data.get("vouch_count", 0) + 1
    store.data["vouch_count"] = number
    when = now()
    store.data["vouches"].append({
        "id": number, "for": user.id, "by": interaction.user.id, "product": product,
        "price": price, "rating": rating.value, "comment": comment, "time": when.timestamp(),
    })
    store.save()

    embed = discord.Embed(title="New vouch created!", description=stars(rating.value), colour=COLOUR, timestamp=when)
    embed.set_thumbnail(url=user.display_avatar.url)
    embed.add_field(name="Vouched for:", value=user.mention, inline=True)
    embed.add_field(name="Vouched by:", value=interaction.user.mention, inline=True)
    embed.add_field(name="Vouch Nº:", value=str(number), inline=True)
    embed.add_field(name="Product:", value=product, inline=True)
    embed.add_field(name="Paid:", value=price, inline=True)
    embed.add_field(name="Vouched at:", value=ts(when, "f"), inline=True)
    if comment:
        embed.add_field(name="Comment:", value=comment, inline=False)
    total = len(vouches_for(user.id))
    icon = interaction.guild.icon.url if interaction.guild.icon else None
    embed.set_footer(text=f"{user.display_name} now has {total} vouch{'es' if total != 1 else ''} • {STORE_NAME}", icon_url=icon)

    target = bot.get_channel(cfg("vouch_channel")) if cfg("vouch_channel") else None
    if target is None or target.id == interaction.channel.id:
        await interaction.response.send_message(embed=embed, allowed_mentions=discord.AllowedMentions.none())
    else:
        await target.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        await reply(interaction, f"⭐ Thanks! Your vouch was posted in {target.mention}.")


def vouches_page(user: discord.abc.User, page: int) -> tuple[discord.Embed, int]:
    items = list(reversed(vouches_for(user.id)))
    pages = max(1, (len(items) + PER_PAGE - 1) // PER_PAGE)
    page = max(0, min(page, pages - 1))
    embed = discord.Embed(title=f"⭐ {user.display_name}'s vouches", colour=COLOUR)
    embed.set_thumbnail(url=user.display_avatar.url)
    if not items:
        embed.description = "No vouches yet."
        return embed, 1
    avg = sum(v["rating"] for v in items) / len(items)
    embed.description = f"**{len(items)}** vouch{'es' if len(items) != 1 else ''} • average **{avg:.1f}/5** {STAR}"
    for v in items[page * PER_PAGE:(page + 1) * PER_PAGE]:
        lines = [f"{stars(v['rating'])}", f"📦 {v['product']} • 💵 {v['price']}", f"👤 by <@{v['by']}> • {ts(v['time'], 'R')}"]
        if v.get("comment"):
            lines.append(f"💬 {clip(v['comment'], 150)}")
        embed.add_field(name=f"Vouch Nº {v['id']}", value="\n".join(lines), inline=False)
    embed.set_footer(text=f"Page {page + 1} of {pages}")
    return embed, pages


class VouchPages(discord.ui.View):
    def __init__(self, user: discord.abc.User, viewer_id: int):
        super().__init__(timeout=300)
        self.user = user
        self.viewer_id = viewer_id
        self.page = 0
        self.sync()

    def sync(self) -> None:
        _, pages = vouches_page(self.user, self.page)
        self.prev.disabled = self.page <= 0
        self.next.disabled = self.page >= pages - 1

    async def show(self, interaction: discord.Interaction) -> None:
        self.sync()
        embed, _ = vouches_page(self.user, self.page)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(emoji="◀️", style=discord.ButtonStyle.secondary)
    async def prev(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.page -= 1
        await self.show(interaction)

    @discord.ui.button(emoji="▶️", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.page += 1
        await self.show(interaction)


@bot.tree.command(name="vouches", description="🔎 See all of someone's vouches (only you can see it)")
@app_commands.describe(user="Whose vouches to look up")
async def vouches_cmd(interaction: discord.Interaction, user: discord.User) -> None:
    embed, pages = vouches_page(user, 0)
    view = VouchPages(user, interaction.user.id) if pages > 1 else discord.utils.MISSING
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


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

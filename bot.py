"""
Build Server bot — one /buildserver command sets up the whole Discord server:
categories, channels (bar style, e.g. "📢 ┃ announcements"), roles, private logs,
a 24-hour auto-nuke on #general, and working logging (deletes/edits, joins/leaves).

Runs off the BOT's own permissions, so your personal account being limited
doesn't matter — the bot just needs Administrator in the server.

Environment variables (set these on your host — never in code/chat):
  DISCORD_TOKEN  - your bot token (reset it if it ever leaks)   [required]
  GUILD_ID       - your server ID (optional, makes /buildserver appear instantly)

Enable in the Developer Portal → your app → Bot → Privileged Gateway Intents:
  * SERVER MEMBERS INTENT  (for join/leave logs)
  * MESSAGE CONTENT INTENT (for message delete/edit logs)
"""

import asyncio
import os
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
GUILD_ID = os.getenv("GUILD_ID", "").strip()
if not TOKEN or TOKEN.startswith("PASTE"):
    raise SystemExit("Set DISCORD_TOKEN in your environment variables.")

BAR = "⠀┃⠀"   # (invisible space) ┃ (invisible space) — Discord keeps these, no dashes


def ch_name(emoji, name):
    return f"{emoji}{BAR}{name}"


# ---- the plan (exactly the channels you asked for + private logs) ----
ROLES = [("Owner", 0xFF3D9A), ("Admin", 0x19E0FF), ("Staff", 0x37D98A),
         ("Customer", 0xFFCE4D), ("Booster", 0xF47FFF), ("Member", 0x8498A9)]
STAFF = {"Owner", "Admin", "Staff"}   # who can see the private logs

PLAN = [
    {"cat": "📌 Important", "private": False, "channels": [
        ("📜", "rules"), ("📢", "announcements"), ("🌐", "website"),
        ("⭐", "vouches"), ("✈️", "telegram"), ("🎫", "support")]},
    {"cat": "💬 Community", "private": False, "channels": [
        ("💬", "general"), ("🎁", "giveaways")]},
    {"cat": "🔒 Logs", "private": True, "channels": [
        ("📝", "message-logs"), ("🛡️", "mod-logs"), ("🚪", "join-leave")]},
]
NUKE_SUFFIX = "general"   # channel whose messages get wiped every 24h

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)


def find_log(guild, suffix):
    return next((c for c in guild.text_channels if c.name.endswith(suffix)), None)


async def do_build(guild):
    made = {"roles": 0, "cats": 0, "channels": 0}
    roles = {}
    for name, color in ROLES:
        r = discord.utils.get(guild.roles, name=name)
        if not r:
            r = await guild.create_role(name=name, colour=discord.Colour(color), reason="buildserver")
            made["roles"] += 1
        roles[name] = r

    for block in PLAN:
        overwrites = None
        if block["private"]:
            overwrites = {guild.default_role: discord.PermissionOverwrite(view_channel=False),
                          guild.me: discord.PermissionOverwrite(view_channel=True)}
            for rn in STAFF:
                if roles.get(rn):
                    overwrites[roles[rn]] = discord.PermissionOverwrite(view_channel=True)

        cat = discord.utils.get(guild.categories, name=block["cat"])
        if not cat:
            cat = await guild.create_category(block["cat"], overwrites=overwrites or {}, reason="buildserver")
            made["cats"] += 1

        for emoji, nm in block["channels"]:
            full = ch_name(emoji, nm).lower()
            if discord.utils.get(guild.text_channels, name=full):
                continue
            await guild.create_text_channel(ch_name(emoji, nm), category=cat,
                                            overwrites=overwrites or {}, reason="buildserver")
            made["channels"] += 1
    return made


class Confirm(discord.ui.View):
    def __init__(self, author_id):
        super().__init__(timeout=60)
        self.author_id = author_id

    async def interaction_check(self, i):
        return i.user.id == self.author_id

    @discord.ui.button(label="Build it", style=discord.ButtonStyle.success)
    async def go(self, i: discord.Interaction, b):
        await i.response.edit_message(content="⚙️ Building… this takes a few seconds.", view=None)
        try:
            made = await do_build(i.guild)
        except discord.Forbidden:
            await i.edit_original_response(
                content="❌ I don't have permission. Give me the **Administrator** role and drag my role "
                        "**above** the others, then run `/buildserver` again.")
            return
        await i.edit_original_response(
            content=f"✅ Done! Added **{made['cats']}** categories, **{made['channels']}** channels, "
                    f"**{made['roles']}** roles.\n#general now auto-wipes every 24h, and the Logs are staff-only.")

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def no(self, i: discord.Interaction, b):
        await i.response.edit_message(content="Cancelled.", view=None)


@bot.tree.command(name="buildserver", description="Build the whole server: channels, roles, logs.")
async def buildserver(interaction: discord.Interaction):
    if not interaction.guild:
        await interaction.response.send_message("Run this in your server.", ephemeral=True)
        return
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("You need **Administrator** to run this.", ephemeral=True)
        return
    total = sum(len(b["channels"]) for b in PLAN)
    await interaction.response.send_message(
        f"This will create **{len(PLAN)}** categories, **{total}** channels, and **{len(ROLES)}** roles "
        "(skipping any that already exist). Build it?",
        view=Confirm(interaction.user.id), ephemeral=True)


# ---------------- 24h auto-nuke of #general ----------------
async def nuke_loop():
    await bot.wait_until_ready()
    while not bot.is_closed():
        await asyncio.sleep(24 * 3600)
        for guild in bot.guilds:
            ch = find_log(guild, NUKE_SUFFIX)
            if not ch:
                continue
            try:
                new = await ch.clone(reason="24h auto-nuke")
                await new.edit(position=ch.position)
                await ch.delete(reason="24h auto-nuke")
                print(f"[nuke] wiped #general in {guild.name}")
            except Exception as ex:
                print("[nuke] error:", ex)


# ---------------- logging ----------------
def ts():
    return datetime.now(timezone.utc)


@bot.event
async def on_message_delete(message):
    if not message.guild or message.author.bot:
        return
    ch = find_log(message.guild, "message-logs")
    if not ch:
        return
    e = discord.Embed(description=(message.content or "*no text*")[:4000], colour=0xFF5D7A, timestamp=ts())
    e.set_author(name=f"Message deleted · {message.author}", icon_url=getattr(message.author.display_avatar, "url", None))
    e.add_field(name="Channel", value=message.channel.mention)
    try:
        await ch.send(embed=e)
    except Exception:
        pass


@bot.event
async def on_message_edit(before, after):
    if not before.guild or before.author.bot or before.content == after.content:
        return
    ch = find_log(before.guild, "message-logs")
    if not ch:
        return
    e = discord.Embed(colour=0xFFCE4D, timestamp=ts())
    e.set_author(name=f"Message edited · {before.author}", icon_url=getattr(before.author.display_avatar, "url", None))
    e.add_field(name="Before", value=(before.content or "—")[:1000], inline=False)
    e.add_field(name="After", value=(after.content or "—")[:1000], inline=False)
    e.add_field(name="Channel", value=before.channel.mention, inline=False)
    try:
        await ch.send(embed=e)
    except Exception:
        pass


@bot.event
async def on_member_join(member):
    ch = find_log(member.guild, "join-leave")
    if ch:
        try:
            await ch.send(embed=discord.Embed(description=f"📥 {member.mention} **joined** ({member})",
                                              colour=0x37D98A, timestamp=ts()))
        except Exception:
            pass


@bot.event
async def on_member_remove(member):
    ch = find_log(member.guild, "join-leave")
    if ch:
        try:
            await ch.send(embed=discord.Embed(description=f"📤 **{member}** left",
                                              colour=0xFF5D7A, timestamp=ts()))
        except Exception:
            pass


@bot.event
async def on_ready():
    try:
        if GUILD_ID.isdigit():
            g = discord.Object(id=int(GUILD_ID))
            bot.tree.copy_global_to(guild=g)
            await bot.tree.sync(guild=g)
        else:
            await bot.tree.sync()
    except discord.Forbidden:
        await bot.tree.sync()
    bot.loop.create_task(nuke_loop())
    print(f"Logged in as {bot.user}")


if __name__ == "__main__":
    bot.run(TOKEN)

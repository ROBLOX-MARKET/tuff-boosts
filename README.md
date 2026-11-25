# Build Server bot

One command — `/buildserver` — sets up your whole Discord server.

## What it builds
- **📌 Important:** rules · announcements · website · vouches · telegram · support
- **💬 Community:** general *(auto-wiped every 24h)* · giveaways
- **🔒 Logs** *(private — Staff/Admin/Owner only):* message-logs · mod-logs · join-leave
- **Roles:** Owner · Admin · Staff · Customer · Booster · Member
- Channels use the bar style, e.g. `📢 ┃ announcements` (clean spacing, no dashes)
- **#general** auto-nukes every 24h (clones + deletes, wiping all messages)
- **Logs work:** message deletes/edits → message-logs, joins/leaves → join-leave

Re-running `/buildserver` skips anything that already exists, so it won't duplicate.

## Setup
1. **Create the bot** at https://discord.com/developers → New Application → **Bot** → **Reset Token** → copy it.
2. In **Bot → Privileged Gateway Intents**, turn ON:
   - **SERVER MEMBERS INTENT** (join/leave logs)
   - **MESSAGE CONTENT INTENT** (message logs)
3. Invite it: **OAuth2 → URL Generator** → scopes `bot` + `applications.commands`,
   bot permission **Administrator** → open the URL → add to your server.
4. In your server, drag the bot's role **near the top** of the role list.
5. **Host it** (see below) and set env vars:
   - `DISCORD_TOKEN` = your fresh token  (never in code or chat)
   - `GUILD_ID` = your server ID (optional, makes the command appear instantly)
6. In your server type **`/buildserver`** → **Build it**.

## Free hosts
- **bot-hosting.net** — free (needs renewing every few days); the one you already use.
- **Sparked Host** — free Discord-bot plan.
- **Discloud** — free tier for bots.
Any of them: upload these files (or pull this repo branch), set the env vars, start `main.py`.

## Note
Keep your token private. If it ever appears anywhere public, reset it immediately.

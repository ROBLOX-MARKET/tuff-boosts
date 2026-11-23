# Crypto Swaps — Discord bot

A **non-custodial** crypto swap bot. Users swap their own coins through
[ChangeNOW](https://changenow.io); the bot **never holds, moves, or can touch any funds** —
it only shows the swap details and tracks public stats.

## What it does
- `/cryptoswaps` → posts the Crypto Swaps panel with a **Start a Swap** button
- Flow: pick FROM coin → pick TO coin → enter amount + **your own** payout wallet
- Shows live quote, then a ChangeNOW **deposit address + QR**; the swapped coin lands in the user's wallet
- `/swapstats` → total swaps, total volume (USD), biggest swap, latest swap
- Coins: USDT · BTC · LTC · SOL · ETH (edit the `COINS` dict in `bot.py` to change)

## Setup (bot-hosting.net or any Python host)
1. **Create the bot** at https://discord.com/developers → New Application → Bot → **Reset Token** → copy it.
   - Under **Installation / OAuth2**, invite it to your server with the `applications.commands` + `bot` scopes.
2. **Get a ChangeNOW API key** (free) at https://changenow.io → Account → API.
3. **Set environment variables** on your host (NOT in code):
   - `DISCORD_TOKEN` = your fresh bot token
   - `CHANGENOW_API_KEY` = your ChangeNOW key
   - `GUILD_ID` = your server ID (optional, makes slash commands appear instantly)
4. Host runs `main.py` (or `python bot.py`). It installs `requirements.txt` automatically.

## Important
- **Never paste your token in chat or commit it.** If it leaks, reset it immediately.
- The payout address is always the **user's own** wallet — that's what keeps it non-custodial.
  Do not change the code to send payouts to a fixed address; that would make it custodial.
- ChangeNOW runs its own KYC on larger swaps. If you're under 18, the API/affiliate
  account may need a parent/guardian.
- Endpoints follow ChangeNOW API v2. If an API call fails, check the current docs at
  https://documenter.getpostman.com/view/8180765/SVfTPnM8 and adjust the URLs in `bot.py`.

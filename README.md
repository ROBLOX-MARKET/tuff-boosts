# Coin Buzzer Server — alerts that ring your phone when it's locked/asleep

A 24/7 crypto price watcher. When a coin hits your target it sends a **push via
ntfy** — the free ntfy app rings your phone **loud, even locked, asleep, on silent,
or in your pocket** (max priority). It also serves a **no-login web page** to manage
your alerts.

(A phone that's fully **powered off** can't receive anything — nothing can change that.
This covers locked / asleep / screen-off / silent.)

## One-time setup

1. **Install ntfy** on your phone (free): App Store or Google Play, search "ntfy".
2. **Pick a secret topic name** — make one up, hard to guess, e.g. `munzy-coins-9f3k2x`.
   Anyone who knows the topic can send you notifications, so keep it private.
3. In the ntfy app → **Subscribe to topic** → type that exact name.
4. In the ntfy app, open that topic's settings and set a **loud/alarm sound** and
   turn on **"override Do Not Disturb"** (so it rings even on silent).

## Deploy (bot-hosting or any 24/7 Python host)

1. Upload these files (or point the host at this repo branch).
2. Set **environment variables** on the host:
   - `NTFY_TOPIC` = the exact secret topic you chose  **(required)**
   - `POLL_SECONDS` = how often to check prices (default 60)
   - `PORT` = usually set automatically by the host
3. Start it (`python main.py` / `python server.py`). It installs `requirements.txt`.
4. Open the host's public URL → the **Coin Buzzer** page (no login) → add your alerts.
5. Tap **Send test push** — if your phone rings, you're done.

## How it works
- Alerts are stored in `alerts.json` next to the script.
- The watcher checks CoinGecko every `POLL_SECONDS`; on a cross it sends the push and
  marks the alert "sent" (tap **Re-arm** on the web page to reuse it).
- Coins: BTC ETH SOL LTC USDT BNB XRP ADA DOGE TRX LINK DOT AVAX SHIB PEPE WIF
  (edit the `COINS` list in `server.py` to add more).

## Note
Keep your topic name private. ntfy.sh is a public relay — the topic name is the only
thing protecting your alerts, so don't share it or post it anywhere.

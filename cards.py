"""Renders the crypto checker cards as PNG images (Munzy's Crypto Checker style)."""
from __future__ import annotations

import io
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT_DIR = Path(__file__).resolve().parent / "fonts"
SS = 2  # supersample for crisp text/edges

# ---- palette ----
OUTER = (32, 33, 37)
PANEL = (43, 45, 49)
PILL = (30, 31, 34)
LINE = (58, 60, 66)
WHITE = (242, 243, 245)
GREY = (181, 186, 193)
MUTED = (142, 148, 158)
GREEN = (59, 183, 108)
GREEN_BAR = (45, 206, 112)
RED = (237, 83, 86)
RED_BAR = (237, 83, 86)


def _f(name, size):
    try:
        return ImageFont.truetype(str(FONT_DIR / name), size * SS)
    except OSError:
        return ImageFont.load_default(size * SS)


def _mono(size):
    for p in (FONT_DIR / "DejaVuSansMono.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"):
        try:
            return ImageFont.truetype(str(p), size * SS)
        except OSError:
            pass
    return _f("Inter-Regular.otf", size)


def _glyph_font(size):
    for p in (FONT_DIR / "DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(str(p), size)
        except OSError:
            pass
    try:
        return ImageFont.truetype(str(FONT_DIR / "Inter-Bold.otf"), size)
    except OSError:
        return ImageFont.load_default(size)


BOLD = lambda s: _f("Inter-Bold.otf", s)
SEMI = lambda s: _f("Inter-SemiBold.otf", s)
REG = lambda s: _f("Inter-Regular.otf", s)
MONO = _mono


# ---------------------------------------------------------------- coin icons
_COIN_BG = {
    "USDC": (39, 117, 202), "USDT": (38, 161, 123), "LTC": (154, 158, 160),
    "BTC": (247, 147, 26), "ETH": (98, 126, 234), "BNB": (243, 186, 47),
}


def coin_icon(symbol: str, d: int) -> Image.Image:
    """Draw a round coin icon d×d (already supersampled by caller passing d*SS)."""
    s = d
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    dr = ImageDraw.Draw(im)
    sym = symbol.upper()
    if sym == "SOL":
        # dark circle + three slanted gradient bars (purple -> teal)
        dr.ellipse([0, 0, s - 1, s - 1], fill=(19, 21, 26, 255))
        bar_h = int(s * 0.12)
        gap = int(s * 0.10)
        skew = int(s * 0.14)
        top = int(s * 0.28)
        cols = [(153, 69, 255), (120, 120, 240), (20, 241, 149)]
        for i, col in enumerate(cols):
            y = top + i * (bar_h + gap)
            if i == 0:
                pts = [(int(s*0.26)+skew, y), (int(s*0.76), y), (int(s*0.70), y+bar_h), (int(s*0.20)+skew, y+bar_h)]
            elif i == 1:
                pts = [(int(s*0.24), y), (int(s*0.74)-skew, y), (int(s*0.80)-skew, y+bar_h), (int(s*0.30), y+bar_h)]
            else:
                pts = [(int(s*0.20)+skew, y), (int(s*0.70), y), (int(s*0.76), y+bar_h), (int(s*0.26)+skew, y+bar_h)]
            dr.polygon(pts, fill=col + (255,))
        return im
    bg = _COIN_BG.get(sym, (90, 94, 104))
    dr.ellipse([0, 0, s - 1, s - 1], fill=bg + (255,))
    if sym == "BTC":
        # draw ₿ (B with two vertical strokes) — the font's ₿ glyph is a placeholder box
        gf = _glyph_font(int(s * 0.60))
        tmp = Image.new("L", (s, s), 0)
        ImageDraw.Draw(tmp).text((s / 2, s / 2 - s * 0.03), "B", font=gf, fill=255, anchor="mm")
        bb = tmp.getbbox()
        dr.bitmap((0, 0), tmp, fill=(255, 255, 255, 255))
        if bb:
            w = bb[2] - bb[0]
            barw = max(2, int(w * 0.14))
            for gx in (bb[0] + int(w * 0.30), bb[0] + int(w * 0.56)):
                dr.rounded_rectangle([gx - barw // 2, bb[1] - int(s * 0.10), gx + barw // 2, bb[3] + int(s * 0.10)],
                                     radius=barw // 2, fill=(255, 255, 255, 255))
        return im
    glyph = {"USDC": "$", "USDT": "₮", "LTC": "Ł", "ETH": "Ξ", "BNB": "B"}.get(sym, sym[:1])
    gf = _glyph_font(int(s * 0.62))
    dr.text((s / 2, s / 2 - s * 0.04), glyph, font=gf, fill=(255, 255, 255, 255), anchor="mm")
    if sym == "USDC":  # ring like the real logo
        dr.ellipse([int(s*0.2), int(s*0.2), int(s*0.8), int(s*0.8)], outline=(255, 255, 255, 230), width=max(2, int(s*0.05)))
    return im


# ---------------------------------------------------------------- helpers
def _round(dr, box, r, fill):
    dr.rounded_rectangle(box, radius=r, fill=fill)


def _text(dr, xy, s, font, fill, anchor="la"):
    dr.text((xy[0] * SS, xy[1] * SS), s, font=font, fill=fill, anchor=anchor)


def _finish(img: Image.Image) -> bytes:
    if SS != 1:
        img = img.resize((img.width // SS, img.height // SS), Image.LANCZOS)
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _canvas(w, h):
    img = Image.new("RGBA", (w * SS, h * SS), (0, 0, 0, 0))
    dr = ImageDraw.Draw(img)
    # outer card with green accent bar
    _round(dr, [0, 0, (w - 1) * SS, (h - 1) * SS], 18 * SS, OUTER)
    _round(dr, [0, 0, 8 * SS, (h - 1) * SS], 6 * SS, GREEN_BAR)
    dr.rectangle([5 * SS, 0, 9 * SS, (h - 1) * SS], fill=GREEN_BAR)
    return img, dr


def _header(dr, img, x, y, w, title, subtitle):
    _text(dr, (x, y), title, BOLD(21), WHITE)
    _text(dr, (x, y + 28), subtitle, SEMI(13), MUTED)
    ly = (y + 52)
    dr.line([x * SS, ly * SS, (x + w) * SS, ly * SS], fill=LINE, width=max(1, SS))
    return y + 66


def _paste_icon(img, sym, x, y, d):
    ic = coin_icon(sym, d * SS)
    img.alpha_composite(ic, (x * SS, y * SS))


# ---------------------------------------------------------------- ADDRESSES
def render_addresses(name: str, wallets: list[dict]) -> bytes:
    W = 900
    pad = 30
    inner_x = 26
    row_h = 78
    top = 26
    h = top + 70 + max(1, len(wallets)) * row_h + 20
    img, dr = _canvas(W, h)
    # inner panel
    px0, py0, px1, py1 = inner_x, top, W - 26, h - 20
    _round(dr, [px0 * SS, py0 * SS, px1 * SS, py1 * SS], 14 * SS, PANEL)
    _round(dr, [px0 * SS, py0 * SS, (px0 + 5) * SS, py1 * SS], 4 * SS, GREEN_BAR)
    x = px0 + 24
    y = _header(dr, img, x, py0 + 20, (px1 - px0) - 48, name, "Payment Methods")
    for w in wallets:
        _paste_icon(img, w["symbol"], x, y + 8, 40)
        tx = x + 56
        _text(dr, (tx, y + 6), w["symbol"], BOLD(17), WHITE)
        _text(dr, (tx, y + 30), w["network"], REG(13), MUTED)
        # address pill on the right
        addr = w["address"]
        af = MONO(13)
        aw = dr.textlength(addr, font=af) / SS
        pill_w = aw + 28
        pill_x1 = px1 - 20
        pill_x0 = pill_x1 - pill_w
        _round(dr, [pill_x0 * SS, (y + 12) * SS, pill_x1 * SS, (y + 44) * SS], 8 * SS, PILL)
        _text(dr, (pill_x1 - 14, y + 28), addr, af, (219, 222, 225), anchor="rm")
        y += row_h
    return _finish(img)


# ---------------------------------------------------------------- BALANCES
def render_balances(name: str, rows: list[dict], total_usd: str) -> bytes:
    """rows: [{symbol, network, amount_str, usd_str}]"""
    W = 900
    inner_x = 26
    row_h = 74
    top = 26
    h = top + 70 + len(rows) * row_h + 86
    img, dr = _canvas(W, h)
    px0, py0, px1, py1 = inner_x, top, W - 26, h - 20
    _round(dr, [px0 * SS, py0 * SS, px1 * SS, py1 * SS], 14 * SS, PANEL)
    _round(dr, [px0 * SS, py0 * SS, (px0 + 5) * SS, py1 * SS], 4 * SS, GREEN_BAR)
    x = px0 + 24
    y = _header(dr, img, x, py0 + 20, (px1 - px0) - 48, name, "Balances")
    for r in rows:
        _paste_icon(img, r["symbol"], x, y + 6, 38)
        tx = x + 54
        _text(dr, (tx, y + 4), r["symbol"], BOLD(16), WHITE)
        _text(dr, (tx, y + 27), r["network"], REG(12), MUTED)
        rx = px1 - 22
        _text(dr, (rx, y + 6), r["amount_str"], BOLD(16), WHITE, anchor="ra")
        _text(dr, (rx, y + 29), r["usd_str"], REG(12), MUTED, anchor="ra")
        y += row_h
    # total line
    ly = y + 4
    dr.line([x * SS, ly * SS, (px1 - 22) * SS, ly * SS], fill=LINE, width=max(1, SS))
    _text(dr, (x, y + 18), "Estimated total", SEMI(14), GREY)
    _text(dr, (px1 - 22, y + 16), total_usd, BOLD(18), WHITE, anchor="ra")
    return _finish(img)


# ---------------------------------------------------------------- TX ALERT
def render_tx(name: str, w: dict, tx: dict, amount_str: str, fee_str: str,
              balance_str: str | None, balance_usd: str | None,
              amount_usd: str, fee_usd: str) -> bytes:
    sent = tx["direction"] == "sent"
    sym = w["symbol"]
    W = 900
    inner_x = 26
    top = 26
    # dynamic height: header + 3 grid rows + tx + balance
    h = top + 70 + 70 + 70 + 56 + 64 + 20
    img, dr = _canvas(W, h)
    px0, py0, px1, py1 = inner_x, top, W - 26, h - 20
    _round(dr, [px0 * SS, py0 * SS, px1 * SS, py1 * SS], 14 * SS, PANEL)
    _round(dr, [px0 * SS, py0 * SS, (px0 + 5) * SS, py1 * SS], 4 * SS, GREEN_BAR)
    x = px0 + 24
    y = py0 + 20
    # header: icon + coin name + badge, right: Transaction Confirmed
    _paste_icon(img, sym, x, y, 38)
    _text(dr, (x + 52, y - 2), w["name"], BOLD(19), WHITE)
    # badge pill
    badge = "SENT" if sent else "RECEIVED"
    bf = BOLD(11)
    bw = dr.textlength(badge, font=bf) / SS + 20
    _round(dr, [(x + 52) * SS, (y + 26) * SS, (x + 52 + bw) * SS, (y + 48) * SS], 7 * SS, RED if sent else GREEN)
    _text(dr, (x + 52 + bw / 2, y + 37), badge, bf, WHITE, anchor="mm")
    _text(dr, (px1 - 22, y - 2), "Transaction Confirmed", BOLD(17), GREEN, anchor="ra")
    _text(dr, (px1 - 22, y + 24), ("Outgoing · confirmed" if sent else "Incoming · confirmed"), REG(12), MUTED, anchor="ra")
    ly = y + 58
    dr.line([x * SS, ly * SS, (px1 - 22) * SS, ly * SS], fill=LINE, width=max(1, SS))
    y = ly + 16

    col = [x, x + 300, x + 560]

    def cell(ci, label, value, vcolour=WHITE, sub=None):
        cx = col[ci]
        _text(dr, (cx, y), label, REG(12), MUTED)
        _text(dr, (cx, y + 20), value, BOLD(15), vcolour)
        if sub:
            _text(dr, (cx, y + 42), sub, REG(12), MUTED)

    amt_col = RED if sent else GREEN
    cell(0, "Amount", amount_str, amt_col, amount_usd)
    cell(1, "Network fee", fee_str, WHITE, fee_usd)
    cell(2, "Fee rate", tx.get("fee_rate") or "—", WHITE)
    y += 66
    cell(0, "To", _short(tx.get("to", "—")), WHITE)
    cell(1, "Confirmations", str(tx.get("confirmations", 0)), WHITE)
    cell(2, "Block", f"{tx['block']:,}" if tx.get("block") else "—", WHITE)
    y += 60
    _text(dr, (x, y), "Transaction", REG(12), MUTED)
    _text(dr, (x, y + 20), _short(tx.get("hash", "—"), 12, 12) if tx.get("hash") else "—", MONO(13), (219, 222, 225))
    y += 52
    dr.line([x * SS, y * SS, (px1 - 22) * SS, y * SS], fill=LINE, width=max(1, SS))
    y += 14
    if balance_str is not None:
        _text(dr, (x, y), f"Balance Now · {w['name']}", REG(12), MUTED)
        _text(dr, (x, y + 20), balance_str, BOLD(16), GREEN)
        if balance_usd:
            _text(dr, (px1 - 22, y + 18), balance_usd, BOLD(15), WHITE, anchor="ra")
    return _finish(img)


def _short(t, head=8, tail=8):
    t = str(t or "")
    return t if len(t) <= head + tail + 1 else f"{t[:head]}…{t[-tail:]}"

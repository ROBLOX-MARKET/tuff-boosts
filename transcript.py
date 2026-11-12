"""Draws a ticket as Discord-style chat images (dark theme)."""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT_DIR = Path(__file__).resolve().parent / "fonts"

W = 1000
PAD = 24
AVATAR = 44
TEXT_X = PAD + AVATAR + 18
TEXT_W = W - TEXT_X - PAD
MAX_PAGE_H = 2600
IMG_LIMIT = (480, 360)

BG = (49, 51, 56)
HEADER_BG = (43, 45, 49)
TEXT = (219, 222, 225)
MUTED = (148, 155, 164)
WHITE = (242, 243, 245)
EMBED_BG = (43, 45, 49)
LINK = (0, 168, 252)
MENTION_BG = (60, 66, 112)
MENTION_FG = (201, 205, 251)
APP_BG = (88, 101, 242)


def _font(name: str, size: int):
    try:
        return ImageFont.truetype(str(FONT_DIR / name), size)
    except OSError:
        return ImageFont.load_default(size)


F_NAME = _font("Inter-SemiBold.otf", 19)
F_TEXT = _font("Inter-Regular.otf", 18)
F_BOLD = _font("Inter-Bold.otf", 18)
F_SMALL = _font("Inter-Regular.otf", 14)
F_SMALL_B = _font("Inter-Bold.otf", 12)
F_TITLE = _font("Inter-Bold.otf", 19)
F_HEAD = _font("Inter-SemiBold.otf", 22)
F_HUGE = _font("Inter-Bold.otf", 24)
LINE = 26


@dataclass
class Embed:
    colour: tuple | None = None
    author: str = ""
    title: str = ""
    description: str = ""
    fields: list[tuple[str, str]] = field(default_factory=list)
    footer: str = ""
    image: bytes | None = None


@dataclass
class Message:
    author: str
    author_id: int
    colour: tuple | None
    bot: bool
    time: str
    minute: float  # unix time, for grouping
    text: str = ""
    avatar: bytes | None = None
    images: list[bytes] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    embeds: list[Embed] = field(default_factory=list)
    buttons: list[str] = field(default_factory=list)


# ---------------- text helpers ----------------

_EMOJI = re.compile(r"<a?:(\w+):\d+>")


def clean(text: str) -> str:
    """Keep characters the font can draw; turn custom emoji into :name:."""
    text = _EMOJI.sub("", text or "")
    text = text.replace("—", "-").replace("–", "-").replace("…", "...")
    out = []
    for ch in text:
        o = ord(ch)
        if ch == "\n" or 32 <= o < 0x2000 or ch in "‘’“”•→←┃├╰":
            out.append(ch)
    return "".join(out)


def strip_md(text: str) -> str:
    text = re.sub(r"^#{1,3} ", "", text, flags=re.M)
    text = re.sub(r"^-# ", "", text, flags=re.M)
    text = re.sub(r"^> ?", "┃ ", text, flags=re.M)
    text = text.replace("**", "").replace("__", "").replace("~~", "").replace("`", "")
    text = re.sub(r"(?<!\w)\*(?!\s)(.+?)(?<!\s)\*(?!\w)", r"\1", text)
    return text


def wrap(text: str, font, width: int) -> list[str]:
    lines = []
    for para in text.split("\n"):
        cur = ""
        for word in para.split(" "):
            cand = f"{cur} {word}" if cur else word
            if font.getlength(cand) <= width:
                cur = cand
                continue
            if cur:
                lines.append(cur)
            cur = ""
            for ch in word:
                if font.getlength(cur + ch) > width:
                    lines.append(cur)
                    cur = ""
                cur += ch
        lines.append(cur)
    return lines


def _img(data: bytes | None, limit) -> Image.Image | None:
    if not data:
        return None
    try:
        im = Image.open(io.BytesIO(data))
        im.seek(0)
        im = im.convert("RGBA")
        im.thumbnail(limit)
        return im
    except Exception:
        return None


def _rounded(im: Image.Image, r: int) -> Image.Image:
    mask = Image.new("L", im.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, im.width - 1, im.height - 1], radius=r, fill=255)
    out = im.copy()
    out.putalpha(Image.composite(im.getchannel("A"), mask, mask) if im.mode == "RGBA" else mask)
    return out


def _avatar(data: bytes | None, name: str) -> Image.Image:
    im = _img(data, (AVATAR, AVATAR))
    if im is None:
        im = Image.new("RGBA", (AVATAR, AVATAR), APP_BG + (255,))
        ImageDraw.Draw(im).text((AVATAR / 2, AVATAR / 2), (clean(name)[:1] or "?").upper(),
                                fill=WHITE, font=F_NAME, anchor="mm")
    im = im.resize((AVATAR, AVATAR))
    mask = Image.new("L", (AVATAR, AVATAR), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, AVATAR - 1, AVATAR - 1], fill=255)
    im.putalpha(mask)
    return im


# ---------------- drawing blocks (each returns (height, draw_fn)) ----------------

def _text_block(text: str, width: int, font=F_TEXT, colour=TEXT):
    lines = wrap(strip_md(clean(text)), font, width) if text.strip() else []

    def draw(d: ImageDraw.ImageDraw, canvas, x, y):
        for i, line in enumerate(lines):
            _draw_line(d, x, y + i * LINE, line, font, colour)
    return len(lines) * LINE, draw


_MENTION = re.compile(r"(@[\w .'&-]{1,32}|#[\w・✦★✧ -]{1,40})")


def _draw_line(d, x, y, line, font, colour):
    """Draw a line, highlighting @mentions like Discord does."""
    pos = x
    for part in re.split(r"(@[\w.&'-]+(?: [A-Z][\w.&'-]+)?)", line):
        if not part:
            continue
        if part.startswith("@") and len(part) > 1:
            w = font.getlength(part)
            d.rounded_rectangle([pos - 2, y + 1, pos + w + 2, y + LINE - 3], radius=4, fill=MENTION_BG)
            d.text((pos, y), part, fill=MENTION_FG, font=font)
        elif part.startswith("http"):
            d.text((pos, y), part, fill=LINK, font=font)
        else:
            d.text((pos, y), part, fill=colour, font=font)
        pos += font.getlength(part)


def _embed_block(e: Embed, width: int):
    inner_w = min(width, 560) - 32
    parts = []  # (height, fn)
    if e.author:
        parts.append(_text_block(e.author, inner_w, F_SMALL_B, WHITE))
    if e.title:
        parts.append(_text_block(e.title, inner_w, F_TITLE, WHITE))
    if e.description:
        parts.append(_text_block(e.description, inner_w, F_TEXT, TEXT))
    for name, value in e.fields:
        parts.append(_text_block(name, inner_w, F_BOLD, WHITE))
        parts.append(_text_block(value, inner_w, F_TEXT, TEXT))
    image = _img(e.image, (inner_w, 300))
    if e.footer:
        parts.append(_text_block(e.footer, inner_w, F_SMALL, MUTED))
    content_h = sum(h for h, _ in parts) + 6 * max(0, len(parts) - 1) + (image.height + 10 if image else 0)
    box_w = min(width, 560)
    h = content_h + 28

    def draw(d, canvas, x, y):
        d.rounded_rectangle([x, y, x + box_w, y + h], radius=6, fill=EMBED_BG)
        bar = e.colour or (32, 34, 37)
        d.rounded_rectangle([x, y, x + 4, y + h], radius=2, fill=bar)
        cy = y + 14
        footer = parts[-1] if e.footer else None
        for ph, fn in (parts[:-1] if footer else parts):
            fn(d, canvas, x + 18, cy)
            cy += ph + 6
        if image:
            r = _rounded(image, 6)
            canvas.paste(r, (x + 18, cy + 2), r)
            cy += image.height + 10
        if footer:
            footer[1](d, canvas, x + 18, cy)
    return h, draw


def _buttons_block(labels: list[str]):
    if not labels:
        return 0, lambda *a: None

    def draw(d, canvas, x, y):
        pos = x
        for lab in labels:
            lab = clean(lab).strip() or "Button"
            w = F_SMALL.getlength(lab) + 28
            d.rounded_rectangle([pos, y, pos + w, y + 34], radius=6, fill=(78, 80, 88))
            d.text((pos + 14, y + 8), lab, fill=WHITE, font=F_SMALL)
            pos += w + 8
    return 42, draw


def _images_block(datas: list[bytes]):
    ims = [i for i in (_img(b, IMG_LIMIT) for b in datas) if i]
    h = sum(i.height + 8 for i in ims)

    def draw(d, canvas, x, y):
        cy = y
        for im in ims:
            r = _rounded(im, 8)
            canvas.paste(r, (x, cy), r)
            cy += im.height + 8
    return h, draw


# ---------------- page layout ----------------

def _message_blocks(m: Message, header: bool):
    blocks = []
    if m.text:
        blocks.append(_text_block(m.text, TEXT_W))
    for f in m.files:
        blocks.append(_text_block(f"[File] {f}", TEXT_W, F_TEXT, LINK))
    if m.images:
        blocks.append(_images_block(m.images))
    for e in m.embeds:
        blocks.append(_embed_block(e, TEXT_W))
    if m.buttons:
        blocks.append(_buttons_block(m.buttons))
    body = sum(h for h, _ in blocks) + 6 * max(0, len(blocks) - 1)
    top = 30 if header else 0
    height = max(top + body, AVATAR if header else LINE) + (18 if header else 6)
    return blocks, height


def render(channel_name: str, subtitle: str, messages: list[Message]) -> list[bytes]:
    laid = []
    prev = None
    for m in messages:
        header = prev is None or prev.author_id != m.author_id or m.minute - prev.minute > 420 or bool(prev.embeds and m.embeds)
        blocks, h = _message_blocks(m, header)
        laid.append((m, header, blocks, h))
        prev = m

    HEAD = 70
    pages, cur, used = [], [], HEAD + PAD
    for item in laid:
        if cur and used + item[3] > MAX_PAGE_H and item[1]:
            pages.append(cur)
            cur, used = [], HEAD + PAD
        cur.append(item)
        used += item[3]
    pages.append(cur)

    out = []
    title = clean(channel_name)
    for n, page in enumerate(pages, 1):
        h = HEAD + PAD * 2 + sum(i[3] for i in page)
        canvas = Image.new("RGBA", (W, h), BG + (255,))
        d = ImageDraw.Draw(canvas)
        d.rectangle([0, 0, W, HEAD], fill=HEADER_BG)
        d.line([0, HEAD, W, HEAD], fill=(31, 33, 36), width=2)
        d.text((PAD, 14), "#", fill=MUTED, font=F_HUGE)
        d.text((PAD + 28, 15), title, fill=WHITE, font=F_HEAD)
        d.text((PAD + 28, 44), clean(subtitle), fill=MUTED, font=F_SMALL)
        d.text((W - PAD, 26), f"Page {n} of {len(pages)}", fill=MUTED, font=F_SMALL, anchor="ra")

        y = HEAD + PAD
        for m, header, blocks, bh in page:
            cy = y
            if header:
                av = _avatar(m.avatar, m.author)
                canvas.paste(av, (PAD, y), av)
                name = clean(m.author).strip() or "User"
                d.text((TEXT_X, y), name, fill=m.colour or WHITE, font=F_NAME)
                tx = TEXT_X + F_NAME.getlength(name) + 8
                if m.bot:
                    d.rounded_rectangle([tx, y + 3, tx + 36, y + 21], radius=4, fill=APP_BG)
                    d.text((tx + 18, y + 12), "APP", fill=WHITE, font=F_SMALL_B, anchor="mm")
                    tx += 44
                d.text((tx, y + 4), m.time, fill=MUTED, font=F_SMALL)
                cy = y + 30
            for bhh, fn in blocks:
                fn(d, canvas, TEXT_X, cy)
                cy += bhh + 6
            y += bh

        buf = io.BytesIO()
        canvas.convert("RGB").save(buf, format="PNG", optimize=True)
        out.append(buf.getvalue())
    return out

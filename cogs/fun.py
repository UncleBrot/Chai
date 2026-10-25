import asyncio
import datetime
import html
import io
import math
import os
import random
import re
import urllib.parse

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
from PIL import Image, ImageDraw, ImageEnhance, ImageFont

import database as db
from utils.r2 import upload_image_to_r2

CUSTOM_EMOJI_RE = re.compile(r"<(a?):([a-zA-Z0-9_]{1,32}):([0-9]{15,22})>")
USER_MENTION_RE = re.compile(r"<@!?([0-9]{15,22})>")
CHANNEL_MENTION_RE = re.compile(r"<#([0-9]{15,22})>")
ROLE_MENTION_RE = re.compile(r"<@&([0-9]{15,22})>")

# Match standard Unicode emojis (surrogate pairs / supplemental symbols / dingbats)
UNICODE_EMOJI_RE = re.compile(
    "["
    "\U0001F1E6-\U0001F1FF"
    "\U0001F300-\U0001F5FF"
    "\U0001F600-\U0001F64F"
    "\U0001F680-\U0001F6FF"
    "\U0001F700-\U0001F77F"
    "\U0001F780-\U0001F7FF"
    "\U0001F800-\U0001F8FF"
    "\U0001F900-\U0001F9FF"
    "\U0001FA00-\U0001FA6F"
    "\U0001FA70-\U0001FAFF"
    "\U00002600-\U000026FF"
    "\U00002700-\U000027BF"
    "]+",
    flags=re.UNICODE,
)

FALLBACK_RIZZ = [
    "Are you a magician? Because whenever I look at you, everyone else disappears.",
    "Do you have a map? Because I just got lost in your eyes.",
    "Are you Wi-Fi? Because I'm really feeling a connection.",
    "If you were a vegetable, you'd be a cute-cumber.",
    "Are you a parking ticket? Because you've got FINE written all over you.",
]

FALLBACK_ROASTS = [
    "You're the reason the gene pool needs a lifeguard.",
    "If I wanted to hear from an idiot, I'd just read your message history.",
    "You bring everyone so much joy… whenever you leave the voice channel.",
    "Your secrets are always safe with me. I never even listen when you tell me them.",
    "Somewhere out there is a tree tirelessly producing oxygen for you. You owe it an apology.",
]

FALLBACK_CORPORATE = [
    "Synergistically leverage cross-platform paradigms to maximize stakeholder value.",
    "Proactively赋能 scalable deliverables while streamlining mission-critical workflows.",
    "Holistically orchestrate cloud-native ecosystems for end-to-end digital transformation.",
    "Dynamically benchmark low-hanging fruit to drive hyper-growth KPIs.",
]


def _load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    font_name = "LiberationSans-Bold.ttf" if bold else "LiberationSans-Regular.ttf"
    candidates = [
        f"/usr/share/fonts/truetype/liberation/{font_name}",
        "C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except IOError:
                continue
    return ImageFont.load_default()


def _circle_crop(img: Image.Image, size: int) -> Image.Image:
    img = img.convert("RGBA").resize((size, size), Image.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    draw.ellipse((0, 0, size - 1, size - 1), fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    return out


def _rounded_image(img: Image.Image, max_width: int, max_height: int, radius: int = 20) -> Image.Image:
    img = img.convert("RGBA")
    ratio = min(max_width / img.width, max_height / img.height, 1.0)
    new_w = max(1, int(img.width * ratio))
    new_h = max(1, int(img.height * ratio))
    img = img.resize((new_w, new_h), Image.LANCZOS)

    mask = Image.new("L", (new_w, new_h), 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle((0, 0, new_w - 1, new_h - 1), radius=radius, fill=255)
    out = Image.new("RGBA", (new_w, new_h), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    return out


def _twemoji_codepoint(char_seq: str) -> str:
    # Strip variation selector-16 if there is no ZWJ, matching Twemoji asset naming
    codepoints = [f"{ord(c):x}" for c in char_seq]
    if "200d" not in codepoints:
        codepoints = [cp for cp in codepoints if cp != "fe0f"]
    return "-".join(codepoints)


def _tokenize_tweet_text(text: str) -> list[tuple[str, str]]:
    """Split text into tokens of type:
    ('newline', '\\n'), ('space', ' '), ('custom_emoji', emoji_id),
    ('unicode_emoji', char_seq), ('word', word_str)
    """
    tokens: list[tuple[str, str]] = []
    lines = text.split("\n")
    for line_idx, line in enumerate(lines):
        if line_idx > 0:
            tokens.append(("newline", "\n"))
        pos = 0
        for match in CUSTOM_EMOJI_RE.finditer(line):
            before = line[pos : match.start()]
            if before:
                _tokenize_plain_segment(before, tokens)
            emoji_id = match.group(3)
            tokens.append(("custom_emoji", emoji_id))
            pos = match.end()
        remainder = line[pos:]
        if remainder:
            _tokenize_plain_segment(remainder, tokens)
    return tokens


def _tokenize_plain_segment(segment: str, tokens: list[tuple[str, str]]):
    parts = re.split(r"(\s+)", segment)
    for part in parts:
        if not part:
            continue
        if part.isspace():
            tokens.append(("space", " "))
            continue
        pos = 0
        for em in UNICODE_EMOJI_RE.finditer(part):
            if em.start() > pos:
                tokens.append(("word", part[pos : em.start()]))
            # Split contiguous unicode emojis into individual characters
            for ch in em.group(0):
                if ord(ch) in (0xFE0F, 0x200D):
                    continue
                tokens.append(("unicode_emoji", ch))
            pos = em.end()
        if pos < len(part):
            tokens.append(("word", part[pos:]))


def render_tweet_card(
    avatar_bytes: bytes,
    display_name: str,
    username: str,
    text: str,
    emoji_map: dict[str, bytes],
    attachment_bytes: bytes | None,
    timestamp_dt: datetime.datetime,
) -> io.BytesIO:
    """Render a realistic Dark-Mode X/Twitter post card using Pillow."""
    W = 800
    PAD = 36
    CONTENT_W = W - (PAD * 2)

    bg_color = (21, 32, 43)       # Twitter Dim Dark Mode (#15202B)
    text_white = (247, 249, 249)  # #F7F9F9
    muted_gray = (139, 152, 165)  # #8B98A5
    twitter_blue = (29, 155, 240) # #1D9BF0
    divider_color = (56, 68, 77)  # #38444D

    font_name = _load_font(26, bold=True)
    font_handle = _load_font(21, bold=False)
    font_body = _load_font(30, bold=False)
    font_meta = _load_font(21, bold=False)
    font_stats_bold = _load_font(22, bold=True)

    dummy_draw = ImageDraw.Draw(Image.new("RGBA", (1, 1)))

    def text_w(s: str, f) -> int:
        bbox = dummy_draw.textbbox((0, 0), s, font=f)
        return bbox[2] - bbox[0]

    tokens = _tokenize_tweet_text(text) if text.strip() else []
    EMOJI_SIZE = 34
    LINE_HEIGHT = 44
    SPACE_W = text_w(" ", font_body)

    # Layout tokens into lines; each line is a list of items:
    # ('text', str, color) or ('emoji', key)
    lines: list[list[tuple]] = [[]]
    cur_w = 0

    for t_type, t_val in tokens:
        if t_type == "newline":
            lines.append([])
            cur_w = 0
        elif t_type == "space":
            if cur_w > 0 and cur_w + SPACE_W <= CONTENT_W:
                lines[-1].append(("space", SPACE_W))
                cur_w += SPACE_W
        elif t_type in ("custom_emoji", "unicode_emoji"):
            key = f"c:{t_val}" if t_type == "custom_emoji" else f"u:{_twemoji_codepoint(t_val)}"
            if cur_w + EMOJI_SIZE > CONTENT_W and lines[-1]:
                lines.append([])
                cur_w = 0
            lines[-1].append(("emoji", key))
            cur_w += EMOJI_SIZE + 4
        elif t_type == "word":
            is_highlight = t_val.startswith(("@", "#", "http://", "https://"))
            color = twitter_blue if is_highlight else text_white
            w = text_w(t_val, font_body)
            if w > CONTENT_W:
                # Break very long words character by character
                chunk = ""
                for ch in t_val:
                    if text_w(chunk + ch, font_body) > CONTENT_W and chunk:
                        if lines[-1]:
                            lines.append([])
                        lines[-1].append(("text", chunk, color))
                        lines.append([])
                        chunk = ch
                        cur_w = text_w(chunk, font_body)
                    else:
                        chunk += ch
                        cur_w = text_w(chunk, font_body)
                if chunk:
                    lines[-1].append(("text", chunk, color))
            else:
                if cur_w + w > CONTENT_W and lines[-1]:
                    lines.append([])
                    cur_w = 0
                lines[-1].append(("text", t_val, color))
                cur_w += w

    if lines == [[]]:
        lines = []

    # Prepare optional attached image
    attached_img = None
    if attachment_bytes:
        try:
            raw_att = Image.open(io.BytesIO(attachment_bytes))
            attached_img = _rounded_image(raw_att, CONTENT_W, 450, radius=20)
        except Exception:
            attached_img = None

    header_h = 72
    body_h = len(lines) * LINE_HEIGHT + (16 if lines else 0)
    att_h = (attached_img.height + 20) if attached_img else 0
    footer_h = 115

    total_h = PAD + header_h + 18 + body_h + att_h + footer_h + PAD
    canvas = Image.new("RGBA", (W, total_h), bg_color)
    draw = ImageDraw.Draw(canvas)

    # 1. Avatar
    try:
        av_img = _circle_crop(Image.open(io.BytesIO(avatar_bytes)), 68)
        canvas.paste(av_img, (PAD, PAD), av_img)
    except Exception:
        draw.ellipse((PAD, PAD, PAD + 68, PAD + 68), fill=twitter_blue)

    # 2. Display Name + Verified Badge + @username
    name_x = PAD + 68 + 16
    name_y = PAD + 6
    clean_display = display_name[:28]
    draw.text((name_x, name_y), clean_display, font=font_name, fill=text_white)

    badge_x = name_x + text_w(clean_display, font_name) + 8
    badge_y = name_y + 4
    draw.ellipse((badge_x, badge_y, badge_x + 24, badge_y + 24), fill=twitter_blue)
    # Draw white checkmark inside badge
    draw.line(
        [(badge_x + 6, badge_y + 12), (badge_x + 10, badge_y + 17), (badge_x + 18, badge_y + 7)],
        fill=(255, 255, 255),
        width=3,
    )

    draw.text((name_x, name_y + 34), f"@{username}", font=font_handle, fill=muted_gray)

    # 3. Render Tweet Body (Text + Inline Custom/Unicode Emojis)
    y_cursor = PAD + header_h + 18
    for line in lines:
        x_cursor = PAD
        for item in line:
            kind = item[0]
            if kind == "space":
                x_cursor += item[1]
            elif kind == "text":
                _, word_str, color = item
                draw.text((x_cursor, y_cursor), word_str, font=font_body, fill=color)
                x_cursor += text_w(word_str, font_body)
            elif kind == "emoji":
                _, key = item
                em_bytes = emoji_map.get(key)
                if em_bytes:
                    try:
                        em_img = Image.open(io.BytesIO(em_bytes)).convert("RGBA")
                        em_img = em_img.resize((EMOJI_SIZE, EMOJI_SIZE), Image.LANCZOS)
                        canvas.paste(em_img, (int(x_cursor), int(y_cursor + 2)), em_img)
                    except Exception:
                        pass
                x_cursor += EMOJI_SIZE + 4
        y_cursor += LINE_HEIGHT

    if lines:
        y_cursor += 10

    # 4. Render Attached Media (if any)
    if attached_img:
        canvas.paste(attached_img, (PAD, y_cursor), attached_img)
        y_cursor += attached_img.height + 20

    # 5. Timestamp & Views line
    time_str = timestamp_dt.strftime("%I:%M %p · %b %d, %Y").lstrip("0")
    views_count = f"{random.randint(12, 950)}.{random.randint(1, 9)}K"
    meta_text = f"{time_str} · "
    draw.text((PAD, y_cursor), meta_text, font=font_meta, fill=muted_gray)
    mx = PAD + text_w(meta_text, font_meta)
    draw.text((mx, y_cursor), views_count, font=font_stats_bold, fill=text_white)
    mx += text_w(views_count, font_stats_bold)
    draw.text((mx, y_cursor), " Views", font=font_meta, fill=muted_gray)

    y_cursor += 40
    draw.line([(PAD, y_cursor), (W - PAD, y_cursor)], fill=divider_color, width=2)
    y_cursor += 20

    # 6. Retweets, Quotes, Likes, Bookmarks stats
    stats = [
        (f"{random.randint(120, 4200):,}", " Reposts"),
        (f"{random.randint(15, 600):,}", " Quotes"),
        (f"{random.randint(1500, 48000):,}", " Likes"),
        (f"{random.randint(80, 2900):,}", " Bookmarks"),
    ]
    sx = PAD
    for num_str, label_str in stats:
        draw.text((sx, y_cursor), num_str, font=font_stats_bold, fill=text_white)
        sx += text_w(num_str, font_stats_bold)
        draw.text((sx, y_cursor), label_str, font=font_meta, fill=muted_gray)
        sx += text_w(label_str, font_meta) + 32

    out = io.BytesIO()
    canvas.convert("RGB").save(out, format="PNG")
    out.seek(0)
    return out


def render_ship_card(avatar1_bytes: bytes, avatar2_bytes: bytes, percentage: int) -> io.BytesIO:
    """Render a custom Ship compatibility banner with both avatars and percentage."""
    W, H = 700, 260
    bg = Image.new("RGBA", (W, H), (30, 27, 46, 255))
    draw = ImageDraw.Draw(bg)

    # Decorative soft border
    draw.rounded_rectangle((8, 8, W - 9, H - 9), radius=24, outline=(235, 69, 158, 180), width=3)

    try:
        av1 = _circle_crop(Image.open(io.BytesIO(avatar1_bytes)), 160)
        bg.paste(av1, (55, 50), av1)
    except Exception:
        draw.ellipse((55, 50, 215, 210), fill=(88, 101, 242))

    try:
        av2 = _circle_crop(Image.open(io.BytesIO(avatar2_bytes)), 160)
        bg.paste(av2, (W - 215, 50), av2)
    except Exception:
        draw.ellipse((W - 215, 50, W - 55, 210), fill=(235, 69, 158))

    # Draw heart / circle in center
    cx, cy = W // 2, H // 2 - 15
    heart_color = (235, 69, 158) if percentage >= 50 else (114, 137, 218)
    draw.ellipse((cx - 56, cy - 56, cx + 56, cy + 56), fill=heart_color, outline=(255, 255, 255), width=4)

    font_pct = _load_font(34, bold=True)
    pct_str = f"{percentage}%"
    bbox = draw.textbbox((0, 0), pct_str, font=font_pct)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text((cx - tw // 2, cy - th // 2 - 4), pct_str, font=font_pct, fill=(255, 255, 255))

    # Progress bar at bottom
    bar_w, bar_h = 240, 22
    bx, by = cx - bar_w // 2, H - 48
    draw.rounded_rectangle((bx, by, bx + bar_w, by + bar_h), radius=11, fill=(55, 52, 75))
    fill_w = int((bar_w * max(0, min(100, percentage))) / 100)
    if fill_w > 0:
        draw.rounded_rectangle((bx, by, bx + max(22, fill_w), by + bar_h), radius=11, fill=heart_color)

    out = io.BytesIO()
    bg.convert("RGB").save(out, format="PNG")
    out.seek(0)
    return out


def render_wanted_poster(avatar_bytes: bytes, display_name: str) -> io.BytesIO:
    """Fallback Wanted Poster renderer in Pillow if external API is unavailable."""
    W, H = 500, 680
    parchment = (238, 216, 174)
    dark_ink = (48, 30, 18)
    poster = Image.new("RGBA", (W, H), parchment)
    draw = ImageDraw.Draw(poster)

    draw.rectangle((18, 18, W - 18, H - 18), outline=dark_ink, width=6)
    draw.rectangle((28, 28, W - 28, H - 28), outline=dark_ink, width=2)

    font_title = _load_font(72, bold=True)
    font_sub = _load_font(30, bold=True)
    font_name = _load_font(36, bold=True)
    font_reward = _load_font(34, bold=True)

    def center_text(y: int, text: str, font):
        bbox = draw.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
        draw.text(((W - tw) // 2, y), text, font=font, fill=dark_ink)

    center_text(42, "WANTED", font_title)
    center_text(125, "DEAD OR ALIVE", font_sub)

    try:
        av = Image.open(io.BytesIO(avatar_bytes)).convert("RGB").resize((320, 320), Image.LANCZOS)
        av = ImageEnhance.Color(av).enhance(0.35)
        poster.paste(av, ((W - 320) // 2, 175))
    except Exception:
        pass
    draw.rectangle(((W - 320) // 2, 175, (W + 320) // 2, 495), outline=dark_ink, width=4)

    center_text(515, display_name.upper()[:20], font_name)
    bounty = f"${random.randint(1, 999)},000,000 REWARD"
    center_text(575, bounty, font_reward)

    out = io.BytesIO()
    poster.convert("RGB").save(out, format="PNG")
    out.seek(0)
    return out


def render_petpet_gif(avatar_bytes: bytes) -> io.BytesIO:
    """Generate a smooth 10-frame animated PetPet GIF with squash-and-stretch physics."""
    av = Image.open(io.BytesIO(avatar_bytes)).convert("RGBA")
    SIZE = 128
    frames = []
    # Squash/stretch deformation curve across 10 frames
    deform = [
        (0, 0, 0, 0),
        (-4, 6, 4, -6),
        (-8, 12, 8, -12),
        (-12, 18, 12, -18),
        (-8, 12, 8, -12),
        (-2, 4, 2, -4),
        (2, -2, -2, 2),
        (0, 0, 0, 0),
    ]

    for i, (dx, dy, dw, dh) in enumerate(deform):
        frame = Image.new("RGBA", (SIZE, SIZE), (255, 255, 255, 0))
        base_w = 96 + dw
        base_h = 96 + dh
        squished = av.resize((max(20, base_w), max(20, base_h)), Image.LANCZOS)
        paste_x = (SIZE - base_w) // 2
        paste_y = SIZE - base_h - 6
        frame.paste(squished, (paste_x, paste_y), squished)

        # Draw patting hand at top
        draw = ImageDraw.Draw(frame)
        hand_y = 6 + max(0, dy)
        hand_x = 34 + (dx // 2)
        palm_box = (hand_x, hand_y, hand_x + 62, hand_y + 30)
        draw.ellipse(palm_box, fill=(255, 220, 177, 255), outline=(90, 55, 30, 255), width=3)
        # Fingers
        for fx in range(3):
            f_left = hand_x - 6 + (fx * 18)
            f_top = hand_y + 12
            draw.ellipse((f_left, f_top, f_left + 20, f_top + 16), fill=(255, 220, 177, 255), outline=(90, 55, 30, 255), width=2)

        # Convert RGBA frame to RGB on dark background for clean GIF palette
        bg = Image.new("RGB", (SIZE, SIZE), (43, 45, 49))
        bg.paste(frame, (0, 0), frame)
        frames.append(bg)

    out = io.BytesIO()
    frames[0].save(
        out,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=45,
        loop=0,
    )
    out.seek(0)
    return out


class FunCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.tweet_ctx_menu = app_commands.ContextMenu(
            name="Make it a Tweet",
            callback=self.tweet_context_menu,
        )
        self.bot.tree.add_command(self.tweet_ctx_menu)

    async def cog_unload(self):
        self.bot.tree.remove_command(self.tweet_ctx_menu.name, type=self.tweet_ctx_menu.type)

    async def _fetch_bytes(self, url: str) -> bytes | None:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                    if resp.status == 200:
                        return await resp.read()
        except Exception:
            pass
        return None

    async def _fetch_json(self, url: str, headers: dict | None = None) -> dict | list | None:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                    if resp.status == 200:
                        return await resp.json(content_type=None)
        except Exception:
            pass
        return None

    async def _send_image_output(
        self,
        out_bytes: io.BytesIO,
        filename: str,
        title: str,
        send_func,
        description: str | None = None,
        color: discord.Color = discord.Color.blurple(),
    ):
        """Upload to R2 (or fall back to Discord file attachment) and send embed."""
        raw_bytes = out_bytes.getvalue()
        content_type = "image/gif" if filename.endswith(".gif") else "image/png"
        try:
            r2_url = await upload_image_to_r2(raw_bytes, filename, content_type)
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(r2_url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                        await resp.read()
            except Exception:
                pass
            embed = discord.Embed(title=title, description=description, color=color)
            embed.set_image(url=r2_url)
            msg = await send_func(embed=embed)
            if isinstance(msg, discord.Message) and msg.embeds:
                img = msg.embeds[0].image
                if not img or not img.proxy_url or not img.width:
                    await asyncio.sleep(1.0)
                    try:
                        await msg.edit(embed=embed)
                    except Exception:
                        pass
        except Exception:
            file = discord.File(fp=io.BytesIO(raw_bytes), filename=filename)
            embed = discord.Embed(title=title, description=description, color=color)
            embed.set_image(url=f"attachment://{filename}")
            await send_func(embed=embed, file=file)

    # ------------------------------------------------------------------
    # Tweet helper (handles mentions, external Discord emojis, Unicode emojis, attachments)
    # ------------------------------------------------------------------

    async def _build_tweet_from_data(
        self,
        author: discord.User | discord.Member,
        raw_text: str,
        guild: discord.Guild | None,
        attachment_url: str | None,
        timestamp_dt: datetime.datetime,
    ) -> io.BytesIO:
        # 1. Resolve <@id>, <#id>, <@&id> mentions to human-readable text
        def replace_user_mention(match: re.Match) -> str:
            uid = int(match.group(1))
            if guild:
                m = guild.get_member(uid)
                if m:
                    return f"@{m.display_name}"
            u = self.bot.get_user(uid)
            return f"@{u.display_name}" if u else f"@user"

        def replace_channel_mention(match: re.Match) -> str:
            cid = int(match.group(1))
            if guild:
                ch = guild.get_channel(cid)
                if ch:
                    return f"#{ch.name}"
            return "#channel"

        def replace_role_mention(match: re.Match) -> str:
            rid = int(match.group(1))
            if guild:
                role = guild.get_role(rid)
                if role:
                    return f"@{role.name}"
            return "@role"

        cleaned_text = USER_MENTION_RE.sub(replace_user_mention, raw_text)
        cleaned_text = CHANNEL_MENTION_RE.sub(replace_channel_mention, cleaned_text)
        cleaned_text = ROLE_MENTION_RE.sub(replace_role_mention, cleaned_text)

        # 2. Collect all custom Discord emojis & Unicode emojis to download in parallel
        custom_ids = set(m.group(3) for m in CUSTOM_EMOJI_RE.finditer(cleaned_text))
        unicode_cps = set()
        for m in UNICODE_EMOJI_RE.finditer(cleaned_text):
            for ch in m.group(0):
                if ord(ch) not in (0xFE0F, 0x200D):
                    unicode_cps.add(_twemoji_codepoint(ch))

        emoji_map: dict[str, bytes] = {}

        async def fetch_custom(eid: str):
            data = await self._fetch_bytes(f"https://cdn.discordapp.com/emojis/{eid}.png?size=64")
            if data:
                emoji_map[f"c:{eid}"] = data

        async def fetch_twemoji(cp: str):
            data = await self._fetch_bytes(
                f"https://cdn.jsdelivr.net/gh/twitter/twemoji@14.0.2/assets/72x72/{cp}.png"
            )
            if data:
                emoji_map[f"u:{cp}"] = data

        avatar_url = author.display_avatar.with_format("png").with_size(128).url
        tasks = [
            self._fetch_bytes(avatar_url),
            self._fetch_bytes(attachment_url) if attachment_url else asyncio.sleep(0, result=None),
        ]
        for eid in custom_ids:
            tasks.append(fetch_custom(eid))
        for cp in unicode_cps:
            tasks.append(fetch_twemoji(cp))

        results = await asyncio.gather(*tasks)
        avatar_bytes = results[0] or b""
        attachment_bytes = results[1]

        return await asyncio.to_thread(
            render_tweet_card,
            avatar_bytes,
            author.display_name,
            author.name,
            cleaned_text,
            emoji_map,
            attachment_bytes,
            timestamp_dt,
        )

    async def _extract_message_tweet_inputs(self, msg: discord.Message) -> tuple[str, str | None]:
        text = msg.content or ""
        att_url = None
        if msg.attachments:
            for att in msg.attachments:
                if (att.content_type or "").startswith("image/") or att.filename.lower().endswith(
                    (".png", ".jpg", ".jpeg", ".webp", ".gif")
                ):
                    att_url = att.url
                    break
        elif msg.stickers:
            att_url = msg.stickers[0].url
        elif msg.embeds:
            for emb in msg.embeds:
                if not text and emb.description:
                    text = emb.description
                if emb.image and emb.image.url:
                    att_url = emb.image.url
                    break
                if emb.thumbnail and emb.thumbnail.url:
                    att_url = emb.thumbnail.url
                    break
        return text, att_url

    # ------------------------------------------------------------------
    # 1. !tweet / /tweet / "Make it a Tweet" Context Menu
    # ------------------------------------------------------------------

    @commands.command(name="tweet")
    async def tweet_prefix(self, ctx: commands.Context, *, text: str | None = None):
        """Reply to any message with !tweet (or pass text) to turn it into a Tweet."""
        async with ctx.typing():
            if ctx.message.reference:
                replied = await ctx.channel.fetch_message(ctx.message.reference.message_id)
                msg_text, att_url = await self._extract_message_tweet_inputs(replied)
                if text:
                    msg_text = text
                author = replied.author
                ts = replied.created_at
            else:
                if not text and not ctx.message.attachments:
                    await ctx.send("⚠️ Reply to a message with `!tweet` or type `!tweet <text>`.")
                    return
                msg_text = text or ""
                att_url = ctx.message.attachments[0].url if ctx.message.attachments else None
                author = ctx.author
                ts = ctx.message.created_at

            out = await self._build_tweet_from_data(author, msg_text, ctx.guild, att_url, ts)
            file = discord.File(fp=out, filename="tweet.png")
            await ctx.send(file=file)

    @app_commands.command(name="tweet", description="Turn text or a user's quote (including custom emojis) into a Tweet")
    @app_commands.describe(
        text="The tweet text (can include custom/external emojis and @mentions)",
        user="The user tweeting (defaults to you)",
        image="Optional image to attach inside the tweet",
    )
    async def tweet_slash(
        self,
        interaction: discord.Interaction,
        text: str,
        user: discord.User | None = None,
        image: discord.Attachment | None = None,
    ):
        await interaction.response.defer()
        target = user or interaction.user
        att_url = image.url if image else None
        out = await self._build_tweet_from_data(
            target, text, interaction.guild, att_url, discord.utils.utcnow()
        )
        file = discord.File(fp=out, filename="tweet.png")
        await interaction.followup.send(file=file)

    async def tweet_context_menu(self, interaction: discord.Interaction, message: discord.Message):
        await interaction.response.defer()
        msg_text, att_url = await self._extract_message_tweet_inputs(message)
        out = await self._build_tweet_from_data(
            message.author, msg_text, interaction.guild, att_url, message.created_at
        )
        file = discord.File(fp=out, filename="tweet.png")
        await interaction.followup.send(file=file)

    # ------------------------------------------------------------------
    # 2. /rizz & !rizz
    # ------------------------------------------------------------------

    async def _get_rizz_line(self) -> str:
        data = await self._fetch_json("https://rizzapi.vercel.app/random")
        if isinstance(data, dict) and data.get("text"):
            return data["text"]
        data2 = await self._fetch_json("https://api.popcat.xyz/pickuplines")
        if isinstance(data2, dict) and data2.get("pickupline"):
            return data2["pickupline"]
        return random.choice(FALLBACK_RIZZ)

    @app_commands.command(name="rizz", description="Send a rizz / pickup line to a user")
    @app_commands.describe(user="The user you want to rizz up")
    async def rizz_slash(self, interaction: discord.Interaction, user: discord.User):
        await interaction.response.defer()
        line = await self._get_rizz_line()
        embed = discord.Embed(
            description=f"{user.mention}, {line}",
            color=discord.Color.from_rgb(255, 105, 180),
        )
        embed.set_author(name=f"{interaction.user.display_name} → {user.display_name}", icon_url=interaction.user.display_avatar.url)
        await interaction.followup.send(content=user.mention, embed=embed)

    @commands.command(name="rizz")
    async def rizz_prefix(self, ctx: commands.Context, user: discord.User | None = None):
        target = user or ctx.author
        line = await self._get_rizz_line()
        embed = discord.Embed(
            description=f"{target.mention}, {line}",
            color=discord.Color.from_rgb(255, 105, 180),
        )
        embed.set_author(name=f"{ctx.author.display_name} → {target.display_name}", icon_url=ctx.author.display_avatar.url)
        await ctx.send(content=target.mention, embed=embed)

    # ------------------------------------------------------------------
    # 3. /roast & !roast
    # ------------------------------------------------------------------

    async def _get_roast_line(self) -> str:
        data = await self._fetch_json("https://evilinsult.com/generate_insult.php?lang=en&type=json")
        if isinstance(data, dict) and data.get("insult"):
            return html.unescape(data["insult"])
        return random.choice(FALLBACK_ROASTS)

    @app_commands.command(name="roast", description="Roast a user")
    @app_commands.describe(user="The user to roast")
    async def roast_slash(self, interaction: discord.Interaction, user: discord.User):
        await interaction.response.defer()
        roast = await self._get_roast_line()
        embed = discord.Embed(
            description=f"🔥 {user.mention}, {roast}",
            color=discord.Color.orange(),
        )
        embed.set_footer(text=f"Roasted by {interaction.user.display_name}")
        await interaction.followup.send(content=user.mention, embed=embed)

    @commands.command(name="roast")
    async def roast_prefix(self, ctx: commands.Context, user: discord.User | None = None):
        target = user or ctx.author
        roast = await self._get_roast_line()
        embed = discord.Embed(
            description=f"🔥 {target.mention}, {roast}",
            color=discord.Color.orange(),
        )
        embed.set_footer(text=f"Roasted by {ctx.author.display_name}")
        await ctx.send(content=target.mention, embed=embed)

    # ------------------------------------------------------------------
    # 4. /ship & !ship (Persisted in SQLite Database)
    # ------------------------------------------------------------------

    def _ship_commentary(self, pct: int) -> str:
        if pct == 100:
            return "💍 **Soulmates!** Literally made for each other!"
        if pct >= 85:
            return "❤️‍🔥 **True Love!** Start planning the wedding!"
        if pct >= 65:
            return "💖 **Great Match!** There's definitely chemistry here."
        if pct >= 45:
            return "💛 **Decent Chance!** Could work with some effort."
        if pct >= 20:
            return "💔 **Friendzone...** Probably better off as friends."
        return "💀 **Disaster!** Stay far away from each other."

    async def _build_ship_response(self, user1: discord.User, user2: discord.User) -> tuple[discord.Embed, discord.File]:
        pct = await db.get_or_create_ship(user1.id, user2.id)
        n1, n2 = user1.display_name, user2.display_name
        ship_name = n1[: max(1, len(n1) // 2)] + n2[len(n2) // 2 :]

        av1_url = user1.display_avatar.with_format("png").with_size(256).url
        av2_url = user2.display_avatar.with_format("png").with_size(256).url
        av1_bytes, av2_bytes = await asyncio.gather(
            self._fetch_bytes(av1_url), self._fetch_bytes(av2_url)
        )
        card_bytes = await asyncio.to_thread(
            render_ship_card, av1_bytes or b"", av2_bytes or b"", pct
        )
        file = discord.File(fp=card_bytes, filename="ship.png")

        filled = round(pct / 10)
        bar = "🟥" * filled + "⬛" * (10 - filled)
        embed = discord.Embed(
            title=f"💘 {n1} × {n2}",
            description=(
                f"**Ship Name:** `{ship_name}`\n"
                f"**Compatibility:** **{pct}%** `{bar}`\n"
                f"{self._ship_commentary(pct)}"
            ),
            color=discord.Color.from_rgb(235, 69, 158),
        )
        embed.set_image(url="attachment://ship.png")
        return embed, file

    @app_commands.command(name="ship", description="Ship two users together (permanently saved in the database!)")
    @app_commands.describe(user1="First user", user2="Second user (defaults to you)")
    async def ship_slash(
        self,
        interaction: discord.Interaction,
        user1: discord.User,
        user2: discord.User | None = None,
    ):
        await interaction.response.defer()
        u2 = user2 or interaction.user
        embed, file = await self._build_ship_response(user1, u2)
        await interaction.followup.send(embed=embed, file=file)

    @commands.command(name="ship")
    async def ship_prefix(
        self,
        ctx: commands.Context,
        user1: discord.User,
        user2: discord.User | None = None,
    ):
        async with ctx.typing():
            u2 = user2 or ctx.author
            embed, file = await self._build_ship_response(user1, u2)
            await ctx.send(embed=embed, file=file)

    # ------------------------------------------------------------------
    # 5. /wanted & !wanted
    # ------------------------------------------------------------------

    async def _build_wanted_bytes(self, target: discord.User) -> io.BytesIO:
        av_url = target.display_avatar.with_format("png").with_size(512).url
        popcat_url = f"https://api.popcat.xyz/wanted?image={urllib.parse.quote(av_url, safe='')}"
        img_bytes = await self._fetch_bytes(popcat_url)
        if img_bytes:
            return io.BytesIO(img_bytes)
        av_bytes = await self._fetch_bytes(av_url) or b""
        return await asyncio.to_thread(render_wanted_poster, av_bytes, target.display_name)

    @app_commands.command(name="wanted", description="Generate a Wanted poster for a user")
    @app_commands.describe(user="The user to put on a Wanted poster (defaults to you)")
    async def wanted_slash(self, interaction: discord.Interaction, user: discord.User | None = None):
        await interaction.response.defer()
        target = user or interaction.user
        out = await self._build_wanted_bytes(target)
        file = discord.File(fp=out, filename="wanted.png")
        embed = discord.Embed(title=f"🤠 Wanted: {target.display_name}", color=discord.Color.gold())
        embed.set_image(url="attachment://wanted.png")
        await interaction.followup.send(embed=embed, file=file)

    @commands.command(name="wanted")
    async def wanted_prefix(self, ctx: commands.Context, user: discord.User | None = None):
        async with ctx.typing():
            target = user or ctx.author
            out = await self._build_wanted_bytes(target)
            file = discord.File(fp=out, filename="wanted.png")
            embed = discord.Embed(title=f"🤠 Wanted: {target.display_name}", color=discord.Color.gold())
            embed.set_image(url="attachment://wanted.png")
            await ctx.send(embed=embed, file=file)

    # ------------------------------------------------------------------
    # 6. /corporate & !corporate (Corporate BS Generator)
    # ------------------------------------------------------------------

    async def _get_corporate_bs(self) -> str:
        data = await self._fetch_json("https://corporatebs-generator.sameerkumar.website/")
        if isinstance(data, dict) and data.get("phrase"):
            return data["phrase"]
        return random.choice(FALLBACK_CORPORATE)

    @app_commands.command(name="corporate", description="Generate a random corporate buzzword phrase")
    async def corporate_slash(self, interaction: discord.Interaction):
        await interaction.response.defer()
        phrase = await self._get_corporate_bs()
        embed = discord.Embed(
            title="💼 Corporate BS Generator",
            description=f"*\"{phrase}\"*",
            color=discord.Color.teal(),
        )
        await interaction.followup.send(embed=embed)

    @commands.command(name="corporate")
    async def corporate_prefix(self, ctx: commands.Context):
        phrase = await self._get_corporate_bs()
        embed = discord.Embed(
            title="💼 Corporate BS Generator",
            description=f"*\"{phrase}\"*",
            color=discord.Color.teal(),
        )
        await ctx.send(embed=embed)

    # ------------------------------------------------------------------
    # 7. /cat, /duck, /capybara (& prefix versions)
    # ------------------------------------------------------------------

    async def _get_cat_url(self) -> str:
        data = await self._fetch_json("https://api.thecatapi.com/v1/images/search")
        if isinstance(data, list) and data and isinstance(data[0], dict) and data[0].get("url"):
            return data[0]["url"]
        return "https://cataas.com/cat"

    async def _get_duck_url(self) -> str:
        data = await self._fetch_json("https://random-d.uk/api/v2/random")
        if isinstance(data, dict) and data.get("url"):
            return data["url"]
        return "https://random-d.uk/api/v2/randomimg"

    async def _get_capybara_url(self) -> str:
        data = await self._fetch_json("https://api.capy.lol/v1/capybara?json=true")
        if isinstance(data, dict) and isinstance(data.get("data"), dict) and data["data"].get("url"):
            return data["data"]["url"]
        return "https://api.capy.lol/v1/capybara"

    @app_commands.command(name="cat", description="Get a random cat picture")
    async def cat_slash(self, interaction: discord.Interaction):
        await interaction.response.defer()
        url = await self._get_cat_url()
        embed = discord.Embed(title="🐱 Meow!", color=discord.Color.blurple())
        embed.set_image(url=url)
        await interaction.followup.send(embed=embed)

    @commands.command(name="cat")
    async def cat_prefix(self, ctx: commands.Context):
        url = await self._get_cat_url()
        embed = discord.Embed(title="🐱 Meow!", color=discord.Color.blurple())
        embed.set_image(url=url)
        await ctx.send(embed=embed)

    @app_commands.command(name="duck", description="Get a random duck picture")
    async def duck_slash(self, interaction: discord.Interaction):
        await interaction.response.defer()
        url = await self._get_duck_url()
        embed = discord.Embed(title="🦆 Quack!", color=discord.Color.gold())
        embed.set_image(url=url)
        await interaction.followup.send(embed=embed)

    @commands.command(name="duck")
    async def duck_prefix(self, ctx: commands.Context):
        url = await self._get_duck_url()
        embed = discord.Embed(title="🦆 Quack!", color=discord.Color.gold())
        embed.set_image(url=url)
        await ctx.send(embed=embed)

    @app_commands.command(name="capybara", description="Get a random capybara picture")
    async def capybara_slash(self, interaction: discord.Interaction):
        await interaction.response.defer()
        url = await self._get_capybara_url()
        embed = discord.Embed(title="🦦 Ok I Pull Up", color=discord.Color.green())
        embed.set_image(url=url)
        await interaction.followup.send(embed=embed)

    @commands.command(name="capybara")
    async def capybara_prefix(self, ctx: commands.Context):
        url = await self._get_capybara_url()
        embed = discord.Embed(title="🦦 Ok I Pull Up", color=discord.Color.green())
        embed.set_image(url=url)
        await ctx.send(embed=embed)

    # ------------------------------------------------------------------
    # 8. /steal & !steal (Steal custom emojis to the server)
    # ------------------------------------------------------------------

    async def _steal_emoji_to_guild(
        self, guild: discord.Guild, emoji_str: str, custom_name: str | None = None
    ) -> discord.Emoji:
        match = CUSTOM_EMOJI_RE.search(emoji_str)
        if not match:
            raise ValueError("Please provide a valid custom Discord emoji (e.g. `<:name:123456>`).")

        is_anim = bool(match.group(1))
        name = custom_name or match.group(2)
        name = re.sub(r"[^a-zA-Z0-9_]", "_", name)[:32]
        if len(name) < 2:
            name = f"{name}_emoji"
        emoji_id = match.group(3)
        ext = "gif" if is_anim else "png"
        url = f"https://cdn.discordapp.com/emojis/{emoji_id}.{ext}"

        img_bytes = await self._fetch_bytes(url)
        if not img_bytes:
            raise ValueError("Failed to download that emoji from Discord CDN.")

        return await guild.create_custom_emoji(name=name, image=img_bytes, reason="Stolen via /steal")

    @app_commands.command(name="steal", description="Steal a custom emoji from another server and add it to this server")
    @app_commands.describe(emoji="The custom emoji to steal", name="Optional custom name for the emoji")
    @app_commands.default_permissions(manage_emojis_and_stickers=True)
    @app_commands.guild_only()
    async def steal_slash(
        self,
        interaction: discord.Interaction,
        emoji: str,
        name: str | None = None,
    ):
        await interaction.response.defer()
        try:
            created = await self._steal_emoji_to_guild(interaction.guild, emoji, name)
            await interaction.followup.send(f"✅ Added {created} (`:{created.name}:`) to the server!")
        except discord.Forbidden:
            await interaction.followup.send("⚠️ I don't have the **Manage Expressions** permission in this server.")
        except Exception as e:
            await interaction.followup.send(f"⚠️ `{e}`")

    @commands.command(name="steal")
    @commands.guild_only()
    @commands.has_permissions(manage_emojis_and_stickers=True)
    async def steal_prefix(self, ctx: commands.Context, emoji: str | None = None, name: str | None = None):
        target_str = emoji
        if not target_str and ctx.message.reference:
            replied = await ctx.channel.fetch_message(ctx.message.reference.message_id)
            target_str = replied.content
        if not target_str:
            await ctx.send("⚠️ Provide a custom emoji (`!steal <:name:id> [new_name]`) or reply to a message with one.")
            return
        try:
            created = await self._steal_emoji_to_guild(ctx.guild, target_str, name)
            await ctx.send(f"✅ Added {created} (`:{created.name}:`) to the server!")
        except discord.Forbidden:
            await ctx.send("⚠️ I don't have the **Manage Expressions** permission in this server.")
        except Exception as e:
            await ctx.send(f"⚠️ `{e}`")

    # ------------------------------------------------------------------
    # 9. /petpet & !petpet (Animated PetPet GIF)
    # ------------------------------------------------------------------

    async def _build_petpet_gif(self, target: discord.User) -> io.BytesIO:
        av_url = target.display_avatar.with_format("png").with_size(256).url
        # Try Some-Random-Api first, fall back to built-in Pillow PetPet generator
        api_url = f"https://some-random-api.com/premium/petpet?avatar={urllib.parse.quote(av_url, safe='')}"
        api_bytes = await self._fetch_bytes(api_url)
        if api_bytes and api_bytes[:3] == b"GIF":
            return io.BytesIO(api_bytes)

        av_bytes = await self._fetch_bytes(av_url)
        if not av_bytes:
            raise ValueError("Could not download the user's avatar.")
        return await asyncio.to_thread(render_petpet_gif, av_bytes)

    @app_commands.command(name="petpet", description="Generate an animated PetPet GIF of a user")
    @app_commands.describe(user="The user to pet (defaults to you)")
    async def petpet_slash(self, interaction: discord.Interaction, user: discord.User | None = None):
        await interaction.response.defer()
        target = user or interaction.user
        try:
            gif_io = await self._build_petpet_gif(target)

            async def followup_send(**kwargs):
                return await interaction.followup.send(**kwargs, wait=True)

            await self._send_image_output(
                gif_io, "petpet.gif", f"👋 Petting {target.display_name}", followup_send
            )
        except Exception as e:
            await interaction.followup.send(f"⚠️ `{e}`")

    @commands.command(name="petpet")
    async def petpet_prefix(self, ctx: commands.Context, user: discord.User | None = None):
        async with ctx.typing():
            target = user or ctx.author
            try:
                gif_io = await self._build_petpet_gif(target)
                await self._send_image_output(
                    gif_io, "petpet.gif", f"👋 Petting {target.display_name}", ctx.send
                )
            except Exception as e:
                await ctx.send(f"⚠️ `{e}`")


async def setup(bot: commands.Bot):
    await bot.add_cog(FunCog(bot))

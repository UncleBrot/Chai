import discord
from discord.ext import commands
from discord import app_commands
import io
import aiohttp
import textwrap
import os
import asyncio
from PIL import Image, ImageDraw, ImageFont

from utils.r2 import upload_image_to_r2


# ---------------------------------------------------------------------------
# Image processing (runs in a thread pool via asyncio.to_thread)
# ---------------------------------------------------------------------------

def compress_image_bytes(out_bytes: io.BytesIO, filename: str, is_animated: bool, max_size: int = 500 * 1024) -> io.BytesIO:
    while len(out_bytes.getvalue()) > max_size:
        base_img = Image.open(out_bytes)
        if base_img.width < 100 or base_img.height < 100:
            break

        compressed_bytes = io.BytesIO()
        if is_animated:
            frames, durations = [], []
            for i in range(base_img.n_frames):
                base_img.seek(i)
                durations.append(base_img.info.get('duration', 100))
                f = base_img.copy().convert("RGB")
                f = f.resize((int(f.width * 0.8), int(f.height * 0.8)), Image.LANCZOS)
                frames.append(f)
            frames[0].save(compressed_bytes, format="GIF", save_all=True,
                           append_images=frames[1:], duration=durations, loop=0)
        else:
            base_img = base_img.convert("RGBA")
            base_img = base_img.resize(
                (int(base_img.width * 0.8), int(base_img.height * 0.8)), Image.LANCZOS
            )
            base_img.save(compressed_bytes, format="PNG", optimize=True)

        compressed_bytes.seek(0)
        out_bytes = compressed_bytes

    return out_bytes


def process_caption_image(img_bytes: bytes, text: str) -> tuple[io.BytesIO, str, bool]:
    base_img = Image.open(io.BytesIO(img_bytes))
    is_animated = getattr(base_img, "is_animated", False)
    width, height = base_img.size

    font_size = max(15, min(100, int(width * 0.08)))
    try:
        font = ImageFont.truetype(
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf", font_size
        )
    except IOError:
        font = ImageFont.load_default()

    avg_char_width = font_size * 0.6
    max_chars_per_line = max(1, int((width * 0.9) / avg_char_width))
    wrapped_text = textwrap.fill(text, width=max_chars_per_line)

    temp_draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    bbox = temp_draw.multiline_textbbox((0, 0), wrapped_text, font=font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    padding = int(font_size * 0.5)
    bar_height = text_height + padding * 2

    out_bytes = io.BytesIO()

    if is_animated:
        frames, durations = [], []
        for i in range(base_img.n_frames):
            base_img.seek(i)
            durations.append(base_img.info.get('duration', 100))
            frame_rgba = base_img.convert("RGBA")
            final = Image.new("RGBA", (width, height + bar_height), "white")
            final.paste(frame_rgba, (0, bar_height))
            draw = ImageDraw.Draw(final)
            draw.multiline_text(
                ((width - text_width) / 2, padding), wrapped_text,
                fill="black", font=font, align="center"
            )
            frames.append(final.convert("RGB"))
        frames[0].save(out_bytes, format="GIF", save_all=True,
                       append_images=frames[1:], duration=durations, loop=0)
        filename = "caption.gif"
    else:
        base_img = base_img.convert("RGBA")
        final = Image.new("RGBA", (width, height + bar_height), "white")
        final.paste(base_img, (0, bar_height))
        draw = ImageDraw.Draw(final)
        draw.multiline_text(
            ((width - text_width) / 2, padding), wrapped_text,
            fill="black", font=font, align="center"
        )
        final.convert("RGB").save(out_bytes, format="PNG")
        filename = "caption.png"

    out_bytes.seek(0)
    return compress_image_bytes(out_bytes, filename, is_animated), filename, is_animated


def process_gif_image(img_bytes: bytes) -> tuple[io.BytesIO, str, bool]:
    base_img = Image.open(io.BytesIO(img_bytes))
    is_animated = getattr(base_img, "is_animated", False)
    out_bytes = io.BytesIO()

    if is_animated:
        frames, durations = [], []
        for i in range(base_img.n_frames):
            base_img.seek(i)
            durations.append(base_img.info.get('duration', 100))
            frames.append(base_img.copy().convert("RGB"))
        frames[0].save(out_bytes, format="GIF", save_all=True,
                       append_images=frames[1:], duration=durations, loop=0)
    else:
        base_img.convert("RGB").save(out_bytes, format="GIF")

    out_bytes.seek(0)
    return compress_image_bytes(out_bytes, "image.gif", True), "image.gif", True


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class ImagesCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._session: aiohttp.ClientSession | None = None

        self._img_log_channel_id: int | None = None
        raw = os.getenv("img_log_id")
        if raw and raw.isdigit():
            self._img_log_channel_id = int(raw)

    # One shared session for all downloads — avoids TCP handshake overhead on every command
    async def cog_load(self):
        self._session = aiohttp.ClientSession()

    async def cog_unload(self):
        if self._session:
            await self._session.close()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _download(self, url: str) -> bytes:
        async with self._session.get(url) as resp:
            if resp.status != 200:
                raise Exception(f"Failed to download the image (HTTP {resp.status}).")
            return await resp.read()

    async def _get_replied_image_url(self, ctx: commands.Context) -> str:
        if not ctx.message.reference:
            raise ValueError("Reply to a message that contains an image.")
        replied_msg = await ctx.channel.fetch_message(ctx.message.reference.message_id)
        if replied_msg.attachments:
            return replied_msg.attachments[0].url
        for embed in replied_msg.embeds:
            if embed.image and embed.image.url:
                return embed.image.url
            if embed.thumbnail and embed.thumbnail.url:
                return embed.thumbnail.url
        raise ValueError("Could not find an image in the replied message.")

    async def _deliver(
        self,
        out_bytes: io.BytesIO,
        filename: str,
        is_animated: bool,
        title: str,
        author_name: str,
        send_func,
    ) -> str | None:
        """Upload to R2, build an embed, and send it.
        Falls back to a Discord attachment if R2 is unavailable.
        Returns the public R2 URL on success, None on fallback."""
        content_type = "image/gif" if (is_animated or filename.endswith(".gif")) else "image/png"
        size_kb = len(out_bytes.getvalue()) / 1024

        # --- Try R2 first ---
        try:
            r2_url = await upload_image_to_r2(out_bytes.getvalue(), filename, content_type)
            embed = discord.Embed(title=title, color=discord.Color.blurple())
            embed.set_image(url=r2_url)
            embed.set_footer(text=f"Requested by {author_name} | {size_kb:.1f} KB")
            await send_func(embed=embed)
            return r2_url
        except Exception as r2_err:
            print(f"[images] R2 upload failed ({r2_err}), falling back to Discord attachment.")

        # --- Fallback: send as Discord file attachment ---
        out_bytes.seek(0)
        await send_func(
            content=f"_{title}_ — {size_kb:.1f} KB",
            file=discord.File(fp=out_bytes, filename=filename),
        )
        return None

    async def _log_gif(
        self,
        user: discord.User | discord.Member,
        image_url: str | None,
        source: str,
    ):
        if not self._img_log_channel_id:
            return
        channel = self.bot.get_channel(self._img_log_channel_id)
        if channel is None:
            return
        embed = discord.Embed(
            title="GIF Command Used",
            color=discord.Color.green(),
            timestamp=discord.utils.utcnow(),
        )
        embed.set_author(name=f"{user} ({user.id})", icon_url=user.display_avatar.url)
        embed.add_field(name="User ID", value=str(user.id))
        embed.add_field(name="Source", value=source)
        if image_url:
            embed.set_image(url=image_url)
        try:
            await channel.send(embed=embed)
        except discord.Forbidden:
            pass

    # ------------------------------------------------------------------
    # !caption  (reply to a message)
    # ------------------------------------------------------------------

    @commands.command(name="caption")
    async def caption_prefix(self, ctx: commands.Context, *, text: str):
        """Caption a replied image with a white bar and bold text at the top."""
        async with ctx.typing():
            try:
                url = await self._get_replied_image_url(ctx)
                img_bytes = await self._download(url)
                out_bytes, filename, is_animated = await asyncio.to_thread(
                    process_caption_image, img_bytes, text
                )
                await self._deliver(out_bytes, filename, is_animated,
                                    "Captioned Image", ctx.author.display_name, ctx.send)
            except Exception as e:
                await ctx.send(f"⚠️ `{e}`")

    # ------------------------------------------------------------------
    # /caption image: text:
    # ------------------------------------------------------------------

    @app_commands.command(name="caption", description="Caption an uploaded image")
    @app_commands.describe(image="The image to caption", text="The caption text")
    async def caption_slash(
        self,
        interaction: discord.Interaction,
        image: discord.Attachment,
        text: str,
    ):
        await interaction.response.defer()
        try:
            img_bytes = await self._download(image.url)
            out_bytes, filename, is_animated = await asyncio.to_thread(
                process_caption_image, img_bytes, text
            )

            async def followup_send(**kwargs):
                await interaction.followup.send(**kwargs, wait=True)

            await self._deliver(out_bytes, filename, is_animated,
                                "Captioned Image", interaction.user.display_name, followup_send)
        except Exception as e:
            await interaction.followup.send(f"⚠️ `{e}`", wait=True)

    # ------------------------------------------------------------------
    # !gif  (reply to a message)
    # ------------------------------------------------------------------

    @commands.command(name="gif")
    async def gif_prefix(self, ctx: commands.Context):
        """Convert a replied image to a GIF."""
        async with ctx.typing():
            try:
                url = await self._get_replied_image_url(ctx)
                img_bytes = await self._download(url)
                out_bytes, filename, is_animated = await asyncio.to_thread(
                    process_gif_image, img_bytes
                )
                r2_url = await self._deliver(out_bytes, filename, is_animated,
                                             "GIF Conversion", ctx.author.display_name, ctx.send)
                await self._log_gif(ctx.author, r2_url, "!gif (prefix)")
            except Exception as e:
                await ctx.send(f"⚠️ `{e}`")

    # ------------------------------------------------------------------
    # /gif image:
    # ------------------------------------------------------------------

    @app_commands.command(name="gif", description="Convert an uploaded image to a GIF")
    @app_commands.describe(image="The image to convert to a GIF")
    async def gif_slash(self, interaction: discord.Interaction, image: discord.Attachment):
        await interaction.response.defer()
        try:
            img_bytes = await self._download(image.url)
            out_bytes, filename, is_animated = await asyncio.to_thread(
                process_gif_image, img_bytes
            )

            async def followup_send(**kwargs):
                await interaction.followup.send(**kwargs, wait=True)

            r2_url = await self._deliver(out_bytes, filename, is_animated,
                                         "GIF Conversion", interaction.user.display_name, followup_send)
            await self._log_gif(interaction.user, r2_url, "/gif (slash)")
        except Exception as e:
            await interaction.followup.send(f"⚠️ `{e}`", wait=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(ImagesCog(bot))

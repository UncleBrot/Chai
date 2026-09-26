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
# Helpers
# ---------------------------------------------------------------------------

async def download_image(url: str) -> bytes:
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status != 200:
                raise Exception("Failed to download the image.")
            return await resp.read()


def compress_image_bytes(out_bytes: io.BytesIO, filename: str, is_animated: bool, max_size: int = 500 * 1024) -> io.BytesIO:
    while len(out_bytes.getvalue()) > max_size:
        base_img = Image.open(out_bytes)
        if base_img.width < 100 or base_img.height < 100:
            break

        compressed_bytes = io.BytesIO()
        if is_animated:
            frames = []
            durations = []
            for frame_idx in range(base_img.n_frames):
                base_img.seek(frame_idx)
                durations.append(base_img.info.get('duration', 100))
                frame_copy = base_img.copy().convert("RGB")
                frame_copy = frame_copy.resize(
                    (int(frame_copy.width * 0.8), int(frame_copy.height * 0.8)), Image.LANCZOS
                )
                frames.append(frame_copy)
            frames[0].save(
                compressed_bytes, format="GIF", save_all=True,
                append_images=frames[1:], duration=durations, loop=0
            )
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
    bar_height = text_height + (padding * 2)

    out_bytes = io.BytesIO()

    if is_animated:
        frames = []
        durations = []
        for frame_idx in range(base_img.n_frames):
            base_img.seek(frame_idx)
            durations.append(base_img.info.get('duration', 100))

            frame_rgba = base_img.convert("RGBA")
            final_img = Image.new("RGBA", (width, height + bar_height), "white")
            final_img.paste(frame_rgba, (0, bar_height))

            draw = ImageDraw.Draw(final_img)
            x_pos = (width - text_width) / 2
            y_pos = padding
            draw.multiline_text((x_pos, y_pos), wrapped_text, fill="black", font=font, align="center")
            frames.append(final_img.convert("RGB"))

        frames[0].save(
            out_bytes, format="GIF", save_all=True,
            append_images=frames[1:], duration=durations, loop=0
        )
        filename = "caption.gif"
    else:
        base_img = base_img.convert("RGBA")
        final_img = Image.new("RGBA", (width, height + bar_height), "white")
        final_img.paste(base_img, (0, bar_height))

        draw = ImageDraw.Draw(final_img)
        x_pos = (width - text_width) / 2
        y_pos = padding
        draw.multiline_text((x_pos, y_pos), wrapped_text, fill="black", font=font, align="center")

        final_img = final_img.convert("RGB")
        final_img.save(out_bytes, format="PNG")
        filename = "caption.png"

    out_bytes.seek(0)
    out_bytes = compress_image_bytes(out_bytes, filename, is_animated)
    return out_bytes, filename, is_animated


def process_gif_image(img_bytes: bytes) -> tuple[io.BytesIO, str, bool]:
    base_img = Image.open(io.BytesIO(img_bytes))
    is_animated = getattr(base_img, "is_animated", False)
    out_bytes = io.BytesIO()

    if is_animated:
        frames = []
        durations = []
        for frame_idx in range(base_img.n_frames):
            base_img.seek(frame_idx)
            durations.append(base_img.info.get('duration', 100))
            frames.append(base_img.copy().convert("RGB"))
        frames[0].save(
            out_bytes, format="GIF", save_all=True,
            append_images=frames[1:], duration=durations, loop=0
        )
    else:
        base_img = base_img.convert("RGB")
        base_img.save(out_bytes, format="GIF")

    out_bytes.seek(0)
    out_bytes = compress_image_bytes(out_bytes, "image.gif", True)
    return out_bytes, "image.gif", True


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class ImagesCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._img_log_channel_id: int | None = None
        raw = os.getenv("img_log_id")
        if raw and raw.isdigit():
            self._img_log_channel_id = int(raw)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

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

    async def _upload_and_send(
        self,
        out_bytes: io.BytesIO,
        filename: str,
        is_animated: bool,
        title: str,
        author_name: str,
        send_func,
    ) -> str | None:
        """Upload to R2 (falling back to Discord attachment) and send the result.
        Returns the public URL if R2 succeeded, else None."""
        content_type = "image/gif" if (is_animated or filename.endswith(".gif")) else "image/png"
        size_kb = len(out_bytes.getvalue()) / 1024
        try:
            r2_url = await upload_image_to_r2(out_bytes.getvalue(), filename, content_type)
            embed = discord.Embed(title=title)
            embed.set_image(url=r2_url)
            embed.set_footer(text=f"Requested by {author_name} | Size: {size_kb:.1f} KB")
            await send_func(embed=embed)
            return r2_url
        except Exception as e:
            print(f"R2 Upload Failed: {e}. Falling back to Discord attachment.")
            out_bytes.seek(0)
            discord_file = discord.File(fp=out_bytes, filename=filename)
            await send_func(content=f"Size: {size_kb:.1f} KB", file=discord_file)
            return None

    async def _log_gif(self, user: discord.User | discord.Member, image_url: str | None, source: str):
        """Send a log embed to the img_log channel for every gif command use."""
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
    # !caption — reply to a message with an image
    # ------------------------------------------------------------------

    @commands.command(name="caption")
    async def caption_prefix(self, ctx: commands.Context, *, text: str):
        """Captions a replied image with a white bar and text at the top."""
        try:
            async with ctx.typing():
                image_url = await self._get_replied_image_url(ctx)
                img_bytes = await download_image(image_url)
                out_bytes, filename, is_animated = await asyncio.to_thread(
                    process_caption_image, img_bytes, text
                )
                await self._upload_and_send(
                    out_bytes, filename, is_animated,
                    "Captioned Image", ctx.author.display_name, ctx.send
                )
        except Exception as e:
            await ctx.send(f"⚠️ Error: `{e}`")

    # ------------------------------------------------------------------
    # /caption image: text:
    # ------------------------------------------------------------------

    @app_commands.command(name="caption", description="Caption an uploaded image")
    @app_commands.describe(image="The image to caption", text="The caption text")
    async def caption_slash(self, interaction: discord.Interaction, image: discord.Attachment, text: str):
        try:
            await interaction.response.defer()
            img_bytes = await download_image(image.url)
            out_bytes, filename, is_animated = await asyncio.to_thread(
                process_caption_image, img_bytes, text
            )
            await self._upload_and_send(
                out_bytes, filename, is_animated,
                "Captioned Image", interaction.user.display_name, interaction.followup.send
            )
        except Exception as e:
            await interaction.followup.send(f"⚠️ Error: `{e}`")

    # ------------------------------------------------------------------
    # !gif — reply to a message with an image
    # ------------------------------------------------------------------

    @commands.command(name="gif")
    async def gif_prefix(self, ctx: commands.Context):
        """Converts a replied image to a GIF."""
        try:
            async with ctx.typing():
                image_url = await self._get_replied_image_url(ctx)
                img_bytes = await download_image(image_url)
                out_bytes, filename, is_animated = await asyncio.to_thread(
                    process_gif_image, img_bytes
                )
                r2_url = await self._upload_and_send(
                    out_bytes, filename, is_animated,
                    "GIF Conversion", ctx.author.display_name, ctx.send
                )
                await self._log_gif(ctx.author, r2_url, "!gif (prefix)")
        except Exception as e:
            await ctx.send(f"⚠️ Error: `{e}`")

    # ------------------------------------------------------------------
    # /gif image:
    # ------------------------------------------------------------------

    @app_commands.command(name="gif", description="Convert an uploaded image to a GIF")
    @app_commands.describe(image="The image to convert to a GIF")
    async def gif_slash(self, interaction: discord.Interaction, image: discord.Attachment):
        try:
            await interaction.response.defer()
            img_bytes = await download_image(image.url)
            out_bytes, filename, is_animated = await asyncio.to_thread(
                process_gif_image, img_bytes
            )
            r2_url = await self._upload_and_send(
                out_bytes, filename, is_animated,
                "GIF Conversion", interaction.user.display_name, interaction.followup.send
            )
            await self._log_gif(interaction.user, r2_url, "/gif (slash)")
        except Exception as e:
            await interaction.followup.send(f"⚠️ Error: `{e}`")


async def setup(bot: commands.Bot):
    await bot.add_cog(ImagesCog(bot))

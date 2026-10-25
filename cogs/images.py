import discord
from discord.ext import commands
from discord import app_commands
import io
import aiohttp
import textwrap
import os
import sys
import asyncio
from PIL import Image, ImageDraw, ImageFont

# Add root to sys path to allow importing utils
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.r2 import upload_image_to_r2

async def download_image(url: str) -> bytes:
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status != 200:
                raise Exception("Failed to download the image.")
            return await resp.read()

def compress_image_bytes(out_bytes: io.BytesIO, filename: str, is_animated: bool, max_size: int = 500 * 1024) -> io.BytesIO:
    while len(out_bytes.getvalue()) > max_size:
        out_bytes.seek(0)
        base_img = Image.open(out_bytes)
        if base_img.width < 100 or base_img.height < 100:
            break
        
        compressed_bytes = io.BytesIO()
        if is_animated or filename.endswith(".gif"):
            n_frames = getattr(base_img, "n_frames", 1)
            if n_frames > 1:
                frames = []
                durations = []
                for frame_idx in range(n_frames):
                    base_img.seek(frame_idx)
                    durations.append(base_img.info.get('duration', 100))
                    frame_copy = base_img.copy().convert("RGB")
                    frame_copy = frame_copy.resize((int(frame_copy.width * 0.8), int(frame_copy.height * 0.8)), Image.LANCZOS)
                    frames.append(frame_copy)
                frames[0].save(compressed_bytes, format="GIF", save_all=True, append_images=frames[1:], duration=durations, loop=0)
            else:
                frame_copy = base_img.convert("RGB")
                frame_copy = frame_copy.resize((int(frame_copy.width * 0.8), int(frame_copy.height * 0.8)), Image.LANCZOS)
                frame_copy.save(compressed_bytes, format="GIF")
        else:
            base_img = base_img.convert("RGBA")
            base_img = base_img.resize((int(base_img.width * 0.8), int(base_img.height * 0.8)), Image.LANCZOS)
            base_img.save(compressed_bytes, format="PNG", optimize=True)
            
        compressed_bytes.seek(0)
        out_bytes = compressed_bytes
        
    out_bytes.seek(0)
    return out_bytes

def process_caption_image(img_bytes: bytes, text: str) -> tuple[io.BytesIO, str, bool]:
    base_img = Image.open(io.BytesIO(img_bytes))
    is_animated = getattr(base_img, "is_animated", False)
    width, height = base_img.size
    
    font_size = max(15, min(100, int(width * 0.08)))
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf", font_size)
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
            frame_duration = base_img.info.get('duration', 100)
            durations.append(frame_duration)
            
            frame_rgba = base_img.convert("RGBA")
            final_img = Image.new("RGBA", (width, height + bar_height), "white")
            final_img.paste(frame_rgba, (0, bar_height))
            
            draw = ImageDraw.Draw(final_img)
            x_pos = (width - text_width) / 2
            y_pos = padding
            draw.multiline_text((x_pos, y_pos), wrapped_text, fill="black", font=font, align="center")
            
            frames.append(final_img.convert("RGB"))
        
        frames[0].save(out_bytes, format="GIF", save_all=True, append_images=frames[1:], duration=durations, loop=0)
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
        frames[0].save(out_bytes, format="GIF", save_all=True, append_images=frames[1:], duration=durations, loop=0)
    else:
        base_img = base_img.convert("RGB")
        base_img.save(out_bytes, format="GIF")
        
    out_bytes.seek(0)
    out_bytes = compress_image_bytes(out_bytes, "image.gif", is_animated)
    return out_bytes, "image.gif", True

class ImagesCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._img_log_channel_id: int | None = None
        raw = os.getenv("img_log_id")
        if raw and raw.isdigit():
            self._img_log_channel_id = int(raw)

        self.gif_ctx_menu = app_commands.ContextMenu(
            name="Convert to GIF",
            callback=self.gif_context_menu,
            allowed_installs=app_commands.AppInstallationType(guild=True, user=True),
            allowed_contexts=app_commands.AppCommandContext(
                guild=True, dm_channel=True, private_channel=True
            ),
        )
        self.bot.tree.add_command(self.gif_ctx_menu)

    async def cog_unload(self):
        self.bot.tree.remove_command(self.gif_ctx_menu.name, type=self.gif_ctx_menu.type)

    def _extract_image_from_message(self, msg: discord.Message) -> str:
        if msg.attachments:
            return msg.attachments[0].url
        for embed in msg.embeds:
            if embed.image and embed.image.url:
                return embed.image.url
            if embed.thumbnail and embed.thumbnail.url:
                return embed.thumbnail.url
        raise ValueError("Could not find an image in that message.")

    async def get_replied_image_url(self, ctx: commands.Context) -> str:
        if not ctx.message.reference:
            raise ValueError("You must reply to a message containing an image.")
        replied_msg = await ctx.channel.fetch_message(ctx.message.reference.message_id)
        return self._extract_image_from_message(replied_msg)

    async def handle_image_upload(self, out_bytes: io.BytesIO, filename: str, is_animated: bool, title: str, author_name: str, send_func) -> str | None:
        try:
            content_type = "image/gif" if is_animated or filename.endswith('.gif') else "image/png"
            raw_bytes = out_bytes.getvalue()
            size_kb = len(raw_bytes) / 1024
            r2_url = await upload_image_to_r2(raw_bytes, filename, content_type)

            # Warm Cloudflare R2 edge cache so Discord's media proxy gets an immediate cache hit
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(r2_url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                        await resp.read()
            except Exception:
                pass

            embed = discord.Embed(title=title)
            embed.set_image(url=r2_url)
            embed.set_footer(text=f"Requested by {author_name} | Size: {size_kb:.1f} KB")
            msg = await send_func(embed=embed)

            # Deferred slash command responses (PATCH @original) can fail to unfurl a brand-new external URL
            # on the first try; re-editing the message triggers Discord to display the proxied image.
            if isinstance(msg, discord.Message) and msg.embeds:
                img = msg.embeds[0].image
                if not img or not img.proxy_url or not img.width:
                    await asyncio.sleep(1.0)
                    try:
                        await msg.edit(embed=embed)
                    except Exception:
                        pass

            return r2_url
        except Exception as e:
            size_kb = len(out_bytes.getvalue()) / 1024
            print(f"R2 Upload Failed: {e}. Falling back to Discord attachment.")
            out_bytes.seek(0)
            discord_file = discord.File(fp=out_bytes, filename=filename)
            await send_func(content=f"Size: {size_kb:.1f} KB", file=discord_file)
            return None

    async def _log_gif(
        self,
        user: discord.User | discord.Member,
        image_url: str | None,
        source: str,
        guild: discord.Guild | None = None,
        guild_id: int | None = None,
    ):
        if not self._img_log_channel_id:
            return
        channel = self.bot.get_channel(self._img_log_channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(self._img_log_channel_id)
            except Exception:
                return

        resolved_guild = guild or (self.bot.get_guild(guild_id) if guild_id else None)
        if resolved_guild:
            server_name = resolved_guild.name
            server_id = str(resolved_guild.id)
        elif guild_id:
            server_name = "External Server (User App)"
            server_id = str(guild_id)
        else:
            server_name = "Direct Messages"
            server_id = "DM"

        embed = discord.Embed(
            title="🖼️ Image Command Used",
            color=discord.Color.green(),
            timestamp=discord.utils.utcnow(),
        )
        embed.set_author(name=f"{user} ({user.id})", icon_url=user.display_avatar.url)
        embed.add_field(name="User", value=f"{user} ({user.mention})", inline=True)
        embed.add_field(name="User ID", value=f"`{user.id}`", inline=True)
        embed.add_field(name="Source", value=source, inline=True)
        embed.add_field(name="Server Name", value=server_name, inline=True)
        embed.add_field(name="Server ID", value=f"`{server_id}`", inline=True)
        if image_url:
            embed.set_image(url=image_url)
        try:
            await channel.send(embed=embed)
        except discord.Forbidden:
            pass

    @commands.command(name="caption")
    async def caption_prefix(self, ctx: commands.Context, *, text: str):
        """Captions a replied image with a white bar and text at the top."""
        try:
            async with ctx.typing():
                image_url = await self.get_replied_image_url(ctx)
                img_bytes = await download_image(image_url)
                
                # Offload heavy image processing to a thread
                out_bytes, filename, is_animated = await asyncio.to_thread(process_caption_image, img_bytes, text)
                
                r2_url = await self.handle_image_upload(out_bytes, filename, is_animated, "Captioned Image", ctx.author.display_name, ctx.send)
                await self._log_gif(ctx.author, r2_url, "!caption (prefix)", guild=ctx.guild, guild_id=ctx.guild.id if ctx.guild else None)
        except Exception as e:
            await ctx.send(f"⚠️ Error: `{e}`")

    @app_commands.command(name="caption", description="Caption an uploaded image")
    @app_commands.describe(image="The image to caption", text="The text for the caption")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def caption_slash(self, interaction: discord.Interaction, image: discord.Attachment, text: str):
        try:
            await interaction.response.defer()
            img_bytes = await download_image(image.url)
            out_bytes, filename, is_animated = await asyncio.to_thread(process_caption_image, img_bytes, text)

            async def followup_send(**kwargs):
                return await interaction.followup.send(**kwargs, wait=True)

            r2_url = await self.handle_image_upload(out_bytes, filename, is_animated, "Captioned Image", interaction.user.display_name, followup_send)
            await self._log_gif(interaction.user, r2_url, "/caption (slash)", guild=interaction.guild, guild_id=interaction.guild_id)
        except Exception as e:
            await interaction.followup.send(f"⚠️ Error: `{e}`")

    @commands.command(name="gif")
    async def gif_prefix(self, ctx: commands.Context):
        """Converts a replied image to a GIF."""
        try:
            async with ctx.typing():
                image_url = await self.get_replied_image_url(ctx)
                img_bytes = await download_image(image_url)
                
                out_bytes, filename, is_animated = await asyncio.to_thread(process_gif_image, img_bytes)
                
                r2_url = await self.handle_image_upload(out_bytes, filename, is_animated, "GIF Conversion", ctx.author.display_name, ctx.send)
                await self._log_gif(ctx.author, r2_url, "!gif (prefix)", guild=ctx.guild, guild_id=ctx.guild.id if ctx.guild else None)
        except Exception as e:
            await ctx.send(f"⚠️ Error: `{e}`")

    @app_commands.command(name="gif", description="Convert an uploaded image to a GIF")
    @app_commands.describe(image="The image to convert")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def gif_slash(self, interaction: discord.Interaction, image: discord.Attachment):
        try:
            await interaction.response.defer()
            img_bytes = await download_image(image.url)
            out_bytes, filename, is_animated = await asyncio.to_thread(process_gif_image, img_bytes)

            async def followup_send(**kwargs):
                return await interaction.followup.send(**kwargs, wait=True)

            r2_url = await self.handle_image_upload(out_bytes, filename, is_animated, "GIF Conversion", interaction.user.display_name, followup_send)
            await self._log_gif(interaction.user, r2_url, "/gif (slash)", guild=interaction.guild, guild_id=interaction.guild_id)
        except Exception as e:
            await interaction.followup.send(f"⚠️ Error: `{e}`")

    async def gif_context_menu(self, interaction: discord.Interaction, message: discord.Message):
        try:
            await interaction.response.defer()
            image_url = self._extract_image_from_message(message)
            img_bytes = await download_image(image_url)
            out_bytes, filename, is_animated = await asyncio.to_thread(process_gif_image, img_bytes)

            async def followup_send(**kwargs):
                return await interaction.followup.send(**kwargs, wait=True)

            r2_url = await self.handle_image_upload(
                out_bytes, filename, is_animated, "GIF Conversion", interaction.user.display_name, followup_send
            )
            await self._log_gif(interaction.user, r2_url, "Convert to GIF (Context Menu)", guild=interaction.guild, guild_id=interaction.guild_id)
        except Exception as e:
            await interaction.followup.send(f"⚠️ Error: `{e}`")

async def setup(bot: commands.Bot):
    await bot.add_cog(ImagesCog(bot))



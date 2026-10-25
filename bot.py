import os

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

import database as db
from utils.r2 import upload_image_to_r2

load_dotenv()
TOKEN = os.getenv("Discord_Token")
OWNER_ID = int(os.getenv("OWNER_ID", "762609713610620948"))

intents = discord.Intents.default()
intents.message_content = True  # required for prefix commands

bot = commands.Bot(
    command_prefix="!",
    intents=intents,
    owner_id=OWNER_ID,
    help_command=None,
    allowed_installs=app_commands.AppInstallationType(guild=True, user=True),
    allowed_contexts=app_commands.AppCommandContext(
        guild=True, dm_channel=True, private_channel=True
    ),
)


@bot.check
async def global_prefix_blacklist_check(ctx: commands.Context) -> bool:
    if ctx.author.id == OWNER_ID:
        return True
    return not db.is_blacklisted(ctx.author.id)


async def global_interaction_blacklist_check(interaction: discord.Interaction) -> bool:
    if interaction.user.id == OWNER_ID:
        return True
    if db.is_blacklisted(interaction.user.id):
        await interaction.response.send_message(
            "🚫 You are blacklisted from using this bot.", ephemeral=True
        )
        return False
    return True


bot.tree.interaction_check = global_interaction_blacklist_check


async def setup_hook():
    await bot.load_extension("cogs.images")
    await bot.load_extension("cogs.fun")
    await bot.load_extension("cogs.owner")


bot.setup_hook = setup_hook


# ---------------------------------------------------------------------------
# Persistent buttons attached to every confession message
# ---------------------------------------------------------------------------

class ConfessButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Confess",
            style=discord.ButtonStyle.primary,
            emoji="💌",
            custom_id="confess:new",
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != OWNER_ID and db.is_blacklisted(interaction.user.id):
            await interaction.response.send_message(
                "🚫 You are blacklisted from using this bot.", ephemeral=True
            )
            return
        await interaction.response.send_modal(ConfessionModal())


class ReplyButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"confess:reply:(?P<confession_id>[0-9]+)",
):
    """Dynamic item so reply buttons keep working after a bot restart,
    without needing to re-register a view per confession message."""

    def __init__(self, confession_id: int):
        super().__init__(
            discord.ui.Button(
                label="Reply",
                style=discord.ButtonStyle.secondary,
                emoji="↩️",
                custom_id=f"confess:reply:{confession_id}",
            )
        )
        self.confession_id = confession_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["confession_id"]))

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != OWNER_ID and db.is_blacklisted(interaction.user.id):
            await interaction.response.send_message(
                "🚫 You are blacklisted from using this bot.", ephemeral=True
            )
            return
        await interaction.response.send_modal(ReplyModal(self.confession_id))


class ConfessionView(discord.ui.View):
    """Sent along with every confession message."""

    def __init__(self, confession_id: int):
        super().__init__(timeout=None)
        self.add_item(ConfessButton())
        self.add_item(ReplyButton(confession_id))


class StaticButtonsView(discord.ui.View):
    """Registered once on startup so the static Confess button custom_id
    is recognized again after a restart. This view is never sent itself."""

    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(ConfessButton())


# ---------------------------------------------------------------------------
# Modals (the "popup" prompts)
# ---------------------------------------------------------------------------

class ConfessionModal(discord.ui.Modal, title="Submit a Confession"):
    content = discord.ui.TextInput(
        label="Confession Content",
        style=discord.TextStyle.paragraph,
        max_length=2000,
        required=True,
    )

    def __init__(self, image: discord.Attachment | None = None):
        super().__init__()
        self.image = image
        self.attachment = discord.ui.FileUpload(
            required=False,
            min_values=0,
            max_values=1,
            custom_id="confession_attachment",
        )
        self.add_item(self.attachment)

    def to_components(self) -> list[dict]:
        components = super().to_components()
        # discord.py dumps FileUpload raw as type 19, but Discord requires it
        # wrapped inside a Label container (type 18) with a "component" key.
        for i, c in enumerate(components):
            if c.get("type") == 19:
                components[i] = {
                    "type": 18,
                    "label": "Attachment (optional)",
                    "component": c,
                }
        return components

    async def on_submit(self, interaction: discord.Interaction):
        uploaded = self.attachment.values[0] if self.attachment.values else None
        await post_confession(interaction, self.content.value, uploaded or self.image)


class ReplyModal(discord.ui.Modal, title="Reply to Confession"):
    content = discord.ui.TextInput(
        label="Your reply",
        style=discord.TextStyle.paragraph,
        max_length=2000,
        required=True,
    )

    def __init__(self, confession_id: int):
        super().__init__()
        self.confession_id = confession_id
        self.attachment = discord.ui.FileUpload(
            required=False,
            min_values=0,
            max_values=1,
            custom_id="reply_attachment",
        )
        self.add_item(self.attachment)

    def to_components(self) -> list[dict]:
        components = super().to_components()
        for i, c in enumerate(components):
            if c.get("type") == 19:
                components[i] = {
                    "type": 18,
                    "label": "Attachment (optional)",
                    "component": c,
                }
        return components

    async def on_submit(self, interaction: discord.Interaction):
        image = self.attachment.values[0] if self.attachment.values else None
        await post_reply(interaction, self.confession_id, self.content.value, image)


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

async def _resolve_confession_image_url(image: discord.Attachment | None) -> str | None:
    """Upload a confession attachment to Cloudflare R2 so it is permanently saved,
    and return the R2 URL (falling back to the Discord attachment URL if R2 is not configured)."""
    if image is None:
        return None
    try:
        img_bytes = await image.read()
        filename = image.filename or "confession.png"
        content_type = image.content_type or (
            "image/gif" if filename.lower().endswith(".gif") else "image/png"
        )
        r2_url = await upload_image_to_r2(img_bytes, filename, content_type)
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(r2_url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    await resp.read()
        except Exception:
            pass
        return r2_url
    except Exception as e:
        print(f"Confession R2 upload failed ({e}), falling back to attachment URL.")
        return image.url


DEFAULT_G_CONFESSION_LOG = 1563882927593619456


async def _send_global_confession_log(
    user: discord.User | discord.Member,
    guild: discord.Guild,
    number: int,
    text: str,
    image_url: str | None,
    jump_url: str,
    is_reply: bool = False,
):
    """Send every confession (and reply) from any server to the global G_CONFESSION_LOG channel."""
    raw_id = os.getenv("G_CONFESSION_LOG", "").strip()
    cid = int(raw_id) if (raw_id and raw_id.isdigit()) else DEFAULT_G_CONFESSION_LOG

    g_channel = bot.get_channel(cid)
    if g_channel is None:
        try:
            g_channel = await bot.fetch_channel(cid)
        except Exception as e:
            print(f"Failed to fetch global confession log channel {cid}: {e}")
            return

    title = f"💌 Global Confession Reply (#{number})" if is_reply else f"💌 Global Confession (#{number})"
    g_embed = discord.Embed(
        title=title,
        color=discord.Color.orange() if is_reply else discord.Color.dark_purple(),
        timestamp=discord.utils.utcnow(),
    )
    g_embed.set_author(
        name=f"{user} ({user.id})",
        icon_url=user.display_avatar.url,
    )
    g_embed.add_field(name="Confession User", value=f"{user} ({user.mention})", inline=True)
    g_embed.add_field(name="Confession User ID", value=f"`{user.id}`", inline=True)
    g_embed.add_field(name="Confession Server Name", value=guild.name, inline=True)
    g_embed.add_field(name="Confession Server ID", value=f"`{guild.id}`", inline=True)
    g_embed.add_field(name="Jump to Confession", value=jump_url, inline=True)
    g_embed.add_field(name="Confession", value=text[:1024] or "*(Empty)*", inline=False)

    if image_url:
        g_embed.add_field(name="Confession Image (R2)", value=image_url, inline=False)
        g_embed.set_image(url=image_url)

    try:
        await g_channel.send(embed=g_embed)
    except Exception as e:
        print(f"Failed to send global confession log to {cid}: {e}")


async def post_confession(
    interaction: discord.Interaction, text: str, image: discord.Attachment | None
):
    await interaction.response.defer(ephemeral=True, thinking=True)

    guild = interaction.guild
    settings = await db.get_settings(guild.id)

    if not settings or not settings["confess_channel_id"]:
        await interaction.followup.send(
            "This server hasn't set up a confession channel yet. "
            "Ask an admin to run `/setup`.",
            ephemeral=True,
        )
        return

    channel = guild.get_channel(settings["confess_channel_id"])
    if channel is None:
        await interaction.followup.send(
            "The configured confession channel no longer exists. "
            "Ask an admin to run `/setup` again.",
            ephemeral=True,
        )
        return

    image_url = await _resolve_confession_image_url(image)
    number = await db.next_number(guild.id)
    confession_id = await db.create_confession(guild.id, number, interaction.user.id)

    embed = discord.Embed(
        description=text,
        color=discord.Color.dark_purple(),
        timestamp=discord.utils.utcnow(),
    )
    embed.set_author(name=f"Anonymous Confession #{number}")
    if image_url is not None:
        embed.set_image(url=image_url)
    embed.set_footer(text=guild.name)

    try:
        message = await channel.send(embed=embed, view=ConfessionView(confession_id))
    except discord.Forbidden:
        await interaction.followup.send(
            "I don't have permission to post in the confession channel.",
            ephemeral=True,
        )
        return

    await db.set_message_id(confession_id, message.id)
    await interaction.followup.send(
        "Your confession was posted anonymously.", ephemeral=True
    )

    if settings["log_channel_id"]:
        log_channel = guild.get_channel(settings["log_channel_id"])
        if log_channel is not None:
            log_embed = discord.Embed(
                description=text,
                color=discord.Color.red(),
                timestamp=discord.utils.utcnow(),
            )
            log_embed.set_author(
                name=f"{interaction.user} ({interaction.user.id})",
                icon_url=interaction.user.display_avatar.url,
            )
            log_embed.add_field(name="Confession", value=f"#{number}")
            log_embed.add_field(
                name="Jump to message", value=message.jump_url, inline=False
            )
            if image_url is not None:
                log_embed.set_image(url=image_url)
            try:
                await log_channel.send(embed=log_embed)
            except discord.Forbidden:
                pass

    await _send_global_confession_log(
        user=interaction.user,
        guild=guild,
        number=number,
        text=text,
        image_url=image_url,
        jump_url=message.jump_url,
        is_reply=False,
    )


async def post_reply(interaction: discord.Interaction, confession_id: int, text: str, image: discord.Attachment | None = None):
    await interaction.response.defer(ephemeral=True, thinking=True)

    confession = await db.get_confession(confession_id)
    if confession is None:
        await interaction.followup.send("This confession no longer exists.", ephemeral=True)
        return

    guild = interaction.guild
    settings = await db.get_settings(guild.id)
    channel = guild.get_channel(settings["confess_channel_id"]) if settings else None
    if channel is None:
        await interaction.followup.send(
            "The confession channel no longer exists.", ephemeral=True
        )
        return

    thread = guild.get_thread(confession["thread_id"]) if confession["thread_id"] else None

    if thread is None:
        try:
            message = await channel.fetch_message(confession["message_id"])
        except (discord.NotFound, discord.Forbidden):
            await interaction.followup.send(
                "Couldn't find the original confession message.", ephemeral=True
            )
            return
        try:
            thread = await message.create_thread(name=f"Confession #{confession['number']}")
            await db.set_thread_id(confession_id, thread.id)
        except discord.HTTPException:
            await interaction.followup.send(
                "Couldn't create a thread for replies.", ephemeral=True
            )
            return

    image_url = await _resolve_confession_image_url(image)
    is_op = interaction.user.id == confession["author_id"]
    
    embed = discord.Embed(
        description=text,
        color=discord.Color.dark_purple(),
        timestamp=discord.utils.utcnow(),
    )
    embed.set_author(name=f"Anonymous Reply {'(OP)' if is_op else ''}")
    embed.set_footer(text=f"Replying to Confession #{confession['number']}")
    if image_url is not None:
        embed.set_image(url=image_url)

    try:
        reply_message = await thread.send(embed=embed)
    except discord.Forbidden:
        await interaction.followup.send("I can't post in that thread.", ephemeral=True)
        return

    await interaction.followup.send(
        "Your reply was posted anonymously in the thread.", ephemeral=True
    )

    if settings and settings["log_channel_id"]:
        log_channel = guild.get_channel(settings["log_channel_id"])
        if log_channel is not None:
            log_embed = discord.Embed(
                description=text,
                color=discord.Color.orange(),
                timestamp=discord.utils.utcnow(),
            )
            log_embed.set_author(
                name=f"{interaction.user} ({interaction.user.id})",
                icon_url=interaction.user.display_avatar.url,
            )
            log_embed.add_field(name="Replied to Confession", value=f"#{confession['number']}")
            log_embed.add_field(name="OP?", value="Yes" if is_op else "No")
            log_embed.add_field(
                name="Jump to reply", value=reply_message.jump_url, inline=False
            )
            if image_url is not None:
                log_embed.set_image(url=image_url)
            try:
                await log_channel.send(embed=log_embed)
            except discord.Forbidden:
                pass

    await _send_global_confession_log(
        user=interaction.user,
        guild=guild,
        number=confession["number"],
        text=text,
        image_url=image_url,
        jump_url=reply_message.jump_url,
        is_reply=True,
    )


# ---------------------------------------------------------------------------
# /setup UI
# ---------------------------------------------------------------------------

async def build_setup_embed(guild_id: int) -> discord.Embed:
    settings = await db.get_settings(guild_id)
    embed = discord.Embed(title="Confession Bot Setup", color=discord.Color.blurple())
    confess = (
        f"<#{settings['confess_channel_id']}>"
        if settings and settings["confess_channel_id"]
        else "Not set"
    )
    log = (
        f"<#{settings['log_channel_id']}>"
        if settings and settings["log_channel_id"]
        else "Disabled"
    )
    embed.add_field(name="Confession channel", value=confess, inline=False)
    embed.add_field(name="Log channel", value=log, inline=False)
    embed.set_footer(text="Use the menus below to configure. Changes save instantly.")
    return embed


class ConfessChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, guild_id: int):
        super().__init__(
            placeholder="Select the confession channel",
            channel_types=[discord.ChannelType.text],
            min_values=1,
            max_values=1,
        )
        self.guild_id = guild_id

    async def callback(self, interaction: discord.Interaction):
        await db.set_confess_channel(self.guild_id, self.values[0].id)
        await interaction.response.edit_message(
            embed=await build_setup_embed(self.guild_id), view=self.view
        )


class LogChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, guild_id: int):
        super().__init__(
            placeholder="Select a log channel (optional)",
            channel_types=[discord.ChannelType.text],
            min_values=1,
            max_values=1,
        )
        self.guild_id = guild_id

    async def callback(self, interaction: discord.Interaction):
        await db.set_log_channel(self.guild_id, self.values[0].id)
        await interaction.response.edit_message(
            embed=await build_setup_embed(self.guild_id), view=self.view
        )


class DisableLoggingButton(discord.ui.Button):
    def __init__(self, guild_id: int):
        super().__init__(label="Disable logging", style=discord.ButtonStyle.danger)
        self.guild_id = guild_id

    async def callback(self, interaction: discord.Interaction):
        await db.set_log_channel(self.guild_id, None)
        await interaction.response.edit_message(
            embed=await build_setup_embed(self.guild_id), view=self.view
        )


class SetupView(discord.ui.View):
    def __init__(self, guild_id: int):
        super().__init__(timeout=300)
        self.add_item(ConfessChannelSelect(guild_id))
        self.add_item(LogChannelSelect(guild_id))
        self.add_item(DisableLoggingButton(guild_id))


# ---------------------------------------------------------------------------
# Slash commands
# ---------------------------------------------------------------------------

@bot.tree.command(name="setup", description="Configure the confession bot for this server")
@app_commands.default_permissions(administrator=True)
@app_commands.allowed_installs(guilds=True, users=False)
@app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
@app_commands.guild_only()
async def setup_cmd(interaction: discord.Interaction):
    await interaction.response.send_message(
        embed=await build_setup_embed(interaction.guild.id),
        view=SetupView(interaction.guild.id),
        ephemeral=True,
    )


@bot.tree.command(name="confess", description="Send an anonymous confession")
@app_commands.describe(image="Optional image to attach to your confession")
@app_commands.allowed_installs(guilds=True, users=False)
@app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
@app_commands.guild_only()
async def confess_cmd(interaction: discord.Interaction, image: discord.Attachment | None = None):
    if image is not None and not (image.content_type or "").startswith("image/"):
        await interaction.response.send_message("That attachment isn't an image.", ephemeral=True)
        return
    await interaction.response.send_modal(ConfessionModal(image=image))


@confess_cmd.error
@setup_cmd.error
async def app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message(
            "You need Administrator permission to do that.", ephemeral=True
        )
    else:
        if not interaction.response.is_done():
            await interaction.response.send_message("Something went wrong.", ephemeral=True)
        raise error


def build_public_help_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🍵 Chai — Help & Commands",
        description="Here are all the commands you can use with **Chai** (in servers or anywhere via User Install):",
        color=discord.Color.blurple(),
    )
    if bot.user and bot.user.display_avatar:
        embed.set_thumbnail(url=bot.user.display_avatar.url)

    embed.add_field(
        name="💌 Anonymous Confessions (Server Only)",
        value=(
            "`/confess` — Submit an anonymous confession (supports images)\n"
            "`/setup` — *(Admin only)* Setup Confessions Channel\n"
            "**Confess / Reply Buttons** — Click the buttons under any confession to post or reply anonymously"
        ),
        inline=False,
    )
    embed.add_field(
        name="🖼️ Image & GIF Manipulation (Works Anywhere)",
        value=(
            "`/caption` or `!caption <text>` — Add a white-bar caption to an image/GIF\n"
            "`/gif` or `!gif` — Convert any image into a GIF (or right-click message → **Apps → Convert to GIF**)\n"
            "`/tweet` or `!tweet` — Turn a message or reply into a Tweet (or right-click message → **Apps → Make it a Tweet**)"
        ),
        inline=False,
    )
    embed.add_field(
        name="🎭 Canvas & Meme Overlays (Works Anywhere)",
        value=(
            "`/petpet [user]` — Animated PetPet GIF of a user's avatar\n"
            "`/amongus [user] [impostor]` — Eject a user into space (Among Us GIF)\n"
            "`/wasted [user]` — GTA Wasted overlay on a user's avatar\n"
            "`/wanted [user]` — Put a user on a Wanted poster\n"
            "`/horny [user]` — Grant a Horny License card\n"
            "`/simpcard [user]` — Generate an official Simp Card\n"
            "`/stupid <text> [user]` — Generate an 'Oh no, it's so stupid' meme"
        ),
        inline=False,
    )
    embed.add_field(
        name="🎉 Fun, Social & Utilities (Works Anywhere)",
        value=(
            "`/ship <user1> [user2]` — Check compatibility between two users (permanently saved!)\n"
            "`/rizz <user>` — Send a pickup line to a user\n"
            "`/roast <user>` — Roast a user\n"
            "`/corporate` — Generate a corporate BS buzzword phrase\n"
            "`/cat` • `/duck` • `/capybara` — Random animal pictures\n"
            "`/steal <emoji>` or `!steal <emoji>` — *(Server Only)* Steal a custom emoji to this server"
        ),
        inline=False,
    )
    embed.set_footer(text="Install Chai to your account ('Add to My Apps') to use commands in any DM or server!")
    return embed


@bot.tree.command(name="help", description="View all available bot commands")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def help_slash(interaction: discord.Interaction):
    from cogs.owner import build_owner_help_embed, get_owner_id

    if interaction.guild is None and interaction.user.id == get_owner_id():
        await interaction.response.send_message(embed=build_owner_help_embed(bot))
    else:
        await interaction.response.send_message(embed=build_public_help_embed())


@bot.command(name="help")
async def help_prefix(ctx: commands.Context):
    from cogs.owner import build_owner_help_embed, get_owner_id

    if ctx.guild is None and ctx.author.id == get_owner_id():
        await ctx.send(embed=build_owner_help_embed(bot))
    else:
        await ctx.send(embed=build_public_help_embed())


# ---------------------------------------------------------------------------
# Prefix command: !sync
# ---------------------------------------------------------------------------

@bot.command(name="sync")
async def sync_cmd(ctx: commands.Context):
    if ctx.guild is not None or ctx.author.id != OWNER_ID:
        return
    synced = await bot.tree.sync()
    await ctx.send(f"✅ Synced {len(synced)} command(s).")


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

@bot.event
async def on_ready():
    await db.init_db()
    bot.add_dynamic_items(ReplyButton)
    bot.add_view(StaticButtonsView())
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.listening, name="Brot"))
    print(f"Logged in as {bot.user} ({bot.user.id})")


if __name__ == "__main__":
    if not TOKEN:
        raise RuntimeError("The 'Discord_Token' environment variable is not set.")
    bot.run(TOKEN)

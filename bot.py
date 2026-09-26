import os

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

import database as db

load_dotenv()
TOKEN = os.getenv("Discord_Token")

intents = discord.Intents.default()
intents.message_content = True  # required for the !sync prefix command

bot = commands.Bot(command_prefix="!", intents=intents)


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
        label="Confession Content *",
        style=discord.TextStyle.paragraph,
        max_length=2000,
        required=True,
    )

    def __init__(self):
        super().__init__()
        self.attachment = discord.ui.FileUpload(
            required=False,
            min_values=0,
            max_values=1,
            custom_id="confession_attachment"
        )
        self.add_item(self.attachment)

    def to_dict(self):
        payload = super().to_dict()
        for i, c in enumerate(payload.get("components", [])):
            if c.get("type") != 1:
                payload["components"][i] = {"type": 1, "components": [c]}
        return payload

    async def on_submit(self, interaction: discord.Interaction):
        image = self.attachment.values[0] if self.attachment.values else None
        await post_confession(interaction, self.content.value, image)


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
            custom_id="reply_attachment"
        )
        self.add_item(self.attachment)

    def to_dict(self):
        payload = super().to_dict()
        for i, c in enumerate(payload.get("components", [])):
            if c.get("type") != 1:
                payload["components"][i] = {"type": 1, "components": [c]}
        return payload

    async def on_submit(self, interaction: discord.Interaction):
        image = self.attachment.values[0] if self.attachment.values else None
        await post_reply(interaction, self.confession_id, self.content.value, image)


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

async def post_confession(
    interaction: discord.Interaction, text: str, image: discord.Attachment | None
):
    guild = interaction.guild
    settings = await db.get_settings(guild.id)

    if not settings or not settings["confess_channel_id"]:
        await interaction.response.send_message(
            "This server hasn't set up a confession channel yet. "
            "Ask an admin to run `/setup`.",
            ephemeral=True,
        )
        return

    channel = guild.get_channel(settings["confess_channel_id"])
    if channel is None:
        await interaction.response.send_message(
            "The configured confession channel no longer exists. "
            "Ask an admin to run `/setup` again.",
            ephemeral=True,
        )
        return

    number = await db.next_number(guild.id)
    confession_id = await db.create_confession(guild.id, number, interaction.user.id)

    embed = discord.Embed(
        description=text,
        color=discord.Color.dark_purple(),
        timestamp=discord.utils.utcnow(),
    )
    embed.set_author(name=f"Anonymous Confession #{number}")
    if image is not None:
        embed.set_image(url=image.url)
    embed.set_footer(text=guild.name)

    try:
        message = await channel.send(embed=embed, view=ConfessionView(confession_id))
    except discord.Forbidden:
        await interaction.response.send_message(
            "I don't have permission to post in the confession channel.",
            ephemeral=True,
        )
        return

    await db.set_message_id(confession_id, message.id)
    await interaction.response.send_message(
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
            if image is not None:
                log_embed.set_image(url=image.url)
            try:
                await log_channel.send(embed=log_embed)
            except discord.Forbidden:
                pass


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

    is_op = interaction.user.id == confession["author_id"]
    
    embed = discord.Embed(
        description=text,
        color=discord.Color.dark_purple(),
        timestamp=discord.utils.utcnow(),
    )
    embed.set_author(name=f"Anonymous Reply {'(OP)' if is_op else ''}")
    embed.set_footer(text=f"Replying to Confession #{confession['number']}")
    if image is not None:
        embed.set_image(url=image.url)

    try:
        await thread.send(embed=embed)
    except discord.Forbidden:
        await interaction.followup.send("I can't post in that thread.", ephemeral=True)
        return

    await interaction.followup.send(
        "Your reply was posted anonymously in the thread.", ephemeral=True
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
@app_commands.guild_only()
async def setup_cmd(interaction: discord.Interaction):
    await interaction.response.send_message(
        embed=await build_setup_embed(interaction.guild.id),
        view=SetupView(interaction.guild.id),
        ephemeral=True,
    )


@bot.tree.command(name="confess", description="Send an anonymous confession")
@app_commands.guild_only()
async def confess_cmd(interaction: discord.Interaction):
    await interaction.response.send_modal(ConfessionModal())


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


# ---------------------------------------------------------------------------
# Prefix command: !sync
# ---------------------------------------------------------------------------

@bot.command(name="sync")
@commands.is_owner()
async def sync_cmd(ctx: commands.Context):
    synced = await bot.tree.sync()
    await ctx.send(f"Synced {len(synced)} command(s).")


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

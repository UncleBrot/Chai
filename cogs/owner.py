import io
import os
import re

import discord
from discord.ext import commands

import database as db
from utils.r2 import get_r2_stats

DEFAULT_OWNER_ID = 762609713610620948
ID_RE = re.compile(r"([0-9]{15,22})")
MSG_LINK_RE = re.compile(
    r"https?://(?:ptb\.|canary\.)?discord(?:app)?\.com/channels/([0-9]{15,22})/([0-9]{15,22})/([0-9]{15,22})"
)


def get_owner_id() -> int:
    raw = os.getenv("OWNER_ID", str(DEFAULT_OWNER_ID)).strip()
    try:
        return int(raw)
    except ValueError:
        return DEFAULT_OWNER_ID


def _extract_id(raw: str) -> int | None:
    match = ID_RE.search(raw)
    return int(match.group(1)) if match else None


def _format_bytes(num_bytes: int) -> str:
    if num_bytes < 1024:
        return f"{num_bytes} B"
    if num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.2f} KB"
    if num_bytes < 1024 * 1024 * 1024:
        return f"{num_bytes / (1024 * 1024):.2f} MB"
    return f"{num_bytes / (1024 * 1024 * 1024):.2f} GB"


def build_owner_help_embed(bot: commands.Bot) -> discord.Embed:
    """Build the Owner-Exclusive DM Commands embed."""
    embed = discord.Embed(
        title="👑 Owner-Exclusive DM Commands",
        description=(
            "These commands **only work in the bot's DMs** and are locked to "
            f"`OWNER_ID` (`{get_owner_id()}`)."
        ),
        color=discord.Color.gold(),
    )
    embed.add_field(
        name="🗣️ Puppet & Messaging",
        value=(
            "`!say <channel_id_or_msg_link> <message>` — Send a message (or reply to a message link) as the bot\n"
            "`!dm <user_id> <message>` — Send a DM to any user as the bot (incoming DMs are logged to `BOT_LOGS_ID`)\n"
            "`!impersonate <channel_id> <user_id> <message>` — Send a message in a channel using a user's name & avatar"
        ),
        inline=False,
    )
    embed.add_field(
        name="🌐 Server Management",
        value=(
            "`!servers` — List all servers the bot is in with their Server IDs\n"
            "`!channels <server_id>` — List all text channels & Channel IDs in a server\n"
            "`!getinvite <server_id>` — Generate or fetch an invite link for a server\n"
            "`!leaveserver <server_id>` — Make the bot leave a server"
        ),
        inline=False,
    )
    embed.add_field(
        name="🚫 Blacklist Control",
        value=(
            "`!blacklist add <user_id> [reason]` — Block a user from using the bot anywhere\n"
            "`!blacklist remove <user_id>` — Unblock a user (`!unblacklist <user_id>`)\n"
            "`!blacklist list` — View all blacklisted users"
        ),
        inline=False,
    )
    embed.add_field(
        name="☁️ Storage & System",
        value=(
            "`!r2stats` — View Cloudflare R2 bucket file counts & storage usage\n"
            "`!sync` — Sync slash commands globally with Discord"
        ),
        inline=False,
    )
    embed.set_footer(text=f"Connected to {len(bot.guilds)} server(s)")
    return embed


class OwnerCog(commands.Cog):
    """Owner-exclusive commands that only work inside the bot's DMs."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def cog_check(self, ctx: commands.Context) -> bool:
        """All commands in this cog require:
        1. Being sent in the bot's DMs (ctx.guild is None)
        2. Author matching OWNER_ID
        """
        return ctx.guild is None and ctx.author.id == get_owner_id()

    @commands.Cog.listener()
    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError):
        # Silently ignore check failures (e.g. non-owners or server usage) and unknown commands
        if isinstance(error, (commands.CheckFailure, commands.CommandNotFound)):
            return
        if ctx.guild is None and ctx.author.id == get_owner_id():
            if isinstance(error, commands.MissingRequiredArgument):
                await ctx.send(f"⚠️ Missing argument: `{error.param.name}`. Use `!help` to see usage.")
            else:
                await ctx.send(f"⚠️ Error: `{error}`")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Log incoming DMs sent to the bot into the channel configured by BOT_LOGS_ID."""
        if message.author.bot or message.guild is not None:
            return
        # Don't log owner commands executed in DMs
        if message.author.id == get_owner_id() and message.content.startswith("!"):
            return

        log_channel_id = os.getenv("BOT_LOGS_ID", "").strip()
        if not log_channel_id or not log_channel_id.isdigit():
            return

        cid = int(log_channel_id)
        log_channel = self.bot.get_channel(cid)
        if log_channel is None:
            try:
                log_channel = await self.bot.fetch_channel(cid)
            except Exception:
                return

        embed = discord.Embed(
            title="📩 Incoming DM to Bot",
            description=message.content or "*(No text content)*",
            color=discord.Color.blurple(),
            timestamp=message.created_at,
        )
        embed.set_author(
            name=f"{message.author} ({message.author.id})",
            icon_url=message.author.display_avatar.url,
        )
        embed.add_field(name="User", value=f"{message.author.mention} (`{message.author.id}`)", inline=False)
        embed.set_footer(text=f"Reply in bot DMs with: !dm {message.author.id} <message>")
        if message.attachments:
            embed.set_image(url=message.attachments[0].url)
            if len(message.attachments) > 1:
                att_links = "\n".join(a.url for a in message.attachments)
                embed.add_field(name="Attachments", value=att_links[:1024], inline=False)

        try:
            await log_channel.send(embed=embed)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 1. !ownerhelp — Show all Owner DM commands
    # ------------------------------------------------------------------

    @commands.command(name="ownerhelp", aliases=["commands"])
    async def owner_help(self, ctx: commands.Context):
        """Show all owner-exclusive DM commands."""
        await ctx.send(embed=build_owner_help_embed(self.bot))

    # ------------------------------------------------------------------
    # Helper commands: !servers and !channels <server_id>
    # ------------------------------------------------------------------

    @commands.command(name="servers", aliases=["guilds"])
    async def list_servers(self, ctx: commands.Context):
        """List all servers the bot is currently in."""
        if not self.bot.guilds:
            await ctx.send("I am not in any servers.")
            return

        lines = []
        for g in sorted(self.bot.guilds, key=lambda x: x.member_count or 0, reverse=True):
            lines.append(f"• **{g.name}** — ID: `{g.id}` ({g.member_count or '?'} members)")

        embed = discord.Embed(
            title=f"🌐 Connected Servers ({len(self.bot.guilds)})",
            description="\n".join(lines)[:4000],
            color=discord.Color.blurple(),
        )
        embed.set_footer(text="Use !channels <server_id> or !getinvite <server_id>")
        await ctx.send(embed=embed)

    @commands.command(name="channels")
    async def list_channels(self, ctx: commands.Context, server_id: str):
        """List all text channels in a server so you can grab a channel_id."""
        gid = _extract_id(server_id)
        guild = self.bot.get_guild(gid) if gid else None
        if not guild:
            await ctx.send("⚠️ Server not found. Use `!servers` to view valid Server IDs.")
            return

        lines = [f"• **#{ch.name}** — ID: `{ch.id}`" for ch in guild.text_channels]
        embed = discord.Embed(
            title=f"📋 Text Channels in {guild.name}",
            description="\n".join(lines)[:4000] if lines else "No text channels found.",
            color=discord.Color.blurple(),
        )
        await ctx.send(embed=embed)

    # ------------------------------------------------------------------
    # 2. !say <channel_id_or_message_link> <message>
    # ------------------------------------------------------------------

    @commands.command(name="say")
    async def say_cmd(self, ctx: commands.Context, target: str, *, message: str = ""):
        """Make the bot send a message in a channel (or reply to a message link)."""
        ref_message = None
        link_match = MSG_LINK_RE.search(target)

        if link_match:
            ch_id = int(link_match.group(2))
            msg_id = int(link_match.group(3))
            channel = self.bot.get_channel(ch_id) or await self.bot.fetch_channel(ch_id)
            try:
                ref_message = await channel.fetch_message(msg_id)
            except Exception:
                ref_message = None
        else:
            ch_id = _extract_id(target)
            if not ch_id:
                await ctx.send("⚠️ Please provide a valid `channel_id` or message link.")
                return
            channel = self.bot.get_channel(ch_id)
            if channel is None:
                try:
                    channel = await self.bot.fetch_channel(ch_id)
                except Exception:
                    await ctx.send(f"⚠️ Could not find channel `{ch_id}`.")
                    return

        files = [await att.to_file() for att in ctx.message.attachments]
        if not message and not files:
            await ctx.send("⚠️ Provide a message or attach a file to send.")
            return

        try:
            if ref_message:
                sent = await ref_message.reply(content=message or None, files=files, mention_author=False)
            else:
                sent = await channel.send(content=message or None, files=files)
            await ctx.send(f"✅ Sent to **#{getattr(channel, 'name', ch_id)}**: {sent.jump_url}")
        except discord.Forbidden:
            await ctx.send("⚠️ I don't have permission to send messages in that channel.")
        except Exception as e:
            await ctx.send(f"⚠️ Failed to send message: `{e}`")

    # ------------------------------------------------------------------
    # 3. !dm <user_id> <message>
    # ------------------------------------------------------------------

    @commands.command(name="dm")
    async def dm_cmd(self, ctx: commands.Context, user_target: str, *, message: str = ""):
        """Send a direct message to a user via the bot."""
        uid = _extract_id(user_target)
        if not uid:
            await ctx.send("⚠️ Please provide a valid `user_id`.")
            return

        user = self.bot.get_user(uid)
        if user is None:
            try:
                user = await self.bot.fetch_user(uid)
            except discord.NotFound:
                await ctx.send(f"⚠️ User `{uid}` not found.")
                return

        files = [await att.to_file() for att in ctx.message.attachments]
        if not message and not files:
            await ctx.send("⚠️ Provide a message or attach a file to DM.")
            return

        try:
            await user.send(content=message or None, files=files)
            await ctx.send(f"✅ Sent DM to **{user}** (`{user.id}`).")
        except discord.Forbidden:
            await ctx.send(f"⚠️ Cannot DM **{user}** — their DMs are closed or they blocked the bot.")
        except Exception as e:
            await ctx.send(f"⚠️ Failed to DM user: `{e}`")

    # ------------------------------------------------------------------
    # 4. !impersonate <channel_id> <user_id> <message>
    # ------------------------------------------------------------------

    @commands.command(name="impersonate", aliases=["imp", "webhook"])
    async def impersonate_cmd(
        self, ctx: commands.Context, channel_target: str, user_target: str, *, message: str = ""
    ):
        """Send a message in a channel impersonating a user's display name and avatar."""
        ch_id = _extract_id(channel_target)
        uid = _extract_id(user_target)
        if not ch_id or not uid:
            await ctx.send("⚠️ Usage: `!impersonate <channel_id> <user_id> <message>`")
            return

        channel = self.bot.get_channel(ch_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(ch_id)
            except Exception:
                await ctx.send(f"⚠️ Could not find channel `{ch_id}`.")
                return

        if not isinstance(channel, discord.TextChannel):
            await ctx.send("⚠️ Target channel must be a server text channel.")
            return

        # Prefer guild Member so server-specific nickname & server avatar are used
        target_user = channel.guild.get_member(uid)
        if target_user is None:
            try:
                target_user = await channel.guild.fetch_member(uid)
            except Exception:
                target_user = self.bot.get_user(uid) or await self.bot.fetch_user(uid)

        if target_user is None:
            await ctx.send(f"⚠️ Could not find user `{uid}`.")
            return

        files = [await att.to_file() for att in ctx.message.attachments]
        if not message and not files:
            await ctx.send("⚠️ Provide a message or attachment to send.")
            return

        try:
            webhook = await channel.create_webhook(name="Chai-Impersonate")
            try:
                sent = await webhook.send(
                    content=message or None,
                    username=target_user.display_name,
                    avatar_url=target_user.display_avatar.url,
                    files=files,
                    wait=True,
                )
            finally:
                try:
                    await webhook.delete()
                except Exception:
                    pass
            await ctx.send(
                f"🎭 Sent as **{target_user.display_name}** in **#{channel.name}** ({channel.guild.name}): {sent.jump_url}"
            )
        except discord.Forbidden:
            await ctx.send(
                f"⚠️ Missing **Manage Webhooks** permission in **#{channel.name}** ({channel.guild.name})."
            )
        except Exception as e:
            await ctx.send(f"⚠️ Failed to impersonate: `{e}`")

    # ------------------------------------------------------------------
    # 5. !getinvite <server_id>
    # ------------------------------------------------------------------

    @commands.command(name="getinvite", aliases=["invite", "get_invite"])
    async def getinvite_cmd(self, ctx: commands.Context, server_id: str):
        """Generate or retrieve an invite link for a server the bot is in."""
        gid = _extract_id(server_id)
        guild = self.bot.get_guild(gid) if gid else None
        if not guild:
            await ctx.send("⚠️ Server not found. Use `!servers` to view valid Server IDs.")
            return

        # 1. Try creating a fresh invite in any text/voice channel where permitted
        me = guild.me
        candidate_channels = [
            ch for ch in ([guild.system_channel, guild.rules_channel] + list(guild.text_channels) + list(guild.voice_channels))
            if ch is not None and ch.permissions_for(me).create_instant_invite
        ]

        for ch in candidate_channels:
            try:
                inv = await ch.create_invite(max_age=0, max_uses=0, reason="Requested by bot owner")
                await ctx.send(f"🔗 Invite for **{guild.name}** (`#{ch.name}`): {inv.url}")
                return
            except Exception:
                continue

        # 2. Fall back to vanity URL if available
        if guild.vanity_url:
            await ctx.send(f"🔗 Vanity invite for **{guild.name}**: {guild.vanity_url}")
            return

        # 3. Fall back to existing guild invites if Manage Guild is granted
        if me.guild_permissions.manage_guild:
            try:
                invites = await guild.invites()
                if invites:
                    await ctx.send(f"🔗 Existing invite for **{guild.name}**: {invites[0].url}")
                    return
            except Exception:
                pass

        await ctx.send(f"⚠️ Could not create or fetch an invite for **{guild.name}** (missing `Create Invite` permission).")

    # ------------------------------------------------------------------
    # 6. !leaveserver <server_id>
    # ------------------------------------------------------------------

    @commands.command(name="leaveserver", aliases=["leave", "leave_server"])
    async def leaveserver_cmd(self, ctx: commands.Context, server_id: str):
        """Force the bot to leave a server by ID."""
        gid = _extract_id(server_id)
        guild = self.bot.get_guild(gid) if gid else None
        if not guild:
            await ctx.send("⚠️ Server not found. Use `!servers` to view valid Server IDs.")
            return

        name = guild.name
        try:
            await guild.leave()
            await ctx.send(f"✅ Left server **{name}** (`{gid}`).")
        except Exception as e:
            await ctx.send(f"⚠️ Failed to leave **{name}**: `{e}`")

    # ------------------------------------------------------------------
    # 7. !blacklist / !unblacklist
    # ------------------------------------------------------------------

    @commands.command(name="blacklist", aliases=["bl"])
    async def blacklist_cmd(
        self,
        ctx: commands.Context,
        action_or_user: str | None = None,
        user_or_reason: str | None = None,
        *,
        extra_reason: str | None = None,
    ):
        """Manage the global user blacklist:
        !blacklist list
        !blacklist add <user_id> [reason]
        !blacklist remove <user_id>
        """
        if not action_or_user or action_or_user.lower() == "list":
            entries = await db.get_blacklist()
            if not entries:
                await ctx.send("✅ The blacklist is currently empty.")
                return
            lines = []
            for item in entries:
                uid = item["user_id"]
                u = self.bot.get_user(uid)
                tag = f"{u} (`{uid}`)" if u else f"`{uid}`"
                lines.append(f"• {tag} — *{item['reason']}* (`{item['created_at']}`)")
            embed = discord.Embed(
                title=f"🚫 Blacklisted Users ({len(entries)})",
                description="\n".join(lines)[:4000],
                color=discord.Color.red(),
            )
            await ctx.send(embed=embed)
            return

        act = action_or_user.lower()
        if act in ("remove", "del", "delete", "unban", "unblacklist"):
            if not user_or_reason:
                await ctx.send("⚠️ Usage: `!blacklist remove <user_id>`")
                return
            uid = _extract_id(user_or_reason)
            if not uid:
                await ctx.send("⚠️ Invalid `user_id`.")
                return
            removed = await db.remove_blacklist(uid)
            if removed:
                await ctx.send(f"✅ Removed `{uid}` from the blacklist.")
            else:
                await ctx.send(f"ℹ️ `{uid}` was not in the blacklist.")
            return

        # Support both `!blacklist add <user_id> [reason]` and `!blacklist <user_id> [reason]`
        if act == "add":
            if not user_or_reason:
                await ctx.send("⚠️ Usage: `!blacklist add <user_id> [reason]`")
                return
            uid = _extract_id(user_or_reason)
            reason = extra_reason or "Blacklisted by bot owner"
        else:
            uid = _extract_id(action_or_user)
            reason_parts = [p for p in (user_or_reason, extra_reason) if p]
            reason = " ".join(reason_parts) if reason_parts else "Blacklisted by bot owner"

        if not uid:
            await ctx.send("⚠️ Usage: `!blacklist add <user_id> [reason]` or `!blacklist remove <user_id>`")
            return
        if uid == get_owner_id():
            await ctx.send("⚠️ You cannot blacklist the bot owner.")
            return

        await db.add_blacklist(uid, reason)
        user_obj = self.bot.get_user(uid)
        label = f"**{user_obj}** (`{uid}`)" if user_obj else f"`{uid}`"
        await ctx.send(f"🚫 Blacklisted {label}. Reason: *{reason}*")

    @commands.command(name="unblacklist", aliases=["unbl"])
    async def unblacklist_cmd(self, ctx: commands.Context, user_target: str):
        """Remove a user from the blacklist."""
        uid = _extract_id(user_target)
        if not uid:
            await ctx.send("⚠️ Usage: `!unblacklist <user_id>`")
            return
        removed = await db.remove_blacklist(uid)
        if removed:
            await ctx.send(f"✅ Removed `{uid}` from the blacklist.")
        else:
            await ctx.send(f"ℹ️ `{uid}` was not in the blacklist.")

    # ------------------------------------------------------------------
    # 8. !r2stats
    # ------------------------------------------------------------------

    @commands.command(name="r2stats", aliases=["r2"])
    async def r2stats_cmd(self, ctx: commands.Context):
        """Show Cloudflare R2 bucket file count and storage usage statistics."""
        async with ctx.typing():
            try:
                stats = await get_r2_stats()
            except Exception as e:
                await ctx.send(f"⚠️ Could not fetch R2 stats: `{e}`")
                return

            free_tier_bytes = 10 * 1024 * 1024 * 1024  # 10 GB Cloudflare R2 Free Tier
            pct_used = (stats["total_bytes"] / free_tier_bytes) * 100

            embed = discord.Embed(
                title="☁️ Cloudflare R2 Storage Stats",
                color=discord.Color.orange(),
            )
            embed.add_field(name="Bucket", value=f"`{stats['bucket']}`", inline=True)
            embed.add_field(name="Total Files", value=f"**{stats['total_objects']:,}**", inline=True)
            embed.add_field(
                name="Storage Used",
                value=f"**{_format_bytes(stats['total_bytes'])}** ({pct_used:.2f}% of 10 GB)",
                inline=True,
            )
            embed.add_field(
                name="File Breakdown",
                value=(
                    f"🎞️ GIFs: **{stats['gif_count']:,}**\n"
                    f"🖼️ PNGs: **{stats['png_count']:,}**\n"
                    f"📦 Other: **{stats['other_count']:,}**"
                ),
                inline=False,
            )
            if stats["latest_key"] and stats["latest_modified"]:
                ts = int(stats["latest_modified"].timestamp())
                embed.add_field(
                    name="Latest Upload",
                    value=f"`{stats['latest_key']}` (<t:{ts}:R>)",
                    inline=False,
                )
            embed.set_footer(text=f"Public URL: {stats['public_url']}")
            await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(OwnerCog(bot))

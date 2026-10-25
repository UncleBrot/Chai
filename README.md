# Chai — Discord Bot

A multi-purpose Discord bot built with `discord.py`, featuring anonymous confessions, image & GIF manipulation, meme/canvas generators, User-Installable App ("Use Anywhere") support, owner DM controls, and Cloudflare R2 integration. Deployable in one click via Portainer.

---

## Features

### 💌 Anonymous Confessions
- Users can submit anonymous confessions via `/confess` or the persistent **Confess** button.
- Supports optional **image attachments** inside both confessions and replies — confession images are automatically uploaded to and permanently saved on **Cloudflare R2**.
- Confessions are posted as rich embeds in a configured channel.
- Anonymous **replies** are supported via threads — the OP is labeled `(OP)` if they reply to their own confession.
- **Per-Server Moderator Logging** — a configurable log channel per server records who sent each confession and reply (with jump links).
- **Global Confession Logging (`G_CONFESSION_LOG`)** — logs every confession and reply across all servers to a central dev channel with the user, user ID, server name, server ID, confession text, jump link, and R2-hosted confession image.

### 🌍 User-Installable App ("Use Anywhere")
- Supports both **Guild Install** (added to a server) and **User Install** (**Add to My Apps**).
- Users who install the bot to their account can run image, meme, and fun slash commands — as well as right-click message context menus (**Make it a Tweet** and **Convert to GIF**) — inside **1-on-1 DMs, Group DMs, and any server**.

### 🖼️ Image & GIF Commands
- **`!caption` / `/caption`** — Adds a classic white-bar caption to any static image or animated GIF.
- **`!gif` / `/gif` / Right-Click → Apps → Convert to GIF** — Converts any image to a GIF.
- Outputs are automatically compressed, uploaded to **Cloudflare R2**, and sent as embeds (falling back to Discord file attachments if R2 is not configured).
- Every image conversion is logged to `img_log_id` with the user, user ID, command source, server name, and server ID.

### 🎭 Memes, Canvas & Fun Commands
- **Tweets (`!tweet` / `/tweet` / Right-Click → Apps → Make it a Tweet)** — Turns any message, reply, or text into a dark/light X (Twitter) screenshot with the user's avatar, display name, and username.
- **Canvas & Animated Overlays** — `/petpet`, `/amongus` (animated ejection GIF), `/wasted`, `/horny`, `/stupid`, `/simpcard`, and `/wanted`.
- **Persistent `/ship` Compatibility** — Ships two users together with a custom compatibility banner; the percentage is permanently saved in SQLite so the same pair always gets their destined score.
- **Social & Animals** — `/rizz`, `/roast`, `/corporate`, `/cat`, `/duck`, `/capybara`.
- **Emoji Stealer (`!steal` / `/steal`)** — Steals any custom static or animated emoji from another server and adds it to yours.

### 👑 Owner-Exclusive DM Control Panel & Global Blacklist
- Locked to `OWNER_ID` and **restricted strictly to the bot's Direct Messages**:
  - Puppet messaging (`!say`, `!dm`, and webhook `!impersonate`)
  - Server management (`!servers`, `!channels`, `!getinvite`, `!leaveserver`)
  - Global user blacklist (`!blacklist add/remove/list`, `!unblacklist`) enforced across all prefix commands, slash commands, and confession buttons
  - Live Cloudflare R2 storage analytics (`!r2stats`)
- All incoming DMs sent to the bot by users are logged to `BOT_LOGS_ID` so the owner can view and reply to them with `!dm`.

---

## Setup Instructions

### 1. Prerequisites
- Create a Discord Bot on the [Discord Developer Portal](https://discord.com/developers/applications).
- Get your bot token.
- Enable the **Message Content** privileged intent (required for prefix commands like `!caption`, `!gif`, `!tweet`, `!steal`, and owner DM commands).
- **Enable User Install ("Use Anywhere")**:
  1. In the Developer Portal, go to the **Installation** tab.
  2. Under **Installation Contexts**, check both **User Install** and **Guild Install**.
  3. Under **Default Install Settings → User Install**, ensure `applications.commands` is added.
- Invite your bot with the following permissions: **Send Messages**, **Send Messages in Threads**, **Create Public Threads**, **Embed Links**, **Attach Files**, **Read Message History**, **Manage Expressions** (for `/steal`), and **Manage Webhooks** (for `!impersonate`).

### 2. Environment Variables

Copy the example file and fill in your values:
```bash
cp .env.example .env
```

| Variable | Required | Description |
|---|---|---|
| `Discord_Token` | ✅ | Your Discord bot token |
| `OWNER_ID` | ⬜ | Bot owner's Discord User ID (defaults to `762609713610620948` — **change this to your own User ID if you host the bot!**) |
| `BOT_LOGS_ID` | ⬜ | Channel ID to log all incoming DMs sent to the bot (defaults to `1563881722557239316`) |
| `G_CONFESSION_LOG` | ⬜ | Global channel ID to log all confessions & R2 confession images across all servers (defaults to `1563882927593619456`) |
| `img_log_id` | ⬜ | Channel ID to log every image/GIF conversion along with Server Name & Server ID |
| `R2_ENDPOINT_URL` | ⬜ | Cloudflare R2 endpoint URL |
| `R2_ACCESS_KEY_ID` | ⬜ | R2 API access key ID |
| `R2_SECRET_ACCESS_KEY` | ⬜ | R2 API secret access key |
| `R2_BUCKET_NAME` | ⬜ | R2 bucket name |
| `R2_PUBLIC_URL` | ⬜ | Public base URL for your R2 bucket |

> **Important for Self-Hosters:** Make sure to change `OWNER_ID` (and `BOT_LOGS_ID` / `G_CONFESSION_LOG`) in your `.env` or Portainer environment variables to your own Discord User ID and log channel IDs.
>
> R2 variables are optional. If not set, image outputs fall back to Discord file attachments.

### 3. Running with Docker / Portainer (Recommended)

1. Push this repo to GitHub.
2. In Portainer, go to **Stacks → Add stack → Repository**.
3. Paste your repository URL.
4. Add your environment variables in the **Environment variables** section.
5. Click **Deploy the stack**.

The SQLite database is stored in a persistent Docker volume (`bot-data`) and survives restarts and redeployments.

### 4. Running Locally (Python)

```bash
pip install -r requirements.txt
python bot.py
```

---

## Commands

### 💌 Confession Commands (Server Only)
| Command | Description |
|---|---|
| `/confess` | Open the anonymous confession modal (supports image upload) |
| `/setup` | *(Admin only)* Setup Confessions Channel |
| **Confess** button | Persistent button under confession messages to submit a new confession |
| **Reply** button | Persistent button to reply anonymously inside a confession thread |

### 🖼️ Image, Meme & Fun Commands (Works in Servers & DMs via User Install)
| Command | Description |
|---|---|
| `/help` / `!help` | Display the rich help embed with all public commands |
| `/caption` / `!caption <text>` | Caption an image or GIF with a classic white bar |
| `/gif` / `!gif` / **Apps → Convert to GIF** | Convert any image to a GIF (uploaded to Cloudflare R2) |
| `/tweet` / `!tweet` / **Apps → Make it a Tweet** | Turn a message, reply, or text into a dark/light Tweet |
| `/petpet [user]` | Generate an animated PetPet GIF of a user's avatar |
| `/amongus [user] [impostor]` | Eject a user into space (Among Us Impostor GIF) |
| `/wasted [user]` | Apply the GTA Wasted overlay to a user's avatar |
| `/horny [user]` | Grant a Horny License card to a user |
| `/stupid <text> [user]` | Generate an "Oh no, it's so stupid" meme |
| `/simpcard [user]` | Generate an official Simp Card for a user |
| `/wanted [user]` | Put a user on a Wanted poster |
| `/ship <user1> [user2]` | Ship two users together (compatibility score permanently saved in SQLite) |
| `/rizz <user>` | Send a pickup line to a user |
| `/roast <user>` | Roast a user |
| `/corporate` | Generate a corporate BS buzzword phrase |
| `/cat`, `/duck`, `/capybara` | Fetch a random cat, duck, or capybara photo |
| `/steal <emoji>` / `!steal <emoji>` | *(Server Only — Manage Expressions)* Steal a custom emoji from another server |

### 👑 Owner-Exclusive Commands (Bot DMs Only)
All owner commands **only work inside the bot's Direct Messages** and are locked to `OWNER_ID` (`762609713610620948` by default — change `OWNER_ID` if you host the bot yourself):

| Command | Description |
|---|---|
| `!help` / `!ownerhelp` | Show the owner-exclusive DM commands embed |
| `!say <channel_id_or_msg_link> <message>` | Make the bot send a message in a channel (or reply to a message link) |
| `!dm <user_id> <message>` | Send a DM to a user through the bot (incoming user DMs are logged to `BOT_LOGS_ID`) |
| `!impersonate <channel_id> <user_id> <message>` | Send a message in a channel using that user's display name and avatar via a temporary webhook |
| `!getinvite <server_id>` | Generate or retrieve an invite link for any server the bot is in |
| `!leaveserver <server_id>` | Force the bot to leave a server |
| `!blacklist add <user_id> [reason]` | Globally block a user from using any bot commands or confessions |
| `!blacklist remove <user_id>` / `!unblacklist <user_id>` | Remove a user from the global blacklist |
| `!blacklist list` | List all currently blacklisted users |
| `!r2stats` | View Cloudflare R2 bucket file counts and total storage usage |
| `!servers` / `!channels <server_id>` | List all connected servers and text channel IDs |
| `!sync` | Sync slash and context-menu commands globally with Discord |

---

## Project Structure

```
.
├── bot.py              # Main bot entry point, confessions, help embed & global checks
├── database.py         # Async SQLite helpers for confessions, ships & global blacklist
├── cogs/
│   ├── images.py       # Image manipulation commands (caption, gif & Convert to GIF context menu)
│   ├── fun.py          # Tweet, petpet, amongus, wasted, horny, stupid, simpcard, ship, rizz, roast, animals & steal
│   └── owner.py        # Owner-exclusive DM commands & incoming DM logger
├── utils/
│   └── r2.py           # Cloudflare R2 upload client & bucket storage analytics
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── .env.example
```

---

## About
**Made by @CaptainBrot**

- **Email:** [hello@unclebrot.xyz](mailto:hello@unclebrot.xyz)
- **Invite the Bot:** [Click here to invite](https://discord.com/oauth2/authorize?client_id=1553385571412607069)
- **GitHub Repository:** [UncleBrot/Chai](https://github.com/UncleBrot/Chai)

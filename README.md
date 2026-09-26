# Chai — Discord Bot

A multi-purpose Discord bot built with `discord.py`, featuring anonymous confessions, image manipulation tools, and Cloudflare R2 integration. Deployable in one click via Portainer.

---

## Features

### 💌 Anonymous Confessions
- Users can submit anonymous confessions via `/confess` or the persistent **Confess** button.
- Supports optional **image attachments** inside the confession popup.
- Confessions are posted as rich embeds in a configured channel.
- Anonymous **replies** are supported via threads — the OP is labeled if they reply to their own confession.
- **Moderator logging** — a configurable log channel records who sent each confession and reply (with jump links).

### 🖼️ Image Commands
- **`!caption` / `/caption`** — Adds a classic white-bar caption to any image.
- **`!gif` / `/gif`** — Converts any image to a GIF.
- Outputs are automatically uploaded to **Cloudflare R2** and sent as embeds. Falls back to a Discord file attachment if R2 is not configured.
- GIF command usage is logged to a private dev channel (configurable via `img_log_id`).

---

## Setup Instructions

### 1. Prerequisites
- Create a Discord Bot on the [Discord Developer Portal](https://discord.com/developers/applications).
- Get your bot token.
- Enable the **Message Content** privileged intent (required for prefix commands like `!caption`).
- Invite your bot with the following permissions: **Send Messages**, **Send Messages in Threads**, **Create Public Threads**, **Embed Links**, **Read Message History**.

### 2. Environment Variables

Copy the example file and fill in your values:
```bash
cp .env.example .env
```

| Variable | Required | Description |
|---|---|---|
| `Discord_Token` | ✅ | Your Discord bot token |
| `img_log_id` | ⬜ | Channel ID to log every `!gif` / `/gif` usage |
| `R2_ENDPOINT_URL` | ⬜ | Cloudflare R2 endpoint URL |
| `R2_ACCESS_KEY_ID` | ⬜ | R2 API access key ID |
| `R2_SECRET_ACCESS_KEY` | ⬜ | R2 API secret access key |
| `R2_BUCKET_NAME` | ⬜ | R2 bucket name |
| `R2_PUBLIC_URL` | ⬜ | Public base URL for your R2 bucket |

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

### Confession Commands
| Command | Description |
|---|---|
| `/confess` | Open the anonymous confession form |
| `/setup` | *(Admin only)* Configure confession and log channels |
| Confess button | Persistent button on confession messages to submit a new confession |
| Reply button | Persistent button to reply anonymously in a confession thread |

### Image Commands
| Command | Description |
|---|---|
| `!caption <text>` | Caption the replied image with a white bar |
| `/caption image: text:` | Caption an uploaded image |
| `!gif` | Convert the replied image to a GIF |
| `/gif image:` | Convert an uploaded image to a GIF |

### Owner / Dev Commands
| Command | Description |
|---|---|
| `!sync` | Sync slash commands to Discord (bot owner only) |

---

## Project Structure

```
.
├── bot.py              # Main bot entry point
├── database.py         # Async SQLite helpers for confessions
├── cogs/
│   └── images.py       # Image manipulation commands (caption, gif)
├── utils/
│   └── r2.py           # Cloudflare R2 upload client
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

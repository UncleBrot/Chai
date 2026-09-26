# Anonymous Confessions Discord Bot

A feature-rich Discord bot that allows users to send anonymous confessions and reply to them anonymously using modern Discord UI components (Modals and Buttons). 

## Features
- **Anonymous Confessions**: Users can click a "Confess" button to open a modal and submit their confession anonymously.
- **Anonymous Replies**: Users can reply to existing confessions anonymously. The original poster (OP) is highlighted if they reply to their own confession thread.
- **Image Attachments**: Natively supports uploading images directly inside the Discord popup (Modal) for both confessions and replies.
- **Persistent Storage**: Uses an async SQLite database to store confession mappings, threads, and settings.
- **Logging**: Configurable logging channel for moderators to keep track of who sent which confession (for moderation purposes).
- **Docker Ready**: Includes a `Dockerfile` and `docker-compose.yml` for seamless deployment to Portainer or any Docker environment.

## Setup Instructions

### 1. Prerequisites
- Create a Discord Bot on the [Discord Developer Portal](https://discord.com/developers/applications).
- Get your bot token.
- Invite your bot to your server with `Administrator` permissions (or permissions to manage messages, threads, and webhooks).

### 2. Configuration
The bot requires an environment variable for its token. You can copy the example file:
```bash
cp .env.example .env
```
Then edit `.env` and set your token:
```env
Discord_Token=your_bot_token_here
```

### 3. Running with Docker / Portainer (Recommended)
This repository includes a `docker-compose.yml` which makes it easy to run using Docker or Portainer Stacks.

1. Ensure Docker is installed.
2. Run the following command in the root directory:
   ```bash
   docker-compose up -d
   ```
   
*(If using Portainer, you can simply create a new Stack, select this repository, and add `Discord_Token` in the Environment Variables section).*

The SQLite database will be safely stored in the persistent `bot-data` volume.

### 4. Running Manually (Python)
If you prefer not to use Docker:
1. Ensure Python 3.11+ is installed.
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Run the bot:
   ```bash
   python bot.py
   ```

## Usage
Once the bot is in your server, an Administrator must run the `/setup` slash command to configure the confession and log channels. 

After setup, users can use the `/confess` slash command (or the static Confess button) to open the submission form.

## Contributing
Since this is a public repository, please ensure that you **never** commit your `.env` file or `confessions.db` to version control. These files are ignored by `.gitignore` by default.

---

## About
**Made by @CaptainBrot**

- **Email:** [hello@unclebrot.xyz](mailto:hello@unclebrot.xyz)
- **Invite the Bot:** [Click here to invite](https://discord.com/oauth2/authorize?client_id=1553385571412607069)
- **GitHub Repository:** [UncleBrot/Chai](https://github.com/UncleBrot/Chai)

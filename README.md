# Discord Voice Time Tracker Bot

Tracks how long users stay in voice channels, stores totals in SQLite, and reports:

- Per-user total voice time
- Per-user time per voice channel
- Server leaderboard by total voice time

## Features

- Automatic tracking when a user joins, leaves, or switches voice channels
- Persistent storage in `voice_time.db`
- Slash commands:
  - `/mytime [member]`
  - `/channeltime <channel> [member]`
  - `/voicetop [limit]`
  - `/rangetime <start> <end> [member] [channel]`

## 1) Create a Discord Bot in Developer Portal

1. Open https://discord.com/developers/applications
2. Click **New Application** and give it a name.
3. Go to **Bot** tab and click **Add Bot**.
4. Privileged intents are **not required** for this bot.
5. Click **Reset Token** (or copy token) and keep it safe.

## 2) Invite Bot to Your Server

1. In the application, open **OAuth2 -> URL Generator**.
2. Scopes:
   - `bot`
   - `applications.commands`
3. Bot permissions (minimum recommended):
   - View Channels
   - Read Message History
   - Send Messages
4. Open the generated URL and invite the bot to your server.

## 3) Local Setup (Windows PowerShell)

Run in this project folder:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```

Edit `.env` and set:

```env
DISCORD_TOKEN=your_real_token_here
# Optional while testing: commands appear faster for one server
# TEST_GUILD_ID=123456789012345678
```

How to get `TEST_GUILD_ID`:

1. In Discord: User Settings -> Advanced -> enable **Developer Mode**
2. Right-click your server -> **Copy Server ID**

## 4) Run the Bot

```powershell
python bot.py
```

## 4.1) Deploy with Docker

Build image:

```powershell
docker build -t discord-voice-time-bot .
```

Run container (use your existing `.env`, keep DB persistent in local folder):

```powershell
docker run -d --name discord-voice-time-bot \
  --restart unless-stopped \
  -e VOICE_DB_PATH=/data/voice_time.db \
  --env-file .env \
  -v ${PWD}:/data \
  discord-voice-time-bot
```

Useful commands:

```powershell
# View logs
docker logs -f discord-voice-time-bot

# Restart bot
docker restart discord-voice-time-bot

# Stop and remove container
docker rm -f discord-voice-time-bot
```

Notes for Docker:

- If `voice_time.db` does not exist yet, create it first in PowerShell:

```powershell
New-Item -ItemType File -Path .\voice_time.db -Force
```

- If your Docker setup has issues with `${PWD}` on Windows, use absolute path instead:

```powershell
docker run -d --name discord-voice-time-bot \
  --restart unless-stopped \
  -e VOICE_DB_PATH=/data/voice_time.db \
  --env-file .env \
  -v D:/tools/calculate_time_in_discord:/data \
  discord-voice-time-bot
```

## 4.2) CI/CD with GitHub Actions (GHCR + VPS)

Two workflows are included:

- `.github/workflows/build-and-push.yml`: build image, push to GHCR with your configured tag, then auto-trigger deploy
- `.github/workflows/deploy.yml`: SSH to VPS, pull image by provided tag, redeploy container

Required GitHub repository secrets:

- `VPS_HOST`: VPS public IP/domain
- `VPS_USER`: SSH user (for example `root` or `ubuntu`)
- `VPS_SSH_KEY`: private SSH key content (PEM/OpenSSH)
- `VPS_PORT`: SSH port (usually `22`)
- `DISCORD_TOKEN`: bot token used in production

Deploy path in workflow:

- Preferred: `/opt/discord-voice-time-bot` (when sudo is available)
- Fallback: `$HOME/discord-voice-time-bot` (when sudo is not available)

Important:

- GHCR package must be set to **Public** so VPS can `docker pull` without GHCR credentials.
- Configure your default image tag in repository variable `IMAGE_TAG` (Settings -> Secrets and variables -> Actions -> Variables).
- If `IMAGE_TAG` is empty, build workflow falls back to `latest`.
- You can also run build manually and pass `image_tag` input to override tag for that run.

Flow:

1. Push to `main` -> **Build and Push** publishes `ghcr.io/<owner>/<repo>:<tag>` where `<tag>` is from `IMAGE_TAG` (or fallback `latest`)
2. Build workflow auto-calls **Deploy** with that exact tag
3. Deploy workflow pulls `:<tag>` and updates VPS container

Manual run:

- Build and Push: optional `image_tag` input
- Deploy: required `image_tag` input

If the bot starts correctly, you will see a log like:

- `Logged in as ...`

## 5) Use Commands in Discord

- `/mytime`
- `/mytime @Someone`
- `/channeltime channel:@General`
- `/channeltime channel:@General member:@Someone`
- `/voicetop`
- `/voicetop limit:20`
- `/rangetime start:2026-03-01 00:00 end:2026-03-19 23:59`
- `/rangetime start:2026-03-01T00:00:00Z end:2026-03-19T23:59:59Z member:@Someone`

`/rangetime` note:

- Time is interpreted in Vietnam timezone (UTC+7).
- Accepted datetime formats:
  - `YYYY-MM-DD HH:MM`
  - `YYYY-MM-DD HH:MM:SS`
  - ISO8601, for example `2026-03-19T23:59:59Z`

## Notes

- Data is saved in `voice_time.db`.
- If the bot restarts while users are already in voice channels, tracking resumes from bot startup time for those active sessions.
- Slash command registration can take time globally; setting `TEST_GUILD_ID` makes testing faster.

## Troubleshooting

- Commands not showing:
  - Ensure bot was invited with `applications.commands` scope.
  - Wait a bit for global sync, or set `TEST_GUILD_ID` and restart.
- No tracking data:
  - Ensure user joins a voice channel after bot is online.
  - Ensure the bot can see the server and channels.
- `Missing DISCORD_TOKEN in .env file`:
  - Check `.env` exists and has a valid token.
- `Improper token has been passed` or `401 Unauthorized`:
  - The token in `.env` is invalid/expired or not a Bot Token.
  - In Discord Developer Portal -> your app -> Bot -> **Reset Token**, then paste the new token into `.env`.
- `sqlite3.OperationalError: attempt to write a readonly database`:
  - Usually caused by host file/folder permission on mounted SQLite path.
  - SSH to VPS and run:

```bash
APP_DIR=/opt/discord-voice-time-bot
sudo chown -R $USER:$USER "$APP_DIR"
sudo chmod 775 "$APP_DIR"
sudo chmod 664 "$APP_DIR/voice_time.db"
docker rm -f discord-voice-time-bot || true
```

- Then rerun deploy workflow.

# Discord moderation bot and dashboard

## Run

Use Python 3.11 or 3.12. Music playback also requires FFmpeg on PATH.

```bash
pip install -r requirements.txt
```

Create `.env` (do not commit it):

```dotenv
DISCORD_TOKEN=your-discord-bot-token
DASH_TOKEN=your-long-random-dashboard-token
FLASK_SECRET=your-long-random-session-secret
PORT=5000
```

Set the real channel, category and role IDs in `main.py`. Enable the Message Content, Server Members and Presence intents in the Discord Developer Portal.

```bash
python main.py
```

Open `http://localhost:5000/?token=YOUR_DASH_TOKEN`. Submit the dashboard token with forms, or use `Authorization: Bearer YOUR_DASH_TOKEN` for API requests. Without `DASH_TOKEN`, the dashboard returns 503. Serve it through HTTPS for remote access. Uploads are limited to 25 MiB.

The dashboard sends through the connected Discord bot; `web.py` is an app factory, not a standalone webhook server.

## Tests

```bash
python -m unittest discover -s tests -v
```

Database and JSON files hold runtime data. Back them up before upgrades and keep them outside public source repositories.

# Discord bot — Web dashboard & media endpoints

This repo now includes a minimal Flask web dashboard to post media notifications and upload short clips to a Discord channel using a webhook.

Quick start

1. Install requirements:
```bash
pip install -r requirements.txt
```

2. Configure webhook: set environment variable `DISCORD_WEBHOOK_URL` or add `{"webhook_url": "https://discord.com/api/webhooks/.."}` to `server_data.json`.

3. Run the dashboard:
```bash
python web.py
```

Then open http://localhost:5000 to use the dashboard.

Notes
- This dashboard uses a Discord webhook to post messages and upload files. If you want deeper integration (control the bot instance, send typed messages, or manage voice/audio), we can integrate endpoints with `main.py` or run an internal API that your bot listens to.
- For production, add authentication and disable `debug` mode.

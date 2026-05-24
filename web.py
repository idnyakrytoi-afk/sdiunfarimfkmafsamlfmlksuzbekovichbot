from flask import Flask, render_template, request, redirect, url_for, flash, Response
import os
import json
from functools import wraps
import io


def token_auth_required():
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            token = os.environ.get('DASH_TOKEN')
            if not token:
                # no token configured -> allow access
                return f(*args, **kwargs)
            # prefer Authorization header `Bearer <token>`
            auth_hdr = request.headers.get('Authorization', '')
            if auth_hdr.startswith('Bearer '):
                given = auth_hdr.split(' ', 1)[1]
                if given == token:
                    return f(*args, **kwargs)
            # allow token via form for browser POSTs
            given_form = request.form.get('token') or request.args.get('token')
            if given_form == token:
                return f(*args, **kwargs)
            return Response('Unauthorized', 401)
        return wrapped
    return decorator


def create_app(bot=None):
    app = Flask(__name__)
    app.secret_key = os.environ.get('FLASK_SECRET', 'change-me')

    @app.route('/')
    @token_auth_required()
    def index():
        # load server_data.json to show feeds
        try:
            with open('server_data.json', 'r', encoding='utf-8') as f:
                cfg = json.load(f)
        except FileNotFoundError:
            cfg = {}
        feeds = cfg.get('feeds', [])
        dash_channel = os.environ.get('DASH_CHANNEL') or cfg.get('feed_channel_id')
        token_present = bool(os.environ.get('DASH_TOKEN'))
        # load scheduled items
        try:
            with open('scheduled.json', 'r', encoding='utf-8') as f:
                scheduled = json.load(f)
        except FileNotFoundError:
            scheduled = []
        return render_template('index.html', feeds=feeds, dash_channel=dash_channel, token_present=token_present, scheduled=scheduled)

    @app.route('/notify', methods=['POST'])
    @token_auth_required()
    def notify():
        # send a message via the bot to configured channel
        channel_id = request.form.get('channel_id') or os.environ.get('DASH_CHANNEL')
        title = request.form.get('title')
        url = request.form.get('url')
        content = f"{title}\n{url}" if title else url
        if not channel_id:
            flash('Channel not configured', 'error')
            return redirect(url_for('index'))
        try:
            ch = bot.get_channel(int(channel_id))
            if not ch:
                flash('Channel not found', 'error')
                return redirect(url_for('index'))
            bot.loop.create_task(ch.send(content))
            flash('Notification queued', 'success')
        except Exception as e:
            flash(f'Error: {e}', 'error')
        return redirect(url_for('index'))

    @app.route('/clip', methods=['POST'])
    @token_auth_required()
    def clip():
        channel_id = request.form.get('channel_id') or os.environ.get('DASH_CHANNEL')
        file = request.files.get('file')
        message = request.form.get('message', '')
        if not channel_id or not file:
            flash('Missing channel or file', 'error')
            return redirect(url_for('index'))
        try:
            ch = bot.get_channel(int(channel_id))
            if not ch:
                flash('Channel not found', 'error')
                return redirect(url_for('index'))
            # read content into memory then send via bot
            data = file.read()
            filename = file.filename
            # Send in background
            async def _send():
                from discord import File
                fp = io.BytesIO(data)
                await ch.send(content=message, file=File(fp, filename=filename))
            bot.loop.create_task(_send())
            flash('Clip upload queued', 'success')
        except Exception as e:
            flash(f'Error: {e}', 'error')
        return redirect(url_for('index'))

    @app.route('/schedule', methods=['POST'])
    @token_auth_required()
    def schedule():
        # schedule a message; body should contain iso datetime and message and channel_id
        iso = request.form.get('datetime')
        message = request.form.get('message')
        channel_id = request.form.get('channel_id') or os.environ.get('DASH_CHANNEL')
        if not iso or not message or not channel_id:
            flash('Missing fields', 'error')
            return redirect(url_for('index'))
        try:
            try:
                with open('scheduled.json', 'r', encoding='utf-8') as f:
                    scheduled = json.load(f)
            except FileNotFoundError:
                scheduled = []
            scheduled.append({'datetime': iso, 'message': message, 'channel_id': int(channel_id)})
            with open('scheduled.json', 'w', encoding='utf-8') as f:
                json.dump(scheduled, f, ensure_ascii=False, indent=2)
            flash('Scheduled', 'success')
        except Exception as e:
            flash(f'Error: {e}', 'error')
        return redirect(url_for('index'))

    return app

    @app.route('/feeds/add', methods=['POST'])
    @token_auth_required()
    def add_feed():
        url = request.form.get('feed_url')
        channel_id = request.form.get('channel_id') or os.environ.get('DASH_CHANNEL')
        if not url or not channel_id:
            flash('Missing feed URL or channel', 'error')
            return redirect(url_for('index'))
        try:
            try:
                with open('server_data.json', 'r', encoding='utf-8') as f:
                    cfg = json.load(f)
            except FileNotFoundError:
                cfg = {}
            feeds = cfg.get('feeds', [])
            feeds.append({'url': url, 'channel_id': int(channel_id)})
            cfg['feeds'] = feeds
            with open('server_data.json', 'w', encoding='utf-8') as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
            flash('Feed added', 'success')
        except Exception as e:
            flash(f'Error: {e}', 'error')
        return redirect(url_for('index'))

    @app.route('/feeds/delete', methods=['POST'])
    @token_auth_required()
    def delete_feed():
        url = request.form.get('feed_url')
        if not url:
            flash('Missing feed URL', 'error')
            return redirect(url_for('index'))
        try:
            try:
                with open('server_data.json', 'r', encoding='utf-8') as f:
                    cfg = json.load(f)
            except FileNotFoundError:
                cfg = {}
            feeds = cfg.get('feeds', [])
            feeds = [f for f in feeds if f.get('url') != url]
            cfg['feeds'] = feeds
            with open('server_data.json', 'w', encoding='utf-8') as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
            flash('Feed removed', 'success')
        except Exception as e:
            flash(f'Error: {e}', 'error')
        return redirect(url_for('index'))

    @app.route('/schedule/delete', methods=['POST'])
    @token_auth_required()
    def delete_schedule():
        idx = request.form.get('index')
        if idx is None:
            flash('Missing index', 'error')
            return redirect(url_for('index'))
        try:
            idx = int(idx)
            try:
                with open('scheduled.json', 'r', encoding='utf-8') as f:
                    scheduled = json.load(f)
            except FileNotFoundError:
                scheduled = []
            if 0 <= idx < len(scheduled):
                scheduled.pop(idx)
                with open('scheduled.json', 'w', encoding='utf-8') as f:
                    json.dump(scheduled, f, ensure_ascii=False, indent=2)
                flash('Scheduled item removed', 'success')
            else:
                flash('Index out of range', 'error')
        except Exception as e:
            flash(f'Error: {e}', 'error')
        return redirect(url_for('index'))

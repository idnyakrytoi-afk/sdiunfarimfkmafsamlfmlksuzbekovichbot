import discord
from discord.ext import commands
import datetime
import re
import urllib.parse
import aiohttp
import asyncio
import io
import random
import json
import yt_dlp
import os
import uuid
from dotenv import load_dotenv
import threading
import time
from web import create_app
import xml.etree.ElementTree as ET

import database # Импортируем наш новый модуль

load_dotenv() # Загружаем переменные из .env файла

intents = discord.Intents.default()
intents.message_content = True
intents.members = True # Нужно для работы с ролями
intents.presences = True # Нужно для отслеживания кастомных статусов
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

# JSON функции удалены в пользу SQLite

# 2. Временные словари
voice_sessions = {}
user_warnings = {}
invite_warnings = {}
spam_warnings = {}
spam_tracker = {}
music_queues = {}

# 3. Настройки и ID каналов
LOG_CHANNEL_ID = 1500133161173254196
TICKET_CATEGORY_ID = 1500197078217789440
WHITELISTED_USERS = ["gs_uzbekovi4"]
our_server_invite = "https://discord.gg/ur5tPZ7umw"
cached_server_context = "Ты крутой бот-помощник."

LEVEL_ROLES = {"хороший участник": 1500210317140037702}
ROLE_REQUIREMENTS = {"хороший участник": {"messages": 100, "voice_hours": 10.0}}

# Магазин: ключ = item_id. Для предметов указываем type="item", для ролей type="role" и role_id
SHOP_ITEMS = {
    "vip": {"name": "VIP", "price": 1000, "type": "role", "role_id": 1506636782614220920},
    "pizza": {"name": "Пицца 🍕", "price": 150, "type": "role", "role_id": 1506636616947466300},
    "color": {"name": "", "price": 500, "type": "item"},
    "lockpick": {"name": "Отмычка 🔓", "price": 300, "type": "item"},
    # Недвижимость / источники пассивного дохода
    "house_small": {"name": "Маленький дом 🏠", "price": 5000, "type": "property", "income": 50},
    "stall": {"name": "Лавка торговца 🏪", "price": 2500, "type": "property", "income": 25},
    # Страховка уменьшает штрафы при неудачах
    "insurance": {"name": "Страховка 🛡️", "price": 400, "type": "service"},
    # Лутбокс — при покупке сразу даёт случайный предмет/монеты
    "lootbox": {"name": "Лутбокс 🎁", "price": 500, "type": "lootbox"}
}
# Локальное хранилище инвентарей/допол. данных (оставлено для совместимости)
users_data = {}
USERS_DATA_FILE = "users_data.json"

def load_user_data():
    global users_data
    try:
        with open(USERS_DATA_FILE, 'r', encoding='utf-8') as f:
            users_data = json.load(f)
    except Exception:
        users_data = {}

async def save_user_data(data):
    try:
        def _write():
            with open(USERS_DATA_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        await asyncio.to_thread(_write)
    except Exception as e:
        print(f"[ERROR] save_user_data: {e}")

REP_COOLDOWN = 43200 # 12 часов в секундах
WORK_COOLDOWN = 86400 # 24 часа
SPAM_TIME = 5
SPAM_LIMIT = 4
TAX_RATE = 0.02  # налог при переводах
INTEREST_RATE_DAILY = 0.001  # 0.1% в день
MARKETPLACE_FILE = "marketplace.json"

URL_REGEX = re.compile(r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+')
TRANSLATE_TABLE = str.maketrans("0123456789@$*!", "оиезабгтвяасхи")

# Доп. таблица для замены латиницы на похожие кириллические буквы
LATIN_TO_CYR = str.maketrans({
    'a':'а', 'b':'в', 'c':'с', 'e':'е', 'h':'н', 'i':'і', 'k':'к', 'm':'м', 'n':'п', 'o':'о', 'p':'р', 'r':'г', 's':'ѕ', 't':'т', 'x':'х', 'y':'у',
})

def normalize_for_filter(text: str) -> tuple:
    """Возвращает два варианта нормализованной строки:
    1) только буквенно-цифровые символы после базовой транслитерации
    2) тот же вариант, но с латинскими буквами заменёнными на похожие кириллические
    Это помогает ловить обходы типа "х у й", "d i s c o r d" и простую лейт‑маскировку.
    """
    s = text.lower()
    # Сначала применяем простую переводную таблицу (цифры/символы -> буквы)
    s = s.translate(TRANSLATE_TABLE)
    # Удаляем всё кроме букв и цифр (склеиваем разделённые буквы)
    compact = re.sub(r'[^a-zA-Z0-9а-яА-ЯёЁіїґєґ]', '', s)
    # Версия с попыткой привести латиницу к похожим кириллическим буквам
    mapped = compact.translate(LATIN_TO_CYR)
    return compact, mapped

def contains_bad_word(text: str) -> bool:
    compact, mapped = normalize_for_filter(text)
    # Проверяем оба варианта (обычный и с латиницей->кириллице)
    for variant in (compact, mapped):
        # явная проверка корней
        for bad in BAD_WORDS:
            if bad in variant:
                # проверяем исключения
                if any(exc in variant for exc in EXCEPTIONS):
                    continue
                return True
    return False

def contains_advertising(text: str) -> bool:
    # Нормализуем и склеиваем символы, чтобы поймать обходы типа d i s c o r d
    compact, mapped = normalize_for_filter(text)
    # Проверяем распространённые маркеры рекламы/приглашений
    ad_markers = ['discord', 'discordgg', 'invite', 'joinserver', 'дискорд', 'дискордгг']
    for variant in (compact, mapped):
        for mark in ad_markers:
            if mark in variant:
                return True
    return False

# Базовые корни мата для фильтра
BAD_WORDS = [
    "хуй", "хуе", "хуё", "хуи", "пизд", "ебат", "ебан", "ебуч", "ебыв", "ебли", "ебл",
    "бляд", "блять", "бля", "шлюх", "мудак", "пидор", "пидар", "пидр", "залуп", 
    "гондон", "гандон", "долбо", "выбля", "сука", "суку", "суки", "сучк",
    "хер", "манда", "чмо"
]

# Слова, в которых содержатся плохие корни, но они нормальные (чтобы избежать ложных срабатываний)
EXCEPTIONS = [
    "оскорбл", "рублей", "колебан", "потреблен", "хлеба", "сукно", "барсук",
    "хлебн", "стебел", "мебел", "гребл", "сабля", "корабля", "ансамбл", "влюбл",
    "рубля", "грабл", "ослабл", "страхуй", "психуй", "штрихуй", "команда", 
    "мандарин", "употреблен", "амёба", "учеба", "херсон", "хирург", "парикмахер",
    "херсонес", "саламандра", "чмок", "дубл", "шабл", "табл", "оглобл"
]

# 4. Клиенты API (Используется встроенный aiohttp клиент вместо openai)
class AsyncChatClient:
    class _Response:
        def __init__(self, content):
            class _Message:
                def __init__(self, c): self.content = c
            class _Choice:
                def __init__(self, c): self.message = _Message(c)
            self.choices = [_Choice(content)]

    def __init__(self, api_key, base_url):
        self.api_key = api_key
        self.base_url = base_url.rstrip('/')
        self.chat = self._Chat(self)

    class _Chat:
        def __init__(self, client):
            self.client = client
            self.completions = self._Completions(client)

        class _Completions:
            def __init__(self, client):
                self.client = client

            async def create(self, model, messages, extra_headers=None, max_tokens=None):
                url = f"{self.client.base_url}/chat/completions"
                headers = {
                    "Authorization": f"Bearer {self.client.api_key}",
                    "Content-Type": "application/json"
                }
                if extra_headers:
                    headers.update(extra_headers)
                
                payload = {"model": model, "messages": messages}
                if max_tokens:
                    payload["max_tokens"] = max_tokens
                    
                async with aiohttp.ClientSession() as session:
                    async with session.post(url, headers=headers, json=payload) as resp:
                        if resp.status != 200:
                            err_text = await resp.text()
                            raise Exception(f"Ошибка API ({resp.status}): {err_text}")
                        data = await resp.json()
                        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                        return AsyncChatClient._Response(content)

ai_client = AsyncChatClient(api_key=os.getenv("OPENROUTER_API_KEY"), base_url="https://openrouter.ai/api/v1")
hf_client = AsyncChatClient(api_key=os.getenv("HF_API_KEY"), base_url="https://api-inference.huggingface.co/v1/")
HF_API_KEY = os.getenv("HF_API_KEY")

# 5. Музыка
ytdl = yt_dlp.YoutubeDL({'format': 'bestaudio', 'noplaylist': True})
FFMPEG_OPTIONS = {'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5', 'options': '-vn'}
# ====================================


async def scheduler_loop():
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            try:
                with open('scheduled.json', 'r', encoding='utf-8') as f:
                    scheduled = json.load(f)
            except FileNotFoundError:
                scheduled = []

            now = datetime.datetime.now(datetime.timezone.utc)
            remaining = []
            for item in scheduled:
                try:
                    dt = datetime.datetime.fromisoformat(item['datetime'])
                    # if naive, assume UTC
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=datetime.timezone.utc)
                    if dt <= now:
                        ch = bot.get_channel(int(item['channel_id']))
                        if ch:
                            await ch.send(item['message'])
                    else:
                        remaining.append(item)
                except Exception:
                    # malformed entry -> skip
                    continue

            # rewrite file with remaining items
            with open('scheduled.json', 'w', encoding='utf-8') as f:
                json.dump(remaining, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[scheduler] error: {e}")
        await asyncio.sleep(30)


async def feed_poller_loop():
    await bot.wait_until_ready()
    POLL_INTERVAL = int(os.environ.get('FEED_POLL_INTERVAL', 120))  # seconds
    while not bot.is_closed():
        try:
            try:
                with open('server_data.json', 'r', encoding='utf-8') as f:
                    cfg = json.load(f)
            except FileNotFoundError:
                cfg = {}

            feeds = cfg.get('feeds', [])
            state = cfg.get('feeds_state', {})

            async with aiohttp.ClientSession() as session:
                for feed in feeds:
                    url = feed.get('url')
                    channel_id = feed.get('channel_id') or cfg.get('feed_channel_id')
                    if not url or not channel_id:
                        continue
                    try:
                        async with session.get(url, timeout=20) as resp:
                            if resp.status != 200:
                                continue
                            text = await resp.text()
                            root = ET.fromstring(text.encode('utf-8'))
                            # find first video id
                            vid = None
                            # YouTube feeds use {http://www.w3.org/2005/Atom}entry
                            ns = {'atom': 'http://www.w3.org/2005/Atom', 'yt': 'http://www.youtube.com/xml/schemas/2015'}
                            entry = root.find('atom:entry', ns)
                            if entry is not None:
                                vid_el = entry.find('yt:videoId', ns)
                                if vid_el is not None:
                                    vid = vid_el.text
                                else:
                                    link = entry.find('atom:link', ns)
                                    if link is not None:
                                        href = link.attrib.get('href')
                                        vid = href.split('v=')[-1]
                            else:
                                # fallback to RSS <item>
                                item = root.find('channel/item')
                                if item is not None:
                                    guid = item.find('guid')
                                    if guid is not None:
                                        vid = guid.text

                            last = state.get(url)
                            if vid and vid != last:
                                # new video
                                ch = bot.get_channel(int(channel_id))
                                if ch:
                                    # build link
                                    link = None
                                    if vid.startswith('http'):
                                        link = vid
                                    else:
                                        link = f"https://youtu.be/{vid}"
                                    await ch.send(f"Новое видео: {link}")
                                state[url] = vid
                    except Exception:
                        # per-feed error (request/parse) — skip this feed
                        continue

            cfg['feeds_state'] = state
            with open('server_data.json', 'w', encoding='utf-8') as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[feed_poller] error: {e}")
        await asyncio.sleep(POLL_INTERVAL)


@bot.event
async def on_ready():
    # Инициализируем базу данных при запуске
    await database.init_db()

    # Загружаем локальные данные пользователей (inventory, properties и т.д.)
    try:
        load_user_data()
        print(f"Загружены users_data из {USERS_DATA_FILE} ({len(users_data)} записей)")
    except Exception as e:
        print(f"[WARN] не удалось загрузить users_data: {e}")

    # Устанавливаем кастомный статус для модерации
    activity = discord.Activity(type=discord.ActivityType.watching, name="за порядком на сервере | /help")
    await bot.change_presence(status=discord.Status.online, activity=activity)
    
    # Синхронизируем слэш-команды с Discord
    try:
        synced = await bot.tree.sync()
        print(f"🔄 Синхронизировано {len(synced)} слэш-команд.")
    except Exception as e:
        print(f"❌ Ошибка синхронизации команд: {e}")
    # Запускаем ротатор presence в фоне
    bot.loop.create_task(presence_rotator())
    # Запускаем планировщик и поллер фидов
    bot.loop.create_task(scheduler_loop())
    bot.loop.create_task(feed_poller_loop())

    print(f"✅ Бот успешно запущен и авторизован как {bot.user}!")
    print("Ожидаю сообщений и команд...")

@bot.event
async def on_presence_update(before, after):
    # Проверка на случай если конфигурация еще не загрузилась
    if 'LEVEL_ROLES' not in globals():
        return
        
    good_member_role_id = LEVEL_ROLES.get("хороший участник")
    
# Проверяем, настроена ли роль
    if not good_member_role_id:
        return

    good_member_role = after.guild.get_role(good_member_role_id)
    if not good_member_role:
        # Эта ошибка будет появляться, пока не вставишь реальный ID
        print(f"[ERROR] Роль 'хороший участник' с ID {good_member_role_id} не найдена.")
        return


# ----------------- UI / Embeds helpers -----------------
def _format_table_codeblock(headers, rows, max_width=40):
    # Выравниваем колонки в моноширинном блоке
    cols = len(headers)
    widths = [len(h) for h in headers]
    for r in rows:
        for i, c in enumerate(r):
            widths[i] = max(widths[i], len(str(c)))
    # Ограничение ширины
    widths = [min(w, max_width) for w in widths]

    def fit(val, w):
        s = str(val)
        if len(s) > w:
            return s[:w-1] + '…'
        return s.ljust(w)

    header_line = ' | '.join(fit(h, widths[i]) for i, h in enumerate(headers))
    sep_line = '-+-'.join('-'*widths[i] for i in range(cols))
    row_lines = []
    for r in rows:
        row_lines.append(' | '.join(fit(r[i], widths[i]) for i in range(cols)))

    table = '\n'.join([header_line, sep_line] + row_lines)
    return f"```\n{table}\n```"

def create_table_embed(title: str, headers, rows, color=discord.Color.blue(), footer=None):
    emb = discord.Embed(title=title, color=color)
    table_block = _format_table_codeblock(headers, rows)
    emb.description = table_block
    if footer:
        emb.set_footer(text=footer)
    return emb


class TablePaginator(discord.ui.View):
    def __init__(self, ctx, title, headers, rows, page_size=8, color=discord.Color.blue()):
        super().__init__(timeout=120)
        self.ctx = ctx
        self.title = title
        self.headers = headers
        self.rows = rows
        self.page_size = page_size
        self.color = color
        self.page = 0

    def _get_page_embed(self):
        start = self.page * self.page_size
        chunk = self.rows[start:start + self.page_size]
        footer = f"Страница {self.page+1}/{(len(self.rows)-1)//self.page_size + 1}"
        return create_table_embed(self.title, self.headers, chunk, color=self.color, footer=footer)

    @discord.ui.button(label='◀', style=discord.ButtonStyle.secondary)
    async def prev(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.ctx.author:
            return await interaction.response.send_message('Только инициатор может листать.', ephemeral=True)
        if self.page > 0:
            self.page -= 1
            try:
                await interaction.response.edit_message(embed=self._get_page_embed(), view=self)
            except (discord.NotFound, discord.HTTPException):
                # fallback: редактируем исходное сообщение напрямую
                try:
                    await interaction.message.edit(embed=self._get_page_embed(), view=self)
                except Exception:
                    pass

    @discord.ui.button(label='▶', style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.ctx.author:
            return await interaction.response.send_message('Только инициатор может листать.', ephemeral=True)
        if (self.page+1) * self.page_size < len(self.rows):
            self.page += 1
            try:
                await interaction.response.edit_message(embed=self._get_page_embed(), view=self)
            except (discord.NotFound, discord.HTTPException):
                try:
                    await interaction.message.edit(embed=self._get_page_embed(), view=self)
                except Exception:
                    pass


# ----------------- Presence rotator -----------------
async def presence_rotator():
    await bot.wait_until_ready()
    idx = 0
    while not bot.is_closed():
        try:
            # Сначала показываем количество гильдий
            guild_count = len(bot.guilds)
            top = await database.get_top_users('balance', limit=3)
            top_names = []
            for r in top:
                try:
                    member = None
                    for g in bot.guilds:
                        m = g.get_member(int(r['user_id']))
                        if m:
                            member = m
                            break
                    if member:
                        top_names.append(member.display_name)
                    else:
                        top_names.append(r['name'] if r['name'] else '—')
                except Exception:
                    top_names.append('—')

            variants = []
            if top_names:
                variants.append(f"Топ: {', '.join(top_names[:2])}")
            variants.append(f"Серверов: {guild_count}")
            variants.append("Экономика: /shop /balance")

            text = variants[idx % len(variants)]
            activity = discord.Activity(type=discord.ActivityType.playing, name=text)
            await bot.change_presence(activity=activity)
            idx += 1
        except Exception as e:
            print(f"[presence_rotator] error: {e}")
        await asyncio.sleep(30)


    # Ищем ссылку в кастомном статусе
    has_link_in_status = False
    invite_link = globals().get('our_server_invite', 'discord.gg/') # Можно заменить на конкретную ссылку
    for activity in after.activities:
        if isinstance(activity, discord.CustomActivity) and activity.name and invite_link in activity.name:
            has_link_in_status = True
            break

    # Выдаем роль, если есть ссылка и еще нет роли
    if has_link_in_status and good_member_role not in after.roles:
        try:
            await after.add_roles(good_member_role, reason="Добавил ссылку на сервер в статус")
            print(f"[INFO] Выдал роль 'хороший участник' пользователю {after.display_name}")
        except discord.Forbidden:
            print(f"[ERROR] Нет прав для выдачи роли 'хороший участник' пользователю {after.display_name}")

    # Забираем роль, если ссылки больше нет, а роль есть
    elif not has_link_in_status and good_member_role in after.roles:
        try:
            await after.remove_roles(good_member_role, reason="Убрал ссылку на сервер из статуса")
            print(f"[INFO] Забрал роль 'хороший участник' у пользователя {after.display_name}")
        except discord.Forbidden:
            print(f"[ERROR] Нет прав для снятия роли 'хороший участник' у пользователя {after.display_name}")

@bot.event
async def on_member_update(before, after):
    # Логирование мутов (таймаутов), выданных модераторами через меню самого Discord
    if not before.is_timed_out() and after.is_timed_out():
        log_channel = bot.get_channel(LOG_CHANNEL_ID)
        if log_channel:
            moderator = "Неизвестно"
            reason = "Не указана"
            is_bot = False
            
            try:
                # Ищем запись в аудит-логах, чтобы узнать, кто выдал таймаут
                async for entry in after.guild.audit_logs(limit=5, action=discord.AuditLogAction.member_update):
                    if entry.target.id == after.id and hasattr(entry.after, 'timed_out_until'):
                        if entry.user.id == bot.user.id:
                            is_bot = True # Мут выдан самим ботом
                        else:
                            moderator = entry.user.mention
                            reason = entry.reason or "Не указана"
                        break
            except Exception:
                pass

            # Если мут выдан ботом (командами /mute или авто-модом), пропускаем, чтобы не дублировать логи
            if is_bot:
                return

            embed = discord.Embed(title="🔇 Выдан мут (через интерфейс)", color=discord.Color.yellow())
            embed.add_field(name="Нарушитель", value=after.mention, inline=True)
            embed.add_field(name="Модератор", value=moderator, inline=True)
            embed.add_field(name="Снимут", value=discord.utils.format_dt(after.timed_out_until, style="R"), inline=False)
            embed.add_field(name="Причина", value=reason, inline=False)
            await log_channel.send(embed=embed)

    # Логирование досрочного снятия мута через интерфейс
    elif before.is_timed_out() and not after.is_timed_out():
        log_channel = bot.get_channel(LOG_CHANNEL_ID)
        if log_channel:
            moderator = "Неизвестно"
            is_bot = False
            
            try:
                async for entry in after.guild.audit_logs(limit=5, action=discord.AuditLogAction.member_update):
                    if entry.target.id == after.id and hasattr(entry.after, 'timed_out_until'):
                        if entry.user.id == bot.user.id:
                            is_bot = True
                        else:
                            moderator = entry.user.mention
                        break
            except Exception:
                pass

            if is_bot:
                return

            embed = discord.Embed(title="🔊 Снят мут (через интерфейс)", color=discord.Color.green())
            embed.add_field(name="Пользователь", value=after.mention, inline=True)
            embed.add_field(name="Модератор", value=moderator, inline=True)
            await log_channel.send(embed=embed)

@bot.hybrid_command(name="clear", description="Очистить чат от сообщений")
@commands.has_permissions(manage_messages=True)
async def clear(ctx, amount: int):
    # Очищаем чат: amount + 1, чтобы удалить и само сообщение с командой !clear
    deleted = await ctx.channel.purge(limit=amount + 1)
    await ctx.send(f'✅ Очищено {len(deleted) - 1} сообщений.', delete_after=5.0)

@clear.error
async def clear_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send(f"{ctx.author.mention}, у тебя нет прав на удаление сообщений!")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"{ctx.author.mention}, пожалуйста, укажи количество сообщений. Пример: `!clear 10`")

@bot.hybrid_command(name="kick", description="Выгнать участника с сервера")
@commands.has_permissions(kick_members=True)
async def kick(ctx, member: discord.Member, *, reason: str = "Не указана"):
    try:
        await member.kick(reason=reason)
        await ctx.send(f'✅ {member.mention} был выгнан. Причина: {reason}')

        log_channel = bot.get_channel(LOG_CHANNEL_ID)
        if log_channel:
            embed = discord.Embed(title="👢 Участник выгнан (Kick)", color=discord.Color.orange())
            embed.add_field(name="Модератор", value=ctx.author.mention, inline=True)
            embed.add_field(name="Нарушитель", value=member.mention, inline=True)
            embed.add_field(name="Причина", value=reason, inline=False)
            await log_channel.send(embed=embed)
    except discord.Forbidden:
        await ctx.send("❌ У меня нет прав на это действие (роль пользователя выше моей).")

@bot.hybrid_command(name="ban", description="Забанить участника на сервере")
@commands.has_permissions(ban_members=True)
async def ban(ctx, member: discord.Member, *, reason: str = "Не указана"):
    try:
        await member.ban(reason=reason)
        await ctx.send(f'✅ {member.mention} был забанен. Причина: {reason}')

        log_channel = bot.get_channel(LOG_CHANNEL_ID)
        if log_channel:
            embed = discord.Embed(title="🔨 Участник забанен (Ban)", color=discord.Color.red())
            embed.add_field(name="Модератор", value=ctx.author.mention, inline=True)
            embed.add_field(name="Нарушитель", value=member.mention, inline=True)
            embed.add_field(name="Причина", value=reason, inline=False)
            await log_channel.send(embed=embed)
    except discord.Forbidden:
        await ctx.send("❌ У меня нет прав на это действие.")

@bot.hybrid_command(name="mute", description="Выдать мут участнику")
@commands.has_permissions(moderate_members=True)
async def mute(ctx, member: discord.Member, minutes: int, *, reason: str = "Не указана"):
    duration = datetime.timedelta(minutes=minutes)
    try:
        await member.timeout(duration, reason=reason)
        await ctx.send(f'✅ {member.mention} получил мут на {minutes} минут. Причина: {reason}')

        log_channel = bot.get_channel(LOG_CHANNEL_ID)
        if log_channel:
            embed = discord.Embed(title="🔇 Выдан мут (Mute)", color=discord.Color.yellow())
            embed.add_field(name="Модератор", value=ctx.author.mention, inline=True)
            embed.add_field(name="Нарушитель", value=member.mention, inline=True)
            embed.add_field(name="Время", value=f"{minutes} мин.", inline=True)
            embed.add_field(name="Причина", value=reason, inline=False)
            await log_channel.send(embed=embed)
    except discord.Forbidden:
        await ctx.send("❌ У меня нет прав на это действие.")

@bot.hybrid_command(name="unmute", description="Снять мут с участника")
@commands.has_permissions(moderate_members=True)
async def unmute(ctx, member: discord.Member):
    try:
        await member.timeout(None, reason="Снятие мута модератором")
        await ctx.send(f'✅ С {member.mention} был снят мут.')

        log_channel = bot.get_channel(LOG_CHANNEL_ID)
        if log_channel:
            embed = discord.Embed(title="🔊 Снят мут (Unmute)", color=discord.Color.green())
            embed.add_field(name="Модератор", value=ctx.author.mention, inline=True)
            embed.add_field(name="Пользователь", value=member.mention, inline=True)
            await log_channel.send(embed=embed)
    except discord.Forbidden:
        await ctx.send("❌ У меня нет прав на это действие.")

@bot.hybrid_command(name="warn", description="Выдать предупреждение участнику")
@commands.has_permissions(manage_messages=True)
async def warn(ctx, member: discord.Member, *, reason: str = "Не указана"):
    user_id = member.id
    if user_id not in user_warnings:
        user_warnings[user_id] = 1
    else:
        user_warnings[user_id] += 1

    await ctx.send(f'⚠️ {member.mention} получил предупреждение ({user_warnings[user_id]}/2). Причина: {reason}')

    log_channel = bot.get_channel(LOG_CHANNEL_ID)
    if log_channel:
        embed = discord.Embed(title="⚠️ Выдано предупреждение (Warn)", color=discord.Color.orange())
        embed.add_field(name="Модератор", value=ctx.author.mention, inline=True)
        embed.add_field(name="Нарушитель", value=member.mention, inline=True)
        embed.add_field(name="Счетчик", value=f"{user_warnings[user_id]}/2", inline=True)
        embed.add_field(name="Причина", value=reason, inline=False)
        await log_channel.send(embed=embed)

    if user_warnings[user_id] == 1:
        try:
            await member.send(f"⚠️ Вы получили предупреждение на сервере **{ctx.guild.name}**.\n**Причина:** {reason}\n*При получении 2 предупреждений вы будете автоматически замучены на 10 минут.*")
        except discord.Forbidden:
            pass

    # Если достигнут лимит (2 варна) - даем мут
    if user_warnings[user_id] >= 2:
        duration = datetime.timedelta(minutes=10)
        try:
            await member.timeout(duration, reason="Превышен лимит предупреждений")
            await ctx.send(f'⛔ {member.mention} получил автоматический мут на 10 минут за 2 предупреждения.')
            
            if log_channel:
                auto_embed = discord.Embed(title="⛔ Автоматический мут (2/2 Warns)", color=discord.Color.red())
                auto_embed.add_field(name="Нарушитель", value=member.mention, inline=True)
                auto_embed.add_field(name="Время", value="10 мин.", inline=True)
                auto_embed.add_field(name="Причина", value="Превышен лимит предупреждений", inline=False)
                await log_channel.send(embed=auto_embed)
                
            user_warnings[user_id] = 0 # Сбрасываем варны после мута
        except discord.Forbidden:
            await ctx.send("❌ Не удалось выдать автоматический мут (нет прав).")

@bot.hybrid_command(name="userinfo", description="Показать информацию о пользователе")
async def userinfo(ctx, member: discord.Member = None):
    # Если пользователь не указан, берем того, кто вызвал команду
    member = member or ctx.author
    
    # Форматируем даты
    created_at = member.created_at.strftime("%d.%m.%Y %H:%M")
    joined_at = member.joined_at.strftime("%d.%m.%Y %H:%M")
    
    # Собираем роли (кроме @everyone)
    roles = [role.mention for role in member.roles if role.name != "@everyone"]
    roles_str = " ".join(roles) if roles else "Нет ролей"
    reputation = users_data.get(str(member.id), {}).get('reputation', 0)

    embed = discord.Embed(title=f"Информация о пользователе {member.display_name}", color=member.color if member.color != discord.Color.default() else discord.Color.blue())
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="ID аккаунта", value=member.id, inline=False)
    embed.add_field(name="Аккаунт создан", value=created_at, inline=True)
    embed.add_field(name="Присоединился к серверу", value=joined_at, inline=True)
    embed.add_field(name="⭐ Репутация", value=reputation, inline=True)
    embed.add_field(name=f"Роли ({len(roles)})", value=roles_str, inline=False)
    
    await ctx.send(embed=embed)

@bot.hybrid_command(name="lock", description="Заблокировать канал (запретить писать)")
@commands.has_permissions(manage_channels=True)
async def lock(ctx, channel: discord.TextChannel = None):
    channel = channel or ctx.channel
    # Забираем право отправки сообщений у роли @everyone
    await channel.set_permissions(ctx.guild.default_role, send_messages=False)
    
    embed = discord.Embed(title="🔒 Канал заблокирован", description=f"Канал {channel.mention} был закрыт модератором {ctx.author.mention}.", color=discord.Color.red())
    await ctx.send(embed=embed)

@bot.hybrid_command(name="unlock", description="Разблокировать канал (разрешить писать)")
@commands.has_permissions(manage_channels=True)
async def unlock(ctx, channel: discord.TextChannel = None):
    channel = channel or ctx.channel
    # Возвращаем дефолтное право отправки сообщений роли @everyone
    # Установка значения None убирает явный запрет/разрешение
    await channel.set_permissions(ctx.guild.default_role, send_messages=None)
    
    embed = discord.Embed(title="🔓 Канал разблокирован", description=f"Канал {channel.mention} снова открыт для общения.", color=discord.Color.green())
    await ctx.send(embed=embed)

@bot.hybrid_command(name="rep", description="Повысить репутацию участника (+1)")
async def rep(ctx, member: discord.Member):
    giver_id = str(ctx.author.id)
    target_id = str(member.id)

    if giver_id == target_id:
        await ctx.send("❌ Вы не можете изменить свою собственную репутацию!", ephemeral=True)
        return

    if member.bot:
        await ctx.send("❌ Нельзя изменять репутацию ботов!", ephemeral=True)
        return

    now_ts = datetime.datetime.now(datetime.timezone.utc).timestamp()

    if giver_id not in users_data:
        users_data[giver_id] = {'messages': 0, 'voice_seconds': 0, 'last_message_ts': 0, 'balance': 0, 'last_work_ts': 0, 'reputation': 0, 'last_rep_ts': 0}

    last_rep_ts = users_data[giver_id].get('last_rep_ts', 0)

    if now_ts - last_rep_ts < REP_COOLDOWN:
        remaining_time = int(REP_COOLDOWN - (now_ts - last_rep_ts))
        hours = remaining_time // 3600
        minutes = (remaining_time % 3600) // 60
        await ctx.send(f"⏳ Вы сможете изменить репутацию снова через **{hours} ч. и {minutes} мин.**", ephemeral=True)
        return

    if target_id not in users_data:
        users_data[target_id] = {'messages': 0, 'voice_seconds': 0, 'last_message_ts': 0, 'balance': 0, 'last_work_ts': 0, 'reputation': 0, 'last_rep_ts': 0}

    users_data[target_id]['reputation'] = users_data[target_id].get('reputation', 0) + 1
    users_data[giver_id]['last_rep_ts'] = now_ts
    await save_user_data(users_data)

    await ctx.send(f"👍 Вы повысили репутацию {member.mention}! Его новая репутация: **{users_data[target_id]['reputation']}**.")

@bot.hybrid_command(name="unrep", description="Понизить репутацию участника (-1)")
async def unrep(ctx, member: discord.Member):
    giver_id = str(ctx.author.id)
    target_id = str(member.id)

    if giver_id == target_id:
        await ctx.send("❌ Вы не можете изменить свою собственную репутацию!", ephemeral=True)
        return

    if member.bot:
        await ctx.send("❌ Нельзя изменять репутацию ботов!", ephemeral=True)
        return

    now_ts = datetime.datetime.now(datetime.timezone.utc).timestamp()

    if giver_id not in users_data:
        users_data[giver_id] = {'messages': 0, 'voice_seconds': 0, 'last_message_ts': 0, 'balance': 0, 'last_work_ts': 0, 'reputation': 0, 'last_rep_ts': 0}

    last_rep_ts = users_data[giver_id].get('last_rep_ts', 0)

    if now_ts - last_rep_ts < REP_COOLDOWN:
        remaining_time = int(REP_COOLDOWN - (now_ts - last_rep_ts))
        hours = remaining_time // 3600
        minutes = (remaining_time % 3600) // 60
        await ctx.send(f"⏳ Вы сможете изменить репутацию снова через **{hours} ч. и {minutes} мин.**", ephemeral=True)
        return

    if target_id not in users_data:
        users_data[target_id] = {'messages': 0, 'voice_seconds': 0, 'last_message_ts': 0, 'balance': 0, 'last_work_ts': 0, 'reputation': 0, 'last_rep_ts': 0}

    users_data[target_id]['reputation'] = users_data[target_id].get('reputation', 0) - 1
    users_data[giver_id]['last_rep_ts'] = now_ts
    await save_user_data(users_data)

    await ctx.send(f"👎 Вы понизили репутацию {member.mention}! Его новая репутация: **{users_data[target_id]['reputation']}**.")

# --- КОМАНДЫ И ЛОГИКА СИСТЕМЫ УРОВНЕЙ ---

async def check_and_award_roles(member: discord.Member):
    user_id = str(member.id)
    if user_id not in users_data:
        return

    user_stats = users_data[user_id]
    user_messages = user_stats.get('messages', 0)
    user_voice_hours = user_stats.get('voice_seconds', 0) / 3600

    for role_name, requirements in ROLE_REQUIREMENTS.items():
        role_id = LEVEL_ROLES.get(role_name)
        placeholder_ids = [
            111111111111111111, 
            222222222222222222, 
            333333333333333333, 
            444444444444444444, 
        ]
        if not role_id or role_id in placeholder_ids: # Пропускаем плейсхолдеры
            continue

        role = member.guild.get_role(role_id)
        if not role:
            print(f"[ERROR] Роль '{role_name}' с ID {role_id} не найдена на сервере.")
            continue

        # Если роль уже есть, пропускаем
        if role in member.roles:
            continue

        # Проверяем, выполнено ли ОДНО из условий
        if user_messages >= requirements.get('messages', float('inf')) or \
           user_voice_hours >= requirements.get('voice_hours', float('inf')):
            try:
                await member.add_roles(role, reason=f"Достигнут уровень '{role_name}'")
                print(f"[LEVEL UP] Пользователь {member.display_name} получил роль '{role_name}'.")
                
                log_channel = bot.get_channel(LOG_CHANNEL_ID)
                if log_channel:
                    embed = discord.Embed(title="🎉 Новый уровень!", color=discord.Color.gold())
                    embed.description = f"{member.mention} достиг нового уровня и получил роль **{role.name}**!"
                    await log_channel.send(embed=embed)

            except discord.Forbidden:
                print(f"[ERROR] Нет прав для выдачи роли '{role_name}' пользователю {member.display_name}.")
            except Exception as e:
                print(f"[ERROR] Не удалось выдать роль: {e}")

@bot.event
async def on_voice_state_update(member, before, after):
    # Игнорируем ботов
    if member.bot:
        return

    # --- Логирование голосовых каналов ---
    log_channel = bot.get_channel(LOG_CHANNEL_ID)
    if log_channel:
        if before.channel is None and after.channel is not None:
            embed = discord.Embed(title="🔊 Вход в голосовой канал", color=discord.Color.green())
            embed.add_field(name="Пользователь", value=member.mention, inline=True)
            embed.add_field(name="Канал", value=after.channel.mention, inline=True)
            await log_channel.send(embed=embed)
        elif before.channel is not None and after.channel is None:
            embed = discord.Embed(title="🔇 Выход из голосового канала", color=discord.Color.red())
            embed.add_field(name="Пользователь", value=member.mention, inline=True)
            embed.add_field(name="Канал", value=before.channel.mention, inline=True)
            await log_channel.send(embed=embed)
        elif before.channel is not None and after.channel is not None and before.channel != after.channel:
            embed = discord.Embed(title="🔄 Перемещение между каналами", color=discord.Color.blue())
            embed.add_field(name="Пользователь", value=member.mention, inline=False)
            embed.add_field(name="Из канала", value=before.channel.mention, inline=True)
            embed.add_field(name="В канал", value=after.channel.mention, inline=True)
            await log_channel.send(embed=embed)
    # --- Конец логирования ---

    user_id = str(member.id)
    now = datetime.datetime.now(datetime.timezone.utc)

    # Определяем, был ли пользователь в "засчитываемом" состоянии (в войсе и не в АФК)
    was_countable = before.channel and not before.afk
    is_countable = after.channel and not after.afk

    # Сценарий 1: Пользователь вошел в засчитываемый канал (из ниоткуда или из АФК)
    if not was_countable and is_countable:
        voice_sessions[user_id] = now
        print(f"[VOICE] {member.display_name} зашел в войс (или вышел из АФК). Сессия началась.")
    
    # Сценарий 2: Пользователь покинул засчитываемый канал (вышел или ушел в АФК)
    elif was_countable and not is_countable:
        if user_id in voice_sessions:
            join_time = voice_sessions.pop(user_id)
            duration_seconds = (now - join_time).total_seconds()

            user = await database.get_user(user_id)
            new_voice_seconds = user['voice_seconds'] + duration_seconds
            
            await database.update_user(
                user_id,
                voice_seconds=new_voice_seconds,
                name=member.display_name,
                avatar=str(member.display_avatar.url)
            )
            print(f"[VOICE] {member.display_name} провел в войсе {duration_seconds:.0f} секунд. Всего: {new_voice_seconds:.0f} сек. Сессия окончена.")

            # Проверяем, не заслужил ли он роль
            await check_and_award_roles(member)

@bot.hybrid_command(name="rank", description="Показать ваш уровень и прогресс активности")
async def rank(ctx, member: discord.Member = None):
    member = member or ctx.author
    user_id = str(member.id)

    if user_id not in users_data or (users_data[user_id].get('messages', 0) == 0 and users_data[user_id].get('voice_seconds', 0) == 0):
        await ctx.send(f"У пользователя {member.mention} пока нет статистики.", ephemeral=True)
        return

    user_stats = users_data[user_id]
    messages = user_stats.get('messages', 0)
    voice_hours = user_stats.get('voice_seconds', 0) / 3600

    embed = discord.Embed(title=f"Статистика активности {member.display_name}", color=member.color if member.color != discord.Color.default() else discord.Color.blue())
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="✉️ Отправлено сообщений", value=f"{messages}", inline=True)
    embed.add_field(name="🎙️ Время в войсе", value=f"{voice_hours:.1f} ч.", inline=True)

    # Логика для отображения прогресса до следующей роли
    next_role_str = "Все доступные роли получены!"
    # Находим роли, которых у пользователя еще нет
    missing_roles = [
        (name, req) for name, req in ROLE_REQUIREMENTS.items() 
        if member.guild.get_role(LEVEL_ROLES.get(name)) not in member.roles
    ]
    
    if missing_roles:
        # Сортируем по сообщениям, чтобы найти ближайшую цель
        missing_roles.sort(key=lambda x: x[1].get('messages', float('inf')))
        next_role_name, next_req = missing_roles[0]
        
        msg_req = next_req.get('messages', float('inf'))
        voice_req = next_req.get('voice_hours', float('inf'))

        # Формируем строку прогресса
        progress_lines = []
        if msg_req != float('inf'):
            msg_progress = (messages / msg_req) * 100
            progress_lines.append(f"**Сообщения:** {messages} / {msg_req} ({msg_progress:.1f}%)")
        
        if voice_req != float('inf'):
            voice_progress = (voice_hours / voice_req) * 100
            progress_lines.append(f"**Время в войсе:** {voice_hours:.1f} / {voice_req} ч. ({voice_progress:.1f}%)")

        next_role_str = f"**Следующая роль: {next_role_name}**\n" + "\n".join(progress_lines)


    embed.add_field(name="🚀 Прогресс", value=next_role_str, inline=False)
    await ctx.send(embed=embed)

@bot.hybrid_command(name="leaderboard", description="Показать топ активных пользователей")
async def leaderboard(ctx):
    top_by_msg = await database.get_top_users('messages', 10)
    top_by_voice = await database.get_top_users('voice_seconds', 10)

    if not top_by_msg and not top_by_voice:
        await ctx.send("Пока нет данных для отображения лидерборда.", ephemeral=True)
        return

    embed = discord.Embed(title="🏆 Таблица лидеров сервера", description="Самые активные участники нашего сервера. Так держать! 🚀\n\u200b", color=discord.Color.gold())

    # Устанавливаем аватарку топ-1 пользователя по сообщениям
    if top_by_msg:
        top_user_data = top_by_msg[0]
        top_user_id = top_user_data['user_id']
        top_user = ctx.guild.get_member(int(top_user_id))
        if top_user:
            embed.set_thumbnail(url=top_user.display_avatar.url)
        elif top_user_data['avatar']:
            embed.set_thumbnail(url=top_user_data['avatar'])

    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]

    # Формируем топ по сообщениям
    msg_leaderboard_str = ""
    for i, data in enumerate(top_by_msg):
        user_id = data['user_id']
        user = ctx.guild.get_member(int(user_id))
        user_name = user.mention if user else f"**{data['name'] or f'ID: {user_id}'}**"
        messages = data['messages']
        if messages > 0:
            msg_leaderboard_str += f"{medals[i]} {user_name} ➔ **{messages}** смс\n"
    
    embed.add_field(name="💬 Топ по сообщениям", value=msg_leaderboard_str or "Пока никто не писал сообщения.", inline=False)

    # Формируем топ по времени в войсе
    voice_leaderboard_str = ""
    for i, data in enumerate(top_by_voice):
        user_id = data['user_id']
        user = ctx.guild.get_member(int(user_id))
        user_name = user.mention if user else f"**{data['name'] or f'ID: {user_id}'}**"
        voice_seconds = data['voice_seconds']
        if voice_seconds > 0:
            hours = int(voice_seconds // 3600)
            minutes = int((voice_seconds % 3600) // 60)
            time_str = f"**{hours}** ч. **{minutes}** мин." if hours > 0 else f"**{minutes}** мин."
            voice_leaderboard_str += f"{medals[i]} {user_name} ➔ {time_str}\n"

    embed.add_field(name="🎙️ Топ по времени в войсе", value=voice_leaderboard_str or "Пока никто не сидел в войсе.", inline=False)
    
    embed.set_footer(text="Спасибо за вашу активность на сервере! ❤️")

    await ctx.send(embed=embed)

@bot.hybrid_command(name="leaderboard_coins", description="Показать топ самых богатых пользователей сервера")
async def leaderboard_coins(ctx):
    await ctx.defer()
    # Табличный лидерборд по чистому состоянию
    rows = await database.get_all_users()
    if not rows:
        await ctx.send("Пока нет данных для отображения лидерборда.", ephemeral=True)
        return

    table_rows = []
    for r in rows:
        uid = r['user_id']
        bal = r['balance'] or 0
        bank = r['bank'] or 0
        props = users_data.get(uid, {}).get('properties', [])
        props_value = sum([p.get('value', 0) for p in props])
        net = bal + bank + props_value
        name = r['name'] if r['name'] else f'ID:{uid}'
        table_rows.append((name, str(net), str(bal), str(bank), str(props_value)))

    # Сортируем и формируем строки
    table_rows = sorted(table_rows, key=lambda x: int(x[1]), reverse=True)
    headers = ["Ник", "Состояние", "Наличные", "Банк", "Недвижимость"]

    paginator = TablePaginator(ctx, "💰 Топ по чистому состоянию", headers, table_rows, page_size=8, color=discord.Color.gold())
    embed = paginator._get_page_embed()
    await ctx.send(embed=embed, view=paginator)

@bot.hybrid_command(name="weather", description="Показать погоду в указанном городе")
async def weather(ctx, *, city: str):
    await ctx.defer()
    try:
        encoded_city = urllib.parse.quote(city)
        url = f"https://wttr.in/{encoded_city}?format=j1"
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    await ctx.send(f"❌ Не удалось найти город '{city}'.", ephemeral=True)
                    return
                data = await resp.json()
                
        current = data['current_condition'][0]
        temp = current['temp_C']
        feels_like = current['FeelsLikeC']
        desc = current['weatherDesc'][0]['value']
        humidity = current['humidity']
        wind = current['windspeedKmph']

        embed = discord.Embed(title=f"🌤 Погода: {city.title()}", color=discord.Color.blue())
        embed.add_field(name="Состояние", value=desc, inline=False)
        embed.add_field(name="Температура", value=f"{temp}°C (Ощущается как {feels_like}°C)", inline=True)
        embed.add_field(name="Влажность", value=f"{humidity}%", inline=True)
        embed.add_field(name="Ветер", value=f"{wind} км/ч", inline=True)
        
        await ctx.send(embed=embed)
    except Exception as e:
        await ctx.send(f"❌ Ошибка при получении погоды: {e}", ephemeral=True)

@bot.hybrid_command(name="translate", description="Перевести текст на русский язык")
async def translate(ctx, *, text: str):
    await ctx.defer()
    try:
        prompt = f"Переведи следующий текст на русский язык. В ответе напиши ТОЛЬКО перевод, без лишних слов и объяснений:\n\n{text}"
        response = await ai_client.chat.completions.create(
            model="meta-llama/llama-3.3-70b-instruct:free", # Используем стабильную бесплатную модель от Meta
            messages=[{"role": "user", "content": prompt}],
            extra_headers={"HTTP-Referer": "https://discord.gg/ur5tPZ7umw", "X-Title": "Discord Bot"}
        )
        translation = response.choices[0].message.content[:4096]
        embed = discord.Embed(title="🌐 Перевод", description=translation, color=discord.Color.blue())
        await ctx.send(embed=embed)
    except Exception as e:
        await ctx.send(f"❌ Ошибка перевода: {e}", ephemeral=True)

def play_next(ctx):
    guild_id = ctx.guild.id
    if guild_id in music_queues and len(music_queues[guild_id]) > 0:
        track = music_queues[guild_id].pop(0)
        try:
            source = discord.FFmpegPCMAudio(track['url'], **FFMPEG_OPTIONS)
            ctx.voice_client.play(source, after=lambda e: bot.loop.call_soon_threadsafe(play_next, ctx))
            bot.loop.create_task(ctx.send(f"🎶 Сейчас играет: **{track['title']}**"))
        except Exception as e:
            bot.loop.create_task(ctx.send(f"❌ Ошибка при воспроизведении следующего трека: {e}"))
            bot.loop.call_soon_threadsafe(play_next, ctx) # Пробуем следующий трек при ошибке (безопасно)

@bot.hybrid_command(name="play", description="Включить музыку с YouTube или добавить в очередь")
async def play(ctx, *, search: str):
    await ctx.defer()
    if not ctx.author.voice:
        await ctx.send("❌ Вы должны находиться в голосовом канале!", ephemeral=True)
        return
    
    channel = ctx.author.voice.channel
    voice_client = ctx.voice_client

    if voice_client is None:
        voice_client = await channel.connect()
    elif voice_client.channel != channel:
        await voice_client.move_to(channel)

    try:
        loop = asyncio.get_event_loop()
        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(search, download=False))
        if 'entries' in data:
            data = data['entries'][0]

        guild_id = ctx.guild.id
        if guild_id not in music_queues:
            music_queues[guild_id] = []
            
        track = {'url': data['url'], 'title': data['title']}
        
        if voice_client.is_playing() or voice_client.is_paused():
            music_queues[guild_id].append(track)
            await ctx.send(f"⏳ Добавлено в очередь: **{data['title']}**")
        else:
            source = discord.FFmpegPCMAudio(track['url'], **FFMPEG_OPTIONS)
            voice_client.play(source, after=lambda e: bot.loop.call_soon_threadsafe(play_next, ctx))
            await ctx.send(f"🎶 Сейчас играет: **{data['title']}**")
    except Exception as e:
        await ctx.send(f"❌ Ошибка воспроизведения (возможно, нужен FFmpeg): {e}", ephemeral=True)

@bot.hybrid_command(name="stop", description="Остановить музыку и очистить очередь")
async def stop(ctx):
    guild_id = ctx.guild.id
    if guild_id in music_queues:
        music_queues[guild_id].clear() # Очищаем очередь

    if ctx.voice_client:
        ctx.voice_client.stop()
        await ctx.voice_client.disconnect()
        await ctx.send("🛑 Музыка остановлена, очередь очищена, бот покинул канал.")
    else:
        await ctx.send("❌ Я сейчас не в голосовом канале.", ephemeral=True)

@bot.hybrid_command(name="skip", description="Пропустить текущий трек")
async def skip(ctx):
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.stop() # Остановка вызовет after callback и автоматически запустит следующий трек
        await ctx.send("⏭ Трек пропущен.")
    else:
        await ctx.send("❌ Сейчас ничего не играет.", ephemeral=True)

@bot.hybrid_command(name="imagine", description="Сгенерировать крутую картинку нейросетью FLUX")
async def imagine(ctx, *, prompt: str):
    # Сообщаем дискорду, что бот "думает", так как генерация занимает несколько секунд
    await ctx.defer()
    
    # Будем использовать бесплатную и очень качественную модель FLUX.1-schnell с Hugging Face
    API_URL = "https://api-inference.huggingface.co/models/black-forest-labs/FLUX.1-schnell"
    headers = {"Authorization": f"Bearer {HF_API_KEY}"}
    payload = {"inputs": prompt}
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(API_URL, headers=headers, json=payload) as resp:
                print(f"[IMAGE] Hugging Face API status: {resp.status}")
                if resp.status == 503:
                    await ctx.send("⏳ Модель сейчас загружается на серверах Hugging Face. Пожалуйста, подождите секунд 20-30 и попробуйте снова!", ephemeral=True)
                    return
                elif resp.status != 200:
                    error_text = await resp.text()
                    print(f"[IMAGE] Hugging Face API error: {resp.status} - {error_text}")
                    await ctx.send(f"❌ Произошла ошибка на сервере генерации (код {resp.status}). Попробуйте позже.", ephemeral=True)
                    return
                
                image_bytes = await resp.read()
                print(f"[IMAGE] Получено {len(image_bytes)} байт изображения")
        
        # Создаем файл для отправки в Discord прямо из памяти
        image_file = discord.File(io.BytesIO(image_bytes), filename="flux_image.png")
        
        embed = discord.Embed(title="🎨 Результат генерации (FLUX)", description=f"**Запрос:** {prompt}", color=discord.Color.purple())
        embed.set_image(url="attachment://flux_image.png")
        embed.set_footer(text=f"Сгенерировал: {ctx.author.display_name}")
        
        await ctx.send(file=image_file, embed=embed)
    except Exception as e:
        print(f"[IMAGE] Exception: {e}")
        await ctx.send(f"❌ Произошла ошибка при генерации картинки: {e}", ephemeral=True)

@bot.hybrid_command(name="ask_cobuddy", description="Задать вопрос бесплатной нейросети Cobuddy от Baidu")
async def ask_cobuddy(ctx, *, question: str):
    await ctx.defer() # Бот будет "думать", пока ждет ответ
    try:
        response = await ai_client.chat.completions.create(
            model="baidu/cobuddy:free",
            messages=[{"role": "user", "content": question}],
            extra_headers={"HTTP-Referer": "https://discord.gg/ur5tPZ7umw", "X-Title": "Discord Bot"}
        )
        embed = discord.Embed(title="🤖 Ответ от Cobuddy (Baidu)", description=response.choices[0].message.content, color=discord.Color.blue())
        await ctx.send(embed=embed)
    except Exception as e:
        await ctx.send(f"❌ Произошла ошибка при обращении к нейросети: {e}", ephemeral=True)

@bot.hybrid_command(name="ask_nemotron", description="Задать вопрос бесплатной нейросети Nemotron от Nvidia")
async def ask_nemotron(ctx, *, question: str):
    await ctx.defer() # Бот будет "думать", пока ждет ответ
    try:
        response = await ai_client.chat.completions.create(
            model="nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
            messages=[{"role": "user", "content": question}],
            extra_headers={"HTTP-Referer": "https://discord.gg/ur5tPZ7umw", "X-Title": "Discord Bot"}
        )
        # Ограничиваем длину ответа, если он слишком большой для карточки
        response_text = response.choices[0].message.content[:4096]
        embed = discord.Embed(title="🤖 Ответ от Nemotron (Nvidia)", description=response_text, color=discord.Color.green())
        await ctx.send(embed=embed)
    except Exception as e:
        await ctx.send(f"❌ Произошла ошибка при обращении к нейросети: {e}", ephemeral=True)

@bot.hybrid_command(name="ask_hf", description="Задать вопрос нейросети с Hugging Face (Qwen 2.5)")
async def ask_hf(ctx, *, question: str):
    await ctx.defer() # Бот будет "думать", пока ждет ответ
    try:
        response = await hf_client.chat.completions.create(
            model="Qwen/Qwen2.5-72B-Instruct", # Можно заменить на любую другую chat-модель с HF
            messages=[{"role": "user", "content": question}],
            max_tokens=1024 # Ограничиваем длину ответа
        )
        # Ограничиваем длину ответа для Discord Embed (максимум 4096 символов)
        response_text = response.choices[0].message.content[:4096]
        
        embed = discord.Embed(title="🤖 Ответ с Hugging Face", description=response_text, color=discord.Color.orange())
        await ctx.send(embed=embed)
    except Exception as e:
        await ctx.send(f"❌ Произошла ошибка при обращении к Hugging Face: {e}", ephemeral=True)

@bot.hybrid_command(name="ask_gemini", description="Задать вопрос бесплатной нейросети Gemini от Google")
async def ask_gemini(ctx, *, question: str):
    await ctx.defer()
    try:
        response = await ai_client.chat.completions.create(
            model="google/gemini-2.0-flash-exp:free",
            messages=[{"role": "user", "content": question}],
            extra_headers={"HTTP-Referer": "https://discord.gg/ur5tPZ7umw", "X-Title": "Discord Bot"}
        )
        
        response_text = response.choices[0].message.content[:4096]
        embed = discord.Embed(title="🤖 Ответ от Gemini (Google)", description=response_text, color=discord.Color.teal())
        await ctx.send(embed=embed)
    except Exception as e:
        await ctx.send(f"❌ Произошла ошибка при обращении к нейросети: {e}", ephemeral=True)

@bot.hybrid_command(name="balance", description="Показать ваш баланс")
async def balance(ctx, member: discord.Member = None):
    member = member or ctx.author
    user_id = str(member.id)

    user = await database.get_user(user_id)
    user_balance = user['balance']
    user_bank = user['bank']

    embed = discord.Embed(title=f"💰 Баланс {member.display_name}", color=discord.Color.gold())
    embed.add_field(name="💵 Наличные", value=f"**{user_balance}** монет", inline=True)
    embed.add_field(name="🏦 В банке", value=f"**{user_bank}** монет", inline=True)
    embed.add_field(name="Всего", value=f"**{user_balance + user_bank}** монет", inline=True)
    
    await ctx.send(embed=embed)

@bot.hybrid_command(name="deposit", aliases=["dep"], description="Положить наличные в банк (защита от ограблений)")
async def deposit(ctx, amount: int):
    if amount <= 0:
        return await ctx.send("❌ Сумма должна быть больше 0.", ephemeral=True)
        
    user_id = str(ctx.author.id)
    user = await database.get_user(user_id)
    
    if user['balance'] < amount:
        return await ctx.send(f"❌ У вас недостаточно наличных! Ваш баланс: **{user['balance']}** монет.", ephemeral=True)
        
    new_balance = user['balance'] - amount
    new_bank = user['bank'] + amount
    
    await database.update_user(user_id, balance=new_balance, bank=new_bank)
    await ctx.send(f"🏦 Вы успешно положили **{amount}** монет на банковский счет.")

@bot.hybrid_command(name="withdraw", aliases=["with"], description="Снять деньги с банковского счета")
async def withdraw(ctx, amount: int):
    if amount <= 0:
        return await ctx.send("❌ Сумма должна быть больше 0.", ephemeral=True)
        
    user_id = str(ctx.author.id)
    user = await database.get_user(user_id)
    
    if user['bank'] < amount:
        return await ctx.send(f"❌ В банке недостаточно средств! На вашем счету: **{user['bank']}** монет.", ephemeral=True)
        
    new_balance = user['balance'] + amount
    new_bank = user['bank'] - amount
    
    await database.update_user(user_id, balance=new_balance, bank=new_bank)
    await ctx.send(f"💵 Вы сняли **{amount}** монет с банковского счета. Будьте осторожны, теперь их могут украсть!")

@bot.hybrid_command(name="rob", description="Попытаться ограбить пользователя")
async def rob(ctx, member: discord.Member):
    user_id = str(ctx.author.id)
    target_id = str(member.id)

    if user_id == target_id: return await ctx.send("❌ Вы не можете ограбить самого себя!", ephemeral=True)
    if member.bot: return await ctx.send("❌ Ботов грабить нельзя, у них нет денег!", ephemeral=True)

    now_ts = datetime.datetime.now(datetime.timezone.utc).timestamp()
    user = await database.get_user(user_id)
    target = await database.get_user(target_id)
    
    cooldown = 3600 # 1 час на ограбление
    if now_ts - user['last_rob_ts'] < cooldown:
        rem = int(cooldown - (now_ts - user['last_rob_ts']))
        return await ctx.send(f"⏳ Полиция патрулирует район! Подождите еще **{rem // 60} мин. {rem % 60} сек.**", ephemeral=True)

    if user['balance'] < 500:
        return await ctx.send("❌ Нужно минимум **500 наличных** для подготовки к ограблению (на случай штрафа).", ephemeral=True)
    
    if target['balance'] < 100:
        return await ctx.send("❌ У этого пользователя слишком мало наличных, грабить нечего.", ephemeral=True)

    # Шанс успешного ограбления повышается при наличии отмычки
    inventory = users_data.get(user_id, {}).get('inventory', {})
    lockpick_name = "Отмычка 🔓"
    has_lockpick = inventory.get(lockpick_name, 0) > 0
    success_chance = 0.60 if has_lockpick else 0.30

    if random.random() < success_chance:
        # Крадем от 10% до 30% НАЛИЧНЫХ жертвы
        stolen = int(target['balance'] * random.uniform(0.1, 0.3))
        await database.update_user(user_id, balance=user['balance'] + stolen, last_rob_ts=now_ts)
        await database.update_user(target_id, balance=target['balance'] - stolen)
        msg = f"🥷 Вы успешно подкрались к {member.mention} и украли **{stolen}** монет!"
        # Используем отмычку (одна штучка) и сохраняем данные
        if has_lockpick:
            inventory[lockpick_name] = inventory.get(lockpick_name, 1) - 1
            if inventory[lockpick_name] <= 0:
                del inventory[lockpick_name]
            users_data.setdefault(user_id, {})
            users_data[user_id]['inventory'] = inventory
            await save_user_data(users_data)
            msg += "\n🔓 Отмычка использована и сломалась."
        await ctx.send(msg)
    else:
        fine = 500
        await database.update_user(user_id, balance=user['balance'] - fine, last_rob_ts=now_ts)
        await ctx.send(f"🚨 Вас заметили! Вы не смогли ничего украсть и заплатили штраф **{fine}** монет полиции.")

@bot.hybrid_command(name="crime", description="Экстремальная работа: огромный риск, огромная награда")
async def crime(ctx):
    user_id = str(ctx.author.id)
    now_ts = datetime.datetime.now(datetime.timezone.utc).timestamp()
    
    user = await database.get_user(user_id)
    
    cooldown = 14400 # 4 часа кулдаун
    if now_ts - user['last_crime_ts'] < cooldown:
        rem = int(cooldown - (now_ts - user['last_crime_ts']))
        return await ctx.send(f"⏳ Вы залегли на дно. Следующее дело можно начать через **{rem // 3600} ч. {(rem % 3600) // 60} мин.**", ephemeral=True)

    if user['balance'] < 1000:
        return await ctx.send("❌ Вам нужно минимум **1000 наличных** для организации крупного дела.", ephemeral=True)

    if random.random() < 0.25: # 25% шанс на успех
        reward = random.randint(5000, 15000)
        await database.update_user(user_id, balance=user['balance'] + reward, last_crime_ts=now_ts)
        await ctx.send(f"😎 **УСПЕХ!** Вы ограбили центральный банк и унесли с собой **{reward}** монет! Ваше фото теперь на всех радарах.")
    else: # 75% шанс проиграть ВСЕ наличные
        lost = user['balance']
        await database.update_user(user_id, balance=0, last_crime_ts=now_ts)
        await ctx.send(f"🚓 **ПРОВАЛ!** Операция сорвалась, вас окружил спецназ. При побеге вы потеряли **ВСЕ** свои наличные (**{lost}** монет)!")

@bot.hybrid_command(name="work", description="Заработать немного денег (раз в день)")
async def work(ctx):
    user_id = str(ctx.author.id)
    now_ts = datetime.datetime.now(datetime.timezone.utc).timestamp()

    user = await database.get_user(user_id)
    last_work_ts = user['last_work_ts']

    if now_ts - last_work_ts < WORK_COOLDOWN:
        remaining_time = int(WORK_COOLDOWN - (now_ts - last_work_ts))
        hours = remaining_time // 3600
        minutes = (remaining_time % 3600) // 60
        await ctx.send(f"Вы сможете снова работать через **{hours}** ч. и **{minutes}** мин.", ephemeral=True)
        return

    earnings = 100 # Сумма заработка
    new_balance = user['balance'] + earnings
    await database.update_user(user_id, balance=new_balance, last_work_ts=now_ts)

    await ctx.send(f"Вы усердно поработали и заработали **{earnings}** монет! Ваш новый баланс: **{new_balance}** монет.")

@bot.hybrid_command(name="pay", description="Перевести деньги другому пользователю")
async def pay(ctx, recipient: discord.Member, amount: int):
    sender_id = str(ctx.author.id)
    recipient_id = str(recipient.id)

    if amount <= 0:
        await ctx.send("Сумма перевода должна быть положительной.", ephemeral=True)
        return

    sender = await database.get_user(sender_id)
    sender_balance = sender['balance']

    if sender_balance < amount:
        await ctx.send(f"У вас недостаточно средств. Ваш баланс: **{sender_balance}** монет.", ephemeral=True)
        return

    recipient_data = await database.get_user(recipient_id)
    tax = int(amount * TAX_RATE)
    net = amount - tax
    if net <= 0:
        return await ctx.send("Сумма слишком мала после удержания налога.", ephemeral=True)

    # Списываем у отправителя
    await database.update_user(sender_id, balance=sender_balance - amount)
    # Переводим получателю
    await database.update_user(recipient_id, balance=recipient_data['balance'] + net)
    # Увеличиваем налоговую казну
    tax_pool = await database.get_server_data('tax_pool')
    await database.update_server_data('tax_pool', tax_pool + tax)

    await ctx.send(f"✅ Вы перевели **{net}** монет пользователю {recipient.mention} (налог **{tax}** монет).")

@bot.hybrid_command(name="shop", description="Показать магазин ролей")
async def shop(ctx):
    await ctx.defer()
    # Показываем магазин в табличном виде
    headers = ["ID", "Название", "Цена", "Тип", "Инфо"]
    rows = []
    for item_key, item_data in SHOP_ITEMS.items():
        info = ''
        if item_data.get('type') == 'role':
            info = f"role_id:{item_data.get('role_id')}"
        elif item_data.get('type') == 'property':
            info = f"доход: {item_data.get('income', 0)}/ч"
        elif item_data.get('type') == 'lootbox':
            info = 'шанс на монеты/предмет'
        rows.append((item_key, item_data.get('name', item_key), str(item_data.get('price', 0)), item_data.get('type', ''), info))

    paginator = TablePaginator(ctx, "🛒 Магазин", headers, rows, page_size=6, color=discord.Color.purple())
    embed = paginator._get_page_embed()
    await ctx.send(embed=embed, view=paginator)

@bot.hybrid_command(name="buy", description="Купить товар в магазине")
async def buy(ctx, item_id: str):
    item_id = item_id.lower()
    if item_id not in SHOP_ITEMS:
        await ctx.send("❌ Такого товара нет в магазине! Используйте `/shop` для просмотра списка.", ephemeral=True)
        return
    item_data = SHOP_ITEMS[item_id]
    price = item_data.get('price', 0)
    item_name = item_data.get('name', item_id)
    item_type = item_data.get('type', 'role' if item_data.get('role_id') else 'item')

    user_id = str(ctx.author.id)
    # Проверяем баланс через базу
    user = await database.get_user(user_id)
    if user['balance'] < price:
        await ctx.send(f"❌ У вас недостаточно средств! Нужно **{price}** монет, а у вас **{user['balance']}**.", ephemeral=True)
        return

    # Покупка роли
    if item_type == 'role':
        role_id = item_data.get('role_id')
        role = ctx.guild.get_role(role_id)
        if not role:
            await ctx.send(f"❌ Ошибка сервера: роль '{item_name}' не найдена. Сообщите администратору.", ephemeral=True)
            return
        if role in ctx.author.roles:
            await ctx.send(f"❌ У вас уже есть роль **{item_name}**!", ephemeral=True)
            return
        try:
            await ctx.author.add_roles(role, reason="Покупка в магазине")
            await database.update_user(user_id, balance=user['balance'] - price)
            await ctx.send(f"🎉 Вы успешно купили роль **{item_name}** за **{price}** монет!")
        except discord.Forbidden:
            await ctx.send("❌ У бота нет прав на выдачу этой роли! Проверьте порядок ролей бота.", ephemeral=True)
        return
    # Покупка недвижимости / источника дохода
    if item_type == 'property':
        users_data.setdefault(user_id, {})
        props = users_data[user_id].get('properties', [])
        props.append({'id': item_id, 'name': item_name, 'income': item_data.get('income', 0), 'value': price})
        users_data[user_id]['properties'] = props
        await database.update_user(user_id, balance=user['balance'] - price)
        await save_user_data(users_data)
        await ctx.send(f"🏠 Вы купили **{item_name}** за **{price}** монет! Доход: **{item_data.get('income',0)}** монет в час.")
        return

    # Покупка сервисов (например, страховка)
    if item_type == 'service':
        users_data.setdefault(user_id, {})
        # Отмечаем страховку (простая реализация — булево значение)
        users_data[user_id]['insurance'] = users_data[user_id].get('insurance', 0) + 1
        await database.update_user(user_id, balance=user['balance'] - price)
        await save_user_data(users_data)
        await ctx.send(f"🛡️ Вы купили **{item_name}** за **{price}** монет! Страховка активирована.")
        return

    # Лутбокс — даём случайную награду сразу
    if item_type == 'lootbox':
        users_data.setdefault(user_id, {})
        reward_msg = ''
        if random.random() < 0.5:
            coins = random.randint(200, 1000)
            await database.update_user(user_id, balance=user['balance'] + coins - price)
            reward_msg = f"Вы получили **{coins}** монет из лутбокса!"
        else:
            # Выдаём случайный предмет из магазина
            possible = [v for k, v in SHOP_ITEMS.items() if v.get('type') == 'item']
            if possible:
                pick = random.choice(possible)
                inv = users_data[user_id].get('inventory', {})
                inv[pick['name']] = inv.get(pick['name'], 0) + 1
                users_data[user_id]['inventory'] = inv
                await database.update_user(user_id, balance=user['balance'] - price)
                await save_user_data(users_data)
                reward_msg = f"Вы получили предмет **{pick['name']}** из лутбокса!"
            else:
                await database.update_user(user_id, balance=user['balance'] - price)
                reward_msg = "Лутбокс оказался пустым..."

        await ctx.send(f"🎁 Лутбокс открыт! {reward_msg}")
        return

    # Покупка предмета в инвентарь (по умолчанию)
    users_data.setdefault(user_id, {})
    inv = users_data[user_id].get('inventory', {})
    inv[item_name] = inv.get(item_name, 0) + 1
    users_data[user_id]['inventory'] = inv

    # Снимаем деньги через базу и сохраняем локальные данные
    await database.update_user(user_id, balance=user['balance'] - price)
    await save_user_data(users_data)
    await ctx.send(f"✅ Вы купили **{item_name}** за **{price}** монет! Предмет добавлен в инвентарь.")


@bot.hybrid_command(name="collect", description="Собрать пассивный доход от недвижимости")
async def collect(ctx):
    user_id = str(ctx.author.id)
    now_ts = datetime.datetime.now(datetime.timezone.utc).timestamp()
    users_data.setdefault(user_id, {})
    props = users_data[user_id].get('properties', [])
    if not props:
        return await ctx.send("У вас нет источников пассивного дохода.", ephemeral=True)
    last = users_data[user_id].get('last_collect_ts', 0)
    elapsed_hours = int((now_ts - last) // 3600) if last else 1
    if elapsed_hours <= 0:
        return await ctx.send("⏳ До следующей сборки ещё время. Попробуйте позже.", ephemeral=True)
    total_income_per_hour = sum([p.get('income', 0) for p in props])
    total = total_income_per_hour * elapsed_hours
    user = await database.get_user(user_id)
    await database.update_user(user_id, balance=user['balance'] + total)
    users_data[user_id]['last_collect_ts'] = now_ts
    await save_user_data(users_data)
    await ctx.send(f"🏠 Вы собрали **{total}** монет пассивного дохода (за {elapsed_hours} ч.).")


@bot.hybrid_command(name="claim_interest", description="Получить начисленные проценты по вкладу")
async def claim_interest(ctx):
    user_id = str(ctx.author.id)
    now_ts = datetime.datetime.now(datetime.timezone.utc).timestamp()
    user = await database.get_user(user_id)
    bank_amt = user.get('bank', 0)
    if bank_amt <= 0:
        return await ctx.send("У вас нет средств на банковском счёте.", ephemeral=True)
    users_data.setdefault(user_id, {})
    last_ts = users_data[user_id].get('last_interest_ts', 0)
    days = int((now_ts - last_ts) // 86400) if last_ts else 1
    if days <= 0:
        return await ctx.send("⏳ Проценты ещё не накопились. Попробуйте позже.", ephemeral=True)
    interest = int(bank_amt * INTEREST_RATE_DAILY * days)
    if interest <= 0:
        return await ctx.send("Проценты слишком малы для начисления.", ephemeral=True)
    await database.update_user(user_id, bank=bank_amt + interest)
    users_data[user_id]['last_interest_ts'] = now_ts
    await save_user_data(users_data)
    await ctx.send(f"🏦 Вы получили **{interest}** монет в виде процентов за {days} дней.")


def load_marketplace():
    try:
        with open(MARKETPLACE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return []

async def save_marketplace(data):
    try:
        def _write():
            with open(MARKETPLACE_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        await asyncio.to_thread(_write)
    except Exception as e:
        print(f"[ERROR] save_marketplace: {e}")


@bot.hybrid_command(name="market_sell", description="Выставить предмет на продажу на маркетплейс")
async def market_sell(ctx, item_name: str, price: int):
    user_id = str(ctx.author.id)
    users_data.setdefault(user_id, {})
    inv = users_data[user_id].get('inventory', {})
    if inv.get(item_name, 0) <= 0:
        return await ctx.send("У вас нет такого предмета в инвентаре.", ephemeral=True)
    if price <= 0:
        return await ctx.send("Цена должна быть положительной.", ephemeral=True)
    inv[item_name] -= 1
    if inv[item_name] <= 0:
        del inv[item_name]
    users_data[user_id]['inventory'] = inv
    await save_user_data(users_data)

    listings = load_marketplace()
    listing = {"id": uuid.uuid4().hex, "seller": user_id, "item": item_name, "price": price}
    listings.append(listing)
    await save_marketplace(listings)
    await ctx.send(f"✅ Вы выставили **{item_name}** за **{price}** монет. ID лота: `{listing['id']}`")


@bot.hybrid_command(name="market_list", description="Показать лоты на маркетплейсе")
async def market_list(ctx):
    await ctx.defer()
    listings = load_marketplace()
    if not listings:
        return await ctx.send("Маркетплейс пуст.")
    rows = [(l['id'][:8], l['item'], str(l['price']), f"<@{l['seller']}") for l in listings]
    headers = ["ID(кор)", "Товар", "Цена", "Продавец"]
    paginator = TablePaginator(ctx, "🛒 Маркетплейс", headers, rows, page_size=6, color=discord.Color.blurple())
    embed = paginator._get_page_embed()
    await ctx.send(embed=embed, view=paginator)


@bot.hybrid_command(name="market_buy", description="Купить лот с маркетплейса по ID")
async def market_buy(ctx, listing_id: str):
    buyer_id = str(ctx.author.id)
    listings = load_marketplace()
    listing = next((l for l in listings if l['id'] == listing_id), None)
    if not listing:
        return await ctx.send("Лот не найден.", ephemeral=True)
    price = listing['price']
    buyer = await database.get_user(buyer_id)
    if buyer['balance'] < price:
        return await ctx.send("Недостаточно средств для покупки.", ephemeral=True)
    # Комиссия маркетплейса 5%
    fee = int(price * 0.05)
    seller_amount = price - fee
    seller_id = listing['seller']
    seller = await database.get_user(seller_id)
    await database.update_user(buyer_id, balance=buyer['balance'] - price)
    await database.update_user(seller_id, balance=seller['balance'] + seller_amount)
    # Добавляем сбор в налоговую казну
    tax_pool = await database.get_server_data('tax_pool')
    await database.update_server_data('tax_pool', tax_pool + fee)

    # Передаём предмет покупателю
    users_data.setdefault(buyer_id, {})
    inv = users_data[buyer_id].get('inventory', {})
    inv[listing['item']] = inv.get(listing['item'], 0) + 1
    users_data[buyer_id]['inventory'] = inv
    await save_user_data(users_data)

    # Удаляем лот
    listings = [l for l in listings if l['id'] != listing_id]
    await save_marketplace(listings)
    await ctx.send(f"✅ Вы купили **{listing['item']}** за **{price}** монет. Комиссия: **{fee}**.")


RECIPES = {
    "lockpick": {"requires": {"Metal": 2, "Wire": 1}, "gives": {"Отмычка 🔓": 1}}
}


@bot.hybrid_command(name="craft", description="Сковать предмет, используя материалы из инвентаря")
async def craft(ctx, recipe_id: str):
    user_id = str(ctx.author.id)
    recipe = RECIPES.get(recipe_id)
    if not recipe:
        return await ctx.send("Рецепт не найден.", ephemeral=True)
    users_data.setdefault(user_id, {})
    inv = users_data[user_id].get('inventory', {})
    # Проверяем наличие материалов
    for mat, qty in recipe['requires'].items():
        if inv.get(mat, 0) < qty:
            return await ctx.send(f"У вас недостаточно материалов: {mat}", ephemeral=True)
    # Убираем материалы
    for mat, qty in recipe['requires'].items():
        inv[mat] -= qty
        if inv[mat] <= 0:
            del inv[mat]
    # Добавляем результат
    for res, qty in recipe['gives'].items():
        inv[res] = inv.get(res, 0) + qty
    users_data[user_id]['inventory'] = inv
    await save_user_data(users_data)
    await ctx.send(f"🔨 Вы успешно скрафтили {', '.join([f'{q}x {r}' for r, q in recipe['gives'].items()])}.")

@bot.hybrid_command(name="gamble", description="Сыграть в Орла или Решку")
async def gamble(ctx, amount: int, choice: str):
    user_id = str(ctx.author.id)

    if amount <= 0:
        await ctx.send("❌ Ставка должна быть положительной!", ephemeral=True)
        return
    
    user = await database.get_user(user_id)
    user_balance = user['balance']

    if user_balance < amount:
        await ctx.send(f"❌ У вас недостаточно средств для такой ставки! Ваш баланс: **{user_balance}** монет.", ephemeral=True)
        return
    
    choice = choice.lower()
    if choice not in ['орел', 'решка']:
        await ctx.send("❌ Ваш выбор должен быть 'орел' или 'решка'!", ephemeral=True)
        return

    # Увеличиваем джекпот на 10% от ставки (или минимум на 1 монету)
    jackpot_contribution = max(1, int(amount * 0.1))
    current_jackpot = await database.get_server_data("jackpot")
    if current_jackpot == 0: current_jackpot = 1000
    new_jackpot = current_jackpot + jackpot_contribution
    await database.update_server_data("jackpot", new_jackpot)

    # 0 - орел, 1 - решка
    result = random.randint(0, 1)
    result_word = "орел" if result == 0 else "решка"
    
    # Шанс выиграть джекпот (например, 2%)
    won_jackpot = random.randint(1, 100) <= 2

    msg = ""
    new_balance = user_balance

    if (choice == "орел" and result == 0) or (choice == "решка" and result == 1):
        new_balance += amount
        msg += f"🎉 Выпала **{result_word}**! Вы выиграли **{amount}** монет!\n"
    else:
        new_balance -= amount
        msg += f"😔 Выпала **{result_word}**! Вы проиграли **{amount}** монет.\n"

    if won_jackpot:
        new_balance += new_jackpot
        msg += f"🎰 **ДЖЕКПОТ!!!** Вы сорвали куш и получили **{new_jackpot}** монет!\n"
        new_jackpot = 1000 # Сбрасываем джекпот (возвращаем к базовым 1000)
        await database.update_server_data("jackpot", new_jackpot)

    await database.update_user(user_id, balance=new_balance)
    msg += f"Ваш текущий баланс: **{new_balance}** монет. *(Текущий джекпот: {new_jackpot})*"
    
    await ctx.send(msg)

@bot.hybrid_command(name="jackpot", description="Посмотреть текущий размер джекпота")
async def jackpot(ctx):
    current_jackpot = await database.get_server_data("jackpot")
    if current_jackpot == 0: current_jackpot = 1000
    embed = discord.Embed(title="🎰 Казино: Джекпот", description=f"Текущий размер джекпота: **{current_jackpot}** монет!\n\nДелайте ставки в `/gamble`, чтобы получить шанс (2%) сорвать куш. С каждой вашей ставки джекпот растет!", color=discord.Color.gold())
    await ctx.send(embed=embed)

@bot.hybrid_command(name="slots", description="Испытать удачу в игровых автоматах")
async def slots(ctx, amount: int):
    user_id = str(ctx.author.id)

    if amount <= 0:
        await ctx.send("❌ Ставка должна быть положительной!", ephemeral=True)
        return
    
    user = await database.get_user(user_id)
    user_balance = user['balance']

    if user_balance < amount:
        await ctx.send(f"❌ У вас недостаточно средств для такой ставки! Ваш баланс: **{user_balance}** монет.", ephemeral=True)
        return
    
    # Снимаем ставку перед игрой
    new_balance = user_balance - amount

    # Увеличиваем джекпот на 10% от ставки
    jackpot_contribution = max(1, int(amount * 0.1))
    current_jackpot = await database.get_server_data("jackpot")
    if current_jackpot == 0: current_jackpot = 1000
    await database.update_server_data("jackpot", current_jackpot + jackpot_contribution)

    emojis = ['🍒', '🍋', '🍊', '🔔', '💎', '🎰']
    reels = [random.choice(emojis) for _ in range(3)]
    
    result_str = f"**[ {reels[0]} | {reels[1]} | {reels[2]} ]**"
    
    winnings = 0
    msg = ""

    # Проверка на 3 одинаковых символа
    if reels[0] == reels[1] == reels[2]:
        symbol = reels[0]
        if symbol == '🎰': winnings = amount * 50; msg = f"**ДЖЕКПОТ СЛОТОВ!** Вы собрали три семерки (x50)! Ваш выигрыш: **{winnings}** монет!"
        elif symbol == '💎': winnings = amount * 20; msg = f"**КРУПНЫЙ ВЫИГРЫШ!** Вы собрали три алмаза (x20)! Ваш выигрыш: **{winnings}** монет!"
        elif symbol == '🔔': winnings = amount * 10; msg = f"**Выигрыш!** Вы собрали три колокольчика (x10)! Ваш выигрыш: **{winnings}** монет!"
        else: winnings = amount * 5; msg = f"**Выигрыш!** Вы собрали три одинаковых фрукта (x5)! Ваш выигрыш: **{winnings}** монет!"
    # Проверка на 2 одинаковых символа
    elif len(set(reels)) == 2:
        winnings = amount * 2; msg = f"**Небольшой выигрыш!** Вы собрали два одинаковых символа (x2)! Ваш выигрыш: **{winnings}** монет."
    # Проигрыш
    else: msg = "К сожалению, в этот раз не повезло. Попробуйте еще раз!"

    new_balance += winnings
    await database.update_user(user_id, balance=new_balance)

    embed = discord.Embed(title="🎰 Игровые автоматы", description=result_str, color=discord.Color.blue())
    embed.add_field(name="Результат", value=msg, inline=False)
    embed.set_footer(text=f"Ваш новый баланс: {new_balance} монет.")
    
    await ctx.send(embed=embed)

@bot.hybrid_command(name="ping", description="Проверить, работает ли бот")
async def ping(ctx):
    await ctx.send("НА МЕСТЕ!")

@bot.hybrid_command(name="suggest", description="Отправить анонимное предложение или жалобу администрации")
async def suggest(ctx, *, text: str):
    log_channel = bot.get_channel(LOG_CHANNEL_ID)
    if not log_channel:
        await ctx.send("Ошибка: Канал для предложений не настроен. Свяжитесь с администрацией.", ephemeral=True)
        print(f"[ERROR] Канал для логов/предложений с ID {LOG_CHANNEL_ID} не найден.")
        return

    try:
        embed = discord.Embed(title="📬 Анонимное сообщение", description=text, color=discord.Color.blue())
        await log_channel.send(embed=embed)
        await ctx.send("✅ Ваше анонимное сообщение было успешно отправлено.", ephemeral=True)
    except Exception as e:
        await ctx.send("Произошла ошибка при отправке вашего сообщения. Попробуйте позже.", ephemeral=True)
        print(f"[ERROR] Не удалось отправить анонимное сообщение: {e}")

class HelpView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300) # Меню будет активно 5 минут после вызова

    async def update_embed(self, interaction: discord.Interaction, title: str, desc: str, color: discord.Color):
        embed = discord.Embed(title=title, description=desc, color=color)
        embed.set_footer(text="Выберите категорию меню ниже ⬇️")
        embed.set_thumbnail(url=interaction.client.user.display_avatar.url)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Главная", style=discord.ButtonStyle.success, emoji="🏠", row=0)
    async def home_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        desc = (
            "Привет! Я **многофункциональный бот-помощник** этого сервера.\n"
            "Воспользуйтесь кнопками ниже, чтобы изучить мои возможности по категориям.\n\n"
            "🛡️ — **Модерация**\n"
            "📈 — **Активность и Репутация**\n"
            "💰 — **Экономика и Казино**\n"
            "🤖 — **Нейросети**\n"
            "🎵 — **Музыка**\n"
            "🎉 — **Развлечения**\n"
            "⚙️ — **Разное**\n\n"
            "*💡 Бот также автоматически наказывает за спам, мат и отправку ссылок!*"
        )
        await self.update_embed(interaction, "🤖 Главное меню помощи", desc, discord.Color.blue())

    @discord.ui.button(label="Модерация", style=discord.ButtonStyle.secondary, emoji="🛡️", row=0)
    async def mod_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        desc = "**Инструменты для поддержания порядка на сервере:**\n\n🔹 `/clear <число>` — Очистить чат\n🔹 `/mute <юзер> <время>` — Выдать мут\n🔹 `/unmute <юзер>` — Снять мут\n🔹 `/warn <юзер>` — Выдать варн (2 варна = мут)\n🔹 `/kick <юзер>` — Выгнать с сервера\n🔹 `/ban <юзер>` — Забанить\n🔹 `/lock` / `/unlock` — Закрыть/открыть текущий канал\n🔹 `/setup_tickets` — Создать панель для тикетов (админ)"
        await self.update_embed(interaction, "🛡️ Команды Модерации", desc, discord.Color.red())

    @discord.ui.button(label="Активность", style=discord.ButtonStyle.secondary, emoji="📈", row=0)
    async def act_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        desc = "**Система уровней, топы и социальные взаимодействия:**\n\n🔹 `/profile` — Красивая карточка профиля\n🔹 `/rank` — Ваш уровень и прогресс\n🔹 `/leaderboard` — Топ активных участников\n🔹 `/rep <юзер>` — Выдать репутацию (+1)\n🔹 `/unrep <юзер>` — Снять репутацию (-1)\n🔹 `/userinfo` — Информация об участнике"
        await self.update_embed(interaction, "📈 Активность и Репутация", desc, discord.Color.green())

    @discord.ui.button(label="Экономика", style=discord.ButtonStyle.secondary, emoji="💰", row=1)
    async def eco_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        desc = "**Зарабатывайте монеты, играйте и покупайте предметы:**\n\n🔹 `/daily` — Получить бонус\n🔹 `/work` — Заработать монеты (раз в 24ч)\n🔹 `/balance` — Узнать свой баланс\n🔹 `/deposit` / `/withdraw` — **Банковская система**\n🔹 `/rob <юзер>` — **Ограбить участника**\n🔹 `/crime` — **Опасное дело (всё или ничего)**\n🔹 `/pay <юзер> <сумма>` — Перевести монеты\n🔹 `/shop` / `/buy` — Магазин\n\n**Казино:**\n🔹 `/gamble <ставка> <орел/решка>` — Орел или решка\n🔹 `/slots <ставка>` — Игровые автоматы\n🔹 `/blackjack <ставка>` — Блэкджек (21)\n🔹 `/jackpot` — Размер джекпота\n🔹 `/leaderboard_coins` — Топ богачей"
        await self.update_embed(interaction, "💰 Экономика и Казино", desc, discord.Color.gold())

    @discord.ui.button(label="Нейросети", style=discord.ButtonStyle.secondary, emoji="🤖", row=1)
    async def ai_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        desc = "**Взаимодействие с самыми умными ИИ-моделями:**\n\n🔹 `/ask_gemini` — Задать вопрос Gemini (Google)\n🔹 `/ask_hf` — Вопросы к модели Qwen 2.5\n🔹 `/ask_cobuddy` / `/ask_nemotron` — Другие бесплатные ИИ\n🔹 `/imagine <текст>` — Сгенерировать картинку (FLUX)\n🔹 `/translate <текст>` — Переводчик от Meta\n\n*💡 Вы также можете упомянуть бота в чате для общения с духом OWL!*"
        await self.update_embed(interaction, "🤖 Нейросети", desc, discord.Color.teal())

    @discord.ui.button(label="Музыка", style=discord.ButtonStyle.secondary, emoji="🎵", row=1)
    async def music_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        desc = "**Управление музыкальным плеером в войсе:**\n\n🔹 `/play <запрос/ссылка>` — Включить трек или добавить в очередь\n🔹 `/pause` / `/resume` — Пауза / Возобновить\n🔹 `/skip` — Пропустить текущий трек\n🔹 `/queue` — Показать очередь треков\n🔹 `/stop` — Остановить музыку и очистить очередь"
        await self.update_embed(interaction, "🎵 Музыка", desc, discord.Color.purple())

    @discord.ui.button(label="Развлечения", style=discord.ButtonStyle.secondary, emoji="🎉", row=2)
    async def fun_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        desc = "**Команды для развлечения и взаимодействия:**\n\n🔹 `/hug <юзер>` — Обнять пользователя\n🔹 `/slap <юзер>` — Дать пощечину"
        await self.update_embed(interaction, "🎉 Развлечения", desc, discord.Color.magenta())

    @discord.ui.button(label="Разное", style=discord.ButtonStyle.secondary, emoji="⚙️", row=2)
    async def misc_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        desc = "**Полезные утилиты и другие команды:**\n\n🔹 `/weather <город>` — Узнать актуальную погоду\n🔹 `/ping` — Проверить работу бота\n🔹 `/suggest <текст>` — Анонимное предложение администрации"
        await self.update_embed(interaction, "⚙️ Разное", desc, discord.Color.light_grey())

@bot.hybrid_command(name="help", description="Показать список всех команд бота")
async def help_command(ctx):
    view = HelpView()
    desc = (
        "Привет! Я **многофункциональный бот-помощник** этого сервера.\n"
        "Воспользуйтесь кнопками ниже, чтобы изучить мои возможности по категориям.\n\n"
        "🛡️ — **Модерация**\n"
        "📈 — **Активность и Репутация**\n"
        "💰 — **Экономика и Казино**\n"
        "🤖 — **Нейросети**\n"
        "🎵 — **Музыка**\n"
        "🎉 — **Развлечения**\n"
        "⚙️ — **Разное**\n\n"
        "*💡 Бот также автоматически наказывает за спам, мат и отправку ссылок!*"
    )
    embed = discord.Embed(title="🤖 Главное меню помощи", description=desc, color=discord.Color.blue())
    embed.set_thumbnail(url=ctx.bot.user.display_avatar.url)
    embed.set_footer(text="Выберите категорию меню ниже ⬇️")
    
    await ctx.send(embed=embed, view=view, ephemeral=True)

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send(f"❌ {ctx.author.mention}, у тебя недостаточно прав для использования этой команды!")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"❌ {ctx.author.mention}, не хватает аргументов! Проверь правильность команды (например, забыл указать пользователя или время).")
    elif isinstance(error, commands.MemberNotFound):
        await ctx.send(f"❌ {ctx.author.mention}, я не могу найти этого пользователя на сервере.")
    elif isinstance(error, commands.BadArgument):
        await ctx.send(f"❌ {ctx.author.mention}, неверный формат (например, время мута должно быть числом).")
    else:
        print(f"[ERROR] Необработанная ошибка в команде: {error}")
        try:
            # Отвечаем пользователю, чтобы Discord не писал "The application did not respond"
            await ctx.send(f"❌ Произошла техническая ошибка при выполнении команды: `{error}`", ephemeral=True)
        except Exception:
            pass

@bot.event
async def on_message(message):
    # Игнорируем сообщения от самого бота, чтобы он не проверял сам себя
    if message.author == bot.user:
        return

    # Игнорируем личные сообщения (DMs), чтобы избежать ошибок с модерацией вне сервера
    if not message.guild:
        return

    # --- СИСТЕМА УРОВНЕЙ (НАЧИСЛЕНИЕ ОПЫТА ЗА СООБЩЕНИЯ) ---
    # Проверяем, что сообщение не от бота и не является командой
    if not message.author.bot and not message.content.startswith(bot.command_prefix):
        user_id = str(message.author.id)
        now_ts = datetime.datetime.now(datetime.timezone.utc).timestamp()

        # Получаем пользователя из БД (он автоматически создастся, если его нет)
        user = await database.get_user(user_id)

        new_messages = user['messages'] + 1
        new_balance = user['balance']
        
        # Выдаем 1 монетку за каждые 5 сообщений
        if new_messages % 5 == 0:
            new_balance += 1
            
        # Обновляем все нужные поля в базе данных
        await database.update_user(
            user_id,
            messages=new_messages,
            last_message_ts=now_ts,
            balance=new_balance,
            name=message.author.display_name,
            avatar=str(message.author.display_avatar.url)
        )
        
        print(f"[XP] {message.author.display_name} получил +1 сообщение. Всего: {new_messages}. Баланс: {new_balance}")
        
        await check_and_award_roles(message.author)

    # !!! ОТЛАДКА: Выводит в консоль (терминал) всё, что видит бот !!!
    print(f"[LOG] Сообщение от {message.author}: {message.content}")

    # Пропускаем проверки на спам и мат для администраторов сервера
    # if message.guild and message.author.guild_permissions.administrator:
    #     await bot.process_commands(message)
    #     return

    # --- Умные автоответы на тикеты (и вопросы к боту) ---
    # Проверяем, находится ли канал сообщения внутри указанной категории тикетов
    is_ticket = getattr(message.channel, 'category_id', None) == TICKET_CATEGORY_ID
    
    # Проверяем текст сообщения и текст внутри карточек (embeds), которые присылают боты
    full_text = message.content.lower()
    for embed in message.embeds:
        if embed.title:
            full_text += f" {embed.title.lower()}"
        if embed.description:
            full_text += f" {embed.description.lower()}"

    # Если это сообщение от бота тикетов и там есть слова приветствия/тикета
    if is_ticket and message.author.bot and ('welcome' in full_text or 'ticket' in full_text or 'тикет' in full_text):
        await message.channel.send("Здравствуй участник, какой вопрос у тебя сегодня? Задай его, просто упомянув меня, и я отвечу на любой твой вопрос! Даже самый глупый вопрос мне по зубам!")

    # Бот ответит, если его пинганули (@Бот) ИЛИ если в тикете написали "!вопрос [текст]"
    if bot.user in message.mentions or (is_ticket and message.content.lower().startswith('!вопрос')):
        async with message.channel.typing(): # Статус "печатает..."
            try:
                # Убираем пинг бота из текста
                question = message.content.replace(f'<@{bot.user.id}>', '')
                # Убираем команду !вопрос, если она есть
                if question.lower().startswith('!вопрос'):
                    question = question[7:]
                question = question.strip()
                
                # Собираем ссылки на все прикрепленные картинки
                image_urls = [att.url for att in message.attachments if att.content_type and att.content_type.startswith('image/')]

                if question or image_urls:
                    if not question and image_urls:
                        question = "Что изображено на этой картинке?"
                        
                    # 1. Задаем нейросети неформальную личность и передаем ей знания о сервере
                    system_prompt = f"Твоя личность — OWL. Ты не бот, а мудрый и старый участник этого сервера, почти его дух-хранитель. Твое имя OWL — потому что ты, как сова, многое видишь, многое знаешь и активен по ночам. Твое настроение меняется: днем ты можешь быть немного сонным и отвечать коротко, но по делу; ночью ты бодр, более разговорчив, можешь пошутить или поделиться какой-нибудь интересной мыслью. Когда спрашивают о правилах или просят помощи, ты становишься серьезным и ответственным, даешь четкие и точные ответы. В обычном разговоре ты можешь быть саркастичным, ироничным, но всегда остаешься дружелюбным. Общайся со всеми на 'ты'. Смайлики используй редко, чтобы подчеркнуть иронию (например, 😏) или усталость (😑). Вот информация о сервере, которую ты знаешь, если спросят:\n{cached_server_context}"
                    
                    if image_urls:
                        # Мультимодальный запрос (текст + картинки)
                        user_content = [{"type": "text", "text": question}]
                        for img_url in image_urls:
                            user_content.append({"type": "image_url", "image_url": {"url": img_url}})
                        
                        response = await ai_client.chat.completions.create(
                            model="meta-llama/llama-3.2-11b-vision-instruct:free", # Бесплатная Vision-модель на OpenRouter
                            messages=[
                                {"role": "system", "content": system_prompt},
                                {"role": "user", "content": user_content}
                            ],
                            extra_headers={"HTTP-Referer": "https://discord.gg/ur5tPZ7umw", "X-Title": "Discord Bot"}
                        )
                    else:
                        # Обычный текстовый запрос
                        response = await ai_client.chat.completions.create(
                            model="openrouter/owl-alpha", # Новая модель на OpenRouter
                            messages=[
                                {"role": "system", "content": system_prompt},
                                {"role": "user", "content": question}
                            ],
                            extra_headers={"HTTP-Referer": "https://discord.gg/ur5tPZ7umw", "X-Title": "Discord Bot"}
                        )
                    reply_text = response.choices[0].message.content[:2000]
                    await message.reply(reply_text)
                else:
                    await message.reply("Напиши свой вопрос или скинь картинку, и я постараюсь помочь!")
            except Exception as e:
                print(f"[AI ERROR] Ошибка генерации: {e}")
                await message.reply("Извини, мой мозг временно недоступен. Дождись ответа администрации!")
        return # Прерываем проверки, чтобы бот случайно не замутил пользователя за мат в вопросе

    # --- Проверка на белый список и роль владельца ---
    owner_role_id = 1500135128725328113
    is_whitelisted = message.author.name in WHITELISTED_USERS
    has_owner_role = isinstance(message.author, discord.Member) and any(role.id == owner_role_id for role in message.author.roles)
    
    if is_whitelisted or has_owner_role:
        await bot.process_commands(message)
        return

    # --- Фильтр любых ссылок / обнаружение замаскированной рекламы ---
    # Сначала ловим обфусцированные приглашения/рекламу (например: d i s c o r d, discordgg, d.isc.ord)
    if contains_advertising(message.content):
        try:
            await message.delete()
        except discord.Forbidden:
            print("Бот не смог удалить сообщение с рекламой. Проверь право 'Управление сообщениями'.")
        user_id = message.author.id
        if user_id not in invite_warnings:
            invite_warnings[user_id] = 1
        else:
            invite_warnings[user_id] += 1
        if invite_warnings[user_id] >= 2:
            duration = datetime.timedelta(minutes=10)
            try:
                await message.author.timeout(duration, reason="Отправка рекламных приглашений")
                await message.channel.send(f'{message.author.mention} получил мут на 10 минут за повторную отправку рекламы/приглашений.')
                log_channel = bot.get_channel(LOG_CHANNEL_ID)
                if log_channel:
                    embed = discord.Embed(title="⛔ Автоматический мут (Ссылки/Реклама)", color=discord.Color.red())
                    embed.add_field(name="Нарушитель", value=message.author.mention, inline=True)
                    embed.add_field(name="Время", value="10 мин.", inline=True)
                    embed.add_field(name="Причина", value="Повторная отправка рекламы/приглашений", inline=False)
                    await log_channel.send(embed=embed)
                invite_warnings[user_id] = 0
            except discord.Forbidden:
                await message.channel.send(f'Не удалось замутить {message.author.mention}. Возможно, его роль выше роли бота.')
        else:
            await message.channel.send(f'{message.author.mention}, реклама и приглашения запрещены на этом сервере!')
            log_channel = bot.get_channel(LOG_CHANNEL_ID)
            if log_channel:
                embed = discord.Embed(title="⚠️ Авто-предупреждение (Реклама)", color=discord.Color.orange())
                embed.add_field(name="Нарушитель", value=message.author.mention, inline=True)
                embed.add_field(name="Счетчик", value=f"{invite_warnings[user_id]}/2", inline=True)
                embed.add_field(name="Сообщение", value=message.content, inline=False)
                await log_channel.send(embed=embed)
        return

    if URL_REGEX.search(message.content):
        try:
            await message.delete()
        except discord.Forbidden:
            print("Бот не смог удалить ссылку. Проверь право 'Управление сообщениями'.")
            
        user_id = message.author.id
        if user_id not in invite_warnings:
            invite_warnings[user_id] = 1
        else:
            invite_warnings[user_id] += 1
            
        if invite_warnings[user_id] >= 2:
            duration = datetime.timedelta(minutes=10)
            try:
                await message.author.timeout(duration, reason="Отправка запрещенных ссылок")
                await message.channel.send(f'{message.author.mention} получил мут на 10 минут за повторную отправку ссылок.')
                
                log_channel = bot.get_channel(LOG_CHANNEL_ID)
                if log_channel:
                    embed = discord.Embed(title="⛔ Автоматический мут (Ссылки)", color=discord.Color.red())
                    embed.add_field(name="Нарушитель", value=message.author.mention, inline=True)
                    embed.add_field(name="Время", value="10 мин.", inline=True)
                    embed.add_field(name="Причина", value="Повторная отправка запрещенных ссылок", inline=False)
                    await log_channel.send(embed=embed)
                    
                invite_warnings[user_id] = 0 # Сбрасываем счетчик после мута
            except discord.Forbidden:
                await message.channel.send(f'Не удалось замутить {message.author.mention}. Возможно, его роль выше роли бота.')
        else:
            await message.channel.send(f'{message.author.mention}, на этом сервере запрещено отправлять любые ссылки!')
            
            log_channel = bot.get_channel(LOG_CHANNEL_ID)
            if log_channel:
                embed = discord.Embed(title="⚠️ Авто-предупреждение (Ссылки)", color=discord.Color.orange())
                embed.add_field(name="Нарушитель", value=message.author.mention, inline=True)
                embed.add_field(name="Счетчик", value=f"{invite_warnings[user_id]}/2", inline=True)
                embed.add_field(name="Сообщение", value=message.content, inline=False)
                await log_channel.send(embed=embed)
            
        return # Прерываем дальнейшие проверки (чтобы сообщение не проверялось еще и на мат)

    # --- Анти-спам система ---
    user_id = message.author.id
    now = datetime.datetime.now(datetime.timezone.utc)
    
    if user_id not in spam_tracker:
        spam_tracker[user_id] = []
        
    spam_tracker[user_id].append(message)
    
    # Оставляем только те сообщения, которые были отправлены за последние 5 секунд (SPAM_TIME)
    spam_tracker[user_id] = [m for m in spam_tracker[user_id] if (now - m.created_at).total_seconds() < SPAM_TIME]
    
    if len(spam_tracker[user_id]) >= SPAM_LIMIT:
        try:
            # Удаляем 4 (и более) последних сообщения нарушителя
            for m in spam_tracker[user_id]:
                try:
                    await m.delete()
                except discord.NotFound:
                    continue # Пропускаем, если сообщение уже было удалено
            
            # Считаем предупреждения за спам
            if user_id not in spam_warnings:
                spam_warnings[user_id] = 1
            else:
                spam_warnings[user_id] += 1

            # Проверяем, это повторный спам или нет
            if spam_warnings[user_id] >= 2:
                duration = datetime.timedelta(minutes=5)
                try:
                    await message.author.timeout(duration, reason="Повторный спам")
                    await message.channel.send(f'{message.author.mention} получил мут на 5 минут за повторный спам.')
                    
                    log_channel = bot.get_channel(LOG_CHANNEL_ID)
                    if log_channel:
                        embed = discord.Embed(title="⛔ Автоматический мут (Спам)", color=discord.Color.red())
                        embed.add_field(name="Нарушитель", value=message.author.mention, inline=True)
                        embed.add_field(name="Время", value="5 мин.", inline=True)
                        embed.add_field(name="Причина", value="Повторный спам сообщениями", inline=False)
                        await log_channel.send(embed=embed)
                        
                    spam_warnings[user_id] = 0 # Сбрасываем счетчик предупреждений после мута
                except discord.Forbidden:
                    await message.channel.send(f'Не удалось замутить {message.author.mention}. Возможно, его роль выше роли бота.')
            else:
                # Если это первое нарушение, только предупреждаем
                await message.channel.send(f'{message.author.mention}, пожалуйста, не спамь! Сообщения удалены. При повторном нарушении будет выдан мут.')
                
                log_channel = bot.get_channel(LOG_CHANNEL_ID)
                if log_channel:
                    embed = discord.Embed(title="⚠️ Авто-предупреждение (Спам)", color=discord.Color.orange())
                    embed.add_field(name="Нарушитель", value=message.author.mention, inline=True)
                    embed.add_field(name="Счетчик", value=f"{spam_warnings[user_id]}/2", inline=True)
                    embed.add_field(name="Причина", value="Спам (отправлено 4+ сообщений за 5 сек.)", inline=False)
                    await log_channel.send(embed=embed)
                
        except discord.Forbidden:
            print("Бот не смог удалить сообщения. Проверь право 'Управление сообщениями'.")
            
        spam_tracker[user_id] = [] # Очищаем историю после наказания
        return # Прерываем функцию, чтобы удаленное сообщение дальше не проверялось на мат

    # Проверка на мат — с учётом обходов через пробелы/разделители и латиницу
    has_bad_word = contains_bad_word(message.content)
    if has_bad_word:
        print(f"[DEBUG MAT] Найдено нарушение в сообщении: '{message.content}'")

    if has_bad_word:
        # 1. Сразу удаляем сообщение нарушителя
        try:
            await message.delete()
        except discord.Forbidden:
            print("Бот не смог удалить сообщение. Проверь, есть ли у него право 'Управление сообщениями'.")
            return

        user_id = message.author.id

        # 2. Считаем нарушения пользователя
        if user_id not in user_warnings:
            user_warnings[user_id] = 1
        else:
            user_warnings[user_id] += 1

        # 3. Проверяем, сколько раз пользователь нарушил правила
        if user_warnings[user_id] >= 2:
            # Если это уже повторное нарушение — отправляем в таймаут (мут) на 10 минут
            duration = datetime.timedelta(minutes=10)
            
            try:
                # Выдаем таймаут с указанием причины
                await message.author.timeout(duration, reason="Оскорбление")
                await message.channel.send(f'{message.author.mention} получил мут на 10 минут. Причина: Оскорбление.')
                
                log_channel = bot.get_channel(LOG_CHANNEL_ID)
                if log_channel:
                    embed = discord.Embed(title="⛔ Автоматический мут (Мат/Оскорбления)", color=discord.Color.red())
                    embed.add_field(name="Нарушитель", value=message.author.mention, inline=True)
                    embed.add_field(name="Время", value="10 мин.", inline=True)
                    embed.add_field(name="Причина", value="Повторное использование нецензурной лексики", inline=False)
                    await log_channel.send(embed=embed)
                
                # Сбрасываем счетчик предупреждений после выдачи мута
                user_warnings[user_id] = 0
            except discord.Forbidden:
                await message.channel.send(f'Не удалось замутить {message.author.mention}. Возможно, его роль выше роли бота.')
        
        else:
            # Если это первое нарушение — просто предупреждаем
            await message.channel.send(f'{message.author.mention}, такие слова не положены на сервере!')
            
            log_channel = bot.get_channel(LOG_CHANNEL_ID)
            if log_channel:
                embed = discord.Embed(title="⚠️ Авто-предупреждение (Мат/Оскорбления)", color=discord.Color.orange())
                embed.add_field(name="Нарушитель", value=message.author.mention, inline=True)
                embed.add_field(name="Счетчик", value=f"{user_warnings[user_id]}/2", inline=True)
                embed.add_field(name="Сообщение", value=message.content, inline=False)
                await log_channel.send(embed=embed)

        try:
            await message.author.send(f"⚠️ **Предупреждение!** На сервере **{message.guild.name}** запрещено использовать нецензурную лексику.\n*При повторном нарушении вы получите автоматический мут на 10 минут.*")
        except discord.Forbidden:
            pass
            
        return # Прерываем выполнение, чтобы не пытаться обработать удаленное сообщение как команду

    # ВАЖНО: Эта строчка обязательна! Без неё обычные команды (например, !ping) перестанут работать
    await bot.process_commands(message)

# --- НОВЫЕ КОМАНДЫ (Ежедневный бонус, Инвентарь, Блэкджек, Музыка, РП) ---

@bot.hybrid_command(name="daily", description="Получить ежедневный бонус")
async def daily(ctx):
    user_id = str(ctx.author.id)
    now_ts = datetime.datetime.now(datetime.timezone.utc).timestamp()
    
    users_data.setdefault(user_id, {})
    user_stats = users_data[user_id]
    
    last_daily = user_stats.get('last_daily_ts', 0)
    streak = user_stats.get('daily_streak', 0)
    time_since_last = now_ts - last_daily
    
    if time_since_last < 86400: # 24 часа
        rem = int(86400 - time_since_last)
        return await ctx.send(f"⏳ Бонус будет доступен через **{rem // 3600} ч. {rem % 3600 // 60} мин.**", ephemeral=True)
        
    if time_since_last > 172800: # Сброс стрика если прошло больше 48 часов
        streak = 0
        
    streak += 1
    reward = min(100 + (streak * 20), 500) # База 100, +20 за стрик, макс 500
    
    user_stats['balance'] = user_stats.get('balance', 0) + reward
    user_stats['last_daily_ts'] = now_ts
    user_stats['daily_streak'] = streak
    await save_user_data(users_data)
    
    embed = discord.Embed(title="🎁 Ежедневный бонус", color=discord.Color.green())
    embed.add_field(name="Получено", value=f"**{reward}** монет")
    embed.add_field(name="Стрик", value=f"🔥 **{streak}** дней подряд")
    await ctx.send(embed=embed)

@bot.hybrid_command(name="inventory", description="Посмотреть инвентарь")
async def inventory(ctx, member: discord.Member = None):
    member = member or ctx.author
    await ctx.defer()
    inv = users_data.get(str(member.id), {}).get('inventory', {})
    if not inv:
        return await ctx.send(f"У {member.mention} пустой инвентарь.")
    rows = [(item, str(count)) for item, count in inv.items()]
    headers = ["Предмет", "Кол-во"]
    paginator = TablePaginator(ctx, f"🎒 Инвентарь {member.display_name}", headers, rows, page_size=8, color=discord.Color.blue())
    embed = paginator._get_page_embed()
    await ctx.send(embed=embed, view=paginator)

@bot.hybrid_command(name="setup_tickets", description="Отправить панель для тикетов")
@commands.has_permissions(administrator=True)
async def setup_tickets_cmd(ctx):
    embed = discord.Embed(title="🎫 Служба поддержки", description="Нажмите кнопку ниже, чтобы создать приватный тикет с администрацией.", color=discord.Color.blurple())
    await ctx.send(embed=embed, view=TicketView())

# --- BlackJack (21) ---
class BlackjackView(discord.ui.View):
    def __init__(self, ctx, bet):
        super().__init__(timeout=60)
        self.ctx = ctx
        self.bet = bet
        self.player = [random.randint(2, 11), random.randint(2, 11)]
        self.dealer = [random.randint(2, 11), random.randint(2, 11)]

    def get_embed(self):
        embed = discord.Embed(title="🃏 Блэкджек (21)", color=discord.Color.gold())
        embed.add_field(name="Ваша рука", value=f"{self.player} (Счет: {sum(self.player)})")
        embed.add_field(name="Рука дилера", value=f"[{self.dealer[0]}, ?]")
        return embed

    async def end_game(self, interaction, result_msg, color, payout):
        for child in self.children: child.disabled = True
        user_id = str(self.ctx.author.id)
        user = await database.get_user(user_id)
        new_balance = user['balance'] + payout
        await database.update_user(user_id, balance=new_balance)
        embed = discord.Embed(title="🃏 Блэкджек", description=result_msg, color=color)
        embed.add_field(name="Ваша рука", value=f"{self.player} (Счет: {sum(self.player)})")
        embed.add_field(name="Рука дилера", value=f"{self.dealer} (Счет: {sum(self.dealer)})")
        embed.set_footer(text=f"Выиграно: {payout} монет | Ваш баланс: {new_balance}")
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Взять (Hit)", style=discord.ButtonStyle.primary)
    async def hit(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.ctx.author: return
        self.player.append(random.randint(2, 11))
        if sum(self.player) > 21:
            await self.end_game(interaction, "Вы перебрали! Проигрыш.", discord.Color.red(), 0)
        else:
            await interaction.response.edit_message(embed=self.get_embed(), view=self)

    @discord.ui.button(label="Хватит (Stand)", style=discord.ButtonStyle.secondary)
    async def stand(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.ctx.author: return
        d_score = sum(self.dealer)
        while d_score < 17:
            self.dealer.append(random.randint(2, 11))
            d_score = sum(self.dealer)
        p_score = sum(self.player)
        if d_score > 21 or p_score > d_score:
            await self.end_game(interaction, "Вы победили!", discord.Color.green(), self.bet * 2)
        elif p_score == d_score:
            await self.end_game(interaction, "Ничья!", discord.Color.gold(), self.bet)
        else:
            await self.end_game(interaction, "Дилер победил!", discord.Color.red(), 0)

@bot.hybrid_command(name="blackjack", description="Сыграть в блэкджек на монеты")
async def blackjack(ctx, bet: int):
    user_id = str(ctx.author.id)
    if bet <= 0: return await ctx.send("Ставка должна быть больше нуля!", ephemeral=True)
    user = await database.get_user(user_id)
    if user['balance'] < bet:
        return await ctx.send("Недостаточно средств!", ephemeral=True)
    await database.update_user(user_id, balance=user['balance'] - bet)
    view = BlackjackView(ctx, bet)
    await ctx.send(embed=view.get_embed(), view=view)

# --- Музыка: пауза, возобновление, очередь ---
@bot.hybrid_command(name="pause", description="Поставить музыку на паузу")
async def pause(ctx):
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.pause()
        await ctx.send("⏸ Музыка поставлена на паузу.")
        
@bot.hybrid_command(name="resume", description="Возобновить воспроизведение")
async def resume(ctx):
    if ctx.voice_client and ctx.voice_client.is_paused():
        ctx.voice_client.resume()
        await ctx.send("▶️ Музыка возобновлена.")
        
@bot.hybrid_command(name="queue", description="Показать очередь треков")
async def queue(ctx):
    guild_id = ctx.guild.id
    if guild_id in music_queues and music_queues[guild_id]:
        q_list = "\n".join([f"**{i+1}.** {track['title']}" for i, track in enumerate(music_queues[guild_id])])
        embed = discord.Embed(title="🎶 Очередь воспроизведения", description=q_list, color=discord.Color.blurple())
        await ctx.send(embed=embed)
    else:
        await ctx.send("Очередь пуста.")

# --- Roleplay ---
@bot.hybrid_command(name="hug", description="Обнять пользователя")
async def hug(ctx, member: discord.Member):
    embed = discord.Embed(description=f"🤗 **{ctx.author.display_name}** обнимает **{member.display_name}**!", color=discord.Color.purple())
    embed.set_image(url="https://media.giphy.com/media/3M4NpbLCTxBqU/giphy.gif")
    await ctx.send(embed=embed)

@bot.hybrid_command(name="slap", description="Дать пощечину пользователю")
async def slap(ctx, member: discord.Member):
    embed = discord.Embed(description=f"💥 **{ctx.author.display_name}** дает пощечину **{member.display_name}**!", color=discord.Color.red())
    embed.set_image(url="https://media.giphy.com/media/jLeyZWgtwgr2U/giphy.gif")
    await ctx.send(embed=embed)

@bot.event
async def on_member_join(member):
    log_channel = bot.get_channel(LOG_CHANNEL_ID)
    if log_channel:
        embed = discord.Embed(title="📥 Участник присоединился", color=discord.Color.green())
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Пользователь", value=f"{member.mention} ({member.name})", inline=False)
        embed.add_field(name="ID", value=member.id, inline=True)
        embed.add_field(name="Аккаунт создан", value=discord.utils.format_dt(member.created_at, style="R"), inline=True)
        embed.set_footer(text=f"Теперь нас {member.guild.member_count}!")
        await log_channel.send(embed=embed)

@bot.event
async def on_member_remove(member):
    log_channel = bot.get_channel(LOG_CHANNEL_ID)
    if log_channel:
        embed = discord.Embed(title="📤 Участник вышел", color=discord.Color.red())
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Пользователь", value=f"{member.mention} ({member.name})", inline=False)
        embed.add_field(name="ID", value=member.id, inline=True)
        embed.set_footer(text=f"Осталось {member.guild.member_count} участников.")
        await log_channel.send(embed=embed)

@bot.event
async def on_message_delete(message):
    if message.author.bot: return
    log_channel = bot.get_channel(LOG_CHANNEL_ID)
    if log_channel:
        embed = discord.Embed(title="🗑️ Сообщение удалено", color=discord.Color.red())
        embed.add_field(name="Автор", value=message.author.mention, inline=True)
        embed.add_field(name="Канал", value=message.channel.mention, inline=True)
        embed.add_field(name="Текст", value=message.content or "Нет текста", inline=False)
        await log_channel.send(embed=embed)

@bot.event
async def on_message_edit(before, after):
    if before.author.bot or before.content == after.content: return
    log_channel = bot.get_channel(LOG_CHANNEL_ID)
    if log_channel:
        embed = discord.Embed(title="✏️ Сообщение изменено", color=discord.Color.yellow())
        embed.add_field(name="Автор", value=before.author.mention, inline=True)
        embed.add_field(name="Канал", value=before.channel.mention, inline=True)
        embed.add_field(name="Было", value=before.content or "Нет текста", inline=False)
        embed.add_field(name="Стало", value=after.content or "Нет текста", inline=False)
        await log_channel.send(embed=embed)

# Запуск бота: вставь свой токен Discord в кавычках
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
if not DISCORD_TOKEN:
    print("❌ Токен бота не найден в .env файле!")
else:
    # Запускаем Flask dashboard в отдельном потоке (если нужно)
    def _run_dashboard():
        try:
            app = create_app(bot)
            app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=False, use_reloader=False)
        except Exception as e:
            print(f"[dashboard] failed to start: {e}")

    dashboard_thread = threading.Thread(target=_run_dashboard, daemon=True)
    dashboard_thread.start()

    bot.run(DISCORD_TOKEN)

# --- AutoMod sync utilities ---
async def discord_api_request(method: str, path: str, json_payload=None):
    """Простой HTTP wrapper для Discord API вызовов с бот-токеном"""
    token = os.getenv('DISCORD_TOKEN')
    if not token:
        raise RuntimeError('DISCORD_TOKEN not set')
    url = f"https://discord.com/api/v10{path}"
    headers = {"Authorization": f"Bot {token}", "Content-Type": "application/json"}
    async with aiohttp.ClientSession() as session:
        async with session.request(method, url, headers=headers, json=json_payload) as resp:
            try:
                data = await resp.json()
            except Exception:
                data = await resp.text()
            return resp.status, data


AUTOMOD_PREFIX = "AutoModBot - "

@bot.hybrid_command(name='automod_sync', description='Создать/синхронизировать AutoMod правила (админ)')
@commands.has_permissions(manage_guild=True)
async def automod_sync(ctx, log_channel: discord.TextChannel = None):
    """Создает набор правил AutoMod по правилам сервера.
    Если у бота нет прав — сохраняет JSON локально и отправляет файл админу.
    """
    guild = ctx.guild
    if not guild:
        return await ctx.send('Эта команда работает только на сервере.', ephemeral=True)

    # Правила (упрощённо)
    rules = []
    # 1) Profanity (preset)
    rules.append({
        'name': AUTOMOD_PREFIX + 'Profanity',
        'event_type': 1,
        'trigger_type': 1,
        'trigger_metadata': {'presets': ['profanity']},
        'actions': [{'type': 1}]
    })
    # 2) Invite/adverts
    rules.append({
        'name': AUTOMOD_PREFIX + 'Invites/Ads',
        'event_type': 1,
        'trigger_type': 1,
        'trigger_metadata': {'keyword_filter': ['discord.gg', 'discord.com/invite', 'invite', 'joinserver', 'дискорд']},
        'actions': [{'type': 1}]
    })
    # 3) Doxxing (simple detection of emails/phones via keywords)
    rules.append({
        'name': AUTOMOD_PREFIX + 'PII/Dox',
        'event_type': 1,
        'trigger_type': 1,
        'trigger_metadata': {'keyword_filter': ['@gmail.com', '@yahoo.com', 'http', 'тел:', 'телефон', '+7', '+1']},
        'actions': [{'type': 1}]
    })

    # Если у бота нет права Manage Guild — не посылаем запрос, а сохраняем JSON
    me = guild.get_member(bot.user.id)
    can_manage = me and me.guild_permissions.manage_guild

    if not can_manage:
        # Сохраняем JSON локально
        filename = f"automod_rules_{guild.id}.json"
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(rules, f, ensure_ascii=False, indent=2)
        await ctx.send(f"У меня нет права Manage Guild. Я сохранил JSON правил в файле {filename}.")
        await ctx.send(file=discord.File(filename))
        return

    created = []
    # Пытаемся создать правила через API
    for r in rules:
        status, data = await discord_api_request('POST', f'/guilds/{guild.id}/auto-moderation/rules', json_payload=r)
        if status in (200, 201):
            created.append(data)
        else:
            await ctx.send(f"Ошибка при создании правила {r['name']}: {status} / {data}")

    await ctx.send(f"Готово. Создано правил: {len(created)}. Проверьте панель AutoMod в настройках сервера.")
    # Логирование
    log_ch = log_channel or bot.get_channel(LOG_CHANNEL_ID)
    if log_ch:
        embed = discord.Embed(title='AutoMod Sync', description=f'Создано правил: {len(created)}', color=discord.Color.green())
        await log_ch.send(embed=embed)


@bot.hybrid_command(name='automod_clear', description='Удалить AutoMod правила, созданные ботом (админ)')
@commands.has_permissions(manage_guild=True)
async def automod_clear(ctx):
    guild = ctx.guild
    if not guild:
        return await ctx.send('Эта команда работает только на сервере.', ephemeral=True)
    me = guild.get_member(bot.user.id)
    can_manage = me and me.guild_permissions.manage_guild
    if not can_manage:
        return await ctx.send('У меня нет права Manage Guild, не могу удалить правила.', ephemeral=True)

    status, data = await discord_api_request('GET', f'/guilds/{guild.id}/auto-moderation/rules')
    if status != 200:
        return await ctx.send(f'Не удалось получить список правил: {status} / {data}')
    rules = data
    removed = 0
    for r in rules:
        if isinstance(r.get('name'), str) and r['name'].startswith(AUTOMOD_PREFIX):
            rid = r['id']
            s2, d2 = await discord_api_request('DELETE', f'/guilds/{guild.id}/auto-moderation/rules/{rid}')
            if s2 in (200, 204):
                removed += 1
    await ctx.send(f'Удалено правил: {removed}')

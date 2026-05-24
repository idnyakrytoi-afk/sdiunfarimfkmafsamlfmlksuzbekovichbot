import aiosqlite

DB_NAME = "bot_database.db"

async def init_db():
    """Инициализация базы данных и создание таблиц"""
    async with aiosqlite.connect(DB_NAME) as db:
        # Таблица пользователей
        await db.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY,
                messages INTEGER DEFAULT 0,
                voice_seconds REAL DEFAULT 0.0,
                last_message_ts REAL DEFAULT 0.0,
                name TEXT,
                avatar TEXT,
                balance INTEGER DEFAULT 0,
                bank INTEGER DEFAULT 0,
                last_work_ts REAL DEFAULT 0.0,
                last_crime_ts REAL DEFAULT 0.0,
                last_rob_ts REAL DEFAULT 0.0,
                reputation INTEGER DEFAULT 0,
                last_rep_ts REAL DEFAULT 0.0,
                last_daily_ts REAL DEFAULT 0.0,
                daily_streak INTEGER DEFAULT 0
            )
        ''')
        
        # Таблица данных сервера (например, джекпот)
        await db.execute('''
            CREATE TABLE IF NOT EXISTS server_data (
                key TEXT PRIMARY KEY,
                value INTEGER DEFAULT 0
            )
        ''')
        
        # Инициализируем джекпот, если его нет
        await db.execute('INSERT OR IGNORE INTO server_data (key, value) VALUES (?, ?)', ("jackpot", 1000))
        await db.commit()
        
        # Безопасное добавление новых колонок в существующую базу (чтобы старая не сломалась)
        try:
            await db.execute('ALTER TABLE users ADD COLUMN bank INTEGER DEFAULT 0')
            await db.execute('ALTER TABLE users ADD COLUMN last_crime_ts REAL DEFAULT 0.0')
            await db.execute('ALTER TABLE users ADD COLUMN last_rob_ts REAL DEFAULT 0.0')
            await db.execute('ALTER TABLE users ADD COLUMN season_points INTEGER DEFAULT 0')
            await db.commit()
        except Exception:
            pass # Если колонки уже существуют, просто игнорируем ошибку

async def get_all_users():
    """Вернуть всех пользователей (list of rows)"""
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('SELECT * FROM users') as cursor:
            return await cursor.fetchall()

async def get_user(user_id: str):
    """Получить данные пользователя (возвращает dict-like объект)"""
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('SELECT * FROM users WHERE user_id = ?', (user_id,)) as cursor:
            row = await cursor.fetchone()
            if row is None:
                # Если юзера нет, создаем его
                await db.execute('INSERT INTO users (user_id) VALUES (?)', (user_id,))
                await db.commit()
                async with db.execute('SELECT * FROM users WHERE user_id = ?', (user_id,)) as new_cursor:
                    return await new_cursor.fetchone()
            return row

async def update_user(user_id: str, **kwargs):
    """Удобная функция для обновления любых полей юзера"""
    if not kwargs:
        return
        
    set_clause = ", ".join([f"{k} = ?" for k in kwargs.keys()])
    values = tuple(kwargs.values()) + (user_id,)
    
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(f'UPDATE users SET {set_clause} WHERE user_id = ?', values)
        await db.commit()

async def get_top_users(order_by: str, limit: int = 10):
    """Получить топ пользователей по определенному полю"""
    allowed_fields = ['messages', 'voice_seconds', 'balance']
    if order_by not in allowed_fields:
        return []
        
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(f'SELECT * FROM users WHERE {order_by} > 0 ORDER BY {order_by} DESC LIMIT ?', (limit,)) as cursor:
            return await cursor.fetchall()

async def get_server_data(key: str):
    """Получить значение из server_data по ключу"""
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute('SELECT value FROM server_data WHERE key = ?', (key,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0

async def update_server_data(key: str, value: int):
    """Обновить значение в server_data"""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('INSERT OR REPLACE INTO server_data (key, value) VALUES (?, ?)', (key, value))
        await db.commit()
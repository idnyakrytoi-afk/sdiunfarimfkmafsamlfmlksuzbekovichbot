import asyncio
import json
import aiosqlite
from database import init_db, DB_NAME

async def migrate():
    print("⏳ Начинаем миграцию данных...")
    await init_db()
    
    try:
        with open('users_data.json', 'r', encoding='utf-8') as f:
            users_data = json.load(f)
            
        async with aiosqlite.connect(DB_NAME) as db:
            for user_id, data in users_data.items():
                await db.execute('''
                    INSERT OR REPLACE INTO users 
                    (user_id, messages, voice_seconds, last_message_ts, name, avatar, 
                    balance, last_work_ts, reputation, last_rep_ts, last_daily_ts, daily_streak) 
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    str(user_id),
                    data.get('messages', 0),
                    data.get('voice_seconds', 0.0),
                    data.get('last_message_ts', 0.0),
                    data.get('name', ''),
                    data.get('avatar', ''),
                    data.get('balance', 0),
                    data.get('last_work_ts', 0.0),
                    data.get('reputation', 0),
                    data.get('last_rep_ts', 0.0),
                    data.get('last_daily_ts', 0.0),
                    data.get('daily_streak', 0)
                ))
            await db.commit()
        print(f"✅ Успешно перенесено {len(users_data)} пользователей в SQLite!")
    except Exception as e:
        print(f"❌ Ошибка миграции: {e}")

if __name__ == "__main__":
    asyncio.run(migrate())
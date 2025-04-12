import os
import logging
from logging.handlers import TimedRotatingFileHandler
import time
import threading
import schedule
from telebot import TeleBot
from database import init_db
from handlers import register_handlers

# Настройка путей
BASE_DIR = os.path.expanduser("~/")
DATA_DIR = os.path.join(BASE_DIR, "bot_data")
LOG_PATH = os.path.join(DATA_DIR, "bot.log")
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR, exist_ok=True)

# Настройка логирования
logger = logging.getLogger("BotLogger")
logger.setLevel(logging.INFO)
formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)
try:
    handler = TimedRotatingFileHandler(LOG_PATH, when="midnight", backupCount=7)
    handler.setFormatter(formatter)
    logger.addHandler(handler)
except PermissionError as e:
    logger.error(f"Нет доступа к файлу логов: {e}, логирование в консоль")
except Exception as e:
    logger.error(f"Ошибка настройки файла логов: {e}, логирование в консоль")

# Константы (берём из переменных окружения для Render)
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x]  # Формат: "123,456"
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "@YourAdmin")
STORAGE_CHANNEL_ID = os.getenv("STORAGE_CHANNEL_ID")
PUBLIC_CHANNEL_ID = os.getenv("PUBLIC_CHANNEL_ID")
CATEGORY_MAP = {
    "Games": "🎮 Игры",
    "Apps": "📱 Приложения",
    "Docs": "📜 Документы"
}
RATE_LIMIT_SECONDS = 2
HOURS_24 = 24 * 60 * 60
REVIEW_RETENTION_DAYS = 30

# Инициализация бота
bot = TeleBot(TOKEN)

# Периодические задачи
def run_scheduled_tasks():
    from database import clean_old_reviews, clean_old_files, clean_top_users
    schedule.every(24).hours.do(clean_old_reviews)
    schedule.every(24).hours.do(clean_old_files)
    schedule.every(24).hours.do(clean_top_users)
    while True:
        schedule.run_pending()
        time.sleep(60)

if __name__ == "__main__":
    try:
        init_db()
        register_handlers(bot)
        scheduler_thread = threading.Thread(target=run_scheduled_tasks, daemon=True)
        scheduler_thread.start()
        logger.info("Бот в деле! 🚀")
        while True:
            try:
                bot.polling(none_stop=True)
            except Exception as e:
                logger.error(f"Polling рухнул: {e}")
                time.sleep(5)
    except Exception as e:
        logger.error(f"Критический косяк при запуске: {e}")
        import os
import psycopg2
from psycopg2 import pool
import redis
import time
from contextlib import contextmanager
import logging

logger = logging.getLogger("BotLogger")

# Настройка подключения к PostgreSQL (для Render)
DATABASE_URL = os.getenv("DATABASE_URL")
db_pool = psycopg2.pool.SimpleConnectionPool(1, 20, DATABASE_URL)

# Настройка Redis
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True)

HOURS_24 = 24 * 60 * 60
REVIEW_RETENTION_DAYS = 30

@contextmanager
def get_db():
    conn = db_pool.getconn()
    try:
        cursor = conn.cursor()
        yield cursor
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error(f"Ошибка базы данных: {e}")
        raise
    finally:
        cursor.close()
        db_pool.putconn(conn)

def init_db():
    try:
        with get_db() as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS files (
                    code TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    category TEXT NOT NULL,
                    file_id TEXT,
                    timestamp FLOAT DEFAULT EXTRACT(EPOCH FROM NOW())
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS download_stats (
                    code TEXT PRIMARY KEY,
                    downloads INTEGER DEFAULT 0,
                    FOREIGN KEY (code) REFERENCES files(code) ON DELETE CASCADE
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS ratings (
                    code TEXT,
                    rating INTEGER,
                    FOREIGN KEY (code) REFERENCES files(code) ON DELETE CASCADE
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    last_active FLOAT,
                    username TEXT
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS channels (
                    channel TEXT PRIMARY KEY
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS reviews (
                    id SERIAL PRIMARY KEY,
                    code TEXT,
                    user_id BIGINT,
                    review TEXT,
                    timestamp FLOAT DEFAULT EXTRACT(EPOCH FROM NOW()),
                    FOREIGN KEY (code) REFERENCES files(code) ON DELETE CASCADE,
                    CONSTRAINT unique_code_user UNIQUE (code, user_id)
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS user_activity (
                    user_id BIGINT,
                    action TEXT,
                    timestamp FLOAT DEFAULT EXTRACT(EPOCH FROM NOW()),
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS feedback (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT,
                    message TEXT NOT NULL,
                    video_id TEXT,
                    feedback_type TEXT,
                    timestamp FLOAT DEFAULT EXTRACT(EPOCH FROM NOW()),
                    status TEXT DEFAULT 'new',
                    reply TEXT,
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                )
            """)
            cursor.execute("""
                DELETE FROM reviews
                WHERE code NOT IN (SELECT code FROM files)
                OR timestamp < EXTRACT(EPOCH FROM NOW()) - %s
            """, (REVIEW_RETENTION_DAYS * HOURS_24,))
            cursor.execute("""
                DELETE FROM user_activity
                WHERE timestamp < EXTRACT(EPOCH FROM NOW()) - %s
            """, (HOURS_24,))
            logger.info("База данных готова, устаревшие данные удалены")
    except psycopg2.Error as e:
        logger.error(f"Ошибка инициализации базы данных: {e}")
        raise

def clean_old_reviews():
    try:
        with get_db() as cursor:
            threshold = time.time() - 30 * 24 * 60 * 60
            cursor.execute("DELETE FROM reviews WHERE timestamp < %s", (threshold,))
        logger.info("Глобальная очистка: старые отзывы (старше 30 дней) удалены")
    except psycopg2.Error as e:
        logger.error(f"Ошибка глобальной очистки старых отзывов: {e}")

def clean_old_reviews_for_file(code, max_pages=5, per_page=3):
    try:
        with get_db() as cursor:
            cursor.execute("SELECT COUNT(*) FROM reviews WHERE code = %s", (code,))
            total_reviews = cursor.fetchone()[0]
            total_pages = (total_reviews + per_page - 1) // per_page
            
            if total_pages <= 3:
                logger.info(f"Отзывов для файла {code} меньше 3 страниц ({total_pages}), пропускаем очистку")
                return
            
            if total_pages >= max_pages:
                threshold = time.time() - 10 * 24 * 60 * 60  # 10 дней
                excess = total_reviews - (max_pages * per_page)
                cursor.execute(
                    """
                    DELETE FROM reviews
                    WHERE code = %s AND timestamp < %s
                    ORDER BY timestamp ASC
                    LIMIT %s
                    """,
                    (code, threshold, excess)
                )
                logger.info(f"Удалено {cursor.rowcount} старых отзывов для файла {code}")
            else:
                logger.info(f"Отзывов для файла {code} меньше 5 страниц ({total_pages}), но больше 3, ждём заполнения")
    except psycopg2.Error as e:
        logger.error(f"Ошибка очистки отзывов для {code}: {e}")

def clean_old_files():
    try:
        with get_db() as cursor:
            threshold = time.time() - 10 * 24 * 60 * 60
            cursor.execute("DELETE FROM files WHERE timestamp < %s RETURNING code", (threshold,))
            deleted = cursor.fetchall()
            logger.info(f"Удалено {len(deleted)} старых файлов")
    except psycopg2.Error as e:
        logger.error(f"Ошибка очистки старых файлов: {e}")

def clean_top_users():
    try:
        with get_db() as cursor:
            cursor.execute("DELETE FROM user_activity WHERE action IN ('download', 'review')")
        logger.info("Статистика топ-5 пользователей очищена")
    except psycopg2.Error as e:
        logger.error(f"Ошибка очистки статистики топ-5: {e}")

def load_files():
    try:
        with get_db() as cursor:
            cursor.execute("SELECT code, name, category, file_id FROM files")
            files = {row[0].strip().lower(): {"name": row[1], "category": row[2], "file_id": row[3]}
                     for row in cursor.fetchall()}
            logger.info(f"Загружено {len(files)} файлов")
            return files
    except psycopg2.Error as e:
        logger.error(f"Ошибка загрузки файлов: {e}")
        return {}

def save_file(code, file_id, name, category):
    try:
        if not all([code, file_id, name, category]):
            logger.error(f"Некорректные данные: code={code}, file_id={file_id}, name={name}, category={category}")
            raise ValueError("Что-то пустое, бро! 😅")
        from main import CATEGORY_MAP
        if category not in CATEGORY_MAP.values():
            logger.error(f"Категория {category} — это что такое?")
            raise ValueError(f"Категория '{category}' не в списке!")
        code = code.strip().lower()
        with get_db() as cursor:
            cursor.execute("SELECT code FROM files WHERE code = %s", (code,))
            existing = cursor.fetchone()
            if existing:
                logger.info(f"Файл {code} уже есть, обновляю...")
                cursor.execute("UPDATE files SET file_id = %s, name = %s, category = %s WHERE code = %s",
                               (file_id, name, category, code))
            else:
                cursor.execute(
                    "INSERT INTO files (code, file_id, name, category, timestamp) VALUES (%s, %s, %s, %s, EXTRACT(EPOCH FROM NOW()))",
                    (code, file_id, name, category)
                )
                cursor.execute("INSERT INTO download_stats (code, downloads) VALUES (%s, 0) ON CONFLICT (code) DO NOTHING", (code,))
        logger.info(f"Файл {code} готов к раздаче в категории {category}! 🚀")
    except (psycopg2.Error, ValueError) as e:
        logger.error(f"Не вышло сохранить файл {code}: {str(e)}")
        raise

def delete_file(code):
    try:
        code = code.strip().lower()
        with get_db() as cursor:
            cursor.execute("DELETE FROM files WHERE code = %s", (code,))
        logger.info(f"Файл {code} и всё, что с ним связано, стёрто! 🗑️")
    except psycopg2.Error as e:
        logger.error(f"Не удалось удалить файл {code}: {e}")
        raise

def load_categories():
    try:
        from main import CATEGORY_MAP
        with get_db() as cursor:
            cursor.execute("SELECT code, category FROM files")
            result = {cat: [] for cat in CATEGORY_MAP.values()}
            for row in cursor.fetchall():
                code = row[0].strip().lower()
                category = row[1]
                if category in result:
                    result[category].append(code)
            return result
    except psycopg2.Error as e:
        logger.error(f"Ошибка загрузки категорий: {e}")
        return {cat: [] for cat in CATEGORY_MAP.values()}

def load_download_stats():
    try:
        with get_db() as cursor:
            cursor.execute("SELECT code, downloads FROM download_stats")
            stats = {row[0].strip().lower(): row[1] for row in cursor.fetchall()}
            logger.info(f"Статистика по {len(stats)} файлам загружена")
            return stats
    except psycopg2.Error as e:
        logger.error(f"Ошибка загрузки статистики скачиваний: {e}")
        return {}

def increment_download(code, user_id):
    try:
        from main import HOURS_24
        code = code.strip().lower()
        with get_db() as cursor:
            one_hour_ago = time.time() - 60 * 60
            cursor.execute(
                "SELECT COUNT(*) FROM user_activity WHERE user_id = %s AND action = 'download' AND timestamp > %s",
                (user_id, one_hour_ago)
            )
            download_count = cursor.fetchone()[0]
            if download_count >= 10:
                raise ValueError("Ты скачал слишком много! Максимум 10 файлов в час! ⏳")
            cursor.execute("UPDATE download_stats SET downloads = downloads + 1 WHERE code = %s", (code,))
            cursor.execute(
                "INSERT INTO user_activity (user_id, action, timestamp) VALUES (%s, %s, EXTRACT(EPOCH FROM NOW()))",
                (user_id, "download")
            )
        logger.info(f"Скачивание файла {code} для юзера {user_id} засчитано")
    except (psycopg2.Error, ValueError) as e:
        logger.error(f"Ошибка при записи скачивания {code} для {user_id}: {e}")
        raise

def save_rating(code, rating):
    try:
        code = code.strip().lower()
        with get_db() as cursor:
            cursor.execute("SELECT code FROM files WHERE code = %s", (code,))
            if not cursor.fetchone():
                logger.error(f"Файл {code} не найден для рейтинга")
                raise ValueError(f"Файл {code} куда-то делся! 😅")
            cursor.execute("INSERT INTO ratings (code, rating) VALUES (%s, %s)", (code, rating))
        logger.info(f"Рейтинг {rating} для файла {code} записан")
    except (psycopg2.Error, ValueError) as e:
        logger.error(f"Не вышло сохранить рейтинг для {code}: {e}")
        raise

def load_ratings():
    try:
        with get_db() as cursor:
            cursor.execute("SELECT code, rating FROM ratings")
            result = {}
            for row in cursor.fetchall():
                code = row[0].strip().lower()
                if code not in result:
                    result[code] = []
                result[code].append(row[1])
            return result
    except psycopg2.Error as e:
        logger.error(f"Ошибка загрузки рейтингов: {e}")
        return {}

def save_review(code, user_id, review):
    try:
        from main import HOURS_24
        if not code or not user_id or not review:
            logger.error(f"Пустой отзыв: code={code}, user_id={user_id}, review={review}")
            raise ValueError("Эй, отзыв не может быть пустым! 😜")
        if len(review) > 200:
            logger.error(f"Отзыв слишком длинный: {len(review)} символов")
            raise ValueError("Бери короче, до 200 символов! 📝")
        
        with get_db() as cursor:
            two_hours_ago = time.time() - 2 * 60 * 60
            cursor.execute(
                "SELECT COUNT(*) FROM reviews WHERE user_id = %s AND timestamp > %s",
                (user_id, two_hours_ago)
            )
            review_count = cursor.fetchone()[0]
            if review_count >= 3:
                raise ValueError("Ты уже оставил 3 отзыва за 2 часа! Подожди немного! ⏳")
        
        code = code.strip().lower()
        with get_db() as cursor:
            cursor.execute("SELECT code FROM files WHERE code = %s", (code,))
            if not cursor.fetchone():
                logger.error(f"Файл {code} не найден для отзыва")
                raise ValueError(f"Файл {code} пропал! 😱")
            cursor.execute(
                "SELECT id FROM reviews WHERE code = %s AND user_id = %s",
                (code, user_id)
            )
            existing_review = cursor.fetchone()
            if existing_review:
                logger.info(f"Юзер {user_id} уже оставил отзыв для {code}")
                raise ValueError("Эй, ты уже написал отзыв для этого файла! 😎")
            cursor.execute(
                "INSERT INTO reviews (code, user_id, review, timestamp) VALUES (%s, %s, %s, EXTRACT(EPOCH FROM NOW()))",
                (code, user_id, review)
            )
            cursor.execute(
                "INSERT INTO user_activity (user_id, action, timestamp) VALUES (%s, %s, EXTRACT(EPOCH FROM NOW()))",
                (user_id, "review")
            )
        logger.info(f"Отзыв для {code} от юзера {user_id} сохранён: {review[:50]}...")
    except (psycopg2.Error, ValueError) as e:
        logger.error(f"Ошибка сохранения отзыва для {code}, юзер {user_id}: {str(e)}")
        raise

def load_reviews():
    try:
        with get_db() as cursor:
            cursor.execute("SELECT id, code, user_id, review FROM reviews")
            result = {}
            for row in cursor.fetchall():
                code = row[1].strip().lower() if row[1] else None
                if not code:
                    logger.warning(f"Отзыв с пустым code: id={row[0]}")
                    continue
                if code not in result:
                    result[code] = []
                result[code].append({"id": row[0], "code": code, "user_id": row[2], "text": row[3]})
            for code in result:
                clean_old_reviews_for_file(code)
            logger.info(f"Загружено {sum(len(r) for r in result.values())} отзывов")
            return result
    except psycopg2.Error as e:
        logger.error(f"Ошибка загрузки отзывов: {str(e)}")
        return {}

def get_reviews_count():
    try:
        with get_db() as cursor:
            cursor.execute("SELECT COUNT(*) FROM reviews r JOIN files f ON r.code = f.code")
            count = cursor.fetchone()[0]
            logger.info(f"Всего {count} отзывов")
            return count
    except psycopg2.Error as e:
        logger.error(f"Ошибка подсчёта отзывов: {e}")
        return 0

def delete_review(review_id):
    try:
        with get_db() as cursor:
            cursor.execute("DELETE FROM reviews WHERE id = %s", (review_id,))
        logger.info(f"Отзыв {review_id} стёрт")
    except psycopg2.Error as e:
        logger.error(f"Ошибка удаления отзыва {review_id}: {e}")

def save_user(user_id, username=None):
    try:
        with get_db() as cursor:
            cursor.execute(
                "INSERT INTO users (user_id, last_active, username) VALUES (%s, EXTRACT(EPOCH FROM NOW()), %s) "
                "ON CONFLICT (user_id) DO UPDATE SET last_active = EXTRACT(EPOCH FROM NOW()), username = EXCLUDED.username",
                (user_id, username)
            )
        logger.info(f"Юзер {user_id} сохранён, username: {username}")
    except psycopg2.Error as e:
        logger.error(f"Ошибка сохранения юзера {user_id}: {e}")

def get_users():
    try:
        with get_db() as cursor:
            cursor.execute("SELECT user_id FROM users")
            return [row[0] for row in cursor.fetchall()]
    except psycopg2.Error as e:
        logger.error(f"Ошибка загрузки юзеров: {e}")
        return []

def get_active_users():
    try:
        from main import HOURS_24
        with get_db() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM users WHERE last_active > EXTRACT(EPOCH FROM NOW()) - %s",
                (HOURS_24,)
            )
            return cursor.fetchone()[0]
    except psycopg2.Error as e:
        logger.error(f"Ошибка подсчёта активных юзеров: {e}")
        return 0

def get_top_users():
    try:
        with get_db() as cursor:
            cursor.execute("""
                SELECT u.user_id, u.username,
                       SUM(CASE WHEN a.action = 'download' THEN 1 ELSE 0 END) as downloads,
                       SUM(CASE WHEN a.action = 'review' THEN 1 ELSE 0 END) as reviews
                FROM users u
                LEFT JOIN user_activity a ON u.user_id = a.user_id
                WHERE a.action IN ('download', 'review')
                GROUP BY u.user_id, u.username
                ORDER BY (COALESCE(downloads, 0) + COALESCE(reviews, 0)) DESC
                LIMIT 5
            """)
            return [
                {
                    "user_id": row[0],
                    "username": row[1] or f"ID{row[0]}",
                    "downloads": row[2] or 0,
                    "reviews": row[3] or 0
                }
                for row in cursor.fetchall()
            ]
    except psycopg2.Error as e:
        logger.error(f"Ошибка загрузки топа юзеров: {e}")
        return []

def load_channels():
    try:
        with get_db() as cursor:
            cursor.execute("SELECT channel FROM channels")
            return [row[0] for row in cursor.fetchall()]
    except psycopg2.Error as e:
        logger.error(f"Ошибка загрузки каналов: {e}")
        return []

def save_channel(channel):
    try:
        with get_db() as cursor:
            cursor.execute("INSERT INTO channels (channel) VALUES (%s) ON CONFLICT (channel) DO NOTHING", (channel,))
        logger.info(f"Канал {channel} добавлен")
    except psycopg2.Error as e:
        logger.error(f"Ошибка сохранения канала {channel}: {e}")

def remove_channel(channel):
    try:
        with get_db() as cursor:
            cursor.execute("DELETE FROM channels WHERE channel = %s", (channel,))
        logger.info(f"Канал {channel} удалён")
    except psycopg2.Error as e:
        logger.error(f"Ошибка удаления канала {channel}: {e}")

def save_feedback(user_id, message, video_id=None, feedback_type=None):
    try:
        from main import ADMIN_IDS
        with get_db() as cursor:
            cursor.execute(
                """
                INSERT INTO feedback (user_id, message, video_id, feedback_type, timestamp)
                VALUES (%s, %s, %s, %s, EXTRACT(EPOCH FROM NOW()))
                RETURNING id
                """,
                (user_id, message, video_id, feedback_type)
            )
            feedback_id = cursor.fetchone()[0]
            for admin_id in ADMIN_IDS:
                bot = TeleBot(os.getenv("BOT_TOKEN"))
                buttons = [
                    create_inline_button("📝 Ответить", callback_data=f"reply_feedback_{feedback_id}"),
                    create_inline_button("📩 Отправить ЛС", callback_data=f"send_pm_{user_id}")
                ]
                markup = create_inline_markup(buttons)
                bot.send_message(
                    admin_id,
                    f"Новый отзыв (ID: {feedback_id}) от пользователя {user_id} (Тип: {feedback_type}):\n{message}" +
                    (f"\nВидео: [Видео прикреплено]" if video_id else ""),
                    reply_markup=markup
                )
                if video_id:
                    bot.send_video(admin_id, video_id)
        logger.info(f"Обратная связь от {user_id} сохранена, ID: {feedback_id}")
        return feedback_id
    except psycopg2.Error as e:
        logger.error(f"Ошибка сохранения обратной связи: {e}")
        raise

def load_feedback(page=0, per_page=5):
    try:
        offset = page * per_page
        with get_db() as cursor:
            cursor.execute(
                """
                SELECT id, user_id, message, video_id, feedback_type, timestamp, status, reply
                FROM feedback
                ORDER BY timestamp DESC
                LIMIT %s OFFSET %s
                """,
                (per_page, offset)
            )
            feedbacks = [
                {
                    "id": row[0],
                    "user_id": row[1],
                    "message": row[2],
                    "video_id": row[3],
                    "feedback_type": row[4],
                    "timestamp": row[5],
                    "status": row[6],
                    "reply": row[7]
                }
                for row in cursor.fetchall()
            ]
            cursor.execute("SELECT COUNT(*) FROM feedback")
            total = cursor.fetchone()[0]
            return feedbacks, total
    except psycopg2.Error as e:
        logger.error(f"Ошибка загрузки отзывов: {e}")
        return [], 0

def update_feedback(feedback_id, status, reply=None):
    try:
        with get_db() as cursor:
            if reply:
                cursor.execute(
                    "UPDATE feedback SET status = %s, reply = %s WHERE id = %s",
                    (status, reply, feedback_id)
                )
            else:
                cursor.execute(
                    "UPDATE feedback SET status = %s WHERE id = %s",
                    (status, feedback_id)
                )
        logger.info(f"Обратная связь {feedback_id} обновлена, статус: {status}")
    except psycopg2.Error as e:
        logger.error(f"Ошибка обновления обратной связи {feedback_id}: {e}")
        raise
        import time
import re
from telebot import types
from telebot.apihelper import ApiTelegramException
import logging

logger = logging.getLogger("BotLogger")

user_last_request = {}

def rate_limit(seconds=2):
    def decorator(func):
        def wrapper(message, *args, **kwargs):
            user_id = message.from_user.id
            current_time = time.time()
            last_request = user_last_request.get(user_id, 0)
            if current_time - last_request < seconds:
                return None
            user_last_request[user_id] = current_time
            return func(message, *args, **kwargs)
        return wrapper
    return decorator

def create_inline_button(text, callback_data=None, url=None):
    if callback_data and len(callback_data.encode('utf-8')) > 64:
        logger.error(f"callback_data длиннее 64 байт: {callback_data}")
        raise ValueError("callback_data слишком длинное!")
    if url:
        return types.InlineKeyboardButton(text, url=url)
    return types.InlineKeyboardButton(text, callback_data=callback_data)

def create_inline_markup(buttons, row_width=2):
    markup = types.InlineKeyboardMarkup(row_width=row_width)
    if isinstance(buttons[0], list):
        for row in buttons:
            markup.row(*row)
    else:
        for i in range(0, len(buttons), row_width):
            markup.row(*buttons[i:i + row_width])
    return markup

def create_inline_markup_with_rows(button_rows):
    markup = types.InlineKeyboardMarkup()
    for row in button_rows:
        markup.row(*[create_inline_button(btn["text"], callback_data=btn.get("callback_data"), url=btn.get("url")) for btn in row])
    return markup

def check_subscription(user_id, channels, admin_ids):
    if user_id in admin_ids or not channels:
        return True
    bot = TeleBot(os.getenv("BOT_TOKEN"))
    for channel in channels:
        try:
            member = bot.get_chat_member(channel, user_id)
            if member.status not in ["member", "administrator", "creator"]:
                return False
        except ApiTelegramException as e:
            logger.error(f"Ошибка проверки подписки на {channel} для {user_id}: {e}")
            return False
    return True

def extract_channel_id(link):
    match = re.search(r't\.me/([a-zA-Z0-9_]+)', link)
    return f"@{match.group(1)}" if match else None

def validate_code(code):
    return bool(re.match(r"^[a-zA-Z0-9_\-\.]{1,50}$", code))
    import time
import threading
from telebot.apihelper import ApiTelegramException
import logging
from database import (load_files, save_file, delete_file, load_categories, load_download_stats, increment_download,
                     save_rating, load_ratings, save_review, load_reviews, get_reviews_count, delete_review,
                     save_user, get_users, get_active_users, get_top_users, load_channels, save_channel, remove_channel,
                     save_feedback, load_feedback, update_feedback)
from utils import rate_limit, create_inline_button, create_inline_markup, create_inline_markup_with_rows, check_subscription, extract_channel_id, validate_code

logger = logging.getLogger("BotLogger")

def register_handlers(bot):
    # Храним временные данные в Redis
    from database import redis_client

    # Главное меню
    def show_main_menu(chat_id, message_id=None):
        buttons = [
            create_inline_button("📂 Файлы", callback_data="catalog"),
            create_inline_button("ℹ️ Про бота", callback_data="about"),
            create_inline_button("📩 Обратная связь", callback_data="feedback"),
            create_inline_button("🏆 Топ-5", callback_data="top_users")
        ]
        markup = create_inline_markup(buttons)
        text = "Йо, чем займёмся? 😎"
        if message_id:
            bot.edit_message_text(text, chat_id, message_id, reply_markup=markup)
        else:
            bot.send_message(chat_id, text, reply_markup=markup)

    # Панель админа
    def show_admin_panel(chat_id, message_id=None):
        buttons = [
            create_inline_button("➕ Залить файл", callback_data="admin_add_file"),
            create_inline_button("📄 Все файлы", callback_data="admin_list_files_0"),
            create_inline_button("🗑️ Удалить файл", callback_data="admin_delete_file_page_0"),
            create_inline_button("📢 Разослать всем", callback_data="admin_broadcast"),
            create_inline_button("📢 Опубликовать в канал", callback_data="admin_publish_post"),
            create_inline_button("📊 Статы", callback_data="admin_stats"),
            create_inline_button("📌 Каналы", callback_data="admin_manage_channels"),
            create_inline_button("📬 Отзывы", callback_data="admin_view_feedback_0"),
            create_inline_button("⬅️ Назад", callback_data="main_menu")
        ]
        markup = create_inline_markup(buttons)
        text = "🔥 Панель босса! Что делаем?"
        if message_id:
            bot.edit_message_text(text, chat_id, message_id, reply_markup=markup)
        else:
            bot.send_message(chat_id, text, reply_markup=markup)

    # Отправка файла
    def send_file(chat_id, code, files, user_id):
        try:
            code = code.strip().lower()
            if code not in files or not files[code]["file_id"]:
                bot.send_message(chat_id, "Файл где-то спрятался! 🕵️‍♂️ Давай другой поищем?")
                return
            increment_download(code, user_id)
            bot.send_document(chat_id, files[code]["file_id"], caption="Лови свой файл! 🚀")
            buttons = [create_inline_button(f"{i} ⭐", callback_data=f"rate_{code}_{i}") for i in range(1, 6)]
            buttons.append(create_inline_button("✍️ Написать отзыв", callback_data=f"review_{code}"))
            markup = create_inline_markup(buttons, row_width=3)
            bot.send_message(chat_id, "Ну как файл? Поставь звёзды! 😎", reply_markup=markup)
        except ValueError as e:
            bot.send_message(chat_id, str(e))
        except ApiTelegramException as e:
            bot.send_message(chat_id, "Ой-ой, файл не долетел! 😅 Попробуй ещё раз позже.")
            logger.error(f"Ошибка отправки файла {code}: {e}")

    # Рассылка
    def send_broadcast(broadcast_data, users):
        success_count = 0
        failed_users = []
        def send_to_user(uid):
            nonlocal success_count, failed_users
            try:
                if broadcast_data["content_type"] == "text":
                    bot.send_message(uid, broadcast_data["text"])
                elif broadcast_data["content_type"] == "photo":
                    bot.send_photo(uid, broadcast_data["media_id"], caption=broadcast_data["caption"])
                elif broadcast_data["content_type"] == "video":
                    bot.send_video(uid, broadcast_data["media_id"], caption=broadcast_data["caption"])
                elif broadcast_data["content_type"] == "document":
                    bot.send_document(uid, broadcast_data["media_id"], caption=broadcast_data["caption"])
                success_count += 1
            except ApiTelegramException as e:
                logger.error(f"Ошибка рассылки юзеру {uid}: {e}")
                failed_users.append(uid)
        threads = []
        for uid in users:
            t = threading.Thread(target=send_to_user, args=(uid,))
            threads.append(t)
            t.start()
        for t in threads:
            t.join()
        return success_count, failed_users

    # Обработчики
    @bot.message_handler(commands=['start'])
    @rate_limit()
    def handle_start(message):
        try:
            from main import ADMIN_IDS
            username = f"@{message.from_user.username}" if message.from_user.username else None
            save_user(message.from_user.id, username)
            user_id = message.from_user.id
            chat_id = message.chat.id
            channels = load_channels()

            if not check_subscription(user_id, channels, ADMIN_IDS):
                buttons = [create_inline_button(f"📢 Подпишись на {ch}", url=f"https://t.me/{ch[1:]}") for ch in channels]
                buttons.append(create_inline_button("✅ Я подписался!", callback_data="check_subscription"))
                markup = create_inline_markup(buttons, row_width=1)
                bot.reply_to(message, "Эй, подпишись на наши каналы, там движуха! 😎", reply_markup=markup)
                return

            files = load_files()
            args = message.text.split()
            if len(args) > 1:
                code = args[1].strip().lower()
                if code in files:
                    send_file(chat_id, code, files, user_id)
                else:
                    bot.reply_to(message, f"Файл с кодом '{code}' пропал! 🕵️‍♂️ Загляни в каталог?",
                                 reply_markup=create_inline_markup([create_inline_button("📂 Каталог", callback_data="catalog")]))
            else:
                redis_client.hset(f"bot_state:{user_id}", "navigation_stack", "")
                show_main_menu(chat_id)
                if user_id in ADMIN_IDS:
                    show_admin_panel(chat_id)
        except Exception as e:
            logger.error(f"Косяк в handle_start для юзера {message.from_user.id}: {e}")
            bot.reply_to(message, "Ой-ой, что-то сломалось! 😅 Давай ещё раз?")

    @bot.message_handler(commands=['delete'])
    def handle_delete_file(message):
        from main import ADMIN_IDS
        if message.from_user.id not in ADMIN_IDS:
            bot.reply_to(message, "Только боссы могут удалять файлы! 😏")
            return
        args = message.text.split(maxsplit=1)
        if len(args) < 2:
            bot.reply_to(message, "Кидай код или имя файла после /delete! 😅")
            return
        search_term = args[1].strip().lower()
        try:
            with get_db() as cursor:
                cursor.execute(
                    "SELECT code FROM files WHERE code = %s OR lower(name) = %s",
                    (search_term, search_term)
                )
                file = cursor.fetchone()
                if file:
                    delete_file(file[0])
                    bot.reply_to(message, f"Файл с кодом '{file[0]}' удалён! ✅")
                else:
                    bot.reply_to(message, f"Файл '{search_term}' не найден! 🕵️‍♂️")
        except psycopg2.Error as e:
            logger.error(f"Ошибка удаления файла {search_term}: {e}")
            bot.reply_to(message, "Не получилось удалить файл! 😅 Попробуй ещё раз.")

    @bot.message_handler(commands=['feedback'])
    @rate_limit()
    def handle_feedback_command(message):
        try:
            username = f"@{message.from_user.username}" if message.from_user.username else None
            save_user(message.from_user.id, username)
            chat_id = message.chat.id
            buttons = [
                create_inline_button("📹 Добавить видео", callback_data="feedback_add_video"),
                create_inline_button("🚀 Улучшить", callback_data="feedback_improve"),
                create_inline_button("🐞 Сообщить об ошибке", callback_data="feedback_report_error")
            ]
            markup = create_inline_markup(buttons, row_width=1)
            bot.reply_to(message, "Выбери тип обращения: 📩", reply_markup=markup)
        except Exception as e:
            logger.error(f"Ошибка в handle_feedback_command для юзера {message.from_user.id}: {e}")
            bot.reply_to(message, "Ой, что-то пошло не так! 😅 Попробуй ещё раз.")

    @bot.message_handler(commands=['reply'])
    def handle_reply(message):
        from main import ADMIN_IDS
        if message.from_user.id not in ADMIN_IDS:
            bot.reply_to(message, "Только админы могут отвечать! 😏")
            return
        args = message.text.split(maxsplit=2)
        if len(args) < 3:
            bot.reply_to(message, "Используй: /reply <feedback_id> <текст ответа>")
            return
        try:
            feedback_id = int(args[1])
            reply_text = args[2]
            with get_db() as cursor:
                cursor.execute(
                    "SELECT user_id FROM feedback WHERE id = %s AND status = 'new'",
                    (feedback_id,)
                )
                feedback = cursor.fetchone()
                if not feedback:
                    bot.reply_to(message, "Не найдено новых отзывов с таким ID! 🕵️‍♂️")
                    return
                user_id = feedback[0]
                update_feedback(feedback_id, status="replied", reply=reply_text)
            bot.send_message(user_id, f"Админ ответил на твой отзыв (ID: {feedback_id}):\n{reply_text}")
            bot.reply_to(message, f"Ответ отправлен пользователю {user_id}! ✅")
        except ValueError:
            bot.reply_to(message, "Feedback_id должен быть числом! 😅")
        except ApiTelegramException as e:
            bot.reply_to(message, "Не удалось отправить ответ! 😅 Возможно, пользователь заблокировал бота.")
            logger.error(f"Ошибка отправки ответа пользователю {user_id}: {e}")
        except psycopg2.Error as e:
            logger.error(f"Ошибка в handle_reply: {e}")
            bot.reply_to(message, "Ошибка базы данных! 😅 Попробуй ещё раз.")
            @bot.callback_query_handler(func=lambda call: True)
    def callback_query(call):
        from main import ADMIN_IDS, CATEGORY_MAP, STORAGE_CHANNEL_ID, PUBLIC_CHANNEL_ID
        user_id = call.from_user.id
        chat_id = call.message.chat.id
        message_id = call.message.message_id
        files = load_files()
        categories = load_categories()
        channels = load_channels()
        ratings = load_ratings()
        download_stats = load_download_stats()

        navigation_stack = redis_client.hget(f"bot_state:{user_id}", "navigation_stack")
        navigation_stack = navigation_stack.split(",") if navigation_stack else []
        if not navigation_stack:
            redis_client.hset(f"bot_state:{user_id}", "navigation_stack", "")

        try:
            if call.data == "main_menu":
                redis_client.hset(f"bot_state:{user_id}", "navigation_stack", "")
                show_main_menu(chat_id, message_id)

            elif call.data == "top_users":
                navigation_stack.append("main_menu")
                redis_client.hset(f"bot_state:{user_id}", "navigation_stack", ",".join(navigation_stack))
                top_users = get_top_users()
                text = "🏆 Топ-5 юзеров за 24 часа:\n"
                if not top_users:
                    text += "Пока тут пусто, будь первым в топе! 😎"
                else:
                    for i, user in enumerate(top_users, 1):
                        text += f"{i}. {user['username']} — 📥 {user['downloads']} скачиваний, ✍️ {user['reviews']} отзывов\n"
                buttons = [create_inline_button("⬅️ Назад", callback_data="main_menu")]
                markup = create_inline_markup(buttons)
                bot.edit_message_text(text, chat_id, message_id, reply_markup=markup)
                logger.info(f"Юзер {user_id} посмотрел топ-5")

            elif call.data == "check_subscription":
                if check_subscription(user_id, channels, ADMIN_IDS):
                    redis_client.hset(f"bot_state:{user_id}", "navigation_stack", "")
                    show_main_menu(chat_id, message_id)
                    if user_id in ADMIN_IDS:
                        show_admin_panel(chat_id)
                else:
                    buttons = [create_inline_button(f"📢 Подпишись на {ch}", url=f"https://t.me/{ch[1:]}") for ch in channels]
                    buttons.append(create_inline_button("✅ Я подписался!", callback_data="check_subscription"))
                    markup = create_inline_markup(buttons, row_width=1)
                    bot.edit_message_text("Эй, подпишись на каналы, без этого не пущу! 😜", chat_id, message_id, reply_markup=markup)

            elif call.data == "catalog":
                navigation_stack.append("main_menu")
                redis_client.hset(f"bot_state:{user_id}", "navigation_stack", ",".join(navigation_stack))
                buttons = [create_inline_button(cat, callback_data=f"category_{key}_0") for key, cat in CATEGORY_MAP.items()]
                buttons.append(create_inline_button("⬅️ Назад", callback_data="main_menu"))
                markup = create_inline_markup(buttons)
                bot.edit_message_text("Выбирай категорию, что качать будем? 📂", chat_id, message_id, reply_markup=markup)

            elif call.data.startswith("category_"):
                parts = call.data.split("_")
                category_key = parts[1]
                page = int(parts[2])
                category = CATEGORY_MAP.get(category_key)
                if category in categories and categories[category]:
                    per_page = 5
                    total_pages = (len(categories[category]) + per_page - 1) // per_page
                    start = page * per_page
                    end = start + per_page
                    buttons = []
                    for code in categories[category][start:end]:
                        avg_rating = sum(ratings.get(code, [])) / len(ratings.get(code, [])) if ratings.get(code) else 0
                        btn_text = f"{files[code]['name']} (⭐ {avg_rating:.1f})"
                        buttons.append(create_inline_button(btn_text, callback_data=f"file_info_{code}"))
                    nav_buttons = []
                    if page > 0:
                        nav_buttons.append(create_inline_button("⬅️ Назад", callback_data=f"category_{category_key}_{page-1}"))
                    if page < total_pages - 1:
                        nav_buttons.append(create_inline_button("Вперёд ➡️", callback_data=f"category_{category_key}_{page+1}"))
                    nav_buttons.append(create_inline_button("⬅️ К категориям", callback_data="catalog"))
                    buttons.extend(nav_buttons)
                    markup = create_inline_markup(buttons, row_width=1)
                    navigation_stack.append(f"catalog")
                    redis_client.hset(f"bot_state:{user_id}", "navigation_stack", ",".join(navigation_stack))
                    bot.edit_message_text(f"Вот файлы в '{category}' (страница {page + 1}/{total_pages}):", chat_id, message_id, reply_markup=markup)
                else:
                    buttons = [create_inline_button("⬅️ К категориям", callback_data="catalog")]
                    markup = create_inline_markup(buttons)
                    bot.edit_message_text(f"В '{category}' пока пусто, загляни позже! 😏", chat_id, message_id, reply_markup=markup)

            elif call.data.startswith("file_info_"):
                code = call.data.replace("file_info_", "").strip().lower()
                if code not in files:
                    bot.edit_message_text("Файл куда-то делся! 😱 Вернись в каталог!", chat_id, message_id,
                                         reply_markup=create_inline_markup([create_inline_button("📂 Каталог", callback_data="catalog")]))
                    return
                avg_rating = sum(ratings.get(code, [])) / len(ratings.get(code, [])) if ratings.get(code) else 0
                downloads = download_stats.get(code, 0)
                text = (f"📄 Файл: {files[code]['name']}\n"
                        f"⭐ Рейтинг: {avg_rating:.1f}\n"
                        f"📥 Скачиваний: {downloads}")
                category = files[code]['category']
                category_key = next((key for key, value in CATEGORY_MAP.items() if value == category), list(CATEGORY_MAP.keys())[0])
                buttons = [
                    create_inline_button("📥 Качнуть", callback_data=f"download_{code}"),
                    create_inline_button("💬 Отзывы", callback_data=f"reviews_{code}_0"),
                    create_inline_button("⬅️ Назад", callback_data=f"category_{category_key}_0")
                ]
                markup = create_inline_markup(buttons, row_width=2)
                navigation_stack.append(f"category_{category_key}_0")
                redis_client.hset(f"bot_state:{user_id}", "navigation_stack", ",".join(navigation_stack))
                bot.edit_message_text(text, chat_id, message_id, reply_markup=markup)
                elif call.data.startswith("reviews_"):
                parts = call.data.split("_")
                if len(parts) != 3:
                    bot.edit_message_text("Ой, отзывы не загрузились! 😅 Попробуй ещё раз!", chat_id, message_id,
                                         reply_markup=create_inline_markup([create_inline_button("⬅️ Каталог", callback_data="catalog")]))
                    return
                code, page = parts[1].strip().lower(), int(parts[2])
                if code not in files:
                    bot.edit_message_text("Файл пропал! 😱 Вернись в каталог!", chat_id, message_id,
                                         reply_markup=create_inline_markup([create_inline_button("📂 Каталог", callback_data="catalog")]))
                    return
                reviews = load_reviews()
                rev_list = reviews.get(code, [])
                per_page = 3
                max_pages = 5
                total_pages = min(max_pages, (len(rev_list) + per_page - 1) // per_page)
                if page >= total_pages:
                    page = total_pages - 1
                if page < 0:
                    page = 0
                start = page * per_page
                end = min(start + per_page, len(rev_list))
                review_text = f"📄 Отзывы для {files[code]['name']} (страница {page + 1}/{total_pages}):\n"
                if rev_list:
                    for i, review in enumerate(rev_list[start:end], start=start + 1):
                        review_text += f"{i}. {review['text']}\n"
                else:
                    review_text += "Пока тишина, напиши первый отзыв! 😜\n"
                buttons = [
                    create_inline_button("✍️ Добавить отзыв", callback_data=f"review_{code}")
                ]
                if page > 0:
                    buttons.append(create_inline_button("⬅️ Назад", callback_data=f"reviews_{code}_{page-1}"))
                if page < total_pages - 1:
                    buttons.append(create_inline_button("Вперёд ➡️", callback_data=f"reviews_{code}_{page+1}"))
                buttons.append(create_inline_button("⬅️ К файлу", callback_data=f"file_info_{code}"))
                markup = create_inline_markup(buttons, row_width=2)
                navigation_stack.append(f"file_info_{code}")
                redis_client.hset(f"bot_state:{user_id}", "navigation_stack", ",".join(navigation_stack))
                bot.edit_message_text(review_text, chat_id, message_id, reply_markup=markup)

            elif call.data.startswith("download_"):
                code = call.data.replace("download_", "").strip().lower()
                if not check_subscription(user_id, channels, ADMIN_IDS):
                    buttons = [create_inline_button(f"📢 Подпишись на {ch}", url=f"https://t.me/{ch[1:]}") for ch in channels]
                    buttons.append(create_inline_button("✅ Я подписался!", callback_data="check_subscription"))
                    markup = create_inline_markup(buttons, row_width=1)
                    bot.send_message(chat_id, "Без подписки не дам файл! 😏 Подпишись на каналы!", reply_markup=markup)
                    return
                if code in files:
                    send_file(chat_id, code, files, user_id)

            elif call.data.startswith("rate_"):
                _, code, rating = call.data.split("_")
                code = code.strip().lower()
                save_rating(code, int(rating))
                bot.edit_message_text(f"Круто, ты дал {rating} ⭐! Спасибо, бро!", chat_id, message_id)

            elif call.data.startswith("review_"):
                code = call.data.replace("review_", "").strip().lower()
                if code not in files:
                    bot.edit_message_text("Файл куда-то сбежал! 😱 Вернись в каталог!", chat_id, message_id,
                                         reply_markup=create_inline_markup([create_inline_button("📂 Каталог", callback_data="catalog")]))
                    return
                msg = bot.send_message(chat_id, f"Что скажешь про {files[code]['name']}? Пиши отзыв! ✍️")
                bot.register_next_step_handler(msg, lambda m: save_review_handler(m, code))

            elif call.data == "about":
                navigation_stack.append("main_menu")
                redis_client.hset(f"bot_state:{user_id}", "navigation_stack", ",".join(navigation_stack))
                buttons = [create_inline_button("⬅️ Назад", callback_data="main_menu")]
                markup = create_inline_markup(buttons)
                bot.edit_message_text("Я твой личный бот для раздачи файлов! 😎 Качай, оценивай, пиши отзывы. Если что, я всегда на связи!",
                                     chat_id, message_id, reply_markup=markup)

            # Новая система обратной связи
            elif call.data == "feedback":
                navigation_stack.append("main_menu")
                redis_client.hset(f"bot_state:{user_id}", "navigation_stack", ",".join(navigation_stack))
                buttons = [
                    create_inline_button("📹 Добавить видео", callback_data="feedback_add_video"),
                    create_inline_button("🚀 Улучшить", callback_data="feedback_improve"),
                    create_inline_button("🐞 Сообщить об ошибке", callback_data="feedback_report_error"),
                    create_inline_button("⬅️ Назад", callback_data="main_menu")
                ]
                markup = create_inline_markup(buttons, row_width=1)
                bot.edit_message_text("Выбери тип обращения: 📩", chat_id, message_id, reply_markup=markup)

            elif call.data.startswith("feedback_"):
                feedback_type = call.data.replace("feedback_", "")
                redis_client.hset(f"bot_state:{user_id}", "feedback_type", feedback_type)
                navigation_stack.append("feedback")
                redis_client.hset(f"bot_state:{user_id}", "navigation_stack", ",".join(navigation_stack))
                bot.send_message(chat_id, "Пиши свой отзыв или предложение! ✍️" +
                                        (" (можешь прикрепить видео)" if feedback_type == "add_video" else ""))
                bot.register_next_step_handler(call.message, process_feedback_message)

            elif call.data.startswith("admin_view_feedback_"):
                page = int(call.data.split("_")[3])
                per_page = 5
                feedbacks, total = load_feedback(page, per_page)
                total_pages = (total + per_page - 1) // per_page
                text = f"📬 Отзывы (страница {page + 1}/{total_pages}):\n"
                if not feedbacks:
                    text += "Пока нет отзывов! 😅"
                else:
                    for feedback in feedbacks:
                        text += (f"ID: {feedback['id']} | От: {feedback['user_id']} | Тип: {feedback['feedback_type']}\n"
                                 f"Сообщение: {feedback['message']}\n"
                                 f"Статус: {feedback['status']}" + (f" | Ответ: {feedback['reply']}" if feedback['reply'] else "") + "\n\n")
                buttons = []
                nav_buttons = []
                if page > 0:
                    nav_buttons.append(create_inline_button("⬅️ Назад", callback_data=f"admin_view_feedback_{page-1}"))
                if page < total_pages - 1:
                    nav_buttons.append(create_inline_button("Вперёд ➡️", callback_data=f"admin_view_feedback_{page+1}"))
                nav_buttons.append(create_inline_button("⬅️ В админку", callback_data="admin_panel"))
                buttons.append(nav_buttons)
                markup = create_inline_markup(buttons)
                navigation_stack.append("admin_panel")
                redis_client.hset(f"bot_state:{user_id}", "navigation_stack", ",".join(navigation_stack))
                bot.edit_message_text(text, chat_id, message_id, reply_markup=markup)

            elif call.data.startswith("reply_feedback_"):
                feedback_id = int(call.data.split("_")[2])
                redis_client.hset(f"bot_state:{user_id}", "replying_feedback_id", feedback_id)
                bot.send_message(chat_id, f"Напиши ответ на отзыв (ID: {feedback_id}):")
                bot.register_next_step_handler(call.message, process_reply_feedback)

            elif call.data.startswith("send_pm_"):
                target_user_id = int(call.data.split("_")[2])
                redis_client.hset(f"bot_state:{user_id}", "sending_pm_user_id", target_user_id)
                bot.send_message(chat_id, f"Напиши личное сообщение для пользователя {target_user_id}:")
                bot.register_next_step_handler(call.message, process_send_pm)

            elif call.data == "admin_add_file":
                redis_client.hset(f"bot_state:{user_id}", "adding_file", "true")
                navigation_stack.append("admin_panel")
                redis_client.hset(f"bot_state:{user_id}", "navigation_stack", ",".join(navigation_stack))
                bot.send_message(chat_id, "Кидай файл в наш секретный канал! 📎")
                bot.register_next_step_handler(call.message, process_file_upload)

            elif call.data.startswith("admin_list_files_"):
                navigation_stack.append("admin_panel")
                redis_client.hset(f"bot_state:{user_id}", "navigation_stack", ",".join(navigation_stack))
                page = int(call.data.split("_")[3])
                per_page = 5
                max_pages = 5
                try:
                    with get_db() as cursor:
                        cursor.execute("SELECT COUNT(*) FROM files")
                        total_files = min(cursor.fetchone()[0], per_page * max_pages)
                        if page >= max_pages:
                            page = max_pages - 1
                        cursor.execute("SELECT name FROM files ORDER BY name LIMIT %s OFFSET %s",
                                       (per_page, page * per_page))
                        file_rows = cursor.fetchall()
                    total_pages = (total_files + per_page - 1) // per_page
                    text = f"📄 Все файлы ({total_files}):\n"
                    for row in file_rows:
                        text += f"- {row[0]}\n"
                    buttons = []
                    nav_buttons = []
                    if page > 0:
                        nav_buttons.append(create_inline_button("⬅️ Назад", callback_data=f"admin_list_files_{page-1}"))
                    if page < total_pages - 1 and page < max_pages - 1:
                        nav_buttons.append(create_inline_button("Вперёд ➡️", callback_data=f"admin_list_files_{page+1}"))
                    buttons.append(nav_buttons)
                    buttons.append([create_inline_button("⬅️ Назад", callback_data="admin_panel")])
                    markup = create_inline_markup(buttons)
                    bot.edit_message_text(text, chat_id, message_id, reply_markup=markup)
                except psycopg2.Error as e:
                    logger.error(f"Ошибка загрузки списка файлов: {e}")
                    bot.edit_message_text(
                        "Файлы не загрузились! 😅 Попробуй ещё раз!",
                        chat_id, message_id,
                        reply_markup=create_inline_markup([create_inline_button("⬅️ Назад", callback_data="admin_panel")])
                    )

            elif call.data.startswith("admin_delete_file_page_"):
                navigation_stack.append("admin_panel")
                redis_client.hset(f"bot_state:{user_id}", "navigation_stack", ",".join(navigation_stack))
                page = int(call.data.split("_")[4])
                per_page = 5
                max_pages = 5
                try:
                    with get_db() as cursor:
                        cursor.execute("SELECT COUNT(*) FROM files")
                        total_files = min(cursor.fetchone()[0], per_page * max_pages)
                        if page >= max_pages:
                            page = max_pages - 1
                        cursor.execute("SELECT code, name FROM files ORDER BY name LIMIT %s OFFSET %s",
                                       (per_page, page * per_page))
                        file_rows = cursor.fetchall()
                    total_pages = (total_files + per_page - 1) // per_page
                    buttons = [create_inline_button(f"🗑️ {row[1]}", callback_data=f"admin_delete_{row[0]}") for row in file_rows]
                    nav_buttons = []
                    if page > 0:
                        nav_buttons.append(create_inline_button("⬅️ Назад", callback_data=f"admin_delete_file_page_{page-1}"))
                    if page < total_pages - 1 and page < max_pages - 1:
                        nav_buttons.append(create_inline_button("Вперёд ➡️", callback_data=f"admin_delete_file_page_{page+1}"))
                    nav_buttons.append(create_inline_button("⬅️ Отмена", callback_data="admin_panel"))
                    buttons.extend(nav_buttons)
                    markup = create_inline_markup(buttons, row_width=1)
                    bot.edit_message_text(f"Какой файл стереть? 🗑️ (страница {page + 1}/{total_pages})", chat_id, message_id, reply_markup=markup)
                except psycopg2.Error as e:
                    logger.error(f"Ошибка загрузки файлов для удаления: {e}")
                    bot.edit_message_text(
                        "Файлы не загрузились! 😅 Попробуй ещё раз!",
                        chat_id, message_id,
                        reply_markup=create_inline_markup([create_inline_button("⬅️ Назад", callback_data="admin_panel")])
                    )

            elif call.data.startswith("admin_delete_"):
                code = call.data.replace("admin_delete_", "").strip().lower()
                if code in files:
                    delete_file(code)
                    bot.edit_message_text(f"Файл '{code}' испарился! ✅", chat_id, message_id,
                                         reply_markup=create_inline_markup([create_inline_button("⬅️ Назад", callback_data="admin_panel")]))
                else:
                    bot.edit_message_text("Файл уже кто-то стёр! 😅", chat_id, message_id,
                                         reply_markup=create_inline_markup([create_inline_button("⬅️ Назад", callback_data="admin_panel")]))
                                         elif call.data == "admin_broadcast":
                redis_client.hset(f"bot_state:{user_id}", "broadcasting", "true")
                navigation_stack.append("admin_panel")
                redis_client.hset(f"bot_state:{user_id}", "navigation_stack", ",".join(navigation_stack))
                bot.send_message(chat_id, "Кидай текст, фотку, видео или документ для рассылки! 📢")
                bot.register_next_step_handler(call.message, process_broadcast_message)

            elif call.data == "admin_publish_post":
                redis_client.hset(f"bot_state:{user_id}", "publishing_post", "true")
                navigation_stack.append("admin_panel")
                redis_client.hset(f"bot_state:{user_id}", "navigation_stack", ",".join(navigation_stack))
                bot.send_message(chat_id, "Кидай текст, фотку, видео или документ для публикации в канал! 📢")
                bot.register_next_step_handler(call.message, process_post_content)

            elif call.data == "admin_stats":
                navigation_stack.append("admin_panel")
                redis_client.hset(f"bot_state:{user_id}", "navigation_stack", ",".join(navigation_stack))
                total_users = len(get_users())
                active_users = get_active_users()
                total_reviews = get_reviews_count()
                total_downloads = sum(load_download_stats().values())
                text = (f"📊 Статистика бота:\n"
                        f"👥 Всего юзеров: {total_users}\n"
                        f"🟢 Активных за 24ч: {active_users}\n"
                        f"📥 Всего скачиваний: {total_downloads}\n"
                        f"✍️ Всего отзывов: {total_reviews}")
                buttons = [create_inline_button("⬅️ Назад", callback_data="admin_panel")]
                markup = create_inline_markup(buttons)
                bot.edit_message_text(text, chat_id, message_id, reply_markup=markup)

            elif call.data == "admin_manage_channels":
                navigation_stack.append("admin_panel")
                redis_client.hset(f"bot_state:{user_id}", "navigation_stack", ",".join(navigation_stack))
                channels = load_channels()
                text = "📌 Управление каналами:\n" + "\n".join([f"- {ch}" for ch in channels]) if channels else "Каналов пока нет! 😅"
                buttons = [
                    create_inline_button("➕ Добавить канал", callback_data="admin_add_channel"),
                    create_inline_button("🗑️ Удалить канал", callback_data="admin_remove_channel"),
                    create_inline_button("⬅️ Назад", callback_data="admin_panel")
                ]
                markup = create_inline_markup(buttons)
                bot.edit_message_text(text, chat_id, message_id, reply_markup=markup)

            elif call.data == "admin_add_channel":
                navigation_stack.append("admin_manage_channels")
                redis_client.hset(f"bot_state:{user_id}", "navigation_stack", ",".join(navigation_stack))
                bot.send_message(chat_id, "Кидай ссылку на канал (например, https://t.me/MyChannel):")
                bot.register_next_step_handler(call.message, set_channel)

            elif call.data == "admin_remove_channel":
                navigation_stack.append("admin_manage_channels")
                redis_client.hset(f"bot_state:{user_id}", "navigation_stack", ",".join(navigation_stack))
                channels = load_channels()
                buttons = [create_inline_button(f"🗑️ {ch}", callback_data=f"remove_channel_{ch}") for ch in channels]
                buttons.append(create_inline_button("⬅️ Назад", callback_data="admin_manage_channels"))
                markup = create_inline_markup(buttons, row_width=1)
                bot.edit_message_text("Какой канал удалить? 🗑️", chat_id, message_id, reply_markup=markup)

            elif call.data.startswith("remove_channel_"):
                channel = call.data.replace("remove_channel_", "")
                remove_channel(channel)
                bot.edit_message_text(f"Канал {channel} удалён! ✅", chat_id, message_id,
                                     reply_markup=create_inline_markup([create_inline_button("⬅️ Назад", callback_data="admin_manage_channels")]))

        except Exception as e:
            logger.error(f"Ошибка в callback_query для юзера {user_id}, callback {call.data}: {e}")
            bot.answer_callback_query(call.id, "Ой, что-то сломалось! 😅 Попробуй ещё раз!")

    def save_review_handler(message, code):
        try:
            user_id = message.from_user.id
            review_text = message.text.strip() if message.text else ""
            if not review_text:
                bot.reply_to(message, "Эй, пустой отзыв не катит! 😜 Напиши что-нибудь!")
                return
            if len(review_text) > 200:
                bot.reply_to(message, "Слишком длинный текст! 😅 До 200 символов, ок?")
                return
            save_review(code, user_id, review_text)
            bot.reply_to(message, "Спасибо за отзыв! 😊")
        except ValueError as e:
            bot.reply_to(message, str(e))
        except Exception as e:
            logger.error(f"Ошибка в save_review_handler для юзера {user_id}, код {code}: {e}")
            bot.reply_to(message, "Ой, отзыв не отправился! 😅 Попробуй ещё раз!")

    def process_reply_feedback(message):
        from main import ADMIN_IDS
        user_id = message.from_user.id
        if user_id not in ADMIN_IDS:
            bot.reply_to(message, "Только админы могут отвечать! 😏")
            return
        feedback_id = redis_client.hget(f"bot_state:{user_id}", "replying_feedback_id")
        if not feedback_id:
            bot.reply_to(message, "Что-то пошло не так! 😅 Попробуй ещё раз.")
            return
        feedback_id = int(feedback_id)
        reply_text = message.text.strip()
        if not reply_text:
            bot.reply_to(message, "Ответ не может быть пустым! 😅 Напиши что-нибудь.")
            return
        try:
            with get_db() as cursor:
                cursor.execute(
                    "SELECT user_id FROM feedback WHERE id = %s AND status = 'new'",
                    (feedback_id,)
                )
                feedback = cursor.fetchone()
                if not feedback:
                    bot.reply_to(message, "Не найдено новых отзывов с таким ID! 🕵️‍♂️")
                    return
                target_user_id = feedback[0]
                update_feedback(feedback_id, status="replied", reply=reply_text)
            bot.send_message(target_user_id, f"Админ ответил на твой отзыв (ID: {feedback_id}):\n{reply_text}")
            bot.reply_to(message, f"Ответ отправлен пользователю {target_user_id}! ✅")
        except ApiTelegramException as e:
            bot.reply_to(message, "Не удалось отправить ответ! 😅 Возможно, пользователь заблокировал бота.")
            logger.error(f"Ошибка отправки ответа пользователю {target_user_id}: {e}")
        except psycopg2.Error as e:
            logger.error(f"Ошибка в process_reply_feedback: {e}")
            bot.reply_to(message, "Ошибка базы данных! 😅 Попробуй ещё раз.")
        finally:
            redis_client.hdel(f"bot_state:{user_id}", "replying_feedback_id")

    def process_send_pm(message):
        from main import ADMIN_IDS, ADMIN_USERNAME
        user_id = message.from_user.id
        if user_id not in ADMIN_IDS:
            bot.reply_to(message, "Только админы могут отправлять ЛС! 😏")
            return
        target_user_id = redis_client.hget(f"bot_state:{user_id}", "sending_pm_user_id")
        if not target_user_id:
            bot.reply_to(message, "Что-то пошло не так! 😅 Попробуй ещё раз.")
            return
        target_user_id = int(target_user_id)
        pm_text = message.text.strip()
        if not pm_text:
            bot.reply_to(message, "Сообщение не может быть пустым! 😅 Напиши что-нибудь.")
            return
        try:
            bot.send_message(target_user_id, f"Сообщение от админа {ADMIN_USERNAME}:\n{pm_text}")
            bot.reply_to(message, f"Личное сообщение отправлено пользователю {target_user_id}! ✅")
        except ApiTelegramException as e:
            bot.reply_to(message, "Не удалось отправить ЛС! 😅 Возможно, пользователь заблокировал бота.")
            logger.error(f"Ошибка отправки ЛС пользователю {target_user_id}: {e}")
        finally:
            redis_client.hdel(f"bot_state:{user_id}", "sending_pm_user_id")

    def process_file_upload(message):
        from main import ADMIN_IDS, STORAGE_CHANNEL_ID
        if redis_client.hget(f"bot_state:{message.from_user.id}", "adding_file") != "true" or message.from_user.id not in ADMIN_IDS:
            bot.reply_to(message, "Эй, ты не босс или загрузка не начата! 😏")
            return
        if not message.document:
            bot.reply_to(message, "Кидай документ, а не что попало! 😅")
            redis_client.hdel(f"bot_state:{message.from_user.id}", "adding_file")
            return
        try:
            file_id = message.document.file_id
            file_name = message.document.file_name
            sent_message = bot.send_document(STORAGE_CHANNEL_ID, file_id, caption=f"Файл от админа: {file_name}")
            redis_client.hset(f"bot_state:{message.from_user.id}", "file_id", file_id)
            redis_client.hset(f"bot_state:{message.from_user.id}", "file_name", file_name)
            buttons = [create_inline_button(cat, callback_data=f"set_category_{key}") for key, cat in CATEGORY_MAP.items()]
            markup = create_inline_markup(buttons)
            bot.reply_to(message, f"Файл '{file_name}' загружен! Выбери категорию:", reply_markup=markup)
        except ApiTelegramException as e:
            bot.reply_to(message, "Не смог загрузить файл в канал! 😅 Проверь права бота.")
            logger.error(f"Ошибка загрузки файла в канал {STORAGE_CHANNEL_ID}: {e}")
            redis_client.hdel(f"bot_state:{message.from_user.id}", "adding_file")
            @bot.callback_query_handler(func=lambda call: call.data.startswith("set_category_"))
    def set_category(call):
        from main import ADMIN_IDS, CATEGORY_MAP
        user_id = call.from_user.id
        if redis_client.hget(f"bot_state:{user_id}", "adding_file") != "true" or user_id not in ADMIN_IDS:
            bot.answer_callback_query(call.id, "Эй, ты не босс или загрузка не начата! 😏")
            return
        category_key = call.data.replace("set_category_", "")
        category = CATEGORY_MAP.get(category_key)
        if not category:
            bot.answer_callback_query(call.id, "Категория какая-то странная! 😅")
            return
        redis_client.hset(f"bot_state:{user_id}", "category", category)
        bot.send_message(call.message.chat.id, "Теперь дай код для файла (только буквы, цифры, дефисы, до 50 символов):")
        bot.register_next_step_handler(call.message, process_file_code)

    def process_file_code(message):
        from main import ADMIN_IDS
        if redis_client.hget(f"bot_state:{message.from_user.id}", "adding_file") != "true" or message.from_user.id not in ADMIN_IDS:
            bot.reply_to(message, "Эй, ты не босс или загрузка не начата! 😏")
            return
        code = message.text.strip()
        if not validate_code(code):
            bot.reply_to(message, "Код должен быть до 50 символов, только буквы, цифры, дефисы! 😅 Попробуй ещё раз.")
            return
        file_id = redis_client.hget(f"bot_state:{message.from_user.id}", "file_id")
        file_name = redis_client.hget(f"bot_state:{message.from_user.id}", "file_name")
        category = redis_client.hget(f"bot_state:{message.from_user.id}", "category")
        if not all([file_id, file_name, category]):
            bot.reply_to(message, "Ой, данные потерялись! 😅 Давай заново!")
            redis_client.hdel(f"bot_state:{message.from_user.id}", "file_id", "file_name", "category", "adding_file")
            return
        try:
            save_file(code, file_id, file_name, category)
            bot.reply_to(message, f"Файл '{file_name}' добавлен с кодом '{code}' в категорию '{category}'! ✅",
                         reply_markup=create_inline_markup([create_inline_button("⬅️ Назад", callback_data="admin_panel")]))
        except ValueError as e:
            bot.reply_to(message, str(e))
        except Exception as e:
            logger.error(f"Ошибка сохранения файла {code}: {e}")
            bot.reply_to(message, "Не удалось сохранить файл! 😅 Попробуй ещё раз.")
        finally:
            redis_client.hdel(f"bot_state:{message.from_user.id}", "file_id", "file_name", "category", "adding_file")

    def process_broadcast_message(message):
        from main import ADMIN_IDS
        if redis_client.hget(f"bot_state:{message.from_user.id}", "broadcasting") != "true" or message.from_user.id not in ADMIN_IDS:
            bot.reply_to(message, "Эй, ты не босс или рассылка не начата! 😏")
            return
        content_type = None
        if message.text:
            content_type = "text"
        elif message.photo:
            content_type = "photo"
        elif message.video:
            content_type = "video"
        elif message.document:
            content_type = "document"
        if not content_type:
            bot.reply_to(message, "Кидай текст, фотку, видео или документ! 😅")
            redis_client.hdel(f"bot_state:{message.from_user.id}", "broadcasting")
            return
        try:
            broadcast_data = {"content_type": content_type}
            if content_type == "text":
                broadcast_data["text"] = message.text
                preview_message = bot.send_message(message.chat.id, f"Вот твоя рассылка:\n{message.text}")
            elif content_type == "photo":
                broadcast_data["media_id"] = message.photo[-1].file_id
                broadcast_data["caption"] = message.caption or ""
                preview_message = bot.send_photo(message.chat.id, broadcast_data["media_id"],
                                                caption=f"Вот твоя рассылка:\n{broadcast_data['caption']}")
            elif content_type == "video":
                broadcast_data["media_id"] = message.video.file_id
                broadcast_data["caption"] = message.caption or ""
                preview_message = bot.send_video(message.chat.id, broadcast_data["media_id"],
                                                caption=f"Вот твоя рассылка:\n{broadcast_data['caption']}")
            elif content_type == "document":
                broadcast_data["media_id"] = message.document.file_id
                broadcast_data["caption"] = message.caption or ""
                preview_message = bot.send_document(message.chat.id, broadcast_data["media_id"],
                                                   caption=f"Вот твоя рассылка:\n{broadcast_data['caption']}")
            import json
            redis_client.hset(f"bot_state:{message.from_user.id}", "broadcast_data", json.dumps(broadcast_data))
            buttons = [
                create_inline_button("✅ Отправить", callback_data="confirm_broadcast"),
                create_inline_button("❌ Отмена", callback_data="cancel_broadcast")
            ]
            markup = create_inline_markup(buttons)
            bot.send_message(message.chat.id, "Всё ок? Отправляем? 🚀", reply_markup=markup)
        except ApiTelegramException as e:
            bot.reply_to(message, "Ой, что-то с рассылкой не так! 😅 Попробуй ещё раз.")
            logger.error(f"Ошибка предпросмотра рассылки: {e}")
            redis_client.hdel(f"bot_state:{message.from_user.id}", "broadcasting")

    @bot.callback_query_handler(func=lambda call: call.data in ["confirm_broadcast", "cancel_broadcast"])
    def handle_broadcast_confirmation(call):
        from main import ADMIN_IDS
        user_id = call.from_user.id
        if redis_client.hget(f"bot_state:{user_id}", "broadcasting") != "true" or user_id not in ADMIN_IDS:
            bot.answer_callback_query(call.id, "Эй, ты не босс или рассылка не начата! 😏")
            return
        if call.data == "cancel_broadcast":
            redis_client.hdel(f"bot_state:{user_id}", "broadcasting", "broadcast_data")
            bot.edit_message_text("Рассылка отменена! 😅", call.message.chat.id, call.message.message_id,
                                 reply_markup=create_inline_markup([create_inline_button("⬅️ Назад", callback_data="admin_panel")]))
            return
        import json
        broadcast_data = redis_client.hget(f"bot_state:{user_id}", "broadcast_data")
        if not broadcast_data:
            bot.edit_message_text("Данные рассылки потерялись! 😅 Давай заново!", call.message.chat.id, call.message.message_id,
                                 reply_markup=create_inline_markup([create_inline_button("⬅️ Назад", callback_data="admin_panel")]))
            redis_client.hdel(f"bot_state:{user_id}", "broadcasting")
            return
        broadcast_data = json.loads(broadcast_data)
        users = get_users()
        success_count, failed_users = send_broadcast(broadcast_data, users)
        bot.edit_message_text(f"Рассылка завершена! ✅\nУспешно: {success_count}\nНе доставлено: {len(failed_users)}",
                             call.message.chat.id, call.message.message_id,
                             reply_markup=create_inline_markup([create_inline_button("⬅️ Назад", callback_data="admin_panel")]))
        redis_client.hdel(f"bot_state:{user_id}", "broadcasting", "broadcast_data")

    def process_post_content(message):
        from main import ADMIN_IDS
        if redis_client.hget(f"bot_state:{message.from_user.id}", "publishing_post") != "true" or message.from_user.id not in ADMIN_IDS:
            bot.reply_to(message, "Эй, ты не босс или публикация не начата! 😏")
            return
        content_type = None
        if message.text:
            content_type = "text"
        elif message.photo:
            content_type = "photo"
        elif message.video:
            content_type = "video"
        elif message.document:
            content_type = "document"
        if not content_type:
            bot.reply_to(message, "Кидай текст, фотку, видео или документ! 😅")
            redis_client.hdel(f"bot_state:{message.from_user.id}", "publishing_post")
            return
        try:
            post_data = {"content_type": content_type}
            if content_type == "text":
                post_data["text"] = message.text
                preview_message = bot.send_message(message.chat.id, f"Вот твой пост:\n{message.text}")
            elif content_type == "photo":
                post_data["media_id"] = message.photo[-1].file_id
                post_data["caption"] = message.caption or ""
                preview_message = bot.send_photo(message.chat.id, post_data["media_id"],
                                                caption=f"Вот твой пост:\n{post_data['caption']}")
            elif content_type == "video":
                post_data["media_id"] = message.video.file_id
                post_data["caption"] = message.caption or ""
                preview_message = bot.send_video(message.chat.id, post_data["media_id"],
                                                caption=f"Вот твой пост:\n{post_data['caption']}")
            elif content_type == "document":
                post_data["media_id"] = message.document.file_id
                post_data["caption"] = message.caption or ""
                preview_message = bot.send_document(message.chat.id, post_data["media_id"],
                                                   caption=f"Вот твой пост:\n{post_data['caption']}")
            import json
            redis_client.hset(f"bot_state:{message.from_user.id}", "post_content", json.dumps(post_data))
            buttons = [
                create_inline_button("➕ Добавить кнопку", callback_data="add_post_button"),
                create_inline_button("✅ Отправить без кнопки", callback_data="skip_post_button")
            ]
            markup = create_inline_markup(buttons)
            bot.send_message(message.chat.id, "Добавить кнопку к посту? 🔘", reply_markup=markup)
        except ApiTelegramException as e:
            bot.reply_to(message, "Ой, что-то с постом не так! 😅 Попробуй ещё раз.")
            logger.error(f"Ошибка предпросмотра поста: {e}")
            redis_client.hdel(f"bot_state:{message.from_user.id}", "publishing_post")

    @bot.callback_query_handler(func=lambda call: call.data in ["add_post_button", "skip_post_button"])
    def handle_post_button(call):
        from main import ADMIN_IDS, PUBLIC_CHANNEL_ID
        user_id = call.from_user.id
        if redis_client.hget(f"bot_state:{user_id}", "publishing_post") != "true" or user_id not in ADMIN_IDS:
            bot.answer_callback_query(call.id, "Эй, ты не босс или публикация не начата! 😏")
            return
        import json
        post_data = redis_client.hget(f"bot_state:{user_id}", "post_content")
        if not post_data:
            bot.edit_message_text("Данные поста потерялись! 😅 Давай заново!", call.message.chat.id, call.message.message_id,
                                 reply_markup=create_inline_markup([create_inline_button("⬅️ Назад", callback_data="admin_panel")]))
            redis_client.hdel(f"bot_state:{user_id}", "publishing_post")
            return
        post_data = json.loads(post_data)
        if call.data == "skip_post_button":
            publish_post(user_id, post_data, None)
            bot.edit_message_text("Пост улетел в канал! 🚀", call.message.chat.id, call.message.message_id,
                                 reply_markup=create_inline_markup([create_inline_button("⬅️ Назад", callback_data="admin_panel")]))
            redis_client.hdel(f"bot_state:{user_id}", "post_content", "publishing_post")
        else:
            bot.send_message(call.message.chat.id, "Введи текст для кнопки:")
            bot.register_next_step_handler(call.message, process_button_text)

    def process_button_text(message):
        from main import ADMIN_IDS
        if redis_client.hget(f"bot_state:{message.from_user.id}", "publishing_post") != "true" or message.from_user.id not in ADMIN_IDS:
            bot.reply_to(message, "Эй, ты не босс или публикация не начата! 😏")
            return
        button_text = message.text.strip()
        if not button_text:
            bot.reply_to(message, "Без текста кнопки не обойдёмся! 😅 Давай ещё раз.")
            return
        redis_client.hset(f"bot_state:{message.from_user.id}", "button_text", button_text)
        buttons = [
            create_inline_button("🔗 Ссылка", callback_data="button_type_url"),
            create_inline_button("🔄 Callback", callback_data="button_type_callback")
        ]
        markup = create_inline_markup(buttons)
        bot.reply_to(message, f"Текст кнопки: '{button_text}'. Какой тип кнопки? 🔘", reply_markup=markup)

    def process_button_url(message):
        from main import ADMIN_IDS, PUBLIC_CHANNEL_ID
        if redis_client.hget(f"bot_state:{message.from_user.id}", "publishing_post") != "true" or message.from_user.id not in ADMIN_IDS:
            bot.reply_to(message, "Эй, ты не босс или публикация не начата! 😏")
            return
        url = message.text.strip()
        if not url.startswith("https://"):
            bot.reply_to(message, "Ссылка должна начинаться с https://! 😅 Поправь!")
            return
        button_text = redis_client.hget(f"bot_state:{message.from_user.id}", "button_text")
        import json
        post_data = redis_client.hget(f"bot_state:{message.from_user.id}", "post_content")
        if not post_data or not button_text:
            bot.reply_to(message, "Ой, данные потерялись! 😅 Давай заново!")
            redis_client.hdel(f"bot_state:{message.from_user.id}", "post_content", "button_text", "publishing_post")
            return
        post_data = json.loads(post_data)
        button = create_inline_button(button_text, url=url)
        publish_post(message.from_user.id, post_data, button)
        bot.reply_to(message, "Пост улетел в канал! 🚀",
                     reply_markup=create_inline_markup([create_inline_button("⬅️ Назад", callback_data="admin_panel")]))
        redis_client.hdel(f"bot_state:{message.from_user.id}", "post_content", "button_text", "publishing_post")

    def process_button_callback(message):
        from main import ADMIN_IDS, PUBLIC_CHANNEL_ID
        if redis_client.hget(f"bot_state:{message.from_user.id}", "publishing_post") != "true" or message.from_user.id not in ADMIN_IDS:
            bot.reply_to(message, "Эй, ты не босс или публикация не начата! 😏")
            return
        callback_data = message.text.strip()
        if len(callback_data.encode('utf-8')) > 64:
            bot.reply_to(message, "Callback_data длиннее 64 байт! 😅 Укороти!")
            return
        button_text = redis_client.hget(f"bot_state:{message.from_user.id}", "button_text")
        import json
        post_data = redis_client.hget(f"bot_state:{message.from_user.id}", "post_content")
        if not post_data or not button_text:
            bot.reply_to(message, "Ой, данные потерялись! 😅 Давай заново!")
            redis_client.hdel(f"bot_state:{message.from_user.id}", "post_content", "button_text", "publishing_post")
            return
        post_data = json.loads(post_data)
        button = create_inline_button(button_text, callback_data=callback_data)
        publish_post(message.from_user.id, post_data, button)
        bot.reply_to(message, "Пост улетел в канал! 🚀",
                     reply_markup=create_inline_markup([create_inline_button("⬅️ Назад", callback_data="admin_panel")]))
        redis_client.hdel(f"bot_state:{message.from_user.id}", "post_content", "button_text", "publishing_post")

    def publish_post(user_id, post_data, button):
        from main import PUBLIC_CHANNEL_ID
        try:
            markup = create_inline_markup([button]) if button else None
            if post_data["content_type"] == "text":
                bot.send_message(PUBLIC_CHANNEL_ID, post_data["text"], reply_markup=markup)
            elif post_data["content_type"] == "photo":
                bot.send_photo(PUBLIC_CHANNEL_ID, post_data["media_id"], caption=post_data["caption"], reply_markup=markup)
            elif post_data["content_type"] == "video":
                bot.send_video(PUBLIC_CHANNEL_ID, post_data["media_id"], caption=post_data["caption"], reply_markup=markup)
            elif post_data["content_type"] == "document":
                bot.send_document(PUBLIC_CHANNEL_ID, post_data["media_id"], caption=post_data["caption"], reply_markup=markup)
            logger.info(f"Пост опубликован в канале {PUBLIC_CHANNEL_ID} юзером {user_id}")
        except ApiTelegramException as e:
            bot.send_message(user_id, "Пост не улетел в канал! 😅 Проверь права бота.")
            logger.error(f"Ошибка публикации в канал {PUBLIC_CHANNEL_ID}: {e}")

    def set_channel(message):
        from main import ADMIN_IDS
        if message.from_user.id not in ADMIN_IDS:
            bot.reply_to(message, "Эй, ты не босс! 😏")
            return
        channel = extract_channel_id(message.text)
        if not channel:
            bot.reply_to(message, "Ссылка какая-то странная! 😅 Давай нормальную: https://t.me/MyChannel")
            return
        save_channel(channel)
        bot.reply_to(message, f"Канал {channel} добавлен! ✅",
                     reply_markup=create_inline_markup([create_inline_button("⬅️ Назад", callback_data="admin_manage_channels")]))

    def process_feedback_message(message):
        try:
            user_id = message.from_user.id
            feedback_type = redis_client.hget(f"bot_state:{user_id}", "feedback_type")
            video_id = message.video.file_id if message.video else None
            feedback_text = message.text.strip() if message.text else (message.caption.strip() if message.caption else "")
            if not feedback_text:
                bot.reply_to(message, "Эй, пустой отзыв не катит! 😜 Напиши что-нибудь!")
                return
            if len(feedback_text) > 500:
                bot.reply_to(message, "Слишком длинный текст! 😅 До 500 символов, ок?")
                return
            save_feedback(user_id, feedback_text, video_id, feedback_type)
            bot.reply_to(message, "Спасибо за отзыв! Админ скоро ответит! 😊")
            redis_client.hdel(f"bot_state:{user_id}", "feedback_type")
        except Exception as e:
            logger.error(f"Ошибка в process_feedback_message для юзера {user_id}: {e}")
            bot.reply_to(message, "Ой, отзыв не отправился! 😅 Попробуй ещё раз!")
            redis_client.hdel(f"bot_state:{user_id}", "feedback_type")
        
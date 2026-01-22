# Discord RSS V1.2

import asyncio
import threading
import logging
import sqlite3
import os
import re
from datetime import datetime, timedelta, timezone
from logging.handlers import TimedRotatingFileHandler

import discord
from discord.ext import commands, tasks
import feedparser

from config import TOKEN, CHANNEL_ID, UPDATE_INTERVAL, LAST_ARTICLE_RANGE, RSS_FEEDS

__version__ = "1.2"

KEYWORDS = [
    "akuisisi",
    "merger",
    "backdoor",
    "pengambilalihan",
    "take over",
    "right issue",
    "wsbp",
    "pcar",
]

# -------------------------------------------------------
# Setup Logging: console + file harian di folder logs/
# -------------------------------------------------------
LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)

log_base_path = os.path.join(LOG_DIR, "logs")

file_handler = TimedRotatingFileHandler(
    log_base_path,
    when="midnight",
    interval=1,
    backupCount=30,
    encoding="utf-8",
)
file_handler.suffix = "_%d-%m-%Y.txt"

console_handler = logging.StreamHandler()

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    handlers=[console_handler, file_handler],
)

# -------------------------------------------------------
# Database SQLite
# -------------------------------------------------------
DB_PATH = "articles.db"

conn = sqlite3.connect(DB_PATH, check_same_thread=False)
cur = conn.cursor()
DB_LOCK = threading.Lock()

cur.execute(
    """
    CREATE TABLE IF NOT EXISTS articles (
        link TEXT PRIMARY KEY,
        title TEXT
    )
"""
)
conn.commit()

def article_already_sent(link: str) -> bool:
    with DB_LOCK:
        cur.execute("SELECT 1 FROM articles WHERE link = ?", (link,))
        return cur.fetchone() is not None

def save_article(link: str, title: str) -> None:
    try:
        with DB_LOCK:
            cur.execute(
                "INSERT OR IGNORE INTO articles (link, title) VALUES (?, ?)",
                (link, title),
            )
            conn.commit()
    except Exception as e:
        logging.error("Gagal menyimpan artikel ke DB: %s", e)

# -------------------------------------------------------
# Keyword filter
# -------------------------------------------------------
def title_has_keyword(title: str) -> bool:
    lower_title = title.lower()

    for keyword in KEYWORDS:
        if " " in keyword:
            if keyword in lower_title:
                return True
        else:
            pattern = rf"\b{re.escape(keyword)}\b"
            if re.search(pattern, title, re.IGNORECASE):
                return True
    return False

# -------------------------------------------------------
# RSS date parser
# -------------------------------------------------------
def get_entry_datetime(entry) -> datetime | None:
    if hasattr(entry, "published_parsed") and entry.published_parsed:
        return datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)

    if hasattr(entry, "published"):
        try:
            return datetime.strptime(
                entry.published,
                "%a, %d %b %Y %H:%M:%S %z",
            ).astimezone(timezone.utc)
        except Exception:
            return None

    return None

# -------------------------------------------------------
# OPSI 1 + OPSI 2: Safe RSS parser
# -------------------------------------------------------
def safe_parse_feed(url: str, retries: int = 3):
    for attempt in range(1, retries + 1):
        try:
            feed = feedparser.parse(url)

            if not feed.bozo:
                return feed

            logging.warning(
                "RSS error (%s) attempt %d/%d: %s",
                url,
                attempt,
                retries,
                feed.bozo_exception,
            )

        except Exception as e:
            logging.warning(
                "Exception parse RSS (%s) attempt %d/%d: %s",
                url,
                attempt,
                retries,
                e,
            )

    logging.error("RSS %s gagal setelah %d percobaan, dilewati.", url, retries)
    return None

# -------------------------------------------------------
# Fetch articles
# -------------------------------------------------------
def fetch_new_articles() -> list[dict]:
    result: list[dict] = []
    now = datetime.now(timezone.utc)
    max_age = timedelta(days=LAST_ARTICLE_RANGE)

    for feed_cfg in RSS_FEEDS:
        url = feed_cfg["url"]
        user_id = feed_cfg.get("user")  # pastikan key sesuai config.py

        parsed_feed = safe_parse_feed(url)
        if not parsed_feed:
            continue

        feed_title = getattr(parsed_feed.feed, "title", "RSS Feed")

        for entry in parsed_feed.entries:
            link = getattr(entry, "link", None)
            title = getattr(entry, "title", "No title")

            if not link:
                continue

            if article_already_sent(link):
                continue

            if not title_has_keyword(title):
                continue

            pub_dt = get_entry_datetime(entry)
            if pub_dt and now - pub_dt > max_age:
                continue

            result.append(
                {
                    "title": title,
                    "link": link,
                    "feed_title": feed_title,
                    "user_id": user_id,
                }
            )

    return result

# -------------------------------------------------------
# Discord message formatter
# -------------------------------------------------------
def build_discord_message(article: dict) -> str:
    title = article["title"]
    link = article["link"]
    feed_title = article["feed_title"]
    user_id = article["user_id"]

    author_part = f"<@{user_id}>" if user_id else feed_title
    return f"**{title}** oleh {author_part}\n{link}"

# -------------------------------------------------------
# Discord bot setup
# -------------------------------------------------------
intents = discord.Intents.default()
intents.guilds = True
intents.messages = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    logging.info(
        "Bot login sebagai %s (ID: %s), versi %s",
        bot.user,
        bot.user.id,
        __version__,
    )
    if not poll_feeds.is_running():
        poll_feeds.start()
        logging.info("Task poll_feeds dimulai.")

# -------------------------------------------------------
# RSS polling task
# -------------------------------------------------------
@tasks.loop(minutes=UPDATE_INTERVAL)
async def poll_feeds():
    await bot.wait_until_ready()

    channel = bot.get_channel(CHANNEL_ID)
    if channel is None:
        logging.error("Channel dengan ID %s tidak ditemukan.", CHANNEL_ID)
        return

    logging.info("Memulai fetch_new_articles() di background thread...")
    new_articles = await asyncio.to_thread(fetch_new_articles)
    logging.info("Selesai fetch_new_articles(), %d artikel kandidat.", len(new_articles))

    if not new_articles:
        logging.info("Tidak ada artikel baru.")
        return

    for art in new_articles:
        try:
            await channel.send(build_discord_message(art))
            save_article(art["link"], art["title"])
        except Exception as e:
            logging.error("Gagal mengirim pesan ke Discord: %s", e)

# -------------------------------------------------------
# Entry point
# -------------------------------------------------------
if __name__ == "__main__":
    try:
        bot.run(TOKEN)
    except KeyboardInterrupt:
        logging.info("Bot dimatikan oleh user.")
    finally:
        conn.close()
        logging.info("Koneksi DB ditutup.")

# Discord RSS V1.1

import asyncio
import threading
import logging
import sqlite3
import os
from datetime import datetime, timedelta, timezone
from logging.handlers import TimedRotatingFileHandler

import discord
from discord.ext import commands, tasks
import feedparser

from config import TOKEN, CHANNEL_ID, UPDATE_INTERVAL, LAST_ARTICLE_RANGE, RSS_FEEDS

__version__ = "1.1"

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

# File dasar log (akan di-rotate harian)
# Rotated file akan menjadi: logs/logs_19-11-2025.txt, logs/logs_20-11-2025.txt, dst.
log_base_path = os.path.join(LOG_DIR, "logs")

file_handler = TimedRotatingFileHandler(
    log_base_path,
    when="midnight",
    interval=1,
    backupCount=30,      # simpan 30 hari log, bisa diubah
    encoding="utf-8",
)

# Format nama file setelah di-rotate:
# logs/logs_19-11-2025.txt
file_handler.suffix = "_%d-%m-%Y.txt"

console_handler = logging.StreamHandler()

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    handlers=[console_handler, file_handler],
)

# -------------------------------------------------------
# Database SQLite untuk menyimpan artikel yang sudah dikirim
# -------------------------------------------------------
DB_PATH = "articles.db"

# check_same_thread=False agar bisa dipakai dari thread lain (to_thread)
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
cur = conn.cursor()
DB_LOCK = threading.Lock()

# Simpan link sebagai PRIMARY KEY supaya tidak ada duplikat
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
    """Cek apakah link artikel sudah pernah disimpan di DB."""
    with DB_LOCK:
        cur.execute("SELECT 1 FROM articles WHERE link = ?", (link,))
        return cur.fetchone() is not None

def save_article(link: str, title: str) -> None:
    """Simpan artikel ke DB setelah pesan sukses dikirim."""
    try:
        with DB_LOCK:
            cur.execute(
                "INSERT OR IGNORE INTO articles (link, title) VALUES (?, ?)",
                (link, title),
            )
            conn.commit()
    except Exception as e:
        logging.error("Gagal menyimpan artikel ke DB: %s", e)

def title_has_keyword(title: str) -> bool:
    """Cek apakah judul mengandung salah satu keyword."""
    lower_title = title.lower()
    return any(keyword in lower_title for keyword in KEYWORDS)


# -------------------------------------------------------
# Fungsi bantu untuk parsing tanggal dari RSS
# -------------------------------------------------------
def get_entry_datetime(entry) -> datetime | None:
    """
    Ambil datetime artikel dalam timezone UTC.
    Coba pakai published_parsed kalau ada,
    kalau tidak ada ya kembalikan None (anggap tanpa batas tanggal).
    """
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
# Ambil artikel baru dari semua RSS_FEEDS
# -------------------------------------------------------
def fetch_new_articles() -> list[dict]:
    """
    Loop semua RSS_FEEDS, ambil entry baru yang:
    - belum ada di DB
    - tanggalnya masih dalam range LAST_ARTICLE_RANGE (kalau bisa ditentukan)
    Return: list dict berisi info artikel + user_id + feed_title.
    """
    result: list[dict] = []
    now = datetime.now(timezone.utc)
    max_age = timedelta(days=LAST_ARTICLE_RANGE)

    for feed_cfg in RSS_FEEDS:
        url = feed_cfg["url"]
        # catatan: di config.py, pastikan pakai key "user_id" kalau mau mention user
        user_id = feed_cfg.get("user_id")

        try:
            parsed_feed = feedparser.parse(url)
        except Exception as e:
            logging.error("Gagal parse RSS %s: %s", url, e)
            continue

        feed_title = getattr(parsed_feed.feed, "title", "RSS Feed")

        for entry in parsed_feed.entries:
            link = getattr(entry, "link", None)
            title = getattr(entry, "title", "No title")

            if not link:
                continue

            # Sudah pernah dikirim?
            if article_already_sent(link):
                continue

            # Filter berdasarkan keyword judul
            if not title_has_keyword(title):
                continue

            # Cek umur artikel (kalau bisa di-parse)
            pub_dt = get_entry_datetime(entry)
            if pub_dt is not None:
                if now - pub_dt > max_age:
                    # Artikel terlalu lama, lewati
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
# Format pesan Discord
# -------------------------------------------------------
def build_discord_message(article: dict) -> str:
    """
    Template:
    **Judul Artikel** oleh <@user_id> / Nama Feed
    link
    """
    title = article["title"]
    link = article["link"]
    feed_title = article["feed_title"]
    user_id = article["user_id"]

    if user_id:
        author_part = f"<@{user_id}>"
    else:
        author_part = feed_title

    message = f"**{title}** oleh {author_part}\n{link}"
    return message


# -------------------------------------------------------
# Setup Bot Discord
# -------------------------------------------------------
intents = discord.Intents.default()
intents.guilds = True
intents.messages = True

bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    logging.info("Bot login sebagai %s (ID: %s), versi %s", bot.user, bot.user.id, __version__)
    if not poll_feeds.is_running():
        poll_feeds.start()
        logging.info("Task poll_feeds dimulai.")


# -------------------------------------------------------
# Task loop cek RSS berkala
# -------------------------------------------------------
@tasks.loop(minutes=UPDATE_INTERVAL)
async def poll_feeds():
    await bot.wait_until_ready()

    channel = bot.get_channel(CHANNEL_ID)
    if channel is None:
        logging.error("Channel dengan ID %s tidak ditemukan.", CHANNEL_ID)
        return

    # Jalankan fetch_new_articles di thread terpisah
    logging.info("Memulai fetch_new_articles() di background thread...")
    new_articles = await asyncio.to_thread(fetch_new_articles)
    logging.info("Selesai fetch_new_articles(), ditemukan %d artikel kandidat.", len(new_articles))

    if not new_articles:
        logging.info("Tidak ada artikel baru.")
        return

    logging.info("Ditemukan %d artikel baru.", len(new_articles))

    for art in new_articles:
        msg = build_discord_message(art)
        try:
            await channel.send(msg)
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

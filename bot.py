import logging
import sqlite3
from datetime import datetime, timedelta, timezone

import discord
from discord.ext import commands, tasks
import feedparser

from config import TOKEN, CHANNEL_ID, UPDATE_INTERVAL, LAST_ARTICLE_RANGE, RSS_FEEDS

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
# Logging sederhana
# -------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
)

# -------------------------------------------------------
# Database SQLite untuk menyimpan artikel yang sudah dikirim
# -------------------------------------------------------
DB_PATH = "articles.db"

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

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
    cur.execute("SELECT 1 FROM articles WHERE link = ?", (link,))
    return cur.fetchone() is not None


def save_article(link: str, title: str) -> None:
    """Simpan artikel ke DB setelah pesan sukses dikirim."""
    try:
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
    # feedparser biasanya punya published_parsed (time.struct_time)
    if hasattr(entry, "published_parsed") and entry.published_parsed:
        # published_parsed → datetime dengan timezone UTC
        return datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)

    # fallback: kalau hanya ada string, bisa coba di-parse manual sesuai format feed
    # Di banyak RSS classic: "Mon, 01 Jan 2024 12:34:56 +0000"
    if hasattr(entry, "published"):
        try:
            return datetime.strptime(
                entry.published,
                "%a, %d %b %Y %H:%M:%S %z",
            ).astimezone(timezone.utc)
        except Exception:
            # kalau gagal parse, kita abaikan batas umur artikel
            return None

    return None


# -------------------------------------------------------
# Ambil artikel baru dari semua RSS_FEDS
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
# Bot cuma perlu bisa melihat guild + pesan untuk mengirim
intents.guilds = True
intents.messages = True

bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    logging.info("Bot login sebagai %s (ID: %s)", bot.user, bot.user.id)
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

    new_articles = fetch_new_articles()

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

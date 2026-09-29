# Bot Token
TOKEN = "your_discord_token"

# Channel ID
CHANNEL_ID = [your_channel_id]

# Update interval for how often the bot is supposed to check if a new entry in the RSS feed exists (in Minutes)
UPDATE_INTERVAL = 10

# How far a new entry in the RSS feed can can be published in the past before being ignored (in Days)
LAST_ARTICLE_RANGE = 5

# Add the RSS feeds here. Each object consists of the RSS feed URL and an optional Discord User-ID, whose user will be tagged in the message
RSS_FEEDS = [
    {
        "url": "https://www.antaranews.com/rss/ekonomi-bursa.xml",
        "user": "None"
    },
    {
        "url": "https://nusantaranews.co/feed/",
        "user": "None"
    },
    {
        "url": "https://www.bloombergtechnoz.com/rss",
        "user": "None"
    },
    {
        "url": "https://pasardana.id/rss",
        "user": "None"
    },
    {
        "url": "https://finance.detik.com/rss",
        "user": "None"
    },
    {
        "url": "https://internasional.kontan.co.id/rss",
        "user": "None"
    },
    {
        "url": "https://insight.kontan.co.id/rss",
        "user": "None"
    },
    {
        "url": "https://investasi.kontan.co.id/rss",
        "user": "None"
    },
    {
        "url": "https://kabarpublik.id/feed/",
        "user": "None"
    },
    {
        "url": "https://www.nasionalnews.id/feed/",
        "user": "None"
    },
]

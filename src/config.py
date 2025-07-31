# ===== CONFIGURATION & CONSTANTS =====
import logging
import os

# --- General Settings ---
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
CACHE_DIR = "cache"
DEFAULT_CACHE_TTL = 3600  # 1 hour in seconds
DATABASE_PATH = "data/games.db"
WEB_DATA_DIR = "web_data"
WEB_DATA_FILE = "free_games.json"

# --- Web Scraping & API Headers ---
COMMON_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
    'Accept-Language': 'en-US,en;q=0.9',
    'Accept': 'application/json, text/plain, */*',
    'Accept-Encoding': 'gzip, deflate, br',
    'Connection': 'keep-alive'
}

# --- Telegram Bot Settings ---
TELEGRAM_STORE_DISPLAY_NAMES = {
    "epicgames": "اپیک گیمز",
    "steam": "استیم",
    "googleplay": "گوگل پلی",
    "android": "اندروید",
    "ios": "آی‌اواس",
    "iosappstore": "اپ استور iOS",
    "playstation": "پلی‌استیشن",
    "xbox": "ایکس‌باکس",
    "gog": "GOG",
    "itch.io": "Itch.io",
    "indiegala": "ایندی‌گالا",
    "stove": "STOVE",
    "microsoftstore": "مایکروسافت استور",
    "other": "سایر",
    "reddit": "ردیت",
    "humblestore": "هامبل استور",
    "fanatical": "فناتیکال",
    "greenmangaming": "گرین من گیمینگ",
    "amazon": "آمازون",
    "blizzard": "بلیزارد",
    "eastore": "EA استور",
    "ubisoftstore": "یوبی‌سافت استور",
    "all": "همه فروشگاه‌ها"
}

# --- Epic Games Source ---
EPIC_GAMES_API_URL = "https://store-content-ipv4.ak.epicgames.com/api/graphql"
EPIC_GAMES_HEADERS = {**COMMON_HEADERS, 'Referer': 'https://www.epicgames.com/store/', 'Origin': 'https://www.epicgames.com'}

# --- ITAD Source ---
ITAD_DEALS_URL = "https://isthereanydeal.com/deals/#filter:N4IgDgTglgxgpiAXKAtlAdk9BXANrgGhBQEMAPJABgF8iAXATzAUQG0BGAXWqA=="

# --- Metacritic Enricher ---
METACRITIC_BASE_URL = "https://www.metacritic.com"
METACRITIC_SEARCH_URL = "https://www.metacritic.com/search/{query}/"

# --- Reddit Source ---
REDDIT_SUBREDDITS = ['GameDeals', 'FreeGameFindings', 'googleplaydeals', 'AppHookup']
REDDIT_RSS_URL_TEMPLATE = "https://www.reddit.com/r/{sub}/new/.rss"

# --- Steam Enricher ---
STEAM_API_URL = "https://store.steampowered.com/api/appdetails?appids={app_id}&cc=us&l=english"
STEAM_SEARCH_URL = "https://store.steampowered.com/search/?term={query}&category1=998"

# --- Translator ---
GOOGLE_TRANSLATE_URL = "https://translate.googleapis.com/translate_a/single"
MYMEMORY_API_URL = "https://api.mymemory.translated.net/get"

# --- Game Classification Keywords ---
DLC_KEYWORDS = ["dlc", "expansion", "season pass", "soundtrack", "artbook", "bonus", "pack", "upgrade", "add-on"]
AMBIGUOUS_KEYWORDS = ["bundle", "edition", "ultimate", "deluxe", "collection", "complete"]
POSITIVE_GAME_KEYWORDS = ["game", "full game", "standard edition"]

# --- Store Detection Keywords ---
# A mapping of domain/keyword to a canonical store name
STORE_KEYWORD_MAP = {
    "steampowered.com": "steam",
    "epicgames.com": "epicgames",
    "play.google.com": "googleplay",
    "apps.apple.com": "iosappstore",
    "gog.com": "gog",
    "itch.io": "itch.io",
    "indiegala.com": "indiegala",
    "humblebundle.com": "humblestore",
    "fanatical.com": "fanatical",
    "greenmangaming.com": "greenmangaming",
    "playstation.com": "playstation",
    "xbox.com": "xbox",
    "microsoft.com": "microsoftstore",
    "amazon.com": "amazon",
    "blizzard.com": "blizzard",
    "ubisoft.com": "ubisoftstore",
    "ea.com": "eastore",
    "onstove.com": "stove",
    "freegamefindings": "reddit",
    "gamedeals": "reddit"
}
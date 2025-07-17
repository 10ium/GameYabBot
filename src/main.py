import os
import asyncio
import logging
import json
import re
from typing import List, Dict, Any
import random # برای تأخیر تصادفی
from urllib.parse import urlparse, urlunparse, parse_qs # وارد کردن ماژول‌های تجزیه URL

# وارد کردن ماژول‌های اصلی
from core.database import Database
from core.telegram_bot import TelegramBot
# وارد کردن منابع داده (ITAD اکنون از Playwright استفاده می‌کند، Epic Games از aiohttp)
from sources.itad import ITADSource
from sources.reddit import RedditSource
from sources.epic_games import EpicGamesSource
# وارد کردن ماژول‌های غنی‌سازی داده
from enrichment.steam_enricher import SteamEnricher
from enrichment.metacritic_enricher import MetacriticEnricher
# وارد کردن ماژول ترجمه
from translation.translator import SmartTranslator
# وارد کردن ابزارهای کمکی
from utils import clean_title_for_search # وارد کردن تابع تمیزکننده مشترک

# تنظیمات اولیه لاگ‌گیری
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# دریافت توکن‌ها از متغیرهای محیطی
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
DEEPL_API_KEY = os.getenv("DEEPL_API_KEY") # این متغیر باید به SmartTranslator پاس داده شود اگر DeepL استفاده می‌شود

def _infer_store_from_game_data(game: Dict[str, Any]) -> str:
    """
    نام فروشگاه را از داده‌های بازی (ترجیحاً از فیلد 'store'، سپس از URL، سپس از عنوان) استنتاج می‌کند.
    """
    # 1. اولویت با فیلد 'store' موجود
    if game.get('store') and game['store'].lower() != 'unknown':
        return game['store'].lower().replace(' ', '')

    # 2. استنتاج از URL
    url = game.get('url')
    if url:
        try:
            parsed_url = urlparse(url)
            domain = parsed_url.netloc.lower()
            if 'steampowered.com' in domain:
                return 'steam'
            elif 'epicgames.com' in domain:
                return 'epicgames'
            elif 'gog.com' in domain:
                return 'gog'
            elif 'itch.io' in domain:
                return 'itch.io'
            elif 'indiegala.com' in domain:
                return 'indiegala'
            elif 'microsoft.com' in domain or 'xbox.com' in domain:
                return 'microsoftstore' # یا 'xbox'
            elif 'playstation.com' in domain:
                return 'playstation'
            elif 'nintendo.com' in domain:
                return 'nintendo'
            elif 'ea.com' in domain:
                return 'eastore'
            elif 'ubisoft.com' in domain:
                return 'ubisoftstore'
            elif 'humblebundle.com' in domain:
                return 'humblestore'
            elif 'fanatical.com' in domain:
                return 'fanatical'
            elif 'greenmangaming.com' in domain:
                return 'greenmangaming'
            elif 'amazon.com' in domain:
                return 'amazon'
            elif 'blizzard.com' in domain:
                return 'blizzard'
            elif 'reddit.com' in domain or 'redd.it' in domain: # برای لینک‌هایی که مستقیماً از Reddit می‌آیند
                return 'reddit'
            # می‌توانید دامنه های بیشتری را اینجا اضافه کنید
        except Exception as e:
            logging.warning(f"⚠️ خطای تجزیه URL برای استنتاج فروشگاه: {url} - {e}")

    # 3. استنتاج از عنوان (با استفاده از تگ‌های رایج)
    title = game.get('title', '').lower()
    if '[steam]' in title:
        return 'steam'
    elif '[epic games]' in title or '[egs]' in title:
        return 'epicgames'
    elif '[gog]' in title:
        return 'gog'
    elif '[xbox]' in title:
        return 'microsoftstore' # یا 'xbox'
    elif '[ps]' in title or '[playstation]' in title:
        return 'playstation'
    elif '[switch]' in title or '[nintendo]' in title:
        return 'nintendo'
    elif '[android]' in title or '[googleplay]' in title or '[google play]' in title:
        return 'google play'
    elif '[ios]' in title or '[apple]' in title:
        return 'ios app store'
    elif '[itch.io]' in title or '[itchio]' in title:
        return 'itch.io'
    elif '[indiegala]' in title:
        return 'indiegala'
    elif '[stove]' in title:
        return 'stove'
    elif '[amazon]' in title:
        return 'amazon'
    elif '[ubisoft]' in title:
        return 'ubisoftstore'
    elif '[humble]' in title:
        return 'humblestore'
    elif '[fanatical]' in title:
        return 'fanatical'
    elif '[gmg]' in title:
        return 'greenmangaming'
    elif '[blizzard]' in title:
        return 'blizzard'
    elif '[ea]' in title:
        return 'eastore'
    elif '[reddit]' in title:
        return 'reddit'

    # 4. در نهایت، به 'other' برگرد
    return 'other'

def _normalize_url_for_key(url: str) -> str:
    """
    URL را برای استفاده به عنوان بخشی از کلید deduplication نرمال‌سازی می‌کند.
    شناسه منحصر به فرد بازی را از URLهای فروشگاه‌های خاص استخراج می‌کند.
    """
    try:
        parsed = urlparse(url)
        # حذف پارامترهای کوئری و قطعات، فقط طرح، دامنه و مسیر را حفظ می‌کند
        normalized_path = parsed.path.rstrip('/') # حذف اسلش انتهایی
        
        # مدیریت خاص برای URLهای فروشگاه برای قوی‌تر کردن آن‌ها
        if 'steampowered.com' in parsed.netloc:
            # شناسه‌های Steam app معمولاً در /app/{id}/ هستند
            match = re.search(r'/app/(\d+)/?', normalized_path)
            if match:
                return f"steam_app_{match.group(1)}"
        elif 'epicgames.com/store/p/' in url:
            # slugهای محصول Epic منحصر به فرد هستند
            match = re.search(r'/store/p/([^/?#]+)', normalized_path)
            if match:
                return f"epic_product_{match.group(1)}"
        elif 'gog.com' in parsed.netloc:
            # slugهای بازی GOG منحصر به فرد هستند
            match = re.search(r'/(game|movie)/([^/?#]+)', normalized_path)
            if match:
                return f"gog_game_{match.group(2)}"
        # می‌توانید منطق نرمال‌سازی خاص فروشگاه‌های بیشتری را اینجا اضافه کنید
        # برای مثال:
        # elif 'microsoft.com/store/p/' in url:
        #     match = re.search(r'/store/p/([^/?#]+)', normalized_path)
        #     if match:
        #         return f"ms_product_{match.group(1)}"

        # برای سایر URLها، فقط طرح+دامنه+مسیر نرمال‌شده را برگردان
        return urlunparse((parsed.scheme, parsed.netloc, normalized_path, '', '', ''))
    except Exception:
        # اگر تجزیه URL با شکست مواجه شد، URL اصلی را به عنوان فال‌بک برگردان، اما لاگ کن
        logging.warning(f"Failed to normalize URL for key: {url}", exc_info=True)
        return url # فال‌بک به URL اصلی اگر نرمال‌سازی با شکست مواجه شد

def _classify_game_type(game: Dict[str, Any]) -> Dict[str, Any]:
    """
    بازی را به عنوان یک بازی کامل یا DLC/محتوای اضافی طبقه‌بندی می‌کند.
    فیلد 'is_dlc_or_addon' را به دیکشنری بازی اضافه می‌کند.
    """
    game['is_dlc_or_addon'] = False # پیش‌فرض

    title_lower = game.get('title', '').lower()
    url_lower = game.get('url', '').lower()
    product_slug_lower = game.get('productSlug', '').lower() # برای Epic Games

    # کلمات کلیدی رایج برای DLC/محتوای اضافی در عنوان
    dlc_keywords = [
        "dlc", "expansion", "season pass", "soundtrack", "artbook", "bonus",
        "pack", "upgrade", "add-on", "bundle", "edition", "ultimate", "deluxe" # "bundle", "edition", "ultimate", "deluxe" ممکن است بازی کامل هم باشند، با احتیاط استفاده شود.
    ]
    # الگوهای خاص برای جلوگیری از false positives برای "bundle", "edition"
    # اگر عنوان شامل "game" یا "full game" باشد، کمتر احتمال دارد DLC باشد.
    positive_game_keywords = ["game", "full game", "standard edition"]

    # بررسی کلمات کلیدی در عنوان
    if any(keyword in title_lower for keyword in dlc_keywords):
        # بررسی برای false positives: اگر کلمه کلیدی DLC وجود دارد اما کلمه کلیدی بازی کامل هم هست
        if not any(pk in title_lower for pk in positive_game_keywords):
            game['is_dlc_or_addon'] = True
            logging.debug(f"بازی '{game.get('title')}' به عنوان DLC/Addon (عنوان) طبقه‌بندی شد.")
            return game # اگر از عنوان تشخیص داده شد، دیگر نیازی به بررسی URL نیست

    # بررسی الگوهای URL/slug برای Epic Games
    if game.get('store', '').lower().replace(' ', '') == 'epicgames':
        if "edition" in product_slug_lower and "standard-edition" not in product_slug_lower:
             game['is_dlc_or_addon'] = True
             logging.debug(f"بازی '{game.get('title')}' به عنوان DLC/Addon (اسلاگ Epic) طبقه‌بندی شد.")
        elif "dlc" in product_slug_lower or "expansion" in product_slug_lower or "soundtrack" in product_slug_lower:
            game['is_dlc_or_addon'] = True
            logging.debug(f"بازی '{game.get('title')}' به عنوان DLC/Addon (اسلاگ Epic) طبقه‌بندی شد.")
        elif "bundle" in product_slug_lower and "game" not in title_lower: # اگر bundle بود و عنوان شامل "game" نبود
            game['is_dlc_or_addon'] = True
            logging.debug(f"بازی '{game.get('title')}' به عنوان DLC/Addon (اسلاگ Epic Bundle) طبقه‌بندی شد.")
    
    # بررسی الگوهای URL عمومی
    if "/dlc/" in url_lower or "/addons/" in url_lower or "/soundtrack/" in url_lower or "/artbook/" in url_lower:
        game['is_dlc_or_addon'] = True
        logging.debug(f"بازی '{game.get('title')}' به عنوان DLC/Addon (URL) طبقه‌بندی شد.")

    # اگر هنوز تشخیص داده نشد و عنوان شامل "bundle" یا "pack" است، با احتیاط بیشتر بررسی کن
    if not game['is_dlc_or_addon'] and ("bundle" in title_lower or "pack" in title_lower):
        # اگر "bundle" یا "pack" بود اما "game" یا "collection" یا "games" در عنوان نبود، احتمالاً DLC است
        if not any(kw in title_lower for kw in ["game", "games", "collection", "complete"]):
            game['is_dlc_or_addon'] = True
            logging.debug(f"بازی '{game.get('title')}' به عنوان DLC/Addon (عنوان مشکوک) طبقه‌بندی شد.")

    return game


def _get_deduplication_key(game: Dict[str, Any]) -> str:
    """
    یک کلید منحصر به فرد برای deduplication بازی‌ها ایجاد می‌کند.
    اولویت با URL نرمال‌شده به همراه نام فروشگاه است.
    """
    store_name = _infer_store_from_game_data(game) # دریافت نام فروشگاه استنتاج شده

    # اگر بازی تخفیف‌دار است، یک پیشوند اضافه کن تا از پیشنهادهای رایگان متمایز شود
    prefix = "discount_" if not game.get('is_free', True) else ""
    # اگر DLC یا Addon است، یک پیشوند اضافه کن تا از بازی‌های کامل متمایز شود
    dlc_prefix = "dlc_" if game.get('is_dlc_or_addon', False) else ""

    # 1. اولویت با URL نرمال‌شده + نام فروشگاه
    if 'url' in game and game['url'] and game['url'].startswith(('http://', 'https://')):
        normalized_url_part = _normalize_url_for_key(game['url'])
        if normalized_url_part: # اطمینان حاصل کن که نرمال‌سازی موفقیت‌آمیز بوده و یک کلید معنی‌دار تولید کرده است
            return f"{prefix}{dlc_prefix}{normalized_url_part}_{store_name}"
    
    # 2. فال‌بک به Steam App ID + نام فروشگاه (اگر URL مناسب نبود یا موجود نبود)
    # این شامل مواردی است که یک بازی Steam ممکن است بدون URL مستقیم Steam لیست شده باشد
    # اما یک Steam App ID از غنی‌سازی داشته باشد.
    if 'steam_app_id' in game and game['steam_app_id']:
        return f"{prefix}{dlc_prefix}steam_app_{game['steam_app_id']}_{store_name}"
    
    # 3. فال‌بک به عنوان تمیز شده + نام فروشگاه
    cleaned_title = clean_title_for_search(game.get('title', ''))
    if cleaned_title:
        return f"{prefix}{dlc_prefix}{cleaned_title}_{store_name}"
    
    # 4. آخرین راه حل: استفاده از id_in_db (شناسه خاص منبع) + هش تصادفی
    # این باید در موارد بسیار نادر رخ دهد اگر منابع داده‌های خوبی ارائه دهند.
    return f"{prefix}{dlc_prefix}fallback_{game.get('id_in_db', os.urandom(8).hex())}"

def _merge_game_data(existing_game: Dict[str, Any], new_game: Dict[str, Any]) -> Dict[str, Any]:
    """
    داده‌های یک بازی جدید را در یک بازی موجود ادغام می‌کند و داده‌های کامل‌تر/معتبرتر را اولویت می‌دهد.
    URL اصلی (که بازی رایگان از آن شناسایی شده) حفظ می‌شود.
    """
    merged_game = existing_game.copy()

    # اولویت‌بندی Steam App ID به عنوان شناسه اصلی
    # این فقط ID را اضافه می‌کند، URL را تغییر نمی‌دهد زیرا URL باید به پیشنهاد خاص اشاره کند.
    if 'steam_app_id' in new_game and new_game['steam_app_id']:
        merged_game['steam_app_id'] = new_game['steam_app_id']

    # اولویت‌بندی image_url: تصویر با کیفیت بالاتر (معمولا از Steam) یا غیر placeholder
    if 'image_url' in new_game and new_game['image_url']:
        # اگر تصویر موجود نیست، یا placeholder است، یا از Reddit است، تصویر جدید را جایگزین کن
        if not merged_game.get('image_url') or \
           "placehold.co" in merged_game['image_url'] or \
           "reddit.com" in merged_game['image_url']:
            merged_game['image_url'] = new_game['image_url']
    
    # اولویت‌بندی description/persian_summary: متن طولانی‌تر یا موجود
    if 'description' in new_game and new_game['description'] and \
       (not merged_game.get('description') or len(new_game['description']) > len(merged_game['description'])):
        merged_game['description'] = new_game['description']
    if 'persian_summary' in new_game and new_game['persian_summary'] and \
       (not merged_game.get('persian_summary') or len(new_game['persian_summary']) > len(merged_game['persian_summary'])):
        merged_game['persian_summary'] = new_game['persian_summary']

    # URL اصلی (url) از منبعی که بازی را به عنوان رایگان گزارش کرده، باید حفظ شود.
    # با توجه به تغییر در _get_deduplication_key، این تابع کمتر برای ادغام پیشنهادهای یک بازی از فروشگاه‌های مختلف فراخوانی می‌شود.
    # بنابراین، URL موجود (existing_game['url']) معمولاً همان URL صحیح پیشنهاد خواهد بود.
    # نیازی به منطق پیچیده برای URL در اینجا نیست، زیرا deduplication آن را مدیریت می‌کند.

    # ادغام نمرات و سایر ویژگی‌ها، با اولویت‌بندی مقادیر غیر خالی
    for key in ['metacritic_score', 'metacritic_userscore',
                'steam_overall_score', 'steam_overall_reviews_count',
                'steam_recent_score', 'steam_recent_reviews_count',
                'genres', 'trailer', 'is_multiplayer', 'is_online', 'age_rating', 'is_free', 'discount_text',
                'persian_genres', 'persian_age_rating', 'is_dlc_or_addon']: # is_dlc_or_addon هم اضافه شد
        if key in new_game and new_game[key]:
            if key in ['is_multiplayer', 'is_online', 'is_free', 'is_dlc_or_addon']: # برای پرچم‌های بولی، OR کن (برای is_free، اگر یکی True بود، True بماند)
                merged_game[key] = merged_game.get(key, False) or new_game[key]
            elif key == 'genres' or key == 'persian_genres': # برای لیست‌ها، آیتم‌های منحصر به فرد را ادغام کن
                merged_game[key] = list(set(merged_game.get(key, []) + new_game[key]))
            elif key == 'discount_text' and not merged_game.get('discount_text'): # فقط اگر discount_text موجود نیست، اضافه کن
                merged_game[key] = new_game[key]
            else: # برای سایر فیلدها، مقدار جدید را جایگزین کن
                merged_game[key] = new_game[key]
    
    # اطمینان از اینکه عنوان تمیز شده، بهترین عنوان ممکن است
    # اگر عنوان جدید پس از تمیز شدن طولانی‌تر (و احتمالا کامل‌تر) باشد، آن را جایگزین کن
    if len(clean_title_for_search(new_game.get('title', ''))) > \
       len(clean_title_for_search(merged_game.get('title', ''))):
        merged_game['title'] = new_game['title']

    return merged_game

async def enrich_and_translate_game(game: Dict[str, Any], steam_enricher: SteamEnricher, metacritic_enricher: MetacriticEnricher, translator: SmartTranslator) -> Dict[str, Any]:
    """
    بازی را با اطلاعات اضافی غنی‌سازی و توضیحات آن را ترجمه می‌کند،
    با اعمال enricherها بر اساس پلتفرم.
    """
    store = game.get('store', '').lower().replace(' ', '')

    # تعیین پلتفرم بر اساس فروشگاه
    # 'epic games' به عنوان دسکتاپ در نظر گرفته می‌شود.
    # 'epic games (android)', 'epic games (ios)' به عنوان موبایل در نظر گرفته می‌شوند.
    is_desktop_store = store in ['steam', 'epicgames', 'gog', 'itch.io', 'indiegala', 'stove', 'other', 'reddit', 'microsoftstore', 'humblestore', 'fanatical', 'greenmangaming', 'amazon', 'blizzard', 'eastore', 'ubisoftstore'] # 'reddit' هم می‌تواند دسکتاپ باشد
    is_console_store = store in ['xbox', 'playstation', 'nintendo']
    is_mobile_store = store in ['google play', 'ios app store', 'epic games (android)', 'epic games (ios)']

    # اعمال SteamEnricher فقط برای بازی‌های دسکتاپ
    if is_desktop_store:
        game = await steam_enricher.enrich_data(game)
    else:
        logging.info(f"ℹ️ SteamEnricher برای بازی موبایل/کنسول '{game.get('title', 'نامشخص')}' (فروشگاه: {game.get('store')}) اعمال نشد.")

    # اعمال MetacriticEnricher برای بازی‌های دسکتاپ، کنسول و موبایل
    if is_desktop_store or is_console_store or is_mobile_store:
        game = await metacritic_enricher.enrich_data(game)
    else:
        logging.info(f"ℹ️ MetacriticEnricher برای بازی '{game.get('title', 'نامشخص')}' (فروشگاه: {game.get('store')}) اعمال نشد.")

    # ترجمه توضیحات در صورت وجود
    description = game.get('description')
    if description and translator:
        game['persian_summary'] = await translator.translate(description)

    # ترجمه ژانرها در صورت وجود
    genres = game.get('genres')
    if genres and isinstance(genres, list) and translator:
        translated_genres = []
        for genre in genres:
            translated_genres.append(await translator.translate(genre))
        game['persian_genres'] = translated_genres

    # ترجمه رده‌بندی سنی در صورت وجود
    age_rating = game.get('age_rating')
    if age_rating and translator:
        game['persian_age_rating'] = await translator.translate(age_rating)

    # طبقه‌بندی نوع بازی (DLC/Addon)
    game = _classify_game_type(game)

    return game

async def main():
    logging.info("🚀 ربات گیم رایگان شروع به کار کرد...")

    if not TELEGRAM_BOT_TOKEN:
        logging.error("متغیر محیطی TELEGRAM_BOT_TOKEN تنظیم نشده است. برنامه متوقف می‌شود.")
        return

    db = Database(db_path="data/games.db")
    # bot = TelegramBot(token=TELEGRAM_BOT_TOKEN, db=db) # غیرفعال کردن موقت ربات تلگرام
    translator = SmartTranslator() 

    # --- مرحله ۱: پردازش دستورات معلق کاربران ---
    # await bot.process_pending_updates() # غیرفعال کردن موقت پردازش آپدیت‌ها

    # --- مرحله ۲: نمونه‌سازی و جمع‌آوری داده از تمام منابع ---
    logging.info("🎮 شروع فرآیند یافتن بازی‌های رایگان...")
    sources = [
        ITADSource(),
        RedditSource(),
        EpicGamesSource()
    ]
    
    fetch_tasks = [source.fetch_free_games() for source in sources]
    results = await asyncio.gather(*fetch_tasks, return_exceptions=True)
    
    all_games_raw = []
    for result in results:
        if isinstance(result, list):
            all_games_raw.extend(result)
        elif isinstance(result, Exception):
            logging.error(f"خطا در یکی از منابع داده: {result}")

    if not all_games_raw:
        logging.info("هیچ بازی از منابع یافت نشد.")
        output_dir = "web_data"
        os.makedirs(output_dir, exist_ok=True)
        output_file_path = os.path.join(output_dir, "free_games.json")
        with open(output_file_path, 'w', encoding='utf-8') as f:
            json.dump([], f, ensure_ascii=False, indent=4)
        logging.info(f"✅ فایل {output_file_path} با لیست خالی به‌روز شد.")
        db.close()
        return

    logging.info(f"✅ {len(all_games_raw)} بازی خام از منابع مختلف یافت شد.")

    # --- مرحله ۳: غنی‌سازی و ترجمه تمام بازی‌های یافت شده (قبل از deduplication) ---
    steam_enricher = SteamEnricher()
    metacritic_enricher = MetacriticEnricher()
    
    enrich_tasks = [
        enrich_and_translate_game(game, steam_enricher, metacritic_enricher, translator)
        for game in all_games_raw
    ]
    
    # این لیست شامل تمام بازی‌های غنی‌شده است که ممکن است شامل تکراری‌ها باشد
    enriched_games_with_potential_duplicates = await asyncio.gather(*enrich_tasks)
    
    # --- مرحله ۴: deduplication بر اساس کلید منحصر به فرد و انتخاب بهترین نسخه ---
    final_unique_games_dict: Dict[str, Dict[str, Any]] = {} # Dictionary to store the best version of each game by key
    
    for game in enriched_games_with_potential_duplicates:
        dedup_key = _get_deduplication_key(game)
        if not dedup_key: # Skip if key generation failed
            logging.warning(f"⚠️ کلید deduplication برای بازی '{game.get('title', 'نامشخص')}' تولید نشد. این بازی ممکن است تکراری باشد.")
            continue

        if dedup_key not in final_unique_games_dict:
            final_unique_games_dict[dedup_key] = game
        else:
            # اگر کلید تکراری بود، داده‌ها را ادغام کن
            existing_game = final_unique_games_dict[dedup_key]
            merged_game = _merge_game_data(existing_game, game)
            final_unique_games_dict[dedup_key] = merged_game
            logging.info(f"✨ بازی تکراری '{game.get('title', 'نامشخص')}' (کلید: {dedup_key}) ادغام شد.")

    final_unique_games = list(final_unique_games_dict.values())

    if not final_unique_games:
        logging.info("پس از deduplication، هیچ بازی منحصر به فردی برای پردازش یافت نشد.")
        output_dir = "web_data"
        os.makedirs(output_dir, exist_ok=True)
        output_file_path = os.path.join(output_dir, "free_games.json")
        with open(output_file_path, 'w', encoding='utf-8') as f:
            json.dump([], f, ensure_ascii=False, indent=4)
        logging.info(f"✅ فایل {output_file_path} با لیست خالی به‌روز شد.")
        db.close()
        return

    logging.info(f"✅ {len(final_unique_games)} بازی منحصر به فرد (پس از deduplication) برای پردازش یافت شد.")

    # --- مرحله ۵: فیلتر کردن بازی‌ها برای ارسال به تلگرام (فقط بازی‌های جدید در ۳۰ روز گذشته) ---
    # games_to_post_to_telegram = [] # غیرفعال کردن موقت ارسال به تلگرام
    # for game in final_unique_games: # از لیست deduplicate شده استفاده می‌کنیم
    #     url = game.get('url')
    #     # فقط بازی‌های کاملاً رایگان و غیر DLC را به تلگرام ارسال کن
    #     if game.get('is_free', True) and not game.get('is_dlc_or_addon', False): 
    #         if url and not db.is_game_posted_in_last_30_days(url):
    #             games_to_post_to_telegram.append(game)
    #         else:
    #             logging.info(f"ℹ️ بازی '{game.get('title', 'نامشخص')}' (فروشگاه: {game.get('store')}) قبلاً در ۳۰ روز گذشته پست شده بود یا URL ندارد. به تلگرام ارسال نمی‌شود.")
    #     else:
    #         # این لاگ برای بازی‌های تخفیف‌دار یا DLCها (رایگان یا تخفیف‌دار) است که به تلگرام ارسال نمی‌شوند.
    #         game_type_info = "تخفیف" if not game.get('is_free', True) else "DLC/Addon"
    #         logging.info(f"ℹ️ بازی '{game.get('title', 'نامشخص')}' (فروشگاه: {game.get('store')}, نوع: {game_type_info}) به تلگرام ارسال نمی‌شود.")


    # --- مرحله ۶: ارسال پیام‌ها به تلگرام ---
    # if not games_to_post_to_telegram: # غیرفعال کردن موقت ارسال به تلگرام
    #     logging.info("هیچ بازی جدیدی برای ارسال به تلگرام (بر اساس فیلتر ۳۰ روز گذشته) یافت نشد.")
    # else:
    #     logging.info(f"📤 {len(games_to_post_to_telegram)} بازی برای ارسال به تلگرام آماده است.")
    #     for game in games_to_post_to_telegram:
    #         store_name = _infer_store_from_game_data(game) # استفاده از تابع جدید برای تعیین نام فروشگاه در اینجا
    #         targets = db.get_targets_for_store(store_name)
            
    #         if not targets:
    #             logging.warning(f"هیچ مشترکی برای فروشگاه '{store_name}' یافت نشد. از ارسال '{game['title']}' صرف نظر شد.")
    #             continue

    #         logging.info(f"📤 در حال ارسال پیام برای '{game['title']}' به {len(targets)} مقصد...")
    #         send_tasks = [
    #             bot.send_formatted_message(game_data=game, chat_id=chat_id, thread_id=thread_id)
    #             for chat_id, thread_id in targets
    #         ]
    #         await asyncio.gather(*send_tasks, return_exceptions=True)
    #         db.add_posted_game(game['url']) # ثبت بازی پس از ارسال موفقیت‌آمیز

    # --- مرحله ۷: ذخیره داده‌های غنی‌شده برای GitHub Pages ---
    # این مرحله همیشه اجرا می‌شود تا فایل JSON برای وب‌سایت با بازی‌های منحصر به فرد به‌روز باشد.
    output_dir = "web_data"
    os.makedirs(output_dir, exist_ok=True)
    output_file_path = os.path.join(output_dir, "free_games.json")
    with open(output_file_path, 'w', encoding='utf-8') as f:
        # اکنون تمام بازی‌های منحصر به فرد (رایگان و تخفیف‌دار، شامل DLC/Addon) برای وب‌سایت ذخیره می‌شوند.
        # فیلترینگ در فرانت‌اند انجام خواهد شد.
        json.dump(final_unique_games, f, ensure_ascii=False, indent=4)
    logging.info(f"✅ داده‌های بازی‌ها (رایگان و تخفیف‌دار) برای GitHub Pages در {output_file_path} ذخیره شد.")

    db.close()
    logging.info("🏁 کار ربات با موفقیت به پایان رسید.")

if __name__ == "__main__":
    asyncio.run(main())

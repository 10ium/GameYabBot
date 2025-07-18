import logging
import re
from typing import Dict, Any
from urllib.parse import urlparse, urlunparse

logger = logging.getLogger(__name__)

# الگوهای URL برای شناسایی فروشگاه‌ها (ترتیب مهم است: خاص‌ترها اول)
URL_STORE_MAP_PRIORITY = [
    (r"epicgames\.com/store/p/.*-android-", "epic games (android)"), 
    (r"epicgames\.com/store/p/.*-ios-", "epic games (ios)"),
    (r"epicgames\.com/store/p/", "epic games"),
    (r"store\.steampowered\.com", "steam"),
    (r"play\.google\.com", "google play"),
    (r"apps\.apple\.com", "ios app store"),
    (r"xbox\.com", "xbox"),
    (r"playstation\.com", "playstation"), 
    (r"gog\.com", "gog"),
    (r"itch\.io", "itch.io"),
    (r"indiegala\.com", "indiegala"),
    (r"onstove\.com", "stove"),
    (r"microsoft\.com", "microsoftstore"), # باید قبل از xbox.com باشد اگر microsoft.com شامل xbox هم می‌شود
    (r"ea\.com", "eastore"),
    (r"ubisoft\.com", "ubisoftstore"),
    (r"humblebundle\.com", "humblestore"),
    (r"fanatical\.com", "fanatical"),
    (r"greenmangaming\.com", "greenmangaming"),
    (r"amazon\.com", "amazon"),
    (r"blizzard\.com", "blizzard"),
    (r"reddit\.com", "reddit"), # برای لینک‌هایی که مستقیماً از Reddit می‌آیند
    (r"redd\.it", "reddit"), # Shortened Reddit links
    (r"givee\.club", "other") # اضافه شدن givee.club
]

# نگاشت کلمات کلیدی در عنوان به نام فروشگاه
TITLE_STORE_MAP = {
    "steam": "steam",
    "epic games": "epic games",
    "egs": "epic games",
    "gog": "gog",
    "xbox": "xbox",
    "ps": "playstation",
    "playstation": "playstation",
    "switch": "nintendo",
    "nintendo": "nintendo",
    "android": "google play",
    "googleplay": "google play",
    "google play": "google play",
    "ios": "ios app store",
    "apple": "ios app store",
    "itch.io": "itch.io",
    "itchio": "itch.io",
    "indiegala": "indiegala",
    "stove": "stove",
    "amazon": "amazon",
    "ubisoft": "ubisoftstore",
    "humble": "humblestore",
    "fanatical": "fanatical",
    "gmg": "greenmangaming",
    "blizzard": "blizzard",
    "ea": "eastore",
    "reddit": "reddit"
}


def infer_store_from_game_data(game: Dict[str, Any]) -> str:
    """
    نام فروشگاه را از داده‌های بازی (ترجیحاً از فیلد 'store'، سپس از URL، سپس از عنوان) استنتاج می‌کند.
    """
    # 1. اولویت با فیلد 'store' موجود
    if game.get('store') and game['store'].lower() != 'unknown':
        store_name = game['store'].lower().replace(' ', '')
        logger.debug(f"[StoreDetector] فروشگاه از فیلد 'store' استنتاج شد: {store_name} (عنوان: {game.get('title')})")
        return store_name

    # 2. استنتاج از URL
    url = game.get('url')
    if url:
        try:
            parsed_url = urlparse(url)
            domain = parsed_url.netloc.lower()
            
            for pattern, store_name in URL_STORE_MAP_PRIORITY:
                if re.search(pattern, domain, re.IGNORECASE): # جستجو در دامنه
                    logger.debug(f"[StoreDetector] فروشگاه '{store_name}' از URL استنتاج شد: {url} (عنوان: {game.get('title')})")
                    return store_name
        except Exception as e:
            logger.warning(f"⚠️ [StoreDetector] خطای تجزیه URL برای استنتاج فروشگاه: {url} - {e}")

    # 3. استنتاج از عنوان (با استفاده از تگ‌های رایج)
    title = game.get('title', '').lower()
    for keyword, store_name in TITLE_STORE_MAP.items():
        if f"[{keyword}]" in title or f"({keyword})" in title or keyword in title: # جستجوی انعطاف‌پذیرتر
            logger.debug(f"[StoreDetector] فروشگاه '{store_name}' از عنوان استنتاج شد: {title}")
            return store_name

    # 4. در نهایت، به 'other' برگرد
    logger.debug(f"[StoreDetector] فروشگاه برای '{game.get('title', 'نامشخص')}' از هیچ منبعی استنتاج نشد. 'other' برگردانده شد.")
    return 'other'

def normalize_url_for_key(url: str) -> str:
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
                logger.debug(f"[StoreDetector - normalize_url_for_key] URL Steam نرمال‌سازی شد به: steam_app_{match.group(1)}")
                return f"steam_app_{match.group(1)}"
        elif 'epicgames.com/store/p/' in url:
            # slugهای محصول Epic منحصر به فرد هستند
            match = re.search(r'/store/p/([^/?#]+)', normalized_path)
            if match:
                logger.debug(f"[StoreDetector - normalize_url_for_key] URL Epic Games نرمال‌سازی شد به: epic_product_{match.group(1)}")
                return f"epic_product_{match.group(1)}"
        elif 'gog.com' in parsed.netloc:
            # slugهای بازی GOG منحصر به فرد هستند
            match = re.search(r'/(game|movie)/([^/?#]+)', normalized_path)
            if match:
                logger.debug(f"[StoreDetector - normalize_url_for_key] URL GOG نرمال‌سازی شد به: gog_game_{match.group(2)}")
                return f"gog_game_{match.group(2)}"

        # برای سایر URLها، فقط طرح+دامنه+مسیر نرمال‌شده را برگردان
        normalized_full_url = urlunparse((parsed.scheme, parsed.netloc, normalized_path, '', '', ''))
        logger.debug(f"[StoreDetector - normalize_url_for_key] URL عمومی نرمال‌سازی شد به: {normalized_full_url}")
        return normalized_full_url
    except Exception:
        logger.warning(f"⚠️ [StoreDetector - normalize_url_for_key] خطای نرمال‌سازی URL برای کلید: {url}. از URL اصلی استفاده می‌شود.", exc_info=True)
        return url # فال‌بک به URL اصلی اگر نرمال‌سازی با شکست مواجه شد

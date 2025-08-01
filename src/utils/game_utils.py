# ===== IMPORTS & DEPENDENCIES =====
import re
import logging
from typing import Dict, Any
from urllib.parse import urlparse, parse_qs, urlencode
from bs4 import BeautifulSoup

from src.config import STORE_KEYWORD_MAP
from src.models.game import GameData

# ===== CONFIGURATION & CONSTANTS =====
logger = logging.getLogger(__name__)

# ===== UTILITY FUNCTIONS =====

def clean_title(raw_title: str) -> str:
    """
    Intelligently cleans a game title for searching and display by removing noise in multiple stages.
    """
    if not raw_title:
        return ""

    logger.debug(f"[clean_title] Original title: '{raw_title}'")
    
    cleaned = raw_title.lower()
    cleaned = re.sub(r'\[\s*(steam|epic\s*games?|gog|pc|windows|mac|linux|drm-?free|itch\.io|indiegala|other|reddit|game|dlc|addon|app)\s*\]', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\(\s*(game|dlc|addon|app|soundtrack|pc|windows|mac|linux)\s*\)', '', cleaned, flags=re.IGNORECASE)

    edition_patterns = [
        r'game of the year edition', r'goty', r'deluxe edition', r'definitive edition',
        r'complete edition', r'ultimate edition', r'gold edition', r'standard edition',
        r'director\'s cut'
    ]
    for pattern in edition_patterns:
        cleaned = re.sub(r'\b' + pattern + r'\b', '', cleaned, flags=re.IGNORECASE)

    cleaned = re.sub(r'\([\s\$\€\£]?\d*[\.,]?\d+[\s\$\€\£]?\s*(\/\s*\d+%\s*off)?\)', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\(\s*\d+%\s*off\s*\)', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\(\s*free\s*\)', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'-\s*\d+%\s*off', '', cleaned, flags=re.IGNORECASE)
    
    parts = re.split(r'\s*[:|–-]\s*', cleaned)
    main_part = parts[0].strip()
    if len(parts) > 1:
        longest_part = max(parts, key=lambda p: len(p.strip()))
        if len(longest_part) > len(main_part) * 1.5:
             main_part = longest_part.strip()

    cleaned = re.sub(r'[^a-z0-9\s]', '', main_part)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()

    logger.debug(f"[clean_title] Intelligently cleaned title: '{cleaned}'")
    
    if len(cleaned) < 4 and len(raw_title) > len(cleaned):
        simpler_cleaned = re.sub(r'\[.*?\]|\(.*?\)', '', raw_title).strip()
        logger.debug(f"[clean_title] Cleaned title was too short, falling back to simpler clean: '{simpler_cleaned}'")
        return simpler_cleaned
        
    return cleaned

def infer_store_from_game_data(game: GameData) -> str:
    """
    Infers the canonical store name with a multi-layered priority system and verbose logging.
    """
    url = game.get('url', '').lower()
    raw_title = game.get('title', '').lower()
    
    logger.debug(f"--- Inferring Store for: '{raw_title[:70]}' ---")
    logger.debug(f"  URL: {url}")

    # Priority 1: Check URL for domain mapping (most reliable)
    logger.debug("  Running Priority 1: URL Domain Matching...")
    for domain, store_name in STORE_KEYWORD_MAP.items():
        if '.' in domain and domain in url:
            logger.debug(f"  ✅ SUCCESS (P1): Found '{store_name}' from domain '{domain}' in URL.")
            return store_name
            
    # Priority 2: Check for explicit tags in the raw title (e.g., [Steam], (GOG))
    logger.debug("  Running Priority 2: Explicit Title Tags...")
    for keyword, store_name in STORE_KEYWORD_MAP.items():
        pattern = r'[\[\(]\s*' + re.escape(keyword) + r'\s*[\]\)]'
        if re.search(pattern, raw_title, re.IGNORECASE):
            logger.debug(f"  ✅ SUCCESS (P2): Found '{store_name}' from tag matching pattern '{pattern}' in title.")
            return store_name
            
    # Priority 3: Check for keywords directly in the title (less reliable)
    logger.debug("  Running Priority 3: Keywords in Title...")
    sorted_keywords = sorted([k for k in STORE_KEYWORD_MAP.keys() if '.' not in k], key=len, reverse=True)
    for keyword in sorted_keywords:
        pattern = r'\b' + re.escape(keyword) + r'\b'
        if re.search(pattern, raw_title, re.IGNORECASE):
             logger.debug(f"  ✅ SUCCESS (P3): Found '{store_name}' from keyword matching pattern '{pattern}' in title.")
             return STORE_KEYWORD_MAP[keyword]

    # Priority 4: Infer from subreddit name
    logger.debug("  Running Priority 4: Subreddit Hints...")
    subreddit = game.get('subreddit', '').lower()
    if subreddit:
        if 'googleplaydeals' in subreddit:
            logger.debug(f"  ✅ SUCCESS (P4): Inferred 'googleplay' from subreddit name.")
            return 'googleplay'
        if 'apphookup' in subreddit:
            if 'apps.apple.com' in url:
                logger.debug(f"  ✅ SUCCESS (P4): Inferred 'iosappstore' from subreddit name and URL.")
                return 'iosappstore'
            if 'play.google.com' in url:
                logger.debug(f"  ✅ SUCCESS (P4): Inferred 'googleplay' from subreddit name and URL.")
                return 'googleplay'
            
    # Priority 5 (Fallback)
    logger.debug("  - FALLBACK: No specific store identified. Returning 'other'.")
    return 'other'

def normalize_url_for_key(url: str) -> str:
    """
    Normalizes a URL to create a consistent key for deduplication.
    """
    if not url: return ""
    try:
        parsed = urlparse(url)
        query_params = parse_qs(parsed.query)
        tracking_params = ['utm_source', 'utm_medium', 'utm_campaign', 'ref', 'source', 'mc_cid', 'mc_eid']
        for param in tracking_params: query_params.pop(param, None)
        path = parsed.path.rstrip('/')
        if 'steampowered.com' in parsed.netloc:
            match = re.search(r'/app/(\d+)', path)
            if match: return f"steam_app_{match.group(1)}"
        if 'epicgames.com' in parsed.netloc:
            match = re.search(r'/(?:p|product)/([a-z0-9-]+)', path)
            if match: return f"epic_product_{match.group(1)}"
        cleaned_query = urlencode(query_params, doseq=True)
        key_parts = [parsed.netloc.replace('www.', ''), path]
        if cleaned_query: key_parts.append(cleaned_query)
        return "_".join(part for part in key_parts if part)
    except Exception: return url

def sanitize_html(html_text: str) -> str:
    """
    Removes all HTML tags from a string, returning only the clean text.
    """
    if not html_text: return ""
    soup = BeautifulSoup(html_text, "lxml")
    text = soup.get_text(separator=' ', strip=True)
    text = re.sub(r'\s\s+', ' ', text)
    return text
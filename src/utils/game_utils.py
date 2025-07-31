# ===== IMPORTS & DEPENDENCIES =====
import re
import logging
from typing import Dict, Any
from urllib.parse import urlparse, parse_qs, urlencode

from src.config import STORE_KEYWORD_MAP
from src.models.game import GameData

# ===== CONFIGURATION & CONSTANTS =====
logger = logging.getLogger(__name__)

# ===== UTILITY FUNCTIONS =====

def clean_title(raw_title: str) -> str:
    """
    Cleans a game title for searching and display by removing store tags,
    platform info, and other noise.
    """
    if not raw_title:
        return ""

    logger.debug(f"[clean_title] Original title: '{raw_title}'")
    
    # Remove store/platform tags in brackets: [Steam], [PC], etc.
    cleaned = re.sub(r'\[\s*(steam|epic\s*games?|gog|pc|windows|mac|linux|drm-?free)\s*\]', '', raw_title, flags=re.IGNORECASE)
    
    # Remove free/discount tags in parentheses: (100% off), (Free), etc.
    cleaned = re.sub(r'\(\s*(100%\s*off|free|free\s*to\s*keep)\s*\)', '', cleaned, flags=re.IGNORECASE)
    
    # Remove promotional suffixes
    cleaned = re.sub(r'\s*-\s*100%\s*off', '', cleaned, flags=re.IGNORECASE)

    # Collapse multiple spaces and strip whitespace
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()

    logger.debug(f"[clean_title] Cleaned title: '{cleaned}'")
    return cleaned

def infer_store_from_game_data(game: GameData) -> str:
    """
    Infers the canonical store name from the game's URL and title.
    """
    url = game.get('url', '').lower()
    title = game.get('title', '').lower()
    
    logger.debug(f"[infer_store] Inferring store for URL: '{url}' and Title: '{title[:50]}...'")

    # 1. Check URL for domain mapping (most reliable)
    for domain, store_name in STORE_KEYWORD_MAP.items():
        if domain in url:
            logger.debug(f"[infer_store] Found store '{store_name}' from domain '{domain}' in URL.")
            return store_name
            
    # 2. Check title for keywords (less reliable, used as fallback)
    for keyword, store_name in STORE_KEYWORD_MAP.items():
        if keyword in title:
            logger.debug(f"[infer_store] Found store '{store_name}' from keyword '{keyword}' in title.")
            return store_name
            
    # Fallback to the store field if present, otherwise 'other'
    fallback_store = game.get('store', 'other').lower().replace(' ', '')
    logger.debug(f"[infer_store] No match found. Falling back to store: '{fallback_store}'")
    return fallback_store

def normalize_url_for_key(url: str) -> str:
    """
    Normalizes a URL to create a consistent key for deduplication.
    Removes tracking parameters and standardizes the path.
    """
    if not url:
        logger.warning("[normalize_url_for_key] Received an empty URL.")
        return ""
        
    logger.debug(f"[normalize_url_for_key] Normalizing URL: '{url}'")
    
    try:
        parsed = urlparse(url)
        
        # Remove common tracking parameters
        query_params = parse_qs(parsed.query)
        tracking_params = ['utm_source', 'utm_medium', 'utm_campaign', 'ref', 'source', 'mc_cid', 'mc_eid']
        for param in tracking_params:
            query_params.pop(param, None)
        
        path = parsed.path.rstrip('/')
        
        # Steam: use app ID from URL path for a stable key
        if 'steampowered.com' in parsed.netloc:
            match = re.search(r'/app/(\d+)', path)
            if match:
                key = f"steam_app_{match.group(1)}"
                logger.debug(f"[normalize_url_for_key] Generated Steam key: '{key}'")
                return key
        
        # Epic Games: use product slug from URL path
        if 'epicgames.com' in parsed.netloc:
            match = re.search(r'/(?:p|product)/([^/]+)', path)
            if match:
                key = f"epic_product_{match.group(1)}"
                logger.debug(f"[normalize_url_for_key] Generated Epic Games key: '{key}'")
                return key

        # Reconstruct a clean path and query for a generic key
        cleaned_query = urlencode(query_params, doseq=True)
        key_parts = [parsed.netloc, path]
        if cleaned_query:
            key_parts.append(cleaned_query)
            
        final_key = "_".join(part for part in key_parts if part) # Join non-empty parts
        logger.debug(f"[normalize_url_for_key] Generated generic key: '{final_key}'")
        return final_key

    except Exception as e:
        logger.error(f"[normalize_url_for_key] Failed to parse URL '{url}': {e}. Falling back to original URL.")
        return url # Fallback to the original URL if parsing fails
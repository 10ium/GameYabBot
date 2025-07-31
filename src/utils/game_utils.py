// ===== IMPORTS & DEPENDENCIES =====
import re
from typing import Dict, Any
from urllib.parse import urlparse, urlunparse, parse_qs, urlencode

from src.config import STORE_KEYWORD_MAP
from src.models.game import GameData

// ===== UTILITY FUNCTIONS =====

def clean_title(raw_title: str) -> str:
    """
    Cleans a game title for searching and display by removing store tags,
    platform info, and other noise.
    """
    if not raw_title:
        return ""

    # Remove store/platform tags in brackets: [Steam], [PC], etc.
    cleaned = re.sub(r'\[\s*(steam|epic\s*games?|gog|pc|windows|mac|linux|drm-?free)\s*\]', '', raw_title, flags=re.IGNORECASE)
    
    # Remove free/discount tags in parentheses: (100% off), (Free), etc.
    cleaned = re.sub(r'\(\s*(100%\s*off|free|free\s*to\s*keep)\s*\)', '', cleaned, flags=re.IGNORECASE)
    
    # Remove promotional suffixes
    cleaned = re.sub(r'\s*-\s*100%\s*off', '', cleaned, flags=re.IGNORECASE)

    # Collapse multiple spaces and strip whitespace
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()

    return cleaned

def infer_store_from_game_data(game: GameData) -> str:
    """
    Infers the canonical store name from the game's URL and title.
    """
    url = game.get('url', '').lower()
    title = game.get('title', '').lower()

    # 1. Check URL for domain mapping
    for domain, store_name in STORE_KEYWORD_MAP.items():
        if domain in url:
            return store_name
            
    # 2. Check title for keywords (less reliable, used as fallback)
    for keyword, store_name in STORE_KEYWORD_MAP.items():
        if keyword in title:
            return store_name
            
    # Fallback to the store field if present, otherwise 'other'
    return game.get('store', 'other').lower().replace(' ', '')

def normalize_url_for_key(url: str) -> str:
    """
    Normalizes a URL to create a consistent key for deduplication.
    Removes tracking parameters and standardizes the path.
    """
    try:
        parsed = urlparse(url)
        
        # Remove common tracking parameters
        query_params = parse_qs(parsed.query)
        tracking_params = ['utm_source', 'utm_medium', 'utm_campaign', 'ref', 'source']
        for param in tracking_params:
            query_params.pop(param, None)
        
        path = parsed.path.rstrip('/')
        
        # Steam: use app ID
        if 'steampowered.com' in parsed.netloc:
            match = re.search(r'/app/(\d+)', path)
            if match: return f"steam_app_{match.group(1)}"
        
        # Epic Games: use product slug
        if 'epicgames.com' in parsed.netloc:
            match = re.search(r'/(?:p|product)/([^/]+)', path)
            if match: return f"epic_product_{match.group(1)}"

        # Reconstruct a clean path and query
        cleaned_query = urlencode(query_params, doseq=True)
        key_parts = [parsed.netloc, path]
        if cleaned_query:
            key_parts.append(cleaned_query)
            
        return "_".join(key_parts)

    except Exception:
        return url # Fallback to the original URL if parsing fails
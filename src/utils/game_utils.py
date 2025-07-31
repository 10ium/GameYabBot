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
    Intelligently cleans a game title for searching and display by removing noise in multiple stages.
    """
    if not raw_title:
        return ""

    logger.debug(f"[clean_title] Original title: '{raw_title}'")
    
    # Stage 1: Basic normalization and removal of common tags
    cleaned = raw_title.lower()
    # Remove store/platform/content tags in brackets or parentheses
    cleaned = re.sub(r'\[\s*(steam|epic\s*games?|gog|pc|windows|mac|linux|drm-?free|itch\.io|indiegala|other|reddit|game|dlc|addon|app)\s*\]', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\(\s*(game|dlc|addon|app|soundtrack|pc|windows|mac|linux)\s*\)', '', cleaned, flags=re.IGNORECASE)

    # Stage 2: Remove edition-specific keywords
    edition_patterns = [
        r'game of the year edition', r'goty', r'deluxe edition', r'definitive edition',
        r'complete edition', r'ultimate edition', r'gold edition', r'standard edition',
        r'director\'s cut'
    ]
    for pattern in edition_patterns:
        cleaned = re.sub(r'\b' + pattern + r'\b', '', cleaned, flags=re.IGNORECASE)

    # Stage 3: Remove price and discount information
    # Matches patterns like ($5.99), (€10), (80% off), (Free), (100% off), etc.
    cleaned = re.sub(r'\([\s\$\€\£]?\d*[\.,]?\d+[\s\$\€\£]?\s*(\/\s*\d+%\s*off)?\)', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\(\s*\d+%\s*off\s*\)', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\(\s*free\s*\)', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'-\s*\d+%\s*off', '', cleaned, flags=re.IGNORECASE)
    
    # Stage 4: Split by common delimiters and take the most likely part
    parts = re.split(r'\s*[:|–-]\s*', cleaned)
    main_part = parts[0].strip()
    if len(parts) > 1:
        # Heuristic: if the first part is very short (e.g., a sale name), prefer a longer part
        longest_part = max(parts, key=lambda p: len(p.strip()))
        if len(longest_part) > len(main_part) * 1.5:
             main_part = longest_part.strip()

    # Final cleanup
    cleaned = re.sub(r'[^a-z0-9\s]', '', main_part) # Remove remaining special characters from the main part
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()

    logger.debug(f"[clean_title] Intelligently cleaned title: '{cleaned}'")
    
    # If the result is too short, fall back to a simpler cleaning of the original title
    if len(cleaned) < 4:
        simpler_cleaned = re.sub(r'\[.*?\]|\(.*?\)', '', raw_title).strip()
        logger.debug(f"[clean_title] Cleaned title was too short, falling back to simpler clean: '{simpler_cleaned}'")
        return simpler_cleaned
        
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
        if f'[{keyword}]' in title or f'({keyword})' in title:
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
        return ""
        
    logger.debug(f"[normalize_url_for_key] Normalizing URL: '{url}'")
    
    try:
        parsed = urlparse(url)
        
        query_params = parse_qs(parsed.query)
        tracking_params = ['utm_source', 'utm_medium', 'utm_campaign', 'ref', 'source', 'mc_cid', 'mc_eid']
        for param in tracking_params:
            query_params.pop(param, None)
        
        path = parsed.path.rstrip('/')
        
        if 'steampowered.com' in parsed.netloc:
            match = re.search(r'/app/(\d+)', path)
            if match: return f"steam_app_{match.group(1)}"
        
        if 'epicgames.com' in parsed.netloc:
            match = re.search(r'/(?:p|product)/([a-z0-9-]+)', path)
            if match: return f"epic_product_{match.group(1)}"

        cleaned_query = urlencode(query_params, doseq=True)
        key_parts = [parsed.netloc.replace('www.', ''), path]
        if cleaned_query:
            key_parts.append(cleaned_query)
            
        final_key = "_".join(part for part in key_parts if part)
        logger.debug(f"[normalize_url_for_key] Generated generic key: '{final_key}'")
        return final_key

    except Exception:
        return url
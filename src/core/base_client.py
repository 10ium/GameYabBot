# ===== IMPORTS & DEPENDENCIES =====
import logging
import asyncio
import aiohttp
import os
import hashlib
import time
import json
import random
from typing import Optional, Any, Dict

from src.config import COMMON_HEADERS

# ===== CONFIGURATION & CONSTANTS =====
logger = logging.getLogger(__name__)

# ===== CORE BUSINESS LOGIC =====
class BaseWebClient:
    """A base class for web clients providing caching and robust fetching."""

    def __init__(self, cache_dir: str, cache_ttl: int, session: aiohttp.ClientSession):
        self._session = session
        self._cache_dir = cache_dir
        self._cache_ttl = cache_ttl
        os.makedirs(self._cache_dir, exist_ok=True)
        logger.debug(f"[{self.__class__.__name__}] Initialized with cache dir: {self._cache_dir} and TTL: {self._cache_ttl}s")

    def _get_cache_path(self, key: str, extension: str = "json") -> str:
        """Generates a cache file path from a given key."""
        hashed_key = hashlib.sha256(key.encode('utf-8')).hexdigest()
        return os.path.join(self._cache_dir, f"{hashed_key}.{extension}")

    def _is_cache_valid(self, cache_path: str) -> bool:
        """Checks if a cache file exists and has not expired."""
        if not os.path.exists(cache_path):
            return False
        
        file_mod_time = os.path.getmtime(cache_path)
        if (time.time() - file_mod_time) > self._cache_ttl:
            logger.debug(f"[{self.__class__.__name__}] Cache file expired: {cache_path}")
            return False
        
        logger.debug(f"[{self.__class__.__name__}] Cache file is valid: {cache_path}")
        return True

    async def _fetch(
        self,
        url: str,
        method: str = 'GET',
        is_json: bool = True,
        max_retries: int = 3,
        initial_delay: float = 2.0,
        headers: Optional[Dict[str, str]] = None,
        payload: Optional[Dict[str, Any]] = None
    ) -> Optional[Any]:
        """
        Fetches a URL with caching, retries, and exponential backoff.
        Handles both JSON and HTML/XML content.
        """
        cache_key = url if method == 'GET' else f"{url}-{json.dumps(payload, sort_keys=True)}"
        cache_ext = "json" if is_json else "html"
        cache_path = self._get_cache_path(cache_key, extension=cache_ext)

        if self._is_cache_valid(cache_path):
            logger.info(f"✅ [{self.__class__.__name__}] Loading content from cache: {cache_path}")
            with open(cache_path, 'r', encoding='utf-8') as f:
                content = f.read()
                if is_json:
                    try:
                        return json.loads(content)
                    except json.JSONDecodeError:
                        logger.warning(f"⚠️ [{self.__class__.__name__}] Invalid JSON in cache file {cache_path}. Deleting and re-fetching.")
                        os.remove(cache_path)
                else:
                    return content

        logger.info(f"➡️ [{self.__class__.__name__}] Fetching from network: {url}")
        request_headers = headers or COMMON_HEADERS

        for attempt in range(max_retries):
            try:
                async with self._session.request(method, url, headers=request_headers, json=payload, timeout=25) as response:
                    response.raise_for_status()
                    
                    if is_json:
                        # content_type=None handles non-standard API content-types
                        content = await response.json(content_type=None)
                        file_content = json.dumps(content, ensure_ascii=False, indent=4)
                    else:
                        content = await response.text()
                        file_content = content

                    with open(cache_path, 'w', encoding='utf-8') as f:
                        f.write(file_content)
                    logger.info(f"💾 [{self.__class__.__name__}] Content saved to cache: {cache_path}")
                    return content
                    
            except aiohttp.ClientResponseError as e:
                logger.warning(f"⚠️ [{self.__class__.__name__}] HTTP error on {url} (Attempt {attempt + 1}/{max_retries}): Status {e.status}")
                if attempt >= max_retries - 1 or e.status not in [403, 429, 502, 503, 504]:
                    logger.error(f"❌ [{self.__class__.__name__}] Unrecoverable error on {url}. Giving up.")
                    return None
            except (asyncio.TimeoutError, aiohttp.ClientConnectorError) as e:
                logger.warning(f"⚠️ [{self.__class__.__name__}] Network error on {url} (Attempt {attempt + 1}/{max_retries}): {type(e).__name__}")
                if attempt >= max_retries - 1:
                    logger.error(f"❌ [{self.__class__.__name__}] Failed to connect to {url} after {max_retries} attempts.")
                    return None
            except Exception as e:
                logger.error(f"❌ [{self.__class__.__name__}] Unexpected error fetching {url}: {e}", exc_info=True)
                return None

            delay = initial_delay * (2 ** attempt) + random.uniform(0, 1)
            logger.info(f"Retrying request to {url} in {delay:.2f} seconds...")
            await asyncio.sleep(delay)
        
        return None```
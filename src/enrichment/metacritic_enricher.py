import logging
import asyncio
from typing import Optional, Dict, Any
import aiohttp
from bs4 import BeautifulSoup

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/107.0.0.0 Safari/537.36'
}

class MetacriticEnricher:
    async def enrich_data(self, game_info: Dict[str, Any]) -> Dict[str, Any]:
        game_title = game_info.get('title')
        if not game_title:
            return game_info
        search_term = game_title.replace('&', 'and').replace(':', '').replace(' ', '-')
        search_url = f"https://www.metacritic.com/game/{search_term.lower()}/"
        logging.info(f"شروع فرآیند غنی‌سازی اطلاعات برای '{game_title}' از Metacritic...")
        try:
            async with aiohttp.ClientSession(headers=HEADERS) as session:
                async with session.get(search_url, allow_redirects=True) as response:
                    if response.status != 200:
                        logging.warning(f"صفحه بازی '{game_title}' در Metacritic با آدرس مستقیم یافت نشد (Status: {response.status}).")
                        return game_info
                    game_page_html = await response.text()
                    page_soup = BeautifulSoup(game_page_html, 'html.parser')
                    metascore_element = page_soup.select_one('div[data-cy="metascore-score"] span')
                    if metascore_element and metascore_element.text.strip().isdigit():
                        score = int(metascore_element.text.strip())
                        game_info['metacritic_score'] = score
                        game_info['metacritic_url'] = str(response.url)
                        logging.info(f"نمره Metascore برای '{game_title}' یافت شد: {score}")
                    else:
                        logging.warning(f"نمره Metascore برای '{game_title}' در صفحه یافت نشد.")
        except aiohttp.ClientError as e:
            logging.error(f"خطای شبکه هنگام ارتباط با Metacritic برای '{game_title}': {e}")
        except Exception as e:
            logging.error(f"خطای پیش‌بینی نشده در MetacriticEnricher برای '{game_title}': {e}", exc_info=True)
        return game_info

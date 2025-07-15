import logging
import aiohttp
from typing import List, Dict, Any
from bs4 import BeautifulSoup

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

class ITADSource:
    DEALS_URL = "https://isthereanydeal.com/specials/"
    HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/107.0.0.0 Safari/537.36'
    }

    def _normalize_game_data(self, card: Any) -> Dict[str, str]:
        title_element = card.select_one('.card__title a')
        title = title_element.text.strip() if title_element else 'بدون عنوان'
        
        relative_url = title_element['href'] if title_element else ''
        url = f"https://isthereanydeal.com{relative_url}"
        
        store_element = card.select_one('.shop-tag__name')
        store = store_element.text.strip() if store_element else 'نامشخص'
        
        return {
            "title": title,
            "store": store,
            "url": url,
            "id_in_db": url
        }

    async def fetch_free_games(self) -> List[Dict[str, str]]:
        logging.info("شروع فرآیند دریافت بازی‌های رایگان با اسکرپینگ از IsThereAnyDeal...")
        free_games_list = []
        params = {'filter': 'price:0/cut:100'}
        
        try:
            async with aiohttp.ClientSession(headers=self.HEADERS) as session:
                async with session.get(self.DEALS_URL, params=params) as response:
                    response.raise_for_status()
                    html_content = await response.text()
                    
                    soup = BeautifulSoup(html_content, 'html.parser')
                    game_cards = soup.select('div.card-container .card')
                    
                    for card in game_cards:
                        normalized_game = self._normalize_game_data(card)
                        free_games_list.append(normalized_game)
                        logging.info(f"بازی رایگان از ITAD (Scraping) یافت شد: {normalized_game['title']} در فروشگاه {normalized_game['store']}")

        except aiohttp.ClientError as e:
            logging.error(f"خطای شبکه هنگام اسکرپینگ از ITAD: {e}")
        except Exception as e:
            logging.error(f"یک خطای پیش‌بینی نشده در ماژول ITAD (Scraping) رخ داد: {e}", exc_info=True)
        
        if not free_games_list:
            logging.info("در حال حاضر بازی رایگان فعالی در ITAD یافت نشد.")
        return free_games_list

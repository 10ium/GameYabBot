import logging
import asyncio
from typing import Optional, Dict, Any
import aiohttp
from bs4 import BeautifulSoup
import re # برای تمیز کردن عنوان

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/107.0.0.0 Safari/537.36'
}

class MetacriticEnricher:
    def _clean_title_for_search(self, title: str) -> str:
        """
        عنوان بازی را برای جستجو در Metacritic تمیز می‌کند.
        حذف عبارات مانند (Game), ($X -> Free), [Platform]
        """
        cleaned_title = re.sub(r'\[[^\]]+\]', '', title).strip() # حذف [Platform]
        cleaned_title = re.sub(r'\(game\)', '', cleaned_title, flags=re.IGNORECASE).strip() # حذف (Game)
        cleaned_title = re.sub(r'\(\$.*?-> Free\)', '', cleaned_title, flags=re.IGNORECASE).strip() # حذف ($X -> Free)
        cleaned_title = re.sub(r'\(\d+%\s*off\)', '', cleaned_title, flags=re.IGNORECASE).strip() # حذف (X% off)
        cleaned_title = re.sub(r'\(\s*free\s*\)', '', cleaned_title, flags=re.IGNORECASE).strip() # حذف (Free)
        return cleaned_title

    async def enrich_data(self, game_info: Dict[str, Any]) -> Dict[str, Any]:
        game_title = game_info.get('title')
        if not game_title:
            return game_info
        
        cleaned_title = self._clean_title_for_search(game_title)
        if not cleaned_title:
            logging.warning(f"عنوان تمیز شده برای '{game_title}' خالی است. غنی‌سازی Metacritic انجام نشد.")
            return game_info

        # Metacritic URL ساختار ثابتی ندارد، بهتر است از جستجو استفاده شود
        # اما برای سادگی، فعلاً از همان ساختار قبلی با عنوان تمیز شده استفاده می‌کنیم
        search_term = cleaned_title.replace('&', 'and').replace(':', '').replace(' ', '-').lower()
        search_url = f"https://www.metacritic.com/game/{search_term}/"
        
        logging.info(f"شروع فرآیند غنی‌سازی اطلاعات برای '{game_title}' (جستجوی Metacritic: '{cleaned_title}')...")
        try:
            async with aiohttp.ClientSession(headers=HEADERS) as session:
                async with session.get(search_url, allow_redirects=True) as response:
                    if response.status != 200:
                        logging.warning(f"صفحه بازی '{game_title}' در Metacritic با آدرس مستقیم یافت نشد (Status: {response.status}).")
                        # تلاش برای جستجوی عمومی‌تر اگر آدرس مستقیم کار نکرد
                        search_results_url = f"https://www.metacritic.com/search/game/{search_term}/results"
                        async with session.get(search_results_url, allow_redirects=True) as search_response:
                            if search_response.status == 200:
                                search_soup = BeautifulSoup(await search_response.text(), 'html.parser')
                                first_result_link = search_soup.select_one('a.title') # اولین لینک نتیجه جستجو
                                if first_result_link and first_result_link.has_attr('href'):
                                    game_page_url = f"https://www.metacritic.com{first_result_link['href']}"
                                    logging.info(f"صفحه بازی '{game_title}' از طریق جستجو در Metacritic یافت شد: {game_page_url}")
                                    async with session.get(game_page_url) as game_page_response:
                                        if game_page_response.status == 200:
                                            game_page_html = await game_page_response.text()
                                            page_soup = BeautifulSoup(game_page_html, 'html.parser')
                                        else:
                                            logging.warning(f"خطا در دریافت صفحه بازی از Metacritic پس از جستجو ({game_page_url}): Status {game_page_response.status}")
                                            return game_info
                                else:
                                    logging.warning(f"هیچ نتیجه جستجوی معتبری برای '{game_title}' در Metacritic یافت نشد.")
                                    return game_info
                            else:
                                logging.warning(f"خطا در صفحه نتایج جستجوی Metacritic برای '{game_title}': Status {search_response.status}")
                                return game_info
                    else:
                        game_page_html = await response.text()
                        page_soup = BeautifulSoup(game_page_html, 'html.parser')

                    # استخراج Metascore
                    metascore_element = page_soup.select_one('div[data-cy="metascore-score"] span')
                    if metascore_element and metascore_element.text.strip().isdigit():
                        score = int(metascore_element.text.strip())
                        game_info['metacritic_score'] = score
                        game_info['metacritic_url'] = str(response.url) # یا game_page_url اگر از جستجو آمده باشد
                        logging.info(f"نمره Metascore برای '{game_title}' یافت شد: {score}")
                    else:
                        logging.warning(f"نمره Metascore برای '{game_title}' در صفحه یافت نشد.")

                    # استخراج User Score (اگر وجود داشته باشد)
                    userscore_element = page_soup.select_one('div.c-siteReviewScore_user span')
                    if userscore_element:
                        userscore_text = userscore_element.text.strip()
                        try:
                            userscore = float(userscore_text)
                            game_info['metacritic_userscore'] = userscore
                            logging.info(f"نمره User Score برای '{game_title}' یافت شد: {userscore}")
                        except ValueError:
                            logging.warning(f"نمره User Score نامعتبر برای '{game_title}': {userscore_text}")
                    else:
                        logging.warning(f"نمره User Score برای '{game_title}' در صفحه یافت نشد.")

        except aiohttp.ClientError as e:
            logging.error(f"خطای شبکه هنگام ارتباط با Metacritic برای '{game_title}': {e}")
        except Exception as e:
            logging.error(f"خطای پیش‌بینی نشده در MetacriticEnricher برای '{game_title}': {e}", exc_info=True)
        return game_info

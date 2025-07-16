import logging
import asyncio
from typing import Optional, Dict, Any
import aiohttp
from bs4 import BeautifulSoup
import re
from utils import clean_title_for_search # وارد کردن تابع تمیزکننده مشترک

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/107.0.0.0 Safari/537.36'
}

class MetacriticEnricher:
    # تابع _clean_title_for_search حذف شد و از utils.clean_title_for_search استفاده می‌شود.

    async def enrich_data(self, game_info: Dict[str, Any]) -> Dict[str, Any]:
        game_title = game_info.get('title')
        if not game_title:
            return game_info
        
        cleaned_title = clean_title_for_search(game_title) # استفاده از تابع مشترک
        if not cleaned_title:
            logging.warning(f"عنوان تمیز شده برای '{game_title}' خالی است. غنی‌سازی Metacritic انجام نشد.")
            return game_info

        search_term_slug = cleaned_title.replace('&', 'and').replace(':', '').replace(' ', '-').lower()
        
        logging.info(f"شروع فرآیند غنی‌سازی اطلاعات برای '{game_title}' (جستجوی Metacritic: '{cleaned_title}')...")
        try:
            async with aiohttp.ClientSession(headers=HEADERS) as session:
                game_page_url = None
                # 1. تلاش برای آدرس مستقیم بر اساس slug
                direct_url = f"https://www.metacritic.com/game/{search_term_slug}/"
                async with session.get(direct_url, allow_redirects=True) as response:
                    if response.status == 200:
                        game_page_url = str(response.url)
                        logging.info(f"صفحه بازی '{game_title}' در Metacritic با آدرس مستقیم یافت شد: {game_page_url}")
                    else:
                        logging.warning(f"صفحه بازی '{game_title}' در Metacritic با آدرس مستقیم یافت نشد (Status: {response.status}).")
                        # 2. تلاش برای جستجوی عمومی‌تر اگر آدرس مستقیم کار نکرد
                        search_results_url = f"https://www.metacritic.com/search/game/{search_term_slug}/results"
                        async with session.get(search_results_url, allow_redirects=True) as search_response:
                            if search_response.status == 200:
                                search_soup = BeautifulSoup(await search_response.text(), 'html.parser')
                                first_result_link = search_soup.select_one('a.title')
                                if first_result_link and first_result_link.has_attr('href'):
                                    game_page_url = f"https://www.metacritic.com{first_result_link['href']}"
                                    logging.info(f"صفحه بازی '{game_title}' از طریق جستجو در Metacritic یافت شد: {game_page_url}")
                                else:
                                    logging.warning(f"هیچ نتیجه جستجوی معتبری برای '{game_title}' در Metacritic یافت نشد.")
                                    return game_info
                            else:
                                logging.warning(f"خطا در صفحه نتایج جستجوی Metacritic برای '{game_title}': Status {search_response.status}")
                                return game_info
                
                if not game_page_url:
                    return game_info

                async with session.get(game_page_url) as game_page_response:
                    if game_page_response.status == 200:
                        game_page_html = await game_page_response.text()
                        page_soup = BeautifulSoup(game_page_html, 'html.parser')
                    else:
                        logging.warning(f"خطا در دریافت صفحه بازی از Metacritic ({game_page_url}): Status {game_page_response.status}")
                        return game_info

                    # استخراج Metascore (نمره منتقدان)
                    # Selector اصلی برای Metascore (جدیدترین ساختار)
                    metascore_element = page_soup.select_one('div.c-siteReviewScore_score span') 
                    # Fallback selector برای ساختارهای قدیمی‌تر یا متفاوت
                    if not metascore_element:
                        metascore_element = page_soup.select_one('div[data-cy="metascore-score"] span')
                    # Fallback selector برای ساختارهای دیگر (مثلاً در صفحات فرعی)
                    if not metascore_element:
                        metascore_element = page_soup.select_one('div.metascore_w.game span')
                    
                    if metascore_element and metascore_element.text.strip().isdigit():
                        score = int(metascore_element.text.strip())
                        game_info['metacritic_score'] = score
                        game_info['metacritic_url'] = game_page_url
                        logging.info(f"نمره Metascore برای '{game_title}' یافت شد: {score}")
                    else:
                        logging.warning(f"نمره Metascore برای '{game_title}' در صفحه یافت نشد (عنوان تمیز شده: '{cleaned_title}').")

                    # استخراج User Score (نمره کاربران)
                    userscore_element = page_soup.select_one('div.c-siteReviewScore_user span')
                    if not userscore_element: # Fallback selector
                        userscore_element = page_soup.select_one('a.metascore_w.user span')

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

                    # استخراج رده‌بندی سنی از Metacritic
                    # Metacritic معمولا رده‌بندی سنی را در بخش "Rating" یا "Details" قرار می‌دهد.
                    age_rating_element = page_soup.find('div', class_='c-gameDetails_sectionContainer')
                    if age_rating_element:
                        rating_label = age_rating_element.find('span', string='Rating:')
                        if rating_label and rating_label.find_next_sibling('span'):
                            game_info['age_rating'] = rating_label.find_next_sibling('span').get_text(strip=True)
                            logging.info(f"رده‌بندی سنی برای '{game_title}' از Metacritic یافت شد: {game_info['age_rating']}")
                        else:
                            # Fallback برای ساختارهای دیگر
                            rating_div = page_soup.find('div', class_='score_and_media')
                            if rating_div:
                                age_rating_text = rating_div.find(string=re.compile(r'(ESRB|PEGI|CERO)\s*\w+'))
                                if age_rating_text:
                                    game_info['age_rating'] = age_rating_text.strip()
                                    logging.info(f"رده‌بندی سنی برای '{game_title}' از Metacritic (fallback) یافت شد: {age_rating_text}")
        except aiohttp.ClientError as e:
            logging.error(f"خطای شبکه هنگام ارتباط با Metacritic برای '{game_title}': {e}")
        except Exception as e:
            logging.error(f"خطای پیش‌بینی نشده در MetacriticEnricher برای '{game_title}': {e}", exc_info=True)
        return game_info

import os
import re
import json
import time
import logging
import traceback
from datetime import datetime, timedelta
from dateutil import parser
from urllib.parse import urlencode, urljoin

# from dotenv import load_dotenv
import requests
from bs4 import BeautifulSoup

from model import DynamoDB
from utils import (
    SCRAPERAPI_API_KEY,

    init_pinecone,
    get_page_content_using_ScraperAPI,
    check_if_is_new_car_accident_related_news,
    upsert_into_pinecone_index,
    generate_content_using_AI,
    generate_title_again,
)

# Load environment variables
# load_dotenv()


class CBS8NewsScraper:
    def __init__(self, db, pc_index):
        self.base_url = 'https://www.cbs8.com/news'
        self.headers = {
            'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
            'accept-language': 'en-US,en;q=0.9',
            'cache-control': 'no-cache',
            'referer': 'https://www.cbs8.com/',
            'sec-ch-ua': '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
        }

        self.db = db
        self.pc_index = pc_index
        self.related_articles = []

        # # Set up logging
        # logging.basicConfig(level=logging.INFO)
        # self.logger = logging.getLogger(__name__)

    def fetch_page(self, url):
        try:
            response = requests.get(url, headers=self.headers)
            response.raise_for_status()
            return response.content
        except requests.exceptions.RequestException as e:
            # logging.error(f"Error fetching {url}: {e}")
            print(f"Error fetching {url}: {e}")
            return None

    def parse_html(self, content):
        return BeautifulSoup(content, 'html.parser')

    def normalize_time(self, time_str):
        """Normalize time like '1 hour ago', '3 days ago', '12/20/24' to exact datetime format."""
        try:
            return ":".join(time_str.split(":")[1:])
            # If it's in relative time format (e.g., "1 hour ago")
            if 'ago' in time_str:
                now = datetime.now()
                time_match = re.match(r'(\d+)\s(\w+)\sago', time_str.lower())
                if time_match:
                    num, unit = int(time_match.group(1)), time_match.group(2)
                    if unit in ['hour', 'hours']:
                        return now - timedelta(hours=num)
                    elif unit in ['day', 'days']:
                        return now - timedelta(days=num)
                    elif unit in ['minute', 'minutes']:
                        return now - timedelta(minutes=num)
            # If it's in specific date format (e.g., '12/20/24')
            elif re.match(r'\d{1,2}/\d{1,2}/\d{2,4}', time_str):
                return datetime.strptime(time_str, '%m/%d/%y')
        except Exception as e:
            # logging.error(f"Error normalizing time: {e}")
            print(f"Error normalizing time: {e}")
        return None

    def get_details_from_story_page(self, news_url):
        try:
            logging.info(f"Scraping story: {news_url}")
            response_content = self.fetch_page(news_url)
            if response_content:
                page_soup = self.parse_html(response_content)

                # Extract headline
                headline = page_soup.find('h1', class_='article__headline')
                title = headline.get_text(strip=True) if headline else "Headline not found."
                # logging.info(f"Scraped headline: {story_data['headline']}")

                # # Extract article summary
                # article_summary = page_soup.find('div', class_='article__summary')
                # story_data['summary'] = article_summary.get_text(strip=True) if article_summary else "Article Summary not found."

                # Extract author
                author_element = page_soup.find('div', class_='article__author')
                author = author_element.get_text(strip=True) if author_element else ""
                author = author.replace("Author:", "")

                # Extract published date
                pub_date = page_soup.find('div', class_='article__published')
                if pub_date:
                    pub_date_str = self.normalize_time(pub_date.get_text(strip=True))
                else:
                    pub_date_str = ""

                # Extract updated date
                update_date = page_soup.find('div', class_='article__updated')
                if update_date:
                    update_date_str = self.normalize_time(update_date.get_text(strip=True))
                else:
                    update_date_str = ""
                posted_time = pub_date_str or update_date_str

                # Extract article body
                article_body = page_soup.find('div', class_='article__body')
                if article_body:
                    sections = [section.get_text(strip=True) for section in article_body.find_all('div', class_='article__section')]
                    content = "\n".join(sections)
                else:
                    content = ""

                is_related = check_if_is_new_car_accident_related_news(
                    self.pc_index, title, content, posted_time
                )

                article = {
                    "title": title,
                    "news_url": news_url,
                    "author": author,
                    "posted_time": posted_time,
                    "content": "",
                    "title_seo_optimized": "",
                    "call_to_action": "",
                    "one_sentence_description": "",
                    "is_related": is_related,
                }

                if is_related:
                    (content_ai, call_to_action, title_seo_optimized, one_sentence_description) = (
                        generate_content_using_AI(title, content)
                    )
                    title = generate_title_again(title, content)
                    article.update(
                        {
                            "title": title,
                            "content": content_ai,
                            "is_related": True,
                            "title_seo_optimized": title_seo_optimized,
                            "call_to_action": "",
                            "one_sentence_description": one_sentence_description,
                        }
                    )

                    self.related_articles.append(article)

                self.db.insert(article)
                if is_related:
                    upsert_into_pinecone_index(
                        self.pc_index, news_url, title, content_ai, posted_time
                    )
                    
            return True
        except Exception as e:
            # logging.error(f"Error fetching details from {news_url}: {e}")
            print(f"Error fetching details from {news_url}: {e}")
        return False

    def extract_stories(self):
        logging.info("Starting to scrape stories from the homepage...")
        content = self.fetch_page(self.base_url)
        if content:
            soup = self.parse_html(content)

            main_story = soup.find('div', class_='story__meta')

            # Extract main story
            if main_story:
                story_link = main_story.find('a', class_='story__link')
                main_story_url = story_link['href'].strip()
                if main_story_url and not self.db.query(main_story_url):
                    logging.info(f"Scraping main story from: {main_story_url}")
                    self.get_details_from_story_page(main_story_url)

            # Extract additional stories
            story_items = soup.find_all('li', class_='story-list__item')
            logging.info(f"Found {len(story_items)} additional stories.")
            for idx, story_item in enumerate(story_items, 1):
                title = story_item.find('h4', class_='story-list__title')
                if title:
                    link = title.find('a')
                    story_url = link['href'].strip()
                    if story_url and not self.db.query(story_url):
                        logging.info(f"Scraping story {idx}: {title.get_text(strip=True)} - {story_url}")
                        self.get_details_from_story_page(story_url)

            # Extract headlines from the headline list
            headline_items = soup.find_all('li', class_='headline-list__item')
            logging.info(f"Found {len(headline_items)} headlines.")
            for idx, headline_item in enumerate(headline_items, 1):
                title = headline_item.find('a', class_='headline-list__title')
                if title:
                    headline_url = title['href'].strip()
                    if headline_url and not self.db.query(headline_url):
                        headline_title = title.get_text(strip=True)
                        logging.info(f"Scraping headline {idx}: {headline_title} - {headline_url}")
                        self.get_details_from_story_page(headline_url)

            logging.info("Completed scraping stories.")
            return True
        else:
            # logging.warning("No content fetched from the homepage.")
            print("No content fetched from the homepage.")
            return False

    def run(self):
        print("Working on CBS8NewsScraper")
        try:
            self.extract_stories()
            return self.related_articles
        except Exception as e:
            # logging.error(f"Error running the scraper: {e}")
            print(f"Error running the scraper: {e}")
            return []


class EastBayNewsScraper:
    def __init__(self, db, pc_index):
        self.db = db
        self.pc_index = pc_index
        self.base_url = 'https://www.eastbaytimes.com/'
        self.news_data = []

    @staticmethod
    def clean_text(input_text):
        """Clean and remove unwanted characters from the article text."""
        soup = BeautifulSoup(input_text, 'html.parser')
        text = soup.get_text()
        text = text.encode('ascii', 'ignore').decode()
        text = re.sub(r'\s+', ' ', text).strip()
        return text

    @staticmethod
    def normalize_date(date_str):
        """Normalize posted date and convert to datetime object."""
        try:
            if 'ago' in date_str:
                time_units = {
                    'hour': 3600,
                    'day': 86400,
                    'week': 604800,
                    'month': 2592000,
                    'year': 31536000
                }
                num, unit = re.findall(r'(\d+)\s(\w+)', date_str)[0]
                num = int(num)
                if unit in time_units:
                    timestamp = time.time() - num * time_units[unit]
                    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
            else:
                parsed_date = parser.parse(date_str)
                if parsed_date.tzinfo:
                    parsed_date = parsed_date.replace(tzinfo=None)
                return parsed_date.strftime("%Y-%m-%d %H:%M:%S")
        except Exception as e:
            # logging.error(f"Error parsing date: {date_str}. Error: {e}")
            print(f"Error parsing date: {date_str}. Error: {e}")
            return ""

    def fetch_page(self, url):
        """Fetch page content."""
        try:
            headers = {'User-Agent': 'Mozilla/5.0'}
            logging.info(f"Fetching URL: {url}")
            response = requests.get(url, headers=headers)
            if response.status_code == 200:
                logging.info(f"Successfully fetched URL: {url}")
                return response.content
            else:
                # logging.error(f"Failed to retrieve page: {url}. Status code: {response.status_code}")
                print(f"Failed to retrieve page: {url}. Status code: {response.status_code}")
                return None
        except requests.exceptions.RequestException as e:
            # logging.error(f"Error fetching page: {url}. Error: {e}")
            print(f"Error fetching page: {url}. Error: {e}")
            return None

    def scrape_article_details(self, article_url):
        """Scrape individual article details."""
        if not article_url or self.db.query(article_url): 
            return []
        logging.info(f"Scraping article details from: {article_url}")
        article_content = self.fetch_page(article_url)
        if article_content is None:
            return []

        soup = BeautifulSoup(article_content, 'html.parser')

        # Extract metadata
        title_element = soup.find('meta', {'property': 'og:title'})
        description = soup.find('meta', {'property': 'og:description'})
        published_time = soup.find('meta', {'property': 'article:published_time'})

        title_element = title_element['content'] if title_element else ""
        title = self.clean_text(title_element)
        description = description['content'] if description else "No description found"
        posted_date = self.normalize_date(published_time['content']) if published_time else ""

        # Author and body
        author = soup.find('div', class_='byline')
        author_name = author.find('a', class_='author-name').text.strip() if author else "No author found"

        article_body = soup.find('div', class_='article-content')
        body_text = article_body.get_text(strip=True) if article_body else "No content found"
        cleaned_text = self.clean_text(body_text)

        content = f"{description}\n\n{cleaned_text}"

        logging.info(f"Scraped article: {title}, URL: {article_url}")

        is_related = check_if_is_new_car_accident_related_news(
            self.pc_index, title, content, posted_date
        )

        article = {
            "title": title,
            "news_url": article_url,
            "author": author_name,
            "posted_time": posted_date,
            "content": "",
            "title_seo_optimized": "",
            "call_to_action": "",
            "one_sentence_description": "",
            "is_related": is_related,
        }

        if is_related:
            (content_ai, call_to_action, title_seo_optimized, one_sentence_description) = (
                generate_content_using_AI(title, content)
            )
            title = generate_title_again(title, content)
            article.update(
                {
                    "title": title,
                    "content": content_ai,
                    "is_related": True,
                    "title_seo_optimized": title_seo_optimized,
                    "call_to_action": "",
                    "one_sentence_description": one_sentence_description,
                }
            )

            self.news_data.append(article)


        self.db.insert(article)
        if is_related:
            upsert_into_pinecone_index(
                self.pc_index, article_url, title, content_ai, posted_date
            )

    def run(self):
        """Main scraping function."""
        print("Working on EastBayNewsScraper")
        logging.info("Starting scrape...")
        page_content = self.fetch_page(self.base_url)
        if page_content is None:
            # logging.error("Failed to fetch the base page content.")
            print("Failed to fetch the base page content.")
            return []

        soup = BeautifulSoup(page_content, 'html.parser')

        # Scrape main story
        main_story = soup.find('div', class_='feature-left')
        if main_story:
            main_story_link = main_story.find('a', class_='article-title')
            if main_story_link:
                main_story_url = main_story_link.get('href')
                self.scrape_article_details(main_story_url)

        # Scrape featured articles
        f_articles = soup.find_all('article', class_='feature-small')
        for article in f_articles:
            headline_tag = article.find('a', title=True)
            if headline_tag:
                link = headline_tag['href']
                self.scrape_article_details(link)

        # Scrape secondary headlines
        headlines = soup.find_all('article', class_='headline-only')
        for headline in headlines:
            headline_link = headline.find('a', class_='article-title')
            if headline_link:
                headline_url = headline_link.get('href')
                self.scrape_article_details(headline_url)

        # Scrape recommended section
        recommended_section = soup.find('div', class_='dfm-most-popular-flex-container')
        if recommended_section:
            headlines = recommended_section.find_all('li')
            for item in headlines:
                link = item.find('a')
                if link:
                    url = link['href']
                    self.scrape_article_details(url)

        logging.info(f"Scraped {len(self.news_data)} articles in total.")
        return self.news_data


class FoxNewsScraper:
    def __init__(self, db, pc_index):
        self.db = db
        self.pc_index = pc_index
        self.api_key = SCRAPERAPI_API_KEY
        self.headers = {
            'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
            'accept-language': 'en-US,en;q=0.9',
            'cache-control': 'no-cache',
            'pragma': 'no-cache',
            'priority': 'u=0, i',
            'sec-ch-ua': '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
            'sec-fetch-dest': 'document',
            'sec-fetch-mode': 'navigate',
            'sec-fetch-site': 'cross-site',
            'sec-fetch-user': '?1',
            'upgrade-insecure-requests': '1',
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
        }
        self.urls = ['https://fox40.com/']
        self.articles_data = []

    def fetch_page(self, url):
        """Fetches the page content using ScraperAPI"""
        params = {'api_key': self.api_key, 'url': url}
        try:
            logging.info(f"Fetching page content from: {url}")
            response = requests.get('http://api.scraperapi.com/', params=urlencode(params), headers=self.headers)
            if response.status_code == 200:
                logging.info(f"Successfully fetched page content from: {url}")
                return response.text
            else:
                # logging.error(f"Failed to fetch the page: {url}, Status Code: {response.status_code}")
                print(f"Failed to fetch the page: {url}, Status Code: {response.status_code}")
                return None
        except requests.exceptions.RequestException as e:
            # logging.error(f"An error occurred while fetching the page: {e}")
            print(f"An error occurred while fetching the page: {e}")
            return None

    def normalize_time(self, time_str):
        """Normalize relative time (e.g., '1 hour ago') to an exact UTC datetime"""
        try:
            return parser.parse(time_str).strftime("%Y-%m-%d %H:%M:%S") if time_str else ""
        except (ValueError, TypeError):
            return time_str

    def scrape_article_details(self, link):
        """Fetch article details like title, author, date, and content"""
        if not link or self.db.query(link): 
            return None
        params = {'api_key': self.api_key, 'url': link}
        try:
            logging.info(f"Scraping article details from: {link}")
            response = requests.get('http://api.scraperapi.com/', params=urlencode(params), headers=self.headers)
            if response.status_code != 200:
                # logging.error(f"Failed to fetch article details for {link}")
                print(f"Failed to fetch article details for {link}")
                return None

            soup = BeautifulSoup(response.content, 'html.parser')
            site_content = soup.find('div', class_='site-content')
            if not site_content:
                return None

            article_header = site_content.find('header', class_='article-header')
            if not article_header:
                return None

            title = article_header.find('h1', class_='article-title').get_text(strip=True)

            author_tag = article_header.find('p', class_='article-authors')
            author = author_tag.find('a').get_text(strip=True) if author_tag and author_tag.find('a') else None

            date_tag = article_header.find('time')
            publish_date = self.normalize_time(date_tag.get('datetime')) if date_tag else ""

            content = ""
            content_div = soup.find('div', class_='article-content article-body rich-text')
            if content_div:
                paragraphs = content_div.find_all('p')
                for p in paragraphs:
                    content += p.get_text(strip=True)

            is_related = check_if_is_new_car_accident_related_news(
                    self.pc_index, title, content, publish_date
                )

            article = {
                "title": title,
                "news_url": link,
                "author": author,
                "posted_time": publish_date,
                "content": "",
                "title_seo_optimized": "",
                "call_to_action": "",
                "one_sentence_description": "",
                "is_related": is_related,
            }

            if is_related:
                (content_ai, call_to_action, title_seo_optimized, one_sentence_description) = (
                    generate_content_using_AI(title, content)
                )
                title = generate_title_again(title, content)
                article.update(
                    {
                        "title": title,
                        "content": content_ai,
                        "is_related": True,
                        "title_seo_optimized": title_seo_optimized,
                        "call_to_action": "",
                        "one_sentence_description": one_sentence_description,
                    }
                )

                self.articles_data.append(article)

            self.db.insert(article)
            if is_related:
                upsert_into_pinecone_index(
                    self.pc_index, link, title, content_ai, publish_date
                )
            
        except requests.exceptions.RequestException as e:
            # logging.error(f"An error occurred while fetching article details: {e}")
            print(f"An error occurred while fetching article details: {e}")
            return None

    def run(self):
        """Run the scraper and collect data from all pages"""
        print("Working on FoxNewsScraper")
        for url in self.urls:
            page_content = self.fetch_page(url)
            if not page_content:
                continue

            soup = BeautifulSoup(page_content, 'html.parser')
            articles = soup.find_all('article', class_='article-list__article')

            for article in articles:
                link_tag = article.find('a', class_='article-list__article-link')
                if link_tag:
                    link = link_tag.get('href')
                    self.scrape_article_details(link)

        logging.info(f"Scraping completed. Total articles collected: {len(self.articles_data)}")
        return self.articles_data


class NBCBayAreaScraper:
    def __init__(self, db, pc_index):
        self.db = db
        self.pc_index = pc_index
        self.url = 'https://www.nbcbayarea.com/'
        self.headers = {
            'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
            'accept-language': 'en-US,en;q=0.9',
            'cache-control': 'no-cache',
            'pragma': 'no-cache',
            'priority': 'u=0, i',
            'sec-ch-ua': '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
            'sec-fetch-dest': 'document',
            'sec-fetch-mode': 'navigate',
            'sec-fetch-site': 'cross-site',
            'sec-fetch-user': '?1',
            'upgrade-insecure-requests': '1',
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
        }
        self.stories_data = []

        # # Configure logging
        # logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
        # self.logger = logging.getLogger('NBCBayAreaScraper')

    def fetch_page(self):
        """Fetch the page and return the content."""
        try:
            response = requests.get(self.url, headers=self.headers)
            response.raise_for_status()
            return response.text
        except requests.exceptions.RequestException as e:
            # logging.error(f"Failed to fetch page: {e}")
            print(f"Failed to fetch page: {e}")
            return ""

    def parse_article_details(self, article_url):
        """Extract article details such as headline, author, and content."""
        try:
            if not article_url or self.db.query(article_url): 
                return None
            response = requests.get(article_url, headers=self.headers)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')

            # Extract the headline
            headline = soup.find('h1', class_='article-headline')
            title = headline.get_text(strip=True) if headline else ''

            # Extract the author names
            byline = soup.find('h4', class_='article-byline')
            authors = [author.get_text(strip=True) for author in byline.find_all('a')] if byline else []
            author = ', '.join(authors) if authors else ''

            # Extract and normalize the publication date
            pub_date = soup.find('time', class_='entry-date published')
            pub_date_text = pub_date['datetime'] if pub_date and pub_date.has_attr('datetime') else ''
            normalized_pub_date = self.normalize_date(pub_date_text)

            # Extract the article content
            article_content = soup.find('div', class_='article-content rich-text')
            if article_content:
                for promo in article_content.find_all('div', class_='mobile-app-promotion'):
                    promo.decompose()

                content = article_content.get_text(separator=" ", strip=True)
            else:
                content = ''

            is_related = check_if_is_new_car_accident_related_news(
                self.pc_index, title, content, normalized_pub_date
            )

            article = {
                "title": title,
                "news_url": article_url,
                "author": author,
                "posted_time": normalized_pub_date,
                "content": "",
                "title_seo_optimized": "",
                "call_to_action": "",
                "one_sentence_description": "",
                "is_related": is_related,
            }

            if is_related:
                (content_ai, call_to_action, title_seo_optimized, one_sentence_description) = (
                    generate_content_using_AI(title, content)
                )
                title = generate_title_again(title, content)
                article.update(
                    {
                        "title": title,
                        "content": content_ai,
                        "is_related": True,
                        "title_seo_optimized": title_seo_optimized,
                        "call_to_action": "",
                        "one_sentence_description": one_sentence_description,
                    }
                )

                self.stories_data.append(article)

            self.db.insert(article)
            if is_related:
                upsert_into_pinecone_index(
                    self.pc_index, article_url, title, content_ai, normalized_pub_date
                )
            
        except Exception as e:
            # logging.error(f"Error parsing article {article_url}: {e}")
            print(f"Error parsing article {article_url}: {e}")
            return None

    @staticmethod
    def normalize_date(date_str):
        """Normalize various date formats into UTC time."""
        try:
            if 'ago' in date_str:
                time_units = {'minute': 1, 'hour': 60, 'day': 1440, 'week': 10080, 'month': 43800}
                parts = date_str.split()
                value = int(parts[0])
                unit = parts[1].rstrip('s')
                minutes_ago = value * time_units.get(unit, 0)
                normalized_time = datetime.utcnow() - timedelta(minutes=minutes_ago)
            else:
                normalized_time = datetime.strptime(date_str, '%m/%d/%y')
            return normalized_time.strftime('%Y-%m-%d %H:%M:%S')
        except Exception as e:
            # logging.warning(f"Failed to normalize date '{date_str}': {e}")
            print(f"Failed to normalize date '{date_str}': {e}")
            return date_str

    def run(self):
        """Main scraping function."""
        print("Working on NBCBayAreaScraper")
        page_content = self.fetch_page()
        if page_content:
            soup = BeautifulSoup(page_content, 'html.parser')

            # Scrape main story
            main_story = soup.find('div', class_='story-card__text')
            if main_story:
                try:
                    main_story_title = main_story.find('h3', class_='story-card__title').get_text(strip=True)
                    main_story_url = main_story.find('a', class_='story-card__title-link')['href']
                    self.parse_article_details(main_story_url)
                except Exception as e:
                    # logging.error(f"Error processing main story: {e}")
                    print(f"Error processing main story: {e}")

            # Scrape top stories
            top_stories = soup.find_all('li', class_='top-stories-hero-list-item')
            for story in top_stories:
                try:
                    story_card = story.find('div', class_='story-card__text')
                    if story_card:
                        story_title = story_card.find('h3', class_='story-card__title').get_text(strip=True)
                        story_url = story_card.find('a', class_='story-card__title-link')['href']
                        self.parse_article_details(story_url)
                except Exception as e:
                    # logging.error(f"Error processing top story: {e}")
                    print(f"Error processing top story: {e}")

            # Scrape other stories
            other_stories = soup.find_all('div', class_='story-card__text')
            for story in other_stories:
                try:
                    title_tag = story.find('h3', class_='story-card__title')
                    if title_tag:
                        title = title_tag.get_text(strip=True)
                        if "Watch: NBC Bay Area News 24/7" in title:
                            continue
                        link = title_tag.find('a', class_='story-card__title-link')['href']
                        self.parse_article_details(link)
                except Exception as e:
                    # logging.error(f"Error processing other story: {e}")
                    print(f"Error processing other story: {e}")

            # Return scraped data
            return self.stories_data
        else:
            return []


def lambda_handler(event, context):
    """
    Main entry point for AWS Lambda function. Initializes DynamoDB and Pinecone,
    and runs scrapers for KTLA, KSBY, and NBC news websites.

    Args:
        event (dict): The event data that triggered the Lambda function.
        context (LambdaContext): The context in which the function is called.

    Returns:
        dict: A response with the HTTP status code and result message.
    """
    try:
        db = DynamoDB()  # Initialize the dynamo database

        # \db.clear_all_items()  #! Only to clear all items in DynamoDB while developing.

        f, pc_index = init_pinecone()
        if not f:
            # logging.error("Error while initializing Pinecone Database.")
            print("Error while initializing Pinecone Database.")
            return {
                "statusCode": 500,
                "body": json.dumps("Error while initializing Pinecone Database."),
            }
        
        all_related_articles = []
        
        try:
            cbs8NewsScraper = CBS8NewsScraper(db, pc_index)
            cbs8_related_articles = cbs8NewsScraper.run()
            all_related_articles.extend(cbs8_related_articles)
        except Exception as ex:
            # logging.exception(f"{ex}\n\n{traceback.format_exc()}")
            print(f"{ex}\n\n{traceback.format_exc()}")
            pass

        try:
            eastBayNewsScraper = EastBayNewsScraper(db, pc_index)
            eastBay_related_articles = eastBayNewsScraper.run()
            all_related_articles.extend(eastBay_related_articles)
        except Exception as ex:
            # logging.exception(f"{ex}\n\n{traceback.format_exc()}")
            print(f"{ex}\n\n{traceback.format_exc()}")
            pass

        try:
            foxNewsScraper = FoxNewsScraper(db, pc_index)
            fox_related_articles = foxNewsScraper.run()
            all_related_articles.extend(fox_related_articles)
        except Exception as ex:
            # logging.exception(f"{ex}\n\n{traceback.format_exc()}")
            print(f"{ex}\n\n{traceback.format_exc()}")
            pass

        try:
            nbcBayAreaScraper = NBCBayAreaScraper(db, pc_index)
            nbcBay_related_articles = nbcBayAreaScraper.run()
            all_related_articles.extend(nbcBay_related_articles)
        except Exception as ex:
            # logging.exception(f"{ex}\n\n{traceback.format_exc()}")
            print(f"{ex}\n\n{traceback.format_exc()}")
            pass
        
        # with open ("out2.json", "w", encoding="utf-8") as f:
        #     f.write(json.dumps(all_related_articles, indent=4))

        if all_related_articles:
            url = "https://lawbrothers.com/wp-json/lawbrother/v1/update-news/"
            data = {
                "items": all_related_articles  # Send the combined related articles
            }
            headers = { 
                'Content-Type': 'application/json',
                'Accept': 'application/json',
                'User-Agent': 'Mozilla/5.0',
                'Authorization': 'Bearer ' + os.getenv("WP_ACCESS_TOKEN")  # Add Bearer token here
            }

            # Send the POST request with the actual data
            webhook_response = requests.post(url, json=data, headers=headers)
            
            # Check webhook response
            if webhook_response.status_code == 200:
                logging.info("Data sent to webhook successfully")
            else:
                # logging.error(f"Failed to send data: {webhook_response.status_code}, {webhook_response.text}")
                print(f"Failed to send data: {webhook_response.status_code}, {webhook_response.text}")

        return {
            "statusCode": 200,
            "body": json.dumps("Finished scraping four websites -- CBS8, EastBay, Fox, and NBCBayArea"),
        }

    except Exception as ex:
        # logging.exception(f"Unexpected error: {ex}")
        print(f"Unexpected error: {ex}")
        return {
            "statusCode": 500,
            "body": json.dumps(f"Unexpected error: {ex}\n{traceback.format_exc()}"),
        }
    

# if __name__ == "__main__":
#     lambda_handler(None, None)

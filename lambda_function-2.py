import os
import json
import logging
import traceback
from datetime import datetime, timedelta
from urllib.parse import urlencode, urljoin

# from dotenv import load_dotenv
import requests
from bs4 import BeautifulSoup

from model import DynamoDB
from utils import (
    init_pinecone,
    get_page_content_using_ScraperAPI,
    check_if_is_new_car_accident_related_news,
    upsert_into_pinecone_index,
    generate_content_using_AI,
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

        # Set up logging
        logging.basicConfig(level=logging.INFO)
        self.logger = logging.getLogger(__name__)

    def fetch_page(self, url):
        try:
            response = requests.get(url, headers=self.headers)
            response.raise_for_status()
            return response.content
        except requests.exceptions.RequestException as e:
            self.logger.error(f"Error fetching {url}: {e}")
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
            self.logger.error(f"Error normalizing time: {e}")
        return None

    def get_details_from_story_page(self, news_url):
        try:
            self.logger.info(f"Scraping story: {news_url}")
            response_content = self.fetch_page(news_url)
            if response_content:
                page_soup = self.parse_html(response_content)

                # Extract headline
                headline = page_soup.find('h1', class_='article__headline')
                title = headline.get_text(strip=True) if headline else "Headline not found."
                # self.logger.info(f"Scraped headline: {story_data['headline']}")

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
                    article.update(
                        {
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
            self.logger.error(f"Error fetching details from {news_url}: {e}")
        return False

    def extract_stories(self):
        self.logger.info("Starting to scrape stories from the homepage...")
        content = self.fetch_page(self.base_url)
        if content:
            soup = self.parse_html(content)

            main_story = soup.find('div', class_='story__meta')

            # Extract main story
            if main_story:
                story_link = main_story.find('a', class_='story__link')
                main_story_url = story_link['href'].strip()
                if main_story_url and not self.db.query(main_story_url):
                    self.logger.info(f"Scraping main story from: {main_story_url}")
                    self.get_details_from_story_page(main_story_url)

            # Extract additional stories
            story_items = soup.find_all('li', class_='story-list__item')
            self.logger.info(f"Found {len(story_items)} additional stories.")
            for idx, story_item in enumerate(story_items, 1):
                title = story_item.find('h4', class_='story-list__title')
                if title:
                    link = title.find('a')
                    story_url = link['href'].strip()
                    if story_url and not self.db.query(story_url):
                        self.logger.info(f"Scraping story {idx}: {title.get_text(strip=True)} - {story_url}")
                        self.get_details_from_story_page(story_url)

            # Extract headlines from the headline list
            headline_items = soup.find_all('li', class_='headline-list__item')
            self.logger.info(f"Found {len(headline_items)} headlines.")
            for idx, headline_item in enumerate(headline_items, 1):
                title = headline_item.find('a', class_='headline-list__title')
                if title:
                    headline_url = title['href'].strip()
                    if headline_url and not self.db.query(headline_url):
                        headline_title = title.get_text(strip=True)
                        self.logger.info(f"Scraping headline {idx}: {headline_title} - {headline_url}")
                        self.get_details_from_story_page(headline_url)

            self.logger.info("Completed scraping stories.")
        self.logger.warning("No content fetched from the homepage.")
        return False

    def run(self):
        try:
            self.extract_stories()
            return self.related_articles
        except Exception as e:
            self.logger.error(f"Error running the scraper: {e}")
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
            logging.error("Error while initializing Pinecone Database.")
            return {
                "statusCode": 500,
                "body": json.dumps("Error while initializing Pinecone Database."),
            }
        
        cbs8NewsScraper = CBS8NewsScraper(db, pc_index)
        cbs8_related_articles = cbs8NewsScraper.run()

        # Combine related articles from all scrapers
        all_related_articles = cbs8_related_articles # + ktla_related_articles + nbc_related_articles

        with open("file.json", "w", encoding="utf-8") as f:
            f.write(json.dumps(all_related_articles, indent=4))

        
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
                logging.error(f"Failed to send data: {webhook_response.status_code}, {webhook_response.text}")

        return {
            "statusCode": 200,
            "body": json.dumps("Successfully scraped three websites."),
        }

    except Exception as ex:
        logging.exception(f"Unexpected error: {ex}")
        return {
            "statusCode": 500,
            "body": json.dumps(f"Unexpected error: {ex}\n{traceback.format_exc()}"),
        }
    

if __name__ == "__main__":
    lambda_handler(None, None)

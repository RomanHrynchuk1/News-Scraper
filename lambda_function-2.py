import os
import json
import logging
import traceback
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
    generate_title_again,
)

# Load environment variables
# load_dotenv()


class KTLA_Scraper:
    def __init__(self, db, pc_index):
        """
        Initialize the KTLA scraper with a DynamoDB instance and Pinecone index.

        Args:
            db (DynamoDB): DynamoDB instance to store articles.
            pc_index (Pinecone.Index): Pinecone index for storing embeddings.
        """
        self.db = db
        self.pc_index = pc_index
        self.start_urls = [
            "https://ktla.com/news/local-news/",
            "https://ktla.com/morning-news/",
            "https://ktla.com/news/california/",
            "https://ktla.com/automotive/",
        ]

    def parse_all_news_list(self, page_content):
        """
        Parse news list page to extract article titles and URLs.

        Args:
            page_content (str): HTML content of the news list page.

        Returns:
            list: List of article dictionaries with title and news URL.
        """
        soup = BeautifulSoup(page_content, "html.parser")
        articles = []
        article_urls = []

        for news in soup.select("h1, h3.article-list__article-title"):
            title = news.get_text(strip=True)
            link_tag = news.find("a")
            link = urljoin("https://ktla.com/", link_tag["href"]) if link_tag else ""
            if link and link not in article_urls:
                # articles.append({"news_url": link})
                articles.append({"title": title, "news_url": link})
                article_urls.append(link)

        logging.info(f"Found {len(articles)} articles.")
        return articles

    def parse_article_details(self, article):
        """
        Extract details (author, content, etc.) from an article.

        Args:
            article (dict): Dictionary containing the article URL and title.

        Returns:
            dict: Updated article dictionary with author, content, etc.
        """
        try:
            try:
                title_element = soup.select_one('h1.article-title')
                title = title_element.get_text(strip=True)
                article["title"] = title or article["title"]
            except Exception as ex:
                pass

            article_content = get_page_content_using_ScraperAPI(article["news_url"])
            soup = BeautifulSoup(article_content, "html.parser")

            author = soup.select_one("p.article-authors a")
            posted_time = soup.select_one("p time")
            content_paragraphs = soup.select("div.article-content.article-body p")

            
            article["author"] = author.get_text(strip=True) if author else ""
            article["posted_time"] = posted_time["datetime"] if posted_time else ""
            article["content"] = "\n".join(
                p.get_text(strip=True) for p in content_paragraphs
            )

            logging.info(f"Details parsed for article: {article['title']}")
            return article
        except Exception as e:
            logging.error(f"Error parsing details for {article['title']}: {e}")
            article["author"] = ""
            article["posted_time"] = ""
            article["content"] = ""
            return article

    def run(self):
        """
        Scrape news from KTLA website and process each article.
        """

        related_articles = []  # Collect related articles
        for start_url in self.start_urls:
            news_list_content = get_page_content_using_ScraperAPI(start_url)
            articles = self.parse_all_news_list(news_list_content)

            for article in articles:
                if self.db.query(article["news_url"]):
                    continue

                self.parse_article_details(article)
                article["is_related"] = check_if_is_new_car_accident_related_news(
                    self.pc_index,
                    article["title"],
                    article["content"],
                    article["posted_time"],
                )

                if article["is_related"]:
                    (content_ai, call_to_action, title_seo_optimized, one_sentence_description) = (
                        generate_content_using_AI(article["title"], article["content"])
                    )
                    title = generate_title_again(article["title"], article["content"])
                    article.update(
                        {
                            "title": title,
                            "content": content_ai,
                            "call_to_action": call_to_action,
                            "title_seo_optimized": title_seo_optimized,
                            "one_sentence_description": one_sentence_description,
                        }
                    )

                    related_articles.append(article)  # Collect related articles

                else:
                    article.update(
                        {
                            "content": "",
                            "call_to_action": "",
                            "title_seo_optimized": "",
                            "one_sentence_description": "",
                        }
                    )

                self.db.insert(article)
                if article["is_related"]:
                    upsert_into_pinecone_index(
                        self.pc_index,
                        article["news_url"],
                        article["title"],
                        article["content"],
                        article["posted_time"],
                    )


        return related_articles  # Return the list of related articles



class KSBY_Scraper:
    def __init__(self, db, pc_index):
        """
        Initialize the KSBY scraper with a DynamoDB instance and Pinecone index.

        Args:
            db (DynamoDB): DynamoDB instance to store articles.
            pc_index (Pinecone.Index): Pinecone index for storing embeddings.
        """
        self.base_url = (
            "https://www.ksby.com/news?0000016b-6620-d24b-ab7b-66f35f3f000c-page="
        )
        self.db = db
        self.pc_index = pc_index
        self.max_pages = 3  #! Limit to first 3 pages

    def run(self):
        """
        Scrape news articles from KSBY website for the defined number of pages
        and process each article.
        """

        related_articles = []  # Collect related articles
        for page in range(1, self.max_pages + 1):  # Loop through the first `max_pages`
            logging.info(f"Scraping page: {page}")
            response = requests.get(self.base_url + str(page))
            if response.status_code != 200:
                logging.error(f"Failed to retrieve page {page}")
                break

            soup = BeautifulSoup(response.content, "html.parser")
            articles = soup.select("div.List-items-item")

            if not articles:  # Break if there are no articles
                logging.info("No more articles found. Exiting...")
                break

            for article in articles:
                news_url = article.find("a")["href"] if article.find("a") else None
                if news_url and not news_url.startswith(("http://", "https://")):
                    news_url = response.urljoin(news_url)
                if news_url is None or self.db.query(news_url):
                    continue

                # Extract title with error handling
                title_tag = article.find("h3", class_="ListItem-title")
                title = title_tag.get_text(strip=True) if title_tag else ""

                # Extract author with error handling
                author_tag = article.find("div", class_="ListItem-authorName")
                author = author_tag.get_text(strip=True) if author_tag else ""

                # Extract date and timestamp
                date_tag = article.find("div", class_="ListItem-date")
                posted_time_str = date_tag["data-timestamp"] if date_tag else ""

                # Get content from the news detail page
                content = self.get_article_content(news_url) if news_url else ""

                if not title or not content:
                    continue

                is_related = check_if_is_new_car_accident_related_news(
                    self.pc_index, title, content, posted_time_str
                )
                article = {
                    "title": title,
                    "news_url": news_url,
                    "author": author,
                    "posted_time": posted_time_str,
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
                            "call_to_action": call_to_action,
                            "title_seo_optimized": title_seo_optimized,
                            "one_sentence_description": one_sentence_description,
                        }
                    )

                    # Collect related articles
                    related_articles.append(article)

                self.db.insert(article)
                if is_related:
                    upsert_into_pinecone_index(
                        self.pc_index, news_url, title, content_ai, posted_time_str
                    )

        return related_articles  # Return the list of related articles


    def get_article_content(self, news_url):
        """
        Fetch the content of the news article from the detail page.

        Args:
            news_url (str): The URL of the news article.

        Returns:
            str: The content of the article or an error message if fetching fails.
        """
        try:
            response = requests.get(news_url)
            if response.status_code == 200:
                soup = BeautifulSoup(response.content, "html.parser")
                content_div = soup.find("div", class_="RichTextArticleBody")
                if content_div:
                    return "\n".join(content_div.stripped_strings).strip()
                else:
                    return "No content found"
            else:
                return "Failed to retrieve content"
        except Exception as e:
            return f"Error occurred: {e}"


class NBC_Scraper:
    def __init__(self, db, pc_index):
        """
        Initialize the NBC scraper with a DynamoDB instance and Pinecone index.

        Args:
            db (DynamoDB): DynamoDB instance to store articles.
            pc_index (Pinecone.Index): Pinecone index for storing embeddings.
        """
        self.db = db
        self.pc_index = pc_index
        self.base_url = (
            "https://www.nbclosangeles.com/wp-json/nbc/v1/template/term/1:9:564?page="
        )
        self.max_pages = 3  # Maximum Pages to scrape

    # Function to fetch data from the API
    def fetch_page_data(self, url, headers):
        """
        Fetch data from the NBC API for a given URL.

        Args:
            url (str): The API endpoint URL.
            headers (dict): Headers to be sent with the request.

        Returns:
            dict or None: JSON response from the API if successful, None otherwise.
        """
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            return response.json()
        return None

    # Function to fetch detail page and parse article content
    def fetch_article_content(self, detail_url, headers):
        """
        Fetch the article content from the detail page.

        Args:
            detail_url (str): The URL of the article's detail page.
            headers (dict): Headers to be sent with the request.

        Returns:
            str: The extracted content of the article.
        """
        response = requests.get(detail_url, headers=headers)
        if response.status_code == 200:
            soup = BeautifulSoup(response.content, "html.parser")
            paragraphs = soup.select("div.article-content.rich-text p")
            content = " ".join([para.get_text() for para in paragraphs])
            return content.strip()
        return ""

    # Main function to scrape and save data
    def run(self):
        """
        Scrape news articles from NBC Los Angeles for the defined number of pages
        and process each article.
        """
        headers = {
            "accept": "*/*",
            "accept-language": "en-US,en;q=0.9",
            "cache-control": "no-cache",
            "pragma": "no-cache",
            "priority": "u=1, i",
            "referer": "https://www.nbclosangeles.com/news/california-news/?page=1",
            "user-agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
                "AppleWebKit/537.36 (KHTML, like Gecko)"
                "Chrome/129.0.0.0 Safari/537.36"
            ),
        }

        page = 1
        total_pages = 1

        related_articles = []  # Collect related articles
        # Loop through pages
        while page <= min(total_pages, self.max_pages):
            data = self.fetch_page_data(f"{self.base_url}{page}", headers)
            if not data:
                break

            # Extract total pages info
            pagination = data.get("template_items", {}).get("pagination", {})
            total_pages = pagination.get("total_pages", 1)

            # Process each record
            for record in data.get("template_items", {}).get("items", []):
                news_url = record.get("canonical_url", "")
                if not news_url or self.db.query(news_url):
                    continue

                title = record.get("title", "")
                modified_time = record.get("modified", "")

                # Extract authors
                bylines = record.get("bylines", [])
                authors = [byline.get("display_name", "") for byline in bylines]
                author = ", ".join(authors)

                # Fetch article content
                content = self.fetch_article_content(news_url, headers)

                is_related = check_if_is_new_car_accident_related_news(
                    self.pc_index, title, content, modified_time
                )
                

                article = {
                    "title": title,
                    "news_url": news_url,
                    "author": author,
                    "posted_time": modified_time,
                    "content": "",
                    "is_related": False,
                    "title_seo_optimized": "",
                    "call_to_action": "",
                    "one_sentence_description": "",
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
                            "call_to_action": call_to_action,
                            "one_sentence_description": one_sentence_description,
                        }
                    )

                    related_articles.append(article)


                self.db.insert(article)
                if is_related:
                    upsert_into_pinecone_index(
                        self.pc_index, news_url, title, content_ai, modified_time
                    )

            page += 1

        return related_articles  # Return the list of related articles



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

        ktla_scraper = KTLA_Scraper(db, pc_index)
        ktla_related_articles = ktla_scraper.run()

        ksby_scraper = KSBY_Scraper(db, pc_index)
        ksby_related_articles = ksby_scraper.run()

        nbc_scraper = NBC_Scraper(db, pc_index)
        nbc_related_articles = nbc_scraper.run()

        # Combine related articles from all scrapers
        all_related_articles = ksby_related_articles + ktla_related_articles + nbc_related_articles

        
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
    

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
    init_pinecone,
    get_page_content_using_ScraperAPI,
    check_if_is_new_car_accident_related_news,
    upsert_into_pinecone_index,
    generate_content_using_AI,
    generate_title_again,
)

# # Load environment variables
# load_dotenv()


class ABC30_Scrapper:
    def __init__(self, db, pc_index):
        """
        Initialize the ABC30 scraper with a DynamoDB instance and Pinecone index.

        Args:
            db (DynamoDB): DynamoDB instance to store articles.
            pc_index (Pinecone.Index): Pinecone index for storing embeddings.
        """
        self.db = db
        self.pc_index = pc_index
        self.start_urls = [
            "https://abc30.com/tag/car-crash/"
        ]

    def parse_all_news_list(self, page_content):
        """
        Parse news list page to extract article titles and URLs.

        Args:
            page_content (str): HTML content of the news list page.

        Returns:
            list: List of article dictionaries with title and news URL.
        """
        try:
            soup = BeautifulSoup(page_content, "html.parser")
            articles = []
            article_urls = []
            
            for article in soup.find_all("div", class_="headline-list-item"):
                anchor = article.find("a", class_="AnchorLink")
                if anchor:
                    link = anchor.get("href")
                    title_tag = anchor.find("div", class_="headline")
                    title = title_tag.get_text(strip=True) if title_tag else ""

                    if link and link not in article_urls:
                        articles.append({"title": title, "news_url": link})
                        article_urls.append(link)
                        
            logging.info(f"Found {len(articles)} articles.")

        except Exception as e:
            logging.error(f"Error parsing list of articles: {e}")

        finally:
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

            article_content = get_page_content_using_ScraperAPI(article["news_url"])
            soup = BeautifulSoup(article_content, "html.parser")
            
            # for posted time
            posted_time= ""
            posted_time = soup.select_one("div.jTKbV.zIIsP.ZdbeE.xAPpq.QtiLO.JQYD")
            if posted_time:
                posted_time = datetime.strptime(posted_time.get_text(strip=True), "%A, %B %d, %Y").strftime("%Y-%m-%d %H:%M:%S")
            article["posted_time"] = posted_time 
            
            # for author
            author = ""
            article["author"] = author.get_text(strip=True) if author else ""
            
            # for content
            content_paragraphs = ""
            content_paragraphs = soup.select("div[data-testid = 'prism-article-body'] p")
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
        Scrape news from ABC30 website and process each article.
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


class JOHNYELAW_Scrapper:
    def __init__(self, db, pc_index):
        """
        Initialize the JOHNYELAW scraper with a DynamoDB instance and Pinecone index.

        Args:
            db (DynamoDB): DynamoDB instance to store articles.
            pc_index (Pinecone.Index): Pinecone index for storing embeddings.
        """
        self.db = db
        self.pc_index = pc_index
        self.start_urls = [
            "https://www.johnyelaw.com/los-angeles-area-accident-reports"
        ]

    def parse_all_news_list(self, page_content):
        """
        Parse news list page to extract article titles and URLs.

        Args:
            page_content (str): HTML content of the news list page.

        Returns:
            list: List of article dictionaries with title and news URL.
        """
        try:
            soup = BeautifulSoup(page_content, "html.parser")
            articles = []
            article_urls = []

            for article in soup.find_all("div", class_="accident-report-item w-dyn-item"):
                anchor = article.find("a")
                if anchor:
                    
                    # for news link
                    link_tag = anchor.get("href")
                    link = urljoin("https://www.johnyelaw.com", link_tag) if link_tag else ""

                    # for title
                    title_tag = anchor.find("h3", class_="card-article-title sm")
                    title = title_tag.get_text(strip=True) if title_tag else ""

                    # for posted time
                    posted_time_tag = anchor.find("h6", class_="medium-blog-date")
                    posted_time = posted_time_tag.get_text(strip=True) if posted_time_tag else ""
                    if posted_time:
                        posted_time = datetime.strptime(posted_time, "%A, %B %d, %Y").strftime("%Y-%m-%d %H:%M:%S")

                    if link and link not in article_urls:
                        articles.append({"title": title, "news_url": link, "posted_time":posted_time})
                        article_urls.append(link)

            logging.info(f"Found {len(articles)} articles.")

        except Exception as e:
            logging.error(f"Error parsing list of articles: {e}")

        finally:
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

            article_content = get_page_content_using_ScraperAPI(article["news_url"])
            soup = BeautifulSoup(article_content, "html.parser")
            
            author = ""
            content_paragraphs = soup.select("div.rtb-intro-content.w-richtext p")
            article["author"] = author.get_text(strip=True) if author else ""
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
        Scrape news from JOHNYELAW website and process each article.
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


def lambda_handler(event, context):
    """
    Main entry point for AWS Lambda function. Initializes DynamoDB and Pinecone,
    and runs scrapers for KTLA, KSBY, NBC, ABC30, MERCURYNEWS, USACCIDENTLAWYER and JOHNYELAW news websites.

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
            abc30_scrapper = ABC30_Scrapper(db, pc_index)
            abc30_related_articles = abc30_scrapper.run()
            all_related_articles.extend(abc30_related_articles)
        except Exception as ex:
            # logging.exception(f"{ex}\n\n{traceback.format_exc()}")
            print(f"{ex}\n\n{traceback.format_exc()}")
            pass

        try:
            johnyelaw = JOHNYELAW_Scrapper(db, pc_index)
            johnyelaw_related_articles = johnyelaw.run()
            all_related_articles.extend(johnyelaw_related_articles)
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
            "body": json.dumps("Finished scraping four websites -- CBS8, EastBay, Fox, NBCBayArea, ABC30, MERCURYNEWS, USACCIDENTLAWYER and JOHNYELAW "),
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

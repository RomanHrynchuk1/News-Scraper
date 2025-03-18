import os
import re
import json
import time
from dateutil.parser import parse
import logging
import traceback
from datetime import datetime, timedelta
# from dateutil import parser
from urllib.parse import urlencode, urljoin

# # Load environment variables #!\~
# from dotenv import load_dotenv
# load_dotenv()

import requests
from bs4 import BeautifulSoup

from model import DynamoDB
from utils import (
    init_pinecone,
    # get_page_content_using_ScraperAPI,
    check_if_is_new_car_accident_related_news,
    upsert_into_pinecone_index,
    generate_content_using_AI,
    generate_title_again,
)


class ABC7Scraper:
    def __init__(self, db, pc_index):
        self.headers = {
            'accept': '*/*',
            'accept-language': 'en-US,en;q=0.9',
            'cache-control': 'no-cache',
            'origin': 'https://abc7.com',
            'pragma': 'no-cache',
            'priority': 'u=1, i',
            'referer': 'https://abc7.com/',
            'sec-ch-ua': '"Not A(Brand";v="8", "Chromium";v="132", "Google Chrome";v="132"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
            'sec-fetch-dest': 'empty',
            'sec-fetch-mode': 'cors',
            'sec-fetch-site': 'cross-site',
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36',
        }
        self.params = {
            'key': 'otv.web.kabc.collection',
            'limit': '18',
            'slug': 'california',
        }
        self.db = db
        self.pc_index = pc_index
        self.all_articles = []

    def parse_and_format_datetime(self, datetime_string):
        """
        Parse a datetime string in the format "Weekday, Month Day, Year Hour:MinuteAM/PM"
        and convert it to "YYYY-MM-DD HH:MM:SS" format.
        Args:
            datetime_string (str): Datetime string in format like "Tuesday, February 25, 2025 2:45AM"
        Returns:
            str: Formatted datetime string in "YYYY-MM-DD HH:MM:SS" format
        """
        try:
            # Parse the input string to a datetime object
            dt_object = datetime.strptime(datetime_string, "%A, %B %d, %Y %I:%M%p")
            # Format the datetime object to the desired output format
            formatted_datetime = dt_object.strftime("%Y-%m-%d %H:%M:%S")
            return formatted_datetime
        except Exception as e:
            return datetime_string  # Return the original string if parsing fails

    def fetch_main_story(self):
        homepage_url = 'https://abc7.com/california/'
        try:
            response = requests.get(homepage_url, headers=self.headers)
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')
                lead_section = soup.find('section', class_='lead-story inner')
                if lead_section:
                    main_article = lead_section.find('div', class_='headline-list-item has-image')
                    if main_article:
                        a_tag = main_article.find('a', class_='AnchorLink')
                        if a_tag and 'href' in a_tag.attrs:
                            relative_link = a_tag['href']
                            full_link = f'https://abc7.com{relative_link}' if not relative_link.startswith('http') else relative_link
                            article_details = self.fetch_article_details(full_link)
                            if article_details:
                                self.all_articles.insert(0, article_details)
                                return
                print('Main story element not found')
            else:
                print(f'Failed to fetch homepage. Status: {response.status_code}')
        except Exception as e:
            print(f'Error fetching main story: {e}')

    def fetch_article_details(self, link):
        try:
            if self.db.query(link):
                return None
            response = requests.get(link, headers=self.headers)
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')

                # Extract title
                title = 'No title found'
                headline_tag = soup.find('h1')
                if headline_tag:
                    title = headline_tag.get_text(strip=True)

                # Extracting the author name
                author = "No author found"
                author_tag = soup.select_one('a.zZygg.UbGlr.iFzkS.qdXbA.WCDhQ.DbOXS.tqUtK.GpWVU.iJYzE')
                if author_tag:
                    author = author_tag.get_text(strip=True)

                # Extracting the published date
                published_date = ""
                date_tag = soup.select_one('div.jTKbV.zIIsP.ZdbeE.xAPpq.QtiLO.JQYD')
                if date_tag:
                    published_date = self.parse_and_format_datetime(date_tag.get_text(strip=True))

                # Extract content
                content = 'No content found'
                article_body_div = soup.find('div', class_='xvlfx ZRifP TKoO eaKKC EcdEg bOdfO qXhdi NFNeu UyHES')
                if article_body_div:
                    paragraphs = article_body_div.find_all('p')
                    content = ' '.join(p.get_text(strip=True) for p in paragraphs)

                article = {"title": title, "news_url": link, "author":author, "posted_time": published_date}
                is_related = check_if_is_new_car_accident_related_news(self.pc_index, title, content, posted_time=published_date)
                if is_related:
                    (content_ai, call_to_action, title_seo_optimized, one_sentence_description) = (
                        generate_content_using_AI(title, content)
                    )
                    title = generate_title_again(title, content)
                    article.update(
                        {
                            "title": title,
                            "is_related": is_related,
                            "content": content_ai,
                            "call_to_action": call_to_action,
                            "title_seo_optimized": title_seo_optimized,
                            "one_sentence_description": one_sentence_description,
                        }
                    )
                else:
                    article.update(
                        {
                            "is_related": is_related,
                            "content": "",
                            "call_to_action": "",
                            "title_seo_optimized": "",
                            "one_sentence_description": "",
                        }
                    )
                
                self.db.insert(article)

                if is_related:
                    upsert_into_pinecone_index(
                        self.pc_index,
                        article["news_url"],
                        article["title"],
                        article["content"],
                        article["posted_time"],
                    )

                return article if is_related else None
            
            else:
                print(f"Failed to fetch article details. Status Code: {response.status_code}")
                return None
        except Exception as e:
            print(f"Error fetching article details: {e}")
            return None

    def fetch_news_batch(self, from_value):
        self.params['from'] = str(from_value)
        response = requests.get('https://api.abcotvs.com/v3/kabc/list', params=self.params, headers=self.headers)
        if response.status_code == 200:
            data = response.json()
            for article in data.get('data', {}).get('items', []):
                link = article.get('link', {}).get('url', '')
                if link:
                    print(f"Link: {link}")
                    article_details = self.fetch_article_details(link)
                    if article_details:
                        self.all_articles.append(article_details)

    def run(self):
        print("Fetching main story...")
        self.fetch_main_story()
        print("Fetching news articles...")
        for page in range(3):
            self.fetch_news_batch(page * 18 + 1)
        return self.all_articles


class CBSNewsScraper:
    def __init__(self, db, pc_index):
        """Initialize the CBSNewsScraper with start URLs."""
        self.start_url = "https://www.cbsnews.com/sanfrancisco/tag/fatal-crash/"
        self.db = db
        self.pc_index = pc_index

    def normalize_time(self, posted_time):
        """
        Normalize posted time to an exact time format.

        Args:
            posted_time (str): The posted time in string format.

        Returns:
            str: The normalized time in YYYY-MM-DD HH:MM:SS format.
        """
        try:
            # Strip any leading 'Updated on: ' and time zone details like ' PST'
            cleaned_time = re.sub(r'Updated on:\s*|\s*PST|/.*$', '', posted_time).strip()
            # Parse the cleaned time string
            return parse(cleaned_time).strftime('%Y-%m-%d %H:%M:%S')
        except Exception as e:
            logging.error(f"Error normalizing time: {e}")
            return posted_time

    def parse_all_news_list(self, page_content):
        """
        Parse the news list page to extract article titles and URLs.

        Args:
            page_content (str): HTML content of the news list page.

        Returns:
            list: List of article dictionaries with title and news URL.
        """
        soup = BeautifulSoup(page_content, "html.parser")
        articles = []
        article_urls = []

        for article in soup.select('article.item.item--type-article.item'):
            try:
                article_url = article.select_one('a.item__anchor')['href']
                article_url = urljoin(self.start_url, article_url)
            except (AttributeError, KeyError):
                article_url = None

            if article_url and article_url not in article_urls:
                articles.append({"news_url": article_url})
                article_urls.append(article_url)

        logging.info(f"Found {len(articles)} articles.")
        return articles

    def parse_article_details(self, article):
        """
        Extract details (author, content, etc.) from an article.

        Args:
            article (dict): Dictionary containing the article URL.

        Returns:
            dict: Updated article dictionary with author, content, etc.
        """
        try:
            if self.db.query(article["news_url"]):
                return None
            article_content = requests.get(article["news_url"]).text
            soup = BeautifulSoup(article_content, "html.parser")

            # Extract title
            title_element = soup.select_one('h1.content__title')
            article["title"] = title_element.get_text(strip=True) if title_element else ""

            # Extract author
            author = soup.select_one('p.content__meta.content__meta--byline a.byline__author__link') or \
                     soup.select_one('p.content__meta.content__meta--byline span.byline__author__text')
            article["author"] = author.get_text(strip=True) if author else ""

            # Extract publication date
            posted_time = soup.select_one('time')
            article["posted_time"] = self.normalize_time(posted_time.get_text(strip=True)) if posted_time else ""

            # Extract content
            content_paragraphs = soup.select('section.content__body p')
            article["content"] = "\n".join(p.get_text(strip=True) for p in content_paragraphs)

            article["is_related"] = check_if_is_new_car_accident_related_news(
                self.pc_index, article["title"], article["content"], posted_time=article["posted_time"]
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

            logging.info(f"Details parsed for article: {article['title']}")
            return article if article["is_related"] else None
        except Exception as e:
            logging.error(f"Error parsing details for {article['news_url']}: {e}")
            article["author"] = ""
            article["posted_time"] = ""
            article["content"] = ""
            return None

    def run(self):
        """
        Scrape news from CBS News website and process each article.
        """
        total_articles = []  # Collect total articles
        news_list_content = requests.get(self.start_url).text
        articles = self.parse_all_news_list(news_list_content)

        for article in articles:
            article = self.parse_article_details(article)
            if article:
                total_articles.append(article)

        return total_articles  # Return the list of total articles


class NDTVScraper:
    def __init__(self, db, pc_index):
        self.start_url = "https://www.ndtv.com/topic/car-accident-in-california"
        self.all_articles = []
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        self.db = db
        self.pc_index = pc_index

    def parse_and_format_datetime(self, datetime_string):
        """
        Parse a datetime string in the format "Abbreviated Weekday, Day Abbreviated Month Year HH:MM:SS"
        and convert it to "YYYY-MM-DD HH:MM:SS" format.
        
        Args:
            datetime_string (str): Datetime string in format like "Thu, 14 Dec 2023 12:54:43"
        
        Returns:
            str: Formatted datetime string in "YYYY-MM-DD HH:MM:SS" format
        """
        try:
            # Clean up any trailing spaces
            datetime_string = datetime_string.strip()
            
            # Parse the input string to a datetime object
            dt_object = datetime.strptime(datetime_string, "%a, %d %b %Y %H:%M:%S")
            
            # Format the datetime object to the desired output format
            formatted_datetime = dt_object.strftime("%Y-%m-%d %H:%M:%S")
            
            return formatted_datetime
        except Exception as e:
            return datetime_string  # Return the original string if parsing fails

    def parse_news_list(self):
        """Parse the main news listing page to extract article URLs (excluding gadgets360.com)"""
        print("Fetching main news listing page...")
        try:
            response = requests.get(self.start_url, headers=self.headers)
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')
                links = [
                    a['href'] for li in soup.select('ul.SrchLstPg_ul li.SrchLstPg-a-li')
                    if (a := li.select_one('a.SrchLstPg_ttl')) and a.has_attr('href') and "gadgets360.com" not in a['href']
                ]
                print(f"Found {len(links)} NDTV articles in listing page")
                return links
            print(f"Failed to fetch news list. Status code: {response.status_code}")
            return []
        except Exception as e:
            print(f"Error fetching news list: {str(e)}")
            return []

    def parse_article_details(self, news_url):
        """Parse individual article page to extract detailed information"""
        try:
            if self.db.query(news_url):
                print(f"Article already exists in DB: {news_url}")
                return None
            print(f"Processing article: {news_url}")
            response = requests.get(news_url, headers=self.headers)
            if response.status_code != 200:
                print(f"Failed to fetch article (Status {response.status_code})")
                return None

            soup = BeautifulSoup(response.text, 'html.parser')

            # Extract headline/title
            title = "N/A"
            if (title_tag := soup.select_one('h1.sp-ttl[itemprop="headline"]')):
                title = title_tag.get_text(strip=True)

            author = "N/A"
            author_tag = soup.select_one('ul.pst-by_ul li.pst-by_li span.pst-by_txt')
            if author_tag:
                # For older articles, the author is directly in the span text
                author_name = author_tag.get_text(strip=True)
                if author_name.startswith("Reported by"):
                    # Remove 'Reported by' and extract the actual author
                    author = author_name.replace("Reported by", "").strip()
                elif author_name.startswith("Edited by:"):
                    # For newer articles, extract name after "Edited by:"
                    edited_by = author_name.replace("Edited by:", "").strip()
                    if (author_link := soup.select_one('ul.pst-by_ul li.pst-by_li a.pst-by_lnk')):
                        author = edited_by if edited_by else author_link.get_text(strip=True)
                else:
                    author = author_name
            else:
                # For newer articles, check for the 'Edited by' tag
                if (author_link := soup.select_one('ul.pst-by_ul li.pst-by_li span.pst-by_txt a.pst-by_lnk')):
                    author = author_link.get_text(strip=True)

            # Extract published date
            posted_time = ""
            if (time_tag := soup.select_one('span[itemprop="dateModified"]')):
                posted_time = time_tag.get('content', '').split('+')[0]
            elif (date_tag := soup.select_one('meta[itemprop="datePublished"]')):
                posted_time = date_tag.get('content', '').split('+')[0]
            posted_time = self.parse_and_format_datetime(posted_time) if posted_time else ""

            # Extract article content
            content = []
            article_body = soup.find('div', {'class': 'Art-exp_wr', 'id': 'ignorediv'})

            if article_body:
                # Try to extract paragraphs for newer articles
                paragraphs = article_body.select('''
                    p:not(.vuukle-ad-label):not(.cmt-ac):not(.cmt-dn):not([class^="vj_"]):not([id^="vuukle-ad-"])
                ''')

                # If paragraphs are found, use them
                if paragraphs:
                    for p in paragraphs:
                        if p.text.strip() and not p.find_parents('div', class_=['vuukle-ads', 'vdo-video-unit']):
                            content.append(p.get_text(strip=True))
                else:
                    # Fallback to extracting content using <br> tags for older articles
                    print("No paragraphs found, falling back to <br> tag extraction")
                    paragraphs = article_body.find_all('br')
                    for br_tag in paragraphs:
                        prev_text = br_tag.find_previous_sibling(text=True)
                        if prev_text and prev_text.strip():
                            content.append(prev_text.strip())

                # Clean any extraneous elements, like the closing <i> tag text at the bottom
                if content and content[-1].startswith("(Except for the headline"):
                    content[-1] = content[-1].split(")", 1)[1].strip()

            is_related = check_if_is_new_car_accident_related_news(
                self.pc_index, title, ' '.join(content), posted_time=posted_time
            )

            if is_related:
                (content_ai, call_to_action, title_seo_optimized, one_sentence_description) = (
                    generate_content_using_AI(title, ' '.join(content))
                )
                content = content_ai
                article = {
                    "news_url": news_url,
                    "title": title,
                    "author": author,
                    "posted_time": posted_time,
                    "is_related": is_related,
                    "content": content,
                    "call_to_action": call_to_action,
                    "title_seo_optimized": title_seo_optimized,
                    "one_sentence_description": one_sentence_description,
                }
            else:
                article = {
                    "news_url": news_url,
                    "title": title,
                    "author": author,
                    "posted_time": posted_time,
                    "is_related": is_related,
                    "content": "",
                    "call_to_action": "",
                    "title_seo_optimized": "",
                    "one_sentence_description": "",
                }
            
            self.db.insert(article)
            if is_related:
                upsert_into_pinecone_index(
                    self.pc_index,
                    news_url,
                    title,
                    content,
                    posted_time,
                )

            return article if is_related else None
        except Exception as e:
            print(f"Error parsing article: {str(e)}")
            return None

    def run(self):
        """Main method to execute the scraping process"""
        print("Starting NDTV scraper...")
        news_urls = self.parse_news_list()

        success_count = 0
        for idx, url in enumerate(news_urls, 1):
            print(f"Article {idx}/{len(news_urls)}")
            if article := self.parse_article_details(url):
                if article:
                    self.all_articles.append(article)
                    success_count += 1

        print(f"Scraping complete! Success: {success_count}/{len(news_urls)} articles")
        return self.all_articles


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
            abc7_scrapper = ABC7Scraper(db, pc_index)
            abc7_related_articles = abc7_scrapper.run()
            all_related_articles.extend(abc7_related_articles)
        except Exception as ex:
            # logging.exception(f"{ex}\n\n{traceback.format_exc()}")
            print(f"{ex}\n\n{traceback.format_exc()}")
            pass
        
        try:
            cbsnews_scrapper = CBSNewsScraper(db, pc_index)
            cbsnews_related_articles = cbsnews_scrapper.run()
            all_related_articles.extend(cbsnews_related_articles)
        except Exception as ex:
            # logging.exception(f"{ex}\n\n{traceback.format_exc()}")
            print(f"{ex}\n\n{traceback.format_exc()}")
            pass
        
        try:
            ndtv_scrapper = NDTVScraper(db, pc_index)
            ndtv_related_articles = ndtv_scrapper.run()
            all_related_articles.extend(ndtv_related_articles)
        except Exception as ex:
            # logging.exception(f"{ex}\n\n{traceback.format_exc()}")
            print(f"{ex}\n\n{traceback.format_exc()}")
            pass
        
        # with open ("out2.json", "w", encoding="utf-8") as f:  #!\~
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
            "body": json.dumps("Finished scraping three websites -- ABC7, CBS News, and NDTV. \n\n" + str(len(all_related_articles)) + " related articles found."),
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

import json
import requests
from bs4 import BeautifulSoup

class NBC_Scraper:
    def __init__(self):
        """
        Initialize the NBC scraper with a DynamoDB instance and Pinecone index.

        Args:
            db (DynamoDB): DynamoDB instance to store articles.
            pc_index (Pinecone.Index): Pinecone index for storing embeddings.
        """
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

                title = record.get("title", "")
                modified_time = record.get("modified", "")

                # Extract authors
                bylines = record.get("bylines", [])
                authors = [byline.get("display_name", "") for byline in bylines]
                author = ", ".join(authors)

                # Fetch article content
                content = self.fetch_article_content(news_url, headers)

                article = {
                    "title": title,
                    "news_url": news_url,
                    "author": author,
                    "posted_time": modified_time,
                    "content": content,
                }

                related_articles.append(article)

                print(f"Author: {author} ; Title: {title}") #!~ Just for logging

            page += 1
            break

        return related_articles  # Return the list of related articles


if __name__ == "__main__":
    nbc_scraper = NBC_Scraper()
    nbc_related_news = nbc_scraper.run()
    with open("nbc_related_news.json", "w", encoding="utf-8") as file:
        file.write(json.dumps(nbc_related_news, indent = 4))

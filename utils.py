import os
import json
import logging
from urllib.parse import urlencode, urljoin

import requests
from openai import OpenAI
from pinecone import Pinecone, ServerlessSpec


OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
SCRAPERAPI_API_KEY = os.getenv("SCRAPERAPI_API_KEY")

# Base URL for the ScraperAPI
SCRAPERAPI_BASE_URL = "https://api.scraperapi.com/?{}"

# [Pinecone Database Configurations]
PINECONE_API_KEY = os.getenv("PINECONE_API_KEY")
PINECONE_REGION = os.getenv("PINECONE_REGION")
PINECONE_INDEX_NAMESPACE = os.getenv("PINECONE_INDEX_NAMESPACE")
INDEX_NAME = os.getenv("INDEX_NAME")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL")


# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)

# Initialize OpenAI client
client = OpenAI(api_key=OPENAI_API_KEY)

pc = Pinecone(api_key=PINECONE_API_KEY)


def init_pinecone():
    """
    Initialize Pinecone by checking if the specified index exists. 
    If not, it creates a new index with the provided specifications.

    Returns:
        tuple: (bool, Pinecone.Index or None) 
               - True and Pinecone index instance if successful, 
               - False and None if initialization fails.
    """
    try:
        if INDEX_NAME not in pc.list_indexes().names():
            logging.info("Index does not exist, creating...")
            pc.create_index(
                name=INDEX_NAME,
                dimension=1536,
                metric="cosine",
                spec=ServerlessSpec(cloud="aws", region=os.getenv("PINECONE_REGION")),
            )
        pc_index = pc.Index(INDEX_NAME)
        return True, pc_index
    except Exception as ex:
        logging.exception(f"Error in init_pinecone: {ex}")
        return False, None


def get_page_content_using_ScraperAPI(url: str) -> str:
    """
    Fetch the content of the given URL using ScraperAPI.

    Args:
        url (str): The URL of the page to fetch content from.

    Returns:
        str: The HTML content of the page, or an empty string if an error occurs.
    """
    try:
        params = {"api_key": SCRAPERAPI_API_KEY, "url": url}
        response = requests.get(SCRAPERAPI_BASE_URL.format(urlencode(params)))
        response.raise_for_status()
        logging.info(f"Successfully fetched content from {url}")
        return response.text
    except requests.RequestException as e:
        logging.error(f"Error fetching content from {url}: {e}")
        return ""


def get_embedding_openai(text):
    """
    Generate an embedding vector for the provided text using OpenAI's embedding API.

    Args:
        text (str): The text to generate an embedding for.

    Returns:
        list or None: The embedding vector as a list if successful, 
                      None if an error occurs.
    """
    try:
        response = client.embeddings.create(
            input=text, model=EMBEDDING_MODEL
        )
        embedding = response.data[0].embedding  # Access the first embedding
        return embedding  # Wrap in a list for Pinecone upsert format
    except Exception as e:
        logging.exception(f"Error generating embedding for chunk: {text}. Error: {e}")
        return None  # Handle errors (consider retry logic or logging)



def upsert_into_pinecone_index(pc_index, pid, title, content, posted_time):
    """
    Insert or update the news article embedding in Pinecone.

    Args:
        pc_index (Pinecone.Index): The Pinecone index instance.
        pid (str): The unique ID of the article.
        title (str): The title of the article.
        content (str): The content of the article.
        posted_time (str): The time when the article was posted.

    Returns:
        None
    """
    # Generate custom page ID based on new_pid and chunk number
    page_id = f"pg-{pid}"

    # Prepare data for upsert (assuming you have a function to prepare data)
    vector_data = get_embedding_openai(f"Title: {title}\n\nContent: {content}")

    # Upsert data into Pinecone index
    pc_index.upsert(
        vectors=[
            {
                "id": page_id,
                "values": vector_data,
                "metadata": {
                    "title": title,
                    "content": content,
                    "posted_time": posted_time,
                },
            }
        ],
        namespace=PINECONE_INDEX_NAMESPACE,
    )

    logging.info(f"`news: {title}` is upserted.")


def get_similar_news(pc_index, title, content):
    """
    Perform a similarity search in Pinecone using the title and content of the news article.

    Args:
        pc_index (Pinecone.Index): The Pinecone index instance.
        title (str): The title of the news article.
        content (str): The content of the news article.

    Returns:
        dict: The metadata of the most similar article if found and its score exceeds 0.7. 
              Returns an empty dictionary if no similar articles are found.
    """
    text_query = f"Title: {title}\n\nContent: {content}"
    response = pc_index.query(
        namespace=os.getenv("PINECONE_INDEX_NAMESPACE"),
        vector=get_embedding_openai(text=text_query),
        top_k=1,
        include_metadata=True,
    )
    filtered_response = {}
    for result in response["matches"]:
        if result["score"] > 0.7:
            filtered_response = result["metadata"]
            break
    return filtered_response


def check_if_news_already_exists(pc_index, title, content, posted_time):
    """
    Check if a similar news article already exists in Pinecone.

    Args:
        pc_index (Pinecone.Index): The Pinecone index instance.
        title (str): The title of the news article.
        content (str): The content of the news article.
        posted_time (str): The time when the article was posted.

    Returns:
        bool: True if the news already exists, False otherwise.
    """
    similar_news = get_similar_news(pc_index=pc_index, title=title, content=content)
    
    if not similar_news:
        return False
    
    prompt = (
        "Please check if the two news are the same:\n"
        f"First News:\nNews Title: {title}\nNews Content: {content}\nPosted Time: {posted_time}\n"
        "\n=======================\n"
        f"Second News:\nNews Title: {similar_news['title']}\nNews Content: {similar_news['content']}\n"
        "Posted Time: {similar_news['posted_time']}\n\n"
        "Please return the result in a single JSON format: {'answer': 'yes' or 'no'}.\n"
    )
    answer = openai_chat(prompt=prompt, is_json_format=True)
    return True if answer["answer"] == "yes" else False


def openai_chat(prompt: str, is_json_format: bool = False) -> dict | str:
    """
    Interact with OpenAI's API to classify or generate text based on the provided prompt.

    Args:
        prompt (str): The input prompt to send to OpenAI.
        is_json_format (bool): Whether to expect a JSON formatted response (default: False).

    Returns:
        dict | str: The response from OpenAI as a dictionary if `is_json_format` is True,
                    otherwise returns the response as a string.
    """
    try:
        if is_json_format:
            response = client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {
                        "role": "user",
                        "content": prompt,
                    }
                ],
                response_format={"type": "json_object"},
            )
            response_json = json.loads(response.choices[0].message.content)
            return response_json
        else:
            response = client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {
                        "role": "user",
                        "content": prompt,
                    }
                ],
            )
            return response.choices[0].message.content
    except Exception as ex:
        logging.exception(f"Exception in openai_chat function: {ex}")
        raise


def check_if_related_to_car_accidents(title: str, content: str) -> bool:
    """
    Check if the article is related to car accidents, specifically in California.

    Args:
        title (str): The title of the article.
        content (str): The content of the article.

    Returns:
        bool: True if the article is related to car accidents, False otherwise.
    """
    prompt = (
        "I have a news article and need to determine if it is about car accidents in California.\n"
        f"###\nTitle: {title}\n\nContent: {content}\n###\n"
        "Please return the result in a single JSON format: {'answer': 'yes' or 'no'}.\n"
        "Respond with 'yes' if the article is about car accidents in California; otherwise, respond with 'no'.\n"
        "Ensure the response is in lowercase and properly formatted.\n"
        "If you are not sure if the accident occurred in California, check to see if it is only about a car accident."
    )
    answer = openai_chat(prompt=prompt, is_json_format=True)
    return True if answer["answer"] == "yes" else False


def check_if_is_new_car_accident_related_news(pc_index, title: str, content: str, posted_time: str) -> bool:
    """
    Check if the article is related to car accidents in California and whether it is a new article.

    Args:
        pc_index (Pinecone.Index): The Pinecone index instance.
        title (str): The title of the article.
        content (str): The content of the article.
        posted_time (str): The time when the article was posted.

    Returns:
        bool: True if the article is both related to car accidents and is a new article, False otherwise.
    """
    ok = False
    if check_if_related_to_car_accidents(title, content):
        ok = True
        if pc_index and check_if_news_already_exists(pc_index, title, content, posted_time):
            ok = False
    if ok:
        print("OOOOO discovered a good news OOOOO")  #!~
    return ok


def generate_content_using_AI(title: str, content: str):
    """
    Use OpenAI to rewrite a news article, call to action, create an SEO-optimized title, 
    and generate a one-sentence description.

    Args:
        title (str): The title of the news article.
        content (str): The content of the news article.

    Returns:
        tuple: (rewritten content, call to action, SEO-optimized title, one-sentence description)
    """
    prompt = """Rewrite the following car accident related news article into our own words as The Law Brothers personal injury lawyers.
Please remove any unnecessary information, such as a further action (the news article may have it since the news is gotton from the other's website, so you should remove all them), from the article.
Rewrite the title to make it good for SEO. Write a one sentence description with about 150 ~ 250 characters.
We are a personal injury law firm that helps people after they've been in an accident. Our url is lawbrothers.com
Here are some references about us:
#####
The Law Brothers® represent accident victims nationwide, led by Shawn and Shervin Lalezary.
With extensive personal injury experience and unique insights from their roles as reserve deputies, they’ve recovered over $400 million across various claims, including car and work accidents, slip and falls, and more.
Dedicated to client-focused advocacy, our team works tirelessly to build your case, guide you through the legal process, and maximize your compensation. Contact us today for a free consultation.
#####

After the article, add a “Call to Action” section with the title `<h2>Contact The Trusted Accident Lawyers at The Law Brothers®</h2>`.
In this section, inform readers why they should reach out to The Law Brothers® after an accident and highlight our commitment to maximizing their compensation.
This section should mention our free consultation offer and emphasize our experience, team dedication, and proven results in accident cases.

Here is the news article:
#####
NEWS_TITLE

NEWS_CONTENT
#####

The output for `Rewritten article` and `Call to action` should be formatted in HTML and the overall should follow the JSON structure below: Please add <br> where needed.

```json
{
    "Rewritten article": "<p>Rewritten article content in paragraph format...</p><br>",
    "Call to action": "<h2>Contact The Trusted Accident Lawyers at The Law Brothers®</h2><p>Why accident victims should call The Law Brothers...</p><br>",
    "SEO-optimized title": "SEO-optimized title here",
    "One-sentence description": "One-sentence description (under 150 characters)"
}
```
"""
    prompt = prompt.replace("NEWS_TITLE", title)
    prompt = prompt.replace("NEWS_CONTENT", content)

    response_json = openai_chat(prompt, True)

    return (
        response_json["Rewritten article"],
        response_json["Call to action"],
        response_json["SEO-optimized title"],
        response_json["One-sentence description"],
    )


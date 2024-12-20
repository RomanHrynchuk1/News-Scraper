import os
import logging

from datetime import timezone, datetime

import boto3
from botocore.exceptions import ClientError
from boto3.dynamodb.conditions import Attr


class DynamoDB:
    def __init__(self, table_name="news_articles", region="us-east-1"):
        """
        Initializes the DynamoDB resource and connects to the specified table.

        Args:
            table_name (str): The name of the DynamoDB table to connect to.
                    Defaults to "news_articles".
            region (str): The AWS region where the DynamoDB table is hosted.
                    Defaults to "us-east-1".
        """
        self.dynamodb = boto3.resource(
            "dynamodb",  #! Must ignore the below 4 lines while deploying on AWS lambda.
            # aws_access_key_id=os.getenv("aws_access_key_id"),
            # aws_secret_access_key=os.getenv("aws_secret_access_key"),
            # aws_session_token=os.getenv("aws_session_token"),
            # region_name="us-east-1",  # Specify the region of your DynamoDB
        )
        self.table = self.dynamodb.Table(table_name)

    def insert(self, article):
        """
        Inserts a new article into the DynamoDB table if the article does not already exist.

        Args:
            article (dict): A dictionary containing the article details
                    (e.g., title, news_url, content).

        Returns:
            bool: True if the item was successfully inserted,
                  False if there was an error or the article already exists.
        """
        try:
            response = self.table.put_item(
                Item={
                    "news_url": article["news_url"],  # Primary key
                    "title": article.get("title", ""),
                    "author": article.get("author", ""),
                    "posted_time": article.get("posted_time", ""),
                    "content": article.get("content", ""),
                    "call_to_action": article.get("call_to_action", ""),
                    "is_related": article.get("is_related", False),
                    "title_seo_optimized": article.get("title_seo_optimized", ""),
                    "one_sentence_description": article.get(
                        "one_sentence_description", ""
                    ),
                    "wordcount": len(article.get("content", "").split()),
                    "timestamp": datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                },
                ConditionExpression="attribute_not_exists(news_url)",  # Ensures the news_url is unique
            )
            logging.info("Successfully put item.")
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                logging.error(f"Article with URL {article['news_url']} already exists.")
            else:
                logging.error(f"Error inserting article: {e}")
            return False

    def query(self, url):
        """
        Checks if an article with the specified URL already exists in the DynamoDB table.

        Args:
            url (str): The URL of the article (used as the primary key).

        Returns:
            bool: True if the article exists, False otherwise.
        """
        try:
            response = self.table.get_item(Key={"news_url": url})
            if "Item" in response:
                print("(previous url)")
                return True
            return False
        except ClientError as e:
            logging.exception(f"Error querying DynamoDB: {e}")
            return False

    def get_all_articles(self):
        """
        Retrieves all articles stored in the DynamoDB table.

        Returns:
            list: A list of dictionaries, each representing an article.
            Returns an empty list if there is an error.
        """
        try:
            response = self.table.scan()
            return response.get("Items", [])
        except ClientError as e:
            logging.exception(f"Error retrieving articles: {e}")
            return []

    def clear_all_items(self):
        """
        Deletes all items from the DynamoDB table.

        Returns:
            bool: True if all items were successfully deleted, False if there was an error.
        """
        try:
            # Scan to retrieve all items in the table
            scan_response = self.table.scan()
            items = scan_response.get("Items", [])

            # Iterate and delete each item
            for item in items:
                self.table.delete_item(Key={"news_url": item["news_url"]})
                logging.info(f"Deleted item with URL: {item['news_url']}")

            logging.info("All items deleted successfully.")
            return True
        except ClientError as e:
            logging.exception(f"Error deleting items from DynamoDB: {e}")
            return False
            
    #get all the articles with is_related = true
    def get_all_related_articles(self):
        """
        Retrieves all articles stored in the DynamoDB table where is_related is true.
    
        Returns:
            list: A list of dictionaries, each representing an article.
            Returns an empty list if there is an error.
        """
        items = []
        try:
            response = self.table.scan(
                FilterExpression=Attr('is_related').eq(True)
            )
            items.extend(response.get("Items", []))
    
            # Handle pagination
            while 'LastEvaluatedKey' in response:
                response = self.table.scan(
                    FilterExpression=Attr('is_related').eq(True),
                    ExclusiveStartKey=response['LastEvaluatedKey']
                )
                items.extend(response.get("Items", []))
    
            return items
        except ClientError as e:
            logging.exception(f"Error retrieving articles: {e}")
            return []

import logging
import os

import psycopg2

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)


def lambda_handler(event, context):
    logger.info("Event: %s", event)
    try:
        with psycopg2.connect(
            dbname=os.getenv("DB_NAME"),
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASSWORD"),
            host=os.getenv("DB_HOST"),
            port=os.getenv("DB_PORT", "5432"),
        ) as conn:
            with conn.cursor() as cursor:
                # Execute version query
                cursor.execute("SELECT version();")

                # Fetch result
                db_version = cursor.fetchone()
                logger.info("Connected to: %s", db_version)

        return {"statusCode": 200, "body": f"Connection successful! {db_version=}"}

    except Exception as e:
        logger.error("Error: %s", e)
        return {"statusCode": 500, "body": "Connection failed!"}

import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()

conn = psycopg2.connect(
    host=os.getenv("DB_HOST"),
    port=os.getenv("DB_PORT"),
    dbname=os.getenv("DB_NAME"),
    user=os.getenv("DB_USER"),
    password=os.getenv("DB_PASSWORD"),
    )

with conn:
    with conn.cursor() as cur:
        with open("schema.sql", "r") as f:
            cur.execute(f.read())

conn.close()
print("Database initialized.")
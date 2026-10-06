from sqlalchemy import create_engine, inspect, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv
import os

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def initialize_database():
    Base.metadata.create_all(bind=engine)

    existing_columns = {column["name"] for column in inspect(engine).get_columns("users")}
    new_columns = {
        "headline": "VARCHAR NOT NULL DEFAULT ''",
        "avatar_url": "VARCHAR",
        "skills_teach": "JSON NOT NULL DEFAULT '[]'",
        "skills_learn": "JSON NOT NULL DEFAULT '[]'",
        "rating": "FLOAT NOT NULL DEFAULT 0",
        "swaps_completed": "INTEGER NOT NULL DEFAULT 0",
    }

    missing_columns = new_columns.keys() - existing_columns
    if not missing_columns:
        return

    with engine.begin() as connection:
        for column_name in missing_columns:
            connection.execute(
                text(f"ALTER TABLE users ADD COLUMN {column_name} {new_columns[column_name]}")
            )

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
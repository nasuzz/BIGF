import getpass
import os

from dotenv import load_dotenv


load_dotenv()


def psycopg2_connection_kwargs() -> dict:
    """환경변수에서 PostgreSQL 접속 정보를 읽는다."""
    password = os.getenv("PGPASSWORD")
    if not password:
        password = getpass.getpass("PostgreSQL postgres 비밀번호: ")

    return {
        "host": os.getenv("PGHOST", "localhost"),
        "port": int(os.getenv("PGPORT", "5432")),
        "dbname": os.getenv("PGDATABASE", "fund_ontology"),
        "user": os.getenv("PGUSER", "postgres"),
        "password": password,
    }


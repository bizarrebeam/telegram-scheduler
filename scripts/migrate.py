import sys
from pathlib import Path

import psycopg

from app.config import get_settings

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def main() -> None:
    settings = get_settings()
    if not settings.database_url:
        sys.exit("DATABASE_URL is not set — fill it in .env first")
    with psycopg.connect(settings.database_url) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version text PRIMARY KEY,
                applied_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )
        applied = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.name in applied:
                continue
            print(f"applying {path.name} ...")
            conn.execute(path.read_text(encoding="utf-8"))
            conn.execute(
                "INSERT INTO schema_migrations (version) VALUES (%s)",
                (path.name,),
            )
        conn.commit()
    print("migrations up to date")


if __name__ == "__main__":
    main()

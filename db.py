"""SQLite connection, schema initialization, and first-run demo data."""

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator

from werkzeug.security import generate_password_hash


BASE_DIR = Path(__file__).resolve().parent
SCHEMA_PATH = BASE_DIR / "schema.sql"
ADMIN_IDENTIFIER = "admin@123"

DEMO_USERS = (
    ("Asha Student", "student@campus.edu", "student", "Student123!"),
    ("Ravi Student", "student2@campus.edu", "student", "Student123!"),
    ("Campus Admin", ADMIN_IDENTIFIER, "admin", "Admin123"),
)

DEMO_RESOURCES = (
    ("Lecture Hall 1", "room", "Academic Block A, Ground Floor", 120),
    ("Seminar Room 204", "room", "Academic Block B, Second Floor", 36),
    ("Computer Lab 3", "room", "Technology Block, First Floor", 40),
    ("Projector Kit A", "equipment", "Equipment Desk, Main Library", 1),
    ("Portable PA System", "equipment", "Equipment Desk, Main Library", 1),
)


@contextmanager
def get_connection(database_path: str) -> Generator[sqlite3.Connection, None, None]:
    """Open a SQLite connection with foreign-key checks enabled."""
    connection = sqlite3.connect(database_path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def init_db(database_path: str, seed_demo_users: bool = True) -> None:
    """Create the database and seed demo records without duplicating them."""
    database = Path(database_path)
    database.parent.mkdir(parents=True, exist_ok=True)

    with get_connection(str(database)) as connection:
        connection.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        issue_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(facility_issues)")
        }
        if "title" not in issue_columns:
            connection.execute(
                """
                ALTER TABLE facility_issues
                ADD COLUMN title TEXT NOT NULL DEFAULT 'Facility issue'
                    CHECK (length(trim(title)) BETWEEN 1 AND 120)
                """
            )
        if "location" not in issue_columns:
            connection.execute(
                """
                ALTER TABLE facility_issues
                ADD COLUMN location TEXT NOT NULL DEFAULT 'Not specified'
                    CHECK (length(trim(location)) BETWEEN 1 AND 160)
                """
            )
        if "admin_category" not in issue_columns:
            connection.execute(
                """
                ALTER TABLE facility_issues
                ADD COLUMN admin_category TEXT
                    CHECK (
                        admin_category IS NULL OR admin_category IN (
                            'electrical', 'plumbing', 'furniture',
                            'network', 'cleaning', 'other'
                        )
                    )
                """
            )
        if "admin_priority" not in issue_columns:
            connection.execute(
                """
                ALTER TABLE facility_issues
                ADD COLUMN admin_priority TEXT
                    CHECK (
                        admin_priority IS NULL OR admin_priority IN (
                            'critical', 'high', 'medium', 'low'
                        )
                    )
                """
            )

        connection.execute(
            "UPDATE users SET role = 'student' WHERE role = 'staff'"
        )
        connection.execute(
            """
            UPDATE users
            SET role = 'student'
            WHERE role = 'admin' AND email != ?
            """,
            (ADMIN_IDENTIFIER,),
        )

        now = datetime.now(timezone.utc).isoformat(timespec="seconds")

        if seed_demo_users:
            for name, email, role, password in DEMO_USERS:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO users
                        (name, email, role, password_hash, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (name, email, role, generate_password_hash(password), now),
                )

        for name, resource_type, location, capacity in DEMO_RESOURCES:
            connection.execute(
                """
                INSERT OR IGNORE INTO resources
                    (name, resource_type, location, capacity, is_active, created_at)
                VALUES (?, ?, ?, ?, 1, ?)
                """,
                (name, resource_type, location, capacity, now),
            )

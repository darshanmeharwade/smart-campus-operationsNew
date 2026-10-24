"""Provision one administrator in an already initialized campus database."""

import argparse
import getpass
import os
import sqlite3
import sys
from pathlib import Path

from werkzeug.security import generate_password_hash

from db import ADMIN_IDENTIFIER, BASE_DIR, get_connection


MINIMUM_PASSWORD_LENGTH = 12


def provision_admin(
    database_path: str | Path,
    name: str,
    email: str,
    password: str,
) -> bool:
    """Insert a new admin without changing any existing account."""
    database = Path(database_path)
    if not database.is_file():
        print(
            "Database file does not exist. Start the application once to initialize it.",
            file=sys.stderr,
        )
        return False

    normalized_name = name.strip()
    normalized_email = email.strip().casefold()
    if not normalized_name or len(normalized_name) > 100:
        print("Enter a name between 1 and 100 characters.", file=sys.stderr)
        return False
    if len(normalized_email) > 254 or normalized_email != ADMIN_IDENTIFIER:
        print(
            f"The administrator identifier is reserved as {ADMIN_IDENTIFIER}.",
            file=sys.stderr,
        )
        return False
    if (
        len(password) < MINIMUM_PASSWORD_LENGTH
        or len(password) > 1024
        or not password.strip()
    ):
        print(
            f"Password must be between {MINIMUM_PASSWORD_LENGTH} and 1024 characters.",
            file=sys.stderr,
        )
        return False

    try:
        with get_connection(str(database)) as connection:
            users_table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'users'"
            ).fetchone()
            if users_table is None:
                print(
                    "Database is not initialized. Start the application once first.",
                    file=sys.stderr,
                )
                return False

            try:
                connection.execute(
                    """
                    INSERT INTO users (name, email, role, password_hash, created_at)
                    VALUES (?, ?, 'admin', ?, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
                    """,
                    (
                        normalized_name,
                        normalized_email,
                        generate_password_hash(password),
                    ),
                )
            except sqlite3.IntegrityError:
                existing_user = connection.execute(
                    "SELECT 1 FROM users WHERE email = ?",
                    (normalized_email,),
                ).fetchone()
                if existing_user is None:
                    raise
                print("An account with that email already exists.", file=sys.stderr)
                return False
    except sqlite3.OperationalError:
        print(
            "Could not access the initialized database. Check DATABASE_PATH.",
            file=sys.stderr,
        )
        return False

    print(f"Administrator account created for {normalized_email}.")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Create an administrator in an initialized campus database."
    )
    parser.add_argument(
        "--database",
        default=os.environ.get(
            "DATABASE_PATH",
            str(BASE_DIR / "instance" / "campus.db"),
        ),
        help="SQLite database path (defaults to DATABASE_PATH or instance/campus.db)",
    )
    args = parser.parse_args(argv)

    name = input("Administrator name: ")
    email = getpass.getpass("Administrator identifier (input hidden): ")
    password = getpass.getpass("Administrator password (input hidden): ")
    confirmation = getpass.getpass("Confirm administrator password: ")
    if password != confirmation:
        print("Passwords do not match.", file=sys.stderr)
        return 1

    return 0 if provision_admin(args.database, name, email, password) else 1


if __name__ == "__main__":
    raise SystemExit(main())

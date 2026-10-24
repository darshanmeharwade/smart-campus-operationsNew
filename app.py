"""Flask application entry point for the Smart Campus MVP."""

import os
import secrets
import sqlite3
from datetime import date, datetime, timedelta
from functools import wraps
from pathlib import Path
from typing import Callable

from flask import (
    Flask,
    abort,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

from db import ADMIN_IDENTIFIER, DEMO_USERS, get_connection, init_db
from issue_triage import recommend_issue_triage


BASE_DIR = Path(__file__).resolve().parent
ISSUE_CATEGORIES = {"electrical", "plumbing", "furniture", "network", "cleaning", "other"}
ISSUE_STATUSES = {"open", "in_progress", "resolved"}
BOOKING_STATUSES = ("pending", "approved", "rejected", "cancelled")
ANALYTICS_ISSUE_CATEGORIES = (
    "electrical",
    "plumbing",
    "furniture",
    "network",
    "cleaning",
    "other",
)


def create_app(test_config: dict | None = None) -> Flask:
    """Create and configure the Flask application."""
    app = Flask(__name__)
    app.config["APP_ENV"] = os.environ.get("APP_ENV", "development").strip().lower()
    app.config["DATABASE"] = os.environ.get(
        "DATABASE_PATH", str(BASE_DIR / "instance" / "campus.db")
    )
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(minutes=30)
    app.config["SESSION_REFRESH_EACH_REQUEST"] = True
    app.config["DEMO_USERS_ENABLED"] = (
        os.environ.get("DEMO_USERS_ENABLED", "true").strip().lower() == "true"
    )
    if test_config is not None:
        app.config.update(test_config)

    is_production = (
        app.config["APP_ENV"] in {"prod", "production"}
        or os.environ.get("RENDER", "").strip().lower() == "true"
    )
    configured_secret = app.config.get("SECRET_KEY")
    secret_key = os.environ.get("SECRET_KEY", "").strip()
    if not secret_key and is_production:
        raise RuntimeError("SECRET_KEY must be set in production.")
    app.config["SECRET_KEY"] = (
        secret_key or configured_secret or secrets.token_hex(32)
    )
    app.config["SESSION_COOKIE_SECURE"] = is_production or (
        os.environ.get("SESSION_COOKIE_SECURE", "").strip().lower() == "true"
    )
    app.config["DEMO_USERS_ENABLED"] = (
        app.config["DEMO_USERS_ENABLED"] and not is_production
    )

    init_db(
        app.config["DATABASE"],
        seed_demo_users=app.config["DEMO_USERS_ENABLED"],
    )

    def destination_for(user) -> str:
        if (
            user["role"] == "admin"
            and user["email"].casefold() == ADMIN_IDENTIFIER
        ):
            return url_for("admin_dashboard")
        return url_for("dashboard")

    def dashboard_url() -> str:
        if g.current_user["role"] == "admin":
            return url_for("admin_dashboard")
        return url_for("dashboard")

    def login_required(view: Callable):
        @wraps(view)
        def wrapped_view(*args, **kwargs):
            if g.current_user is None:
                return redirect(url_for("login"))
            return view(*args, **kwargs)

        return wrapped_view

    def role_required(required_role: str):
        def decorator(view: Callable):
            @wraps(view)
            @login_required
            def wrapped_view(*args, **kwargs):
                if (
                    g.current_user["role"] != required_role
                    or (
                        required_role == "admin"
                        and g.current_user["email"].casefold() != ADMIN_IDENTIFIER
                    )
                ):
                    abort(403)
                return view(*args, **kwargs)

            return wrapped_view

        return decorator

    def valid_csrf_token() -> bool:
        submitted_token = request.form.get("csrf_token", "")
        expected_token = session.get("csrf_token", "")
        return (
            isinstance(submitted_token, str)
            and isinstance(expected_token, str)
            and bool(submitted_token)
            and secrets.compare_digest(submitted_token, expected_token)
        )

    def parse_booking_slot(
        booking_date: str, start_time: str, end_time: str
    ) -> tuple[str, str, str] | None:
        try:
            parsed_date = datetime.strptime(booking_date, "%Y-%m-%d").date()
            parsed_start = datetime.strptime(start_time, "%H:%M").time()
            parsed_end = datetime.strptime(end_time, "%H:%M").time()
        except ValueError:
            return None

        now = datetime.now().astimezone()
        requested_start = datetime.combine(
            parsed_date, parsed_start, tzinfo=now.tzinfo
        )
        if (
            parsed_date.isoformat() != booking_date
            or parsed_date < now.date()
            or parsed_start.strftime("%H:%M") != start_time
            or parsed_end.strftime("%H:%M") != end_time
            or parsed_end <= parsed_start
            or requested_start <= now
        ):
            return None
        return booking_date, start_time, end_time

    def dashboard_data(
        availability_date: str = "",
        start_time: str = "",
        end_time: str = "",
        resource_type: str = "",
        is_admin: bool = False,
    ) -> dict:
        slot = None
        search_error = None
        if any((availability_date, start_time, end_time)):
            if not all((availability_date, start_time, end_time)):
                search_error = "Enter a date, start time, and end time to check availability."
            else:
                slot = parse_booking_slot(availability_date, start_time, end_time)
                if slot is None:
                    search_error = "Choose a valid date and time range. The date cannot be in the past."

        if resource_type not in ("", "room", "equipment"):
            resource_type = ""

        resources = []
        bookings = []
        with get_connection(app.config["DATABASE"]) as connection:
            query = "SELECT id, name, resource_type, location, capacity FROM resources WHERE is_active = 1"
            parameters: list[object] = []
            if resource_type:
                query += " AND resource_type = ?"
                parameters.append(resource_type)
            query += " ORDER BY resource_type, name"
            resource_rows = connection.execute(query, parameters).fetchall()

            for resource in resource_rows:
                available = None
                if slot is not None:
                    conflict = connection.execute(
                        """
                        SELECT 1 FROM bookings
                        WHERE resource_id = ? AND booking_date = ?
                          AND status IN ('pending', 'approved')
                          AND start_time < ? AND end_time > ?
                        LIMIT 1
                        """,
                        (resource["id"], slot[0], slot[2], slot[1]),
                    ).fetchone()
                    available = conflict is None
                resources.append({**dict(resource), "available": available})

            bookings = connection.execute(
                """
                SELECT b.id, b.resource_id, r.name AS resource_name, b.purpose,
                       b.booking_date, b.start_time, b.end_time, b.status
                FROM bookings AS b
                JOIN resources AS r ON r.id = b.resource_id
                WHERE b.user_id = ?
                ORDER BY b.booking_date DESC, b.start_time DESC, b.id DESC
                """,
                (g.current_user["id"],),
            ).fetchall()

            if is_admin:
                issues = connection.execute(
                    """
                    SELECT i.id, i.title, i.description, i.location, i.category,
                           i.status, i.created_at, i.admin_category,
                           i.admin_priority, u.name AS reporter_name
                    FROM facility_issues AS i
                    JOIN users AS u ON u.id = i.user_id
                    ORDER BY i.created_at DESC, i.id DESC
                    """
                ).fetchall()
                pending_bookings = connection.execute(
                    """
                    SELECT b.id, b.purpose, b.booking_date, b.start_time,
                           b.end_time, u.name AS requester_name,
                           r.name AS resource_name
                    FROM bookings AS b
                    JOIN users AS u ON u.id = b.user_id
                    JOIN resources AS r ON r.id = b.resource_id
                    WHERE b.status = 'pending'
                    ORDER BY b.booking_date, b.start_time, b.id
                    """
                ).fetchall()
            else:
                issues = connection.execute(
                    """
                    SELECT id, title, description, location, category,
                           status, created_at
                    FROM facility_issues
                    WHERE user_id = ?
                    ORDER BY created_at DESC, id DESC
                    """,
                    (g.current_user["id"],),
                ).fetchall()
                pending_bookings = []

        if is_admin:
            issues = [
                {
                    **dict(issue),
                    **recommend_issue_triage(dict(issue)),
                }
                for issue in issues
            ]

        return {
            "user": g.current_user,
            "resources": resources,
            "bookings": bookings,
            "issues": issues,
            "pending_bookings": pending_bookings,
            "is_admin": is_admin,
            "availability_date": availability_date,
            "start_time": start_time,
            "end_time": end_time,
            "resource_type": resource_type,
            "search_error": search_error,
            "today": datetime.now().astimezone().date().isoformat(),
        }

    def admin_analytics(selected_date: str) -> dict:
        if selected_date:
            try:
                parsed_date = date.fromisoformat(selected_date)
            except ValueError:
                abort(400, description="Choose a valid analytics date.")
            if len(selected_date) != 10 or parsed_date.isoformat() != selected_date:
                abort(400, description="Choose a valid analytics date.")

        booking_counts = dict.fromkeys(BOOKING_STATUSES, 0)
        issue_status_counts = dict.fromkeys(ISSUE_STATUSES, 0)
        issue_category_counts = dict.fromkeys(ANALYTICS_ISSUE_CATEGORIES, 0)
        booking_filter = " WHERE booking_date = ?" if selected_date else ""
        issue_filter = (
            " WHERE substr(created_at, 1, 10) = ?" if selected_date else ""
        )
        parameters = (selected_date,) if selected_date else ()

        with get_connection(app.config["DATABASE"]) as connection:
            for row in connection.execute(
                "SELECT status, COUNT(*) AS count "
                f"FROM bookings{booking_filter} GROUP BY status",
                parameters,
            ):
                booking_counts[row["status"]] = row["count"]

            for row in connection.execute(
                "SELECT status, COUNT(*) AS count "
                f"FROM facility_issues{issue_filter} GROUP BY status",
                parameters,
            ):
                issue_status_counts[row["status"]] = row["count"]

            top_facilities = connection.execute(
                """
                SELECT r.name AS resource_name, COUNT(*) AS booking_count
                FROM bookings AS b
                JOIN resources AS r ON r.id = b.resource_id
                WHERE b.status = 'approved'
                """
                + (" AND b.booking_date = ?" if selected_date else "")
                + """
                GROUP BY r.id, r.name
                ORDER BY booking_count DESC, r.name
                LIMIT 5
                """,
                parameters,
            ).fetchall()

            for row in connection.execute(
                "SELECT category, COUNT(*) AS count "
                f"FROM facility_issues{issue_filter} GROUP BY category",
                parameters,
            ):
                issue_category_counts[row["category"]] = row["count"]

        return {
            "analytics_date": selected_date,
            "booking_counts": booking_counts,
            "booking_total": sum(booking_counts.values()),
            "issue_status_counts": issue_status_counts,
            "issue_total": sum(issue_status_counts.values()),
            "top_facilities": top_facilities,
            "issue_category_counts": issue_category_counts,
        }

    @app.before_request
    def load_current_user() -> None:
        user_id = session.get("user_id")
        g.current_user = None
        if type(user_id) is not int:
            return

        with get_connection(app.config["DATABASE"]) as connection:
            g.current_user = connection.execute(
                "SELECT id, name, email, role FROM users WHERE id = ?",
                (user_id,),
            ).fetchone()

        if g.current_user is None:
            session.clear()

    @app.context_processor
    def inject_csrf_token():
        def csrf_token() -> str:
            token = session.get("csrf_token")
            if not isinstance(token, str):
                token = secrets.token_urlsafe(32)
                session["csrf_token"] = token
            return token

        return {"csrf_token": csrf_token}

    @app.get("/")
    def index():
        if g.current_user is not None:
            return redirect(destination_for(g.current_user))
        return redirect(url_for("login"))

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if g.current_user is not None:
            return redirect(destination_for(g.current_user))

        email = ""
        error = None
        status_code = 200

        if request.method == "POST":
            email_value = request.form.get("email", "")
            password = request.form.get("password", "")
            email = email_value.strip().casefold() if isinstance(email_value, str) else ""

            if not valid_csrf_token():
                error = "Your form session expired. Please try again."
                status_code = 400
            elif (
                not isinstance(password, str)
                or not email
                or len(email) > 254
                or len(password) > 1024
                or not password
            ):
                error = "Enter an identifier and password."
                status_code = 400
            else:
                with get_connection(app.config["DATABASE"]) as connection:
                    user = connection.execute(
                        """
                        SELECT id, name, email, role, password_hash
                        FROM users
                        WHERE email = ?
                        """,
                        (email,),
                    ).fetchone()

                demo_passwords = {
                    demo_user[1]: demo_user[3] for demo_user in DEMO_USERS
                }
                is_blocked_demo_login = (
                    not app.config["DEMO_USERS_ENABLED"]
                    and email in demo_passwords
                    and password == demo_passwords[email]
                )
                if user is None or not check_password_hash(
                    user["password_hash"], password
                ) or is_blocked_demo_login:
                    error = "Email or password is incorrect."
                    status_code = 401
                else:
                    session.clear()
                    session["user_id"] = user["id"]
                    session["csrf_token"] = secrets.token_urlsafe(32)
                    session.permanent = True
                    flash(f"Welcome, {user['name']}!")
                    return redirect(destination_for(user))

        return (
            render_template("login.html", error=error, email=email),
            status_code,
        )

    @app.route("/register", methods=["GET", "POST"])
    def register_student():
        if g.current_user is not None:
            return redirect(destination_for(g.current_user))

        name = ""
        email = ""
        error = None
        status_code = 200

        if request.method == "POST":
            name_value = request.form.get("name", "")
            email_value = request.form.get("email", "")
            password = request.form.get("password", "")
            name = name_value.strip() if isinstance(name_value, str) else ""
            email = email_value.strip().casefold() if isinstance(email_value, str) else ""

            if not valid_csrf_token():
                error = "Your form session expired. Please try again."
                status_code = 400
            elif (
                len(name) > 100
                or not name
                or not email
                or email == ADMIN_IDENTIFIER
                or len(email) > 254
                or not isinstance(password, str)
                or len(password) < 8
                or len(password) > 1024
                or not password.strip()
            ):
                error = (
                    "Enter a name and available identifier, and a password "
                    "of at least 8 characters."
                )
                status_code = 400
            else:
                now = datetime.now().astimezone().isoformat(timespec="seconds")
                try:
                    with get_connection(app.config["DATABASE"]) as connection:
                        connection.execute(
                            """
                            INSERT INTO users
                                (name, email, role, password_hash, created_at)
                            VALUES (?, ?, 'student', ?, ?)
                            """,
                            (name, email, generate_password_hash(password), now),
                        )
                except sqlite3.IntegrityError:
                    with get_connection(app.config["DATABASE"]) as connection:
                        existing_user = connection.execute(
                            "SELECT 1 FROM users WHERE email = ?",
                            (email,),
                        ).fetchone()
                    if existing_user is None:
                        raise
                    error = "An account with that email already exists."
                    status_code = 409
                else:
                    flash("Student account created. Please log in.")
                    return redirect(url_for("login"))

        return (
            render_template(
                "register.html",
                error=error,
                name=name,
                email=email,
            ),
            status_code,
        )

    @app.post("/logout")
    @login_required
    def logout():
        if not valid_csrf_token():
            abort(400, description="Invalid security token. Refresh and try again.")
        session.clear()
        flash("You have been signed out.")
        return redirect(url_for("login"))

    @app.get("/dashboard")
    @login_required
    def dashboard():
        return render_template(
            "dashboard.html",
            **dashboard_data(
                request.args.get("date", "").strip(),
                request.args.get("start_time", "").strip(),
                request.args.get("end_time", "").strip(),
                request.args.get("resource_type", "").strip(),
            ),
        )

    @app.post("/bookings")
    @login_required
    def create_booking():
        if not valid_csrf_token():
            abort(400, description="Invalid security token. Refresh and try again.")

        resource_id_value = request.form.get("resource_id", "")
        booking_date = request.form.get("booking_date", "").strip()
        start_time = request.form.get("start_time", "").strip()
        end_time = request.form.get("end_time", "").strip()
        purpose = request.form.get("purpose", "").strip()

        if (
            not resource_id_value.isdecimal()
            or len(booking_date) != 10
            or not all((start_time, end_time))
            or len(purpose) < 1
            or len(purpose) > 500
        ):
            flash("Enter a facility, valid date and time range, and a purpose of 1–500 characters.")
            return redirect(dashboard_url())

        slot = parse_booking_slot(booking_date, start_time, end_time)
        if slot is None:
            flash("Choose a valid date and time range. The date cannot be in the past.")
            return redirect(dashboard_url())

        resource_id = int(resource_id_value)
        try:
            with get_connection(app.config["DATABASE"]) as connection:
                connection.execute("BEGIN IMMEDIATE")
                resource = connection.execute(
                    "SELECT id FROM resources WHERE id = ? AND is_active = 1",
                    (resource_id,),
                ).fetchone()
                if resource is None:
                    flash("That facility is unavailable. Please choose another.")
                    return redirect(dashboard_url())

                conflict = connection.execute(
                    """
                    SELECT 1 FROM bookings
                    WHERE resource_id = ? AND booking_date = ?
                      AND status IN ('pending', 'approved')
                      AND start_time < ? AND end_time > ?
                    LIMIT 1
                    """,
                    (resource_id, slot[0], slot[2], slot[1]),
                ).fetchone()
                if conflict is not None:
                    flash("That facility is already booked during the selected time.")
                    return redirect(dashboard_url())

                now = datetime.now().isoformat(timespec="seconds")
                connection.execute(
                    """
                    INSERT INTO bookings
                        (user_id, resource_id, purpose, booking_date,
                         start_time, end_time, status, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                    """,
                    (
                        g.current_user["id"],
                        resource_id,
                        purpose,
                        slot[0],
                        slot[1],
                        slot[2],
                        now,
                        now,
                    ),
                )
        except sqlite3.Error:
            app.logger.exception("Could not create facility booking")
            flash("We could not save that booking. Please try again.")
            return redirect(dashboard_url())

        flash("Booking request submitted. Status: Pending.")
        return redirect(dashboard_url())

    @app.post("/bookings/<int:booking_id>/cancel")
    @login_required
    def cancel_booking(booking_id: int):
        if not valid_csrf_token():
            abort(400, description="Invalid security token. Refresh and try again.")

        now = datetime.now().isoformat(timespec="seconds")
        with get_connection(app.config["DATABASE"]) as connection:
            cursor = connection.execute(
                """
                UPDATE bookings
                SET status = 'cancelled', updated_at = ?
                WHERE id = ? AND user_id = ?
                  AND status IN ('pending', 'approved')
                """,
                (now, booking_id, g.current_user["id"]),
            )

        if cursor.rowcount == 1:
            flash("Booking cancelled.")
        else:
            flash("That booking cannot be cancelled.")
        return redirect(dashboard_url())

    @app.post("/issues")
    @login_required
    def submit_issue():
        if not valid_csrf_token():
            abort(400, description="Invalid security token. Refresh and try again.")

        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        location = request.form.get("location", "").strip()
        category = request.form.get("category", "").strip().lower()
        if (
            not 1 <= len(title) <= 120
            or not 1 <= len(description) <= 2000
            or not 1 <= len(location) <= 160
            or category not in ISSUE_CATEGORIES
        ):
            flash("Enter a title, description, location, and valid issue category.")
            return redirect(dashboard_url())

        now = datetime.now().astimezone().isoformat(timespec="seconds")
        try:
            with get_connection(app.config["DATABASE"]) as connection:
                connection.execute(
                    """
                    INSERT INTO facility_issues
                        (user_id, title, category, description, location,
                         priority, status, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, 'medium', 'open', ?, ?)
                    """,
                    (
                        g.current_user["id"],
                        title,
                        category,
                        description,
                        location,
                        now,
                        now,
                    ),
                )
        except sqlite3.Error:
            app.logger.exception("Could not save facility issue report")
            flash("We could not save that report. Please try again.")
            return redirect(dashboard_url())

        flash("Facility issue report submitted.")
        return redirect(dashboard_url())

    @app.post("/issues/<int:issue_id>/status")
    @role_required("admin")
    def update_issue_status(issue_id: int):
        if not valid_csrf_token():
            abort(400, description="Invalid security token. Refresh and try again.")

        status = request.form.get("status", "").strip().lower()
        if status not in ISSUE_STATUSES:
            abort(400, description="Choose a valid issue status.")

        now = datetime.now().astimezone().isoformat(timespec="seconds")
        with get_connection(app.config["DATABASE"]) as connection:
            cursor = connection.execute(
                """
                UPDATE facility_issues
                SET status = ?, updated_at = ?
                WHERE id = ?
                """,
                (status, now, issue_id),
            )

        if cursor.rowcount == 1:
            flash("Issue status updated.")
        else:
            flash("That issue report could not be found.")
        return redirect(url_for("admin_dashboard"))

    @app.post("/issues/<int:issue_id>/triage")
    @role_required("admin")
    def confirm_issue_triage(issue_id: int):
        if not valid_csrf_token():
            abort(400, description="Invalid security token. Refresh and try again.")

        category = request.form.get("admin_category", "").strip().lower()
        priority = request.form.get("admin_priority", "").strip().lower()
        if category not in ISSUE_CATEGORIES or priority not in {
            "critical",
            "high",
            "medium",
            "low",
        }:
            abort(400, description="Choose a valid category and priority.")

        now = datetime.now().astimezone().isoformat(timespec="seconds")
        with get_connection(app.config["DATABASE"]) as connection:
            cursor = connection.execute(
                """
                UPDATE facility_issues
                SET admin_category = ?, admin_priority = ?, updated_at = ?
                WHERE id = ?
                """,
                (category, priority, now, issue_id),
            )

        if cursor.rowcount == 1:
            flash("Admin triage decision saved. The submitted report is unchanged.")
        else:
            flash("That issue report could not be found.")
        return redirect(url_for("admin_dashboard"))

    @app.post("/admin/bookings/<int:booking_id>/approve")
    @role_required("admin")
    def approve_booking(booking_id: int):
        if not valid_csrf_token():
            abort(400, description="Invalid security token. Refresh and try again.")

        try:
            with get_connection(app.config["DATABASE"]) as connection:
                connection.execute("BEGIN IMMEDIATE")
                booking = connection.execute(
                    """
                    SELECT resource_id, booking_date, start_time, end_time
                    FROM bookings
                    WHERE id = ? AND status = 'pending'
                    """,
                    (booking_id,),
                ).fetchone()
                if booking is None:
                    flash("That pending booking could not be found.")
                    return redirect(url_for("admin_dashboard"))

                conflict = connection.execute(
                    """
                    SELECT 1
                    FROM bookings
                    WHERE resource_id = ? AND booking_date = ?
                      AND id != ? AND status IN ('pending', 'approved')
                      AND start_time < ? AND end_time > ?
                    LIMIT 1
                    """,
                    (
                        booking["resource_id"],
                        booking["booking_date"],
                        booking_id,
                        booking["end_time"],
                        booking["start_time"],
                    ),
                ).fetchone()
                if conflict is not None:
                    flash("This booking conflicts with another active booking and cannot be approved.")
                    return redirect(url_for("admin_dashboard"))

                connection.execute(
                    """
                    UPDATE bookings
                    SET status = 'approved', updated_at = ?
                    WHERE id = ? AND status = 'pending'
                    """,
                    (
                        datetime.now().astimezone().isoformat(timespec="seconds"),
                        booking_id,
                    ),
                )
        except sqlite3.Error:
            app.logger.exception("Could not approve facility booking")
            flash("We could not approve that booking. Please try again.")
            return redirect(url_for("admin_dashboard"))

        flash("Booking approved.")
        return redirect(url_for("admin_dashboard"))

    @app.post("/admin/bookings/<int:booking_id>/reject")
    @role_required("admin")
    def reject_booking(booking_id: int):
        if not valid_csrf_token():
            abort(400, description="Invalid security token. Refresh and try again.")

        with get_connection(app.config["DATABASE"]) as connection:
            cursor = connection.execute(
                """
                UPDATE bookings
                SET status = 'rejected', updated_at = ?
                WHERE id = ? AND status = 'pending'
                """,
                (
                    datetime.now().astimezone().isoformat(timespec="seconds"),
                    booking_id,
                ),
            )

        if cursor.rowcount == 1:
            flash("Booking rejected.")
        else:
            flash("That pending booking could not be found.")
        return redirect(url_for("admin_dashboard"))

    @app.get("/admin")
    @role_required("admin")
    def admin_dashboard():
        analytics_date = request.args.get("analytics_date", "")
        return render_template(
            "dashboard.html",
            **dashboard_data(
                request.args.get("date", "").strip(),
                request.args.get("start_time", "").strip(),
                request.args.get("end_time", "").strip(),
                request.args.get("resource_type", "").strip(),
                is_admin=True,
            ),
            **admin_analytics(analytics_date),
        )

    @app.get("/health")
    def health():
        return jsonify({"status": "ok", "application": "smart-campus"})

    return app


app = create_app()


if __name__ == "__main__":
    app.run(
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "5000")),
        debug=os.environ.get("FLASK_DEBUG", "").lower() == "true",
    )

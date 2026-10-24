# Smart Campus Operations and Facility Booking Platform

A beginner-friendly Flask and SQLite MVP for managing campus rooms, equipment bookings, facility reports, and admin issue triage.

## Implemented so far

- A Flask application factory and a simple responsive starter page.
- A SQLite schema for users, resources, bookings, facility issues, and audit history.
- Automatic, repeatable database initialization and sample data seeding.
- Password hashes for demo accounts (no plain-text passwords are stored in SQLite).
- A `/health` endpoint for a basic application check.
- Login and logout with secure password hash verification.
- Session expiry, signed session cookies, and server-side role checks.
- CSRF checks on state-changing login, logout, booking, issue-reporting, and admin actions.
- An authenticated user dashboard and a role-protected admin dashboard.
- Automated tests for authentication, role permissions, booking behavior, issue reports, and admin workflows.
- Authenticated users can filter active rooms/equipment, check time-slot availability, submit booking requests, view their bookings, and cancel their own pending or approved bookings.
- Overlapping active booking requests are blocked server-side inside a SQLite write transaction; cancelled and rejected bookings do not block a slot.
- Students can submit facility issue reports and view only their own reports. Reports include a title, description, campus location, category, status, and creation time.
- Admins can view all issue reports and change their status to Open, In Progress, or Resolved.
- Admins can approve or reject pending booking requests. Approval rechecks for overlapping active bookings inside a SQLite write transaction.
- The admin dashboard includes database-backed booking and issue totals, status counts, top facilities by approved bookings, and issue counts by category. Admins can optionally filter analytics to a calendar day.
- The admin issue list provides local, explainable category and priority suggestions with matched-term explanations. Admins can explicitly save their final category and priority separately from the reporter's original category and description.
- Production WSGI deployment support with Gunicorn, persistent database path configuration, secure production cookies, and production-safe demo-account handling.

## AI-assisted issue triage

Issue triage runs locally with a deterministic keyword rules engine. It uses report title and description to suggest a category and a priority (Critical, High, Medium, or Low), and shows the matched terms or why the cautious Medium default was used. The existing submitted category remains visible and unchanged. Suggestions are computed for the admin view, not stored as facts; an authenticated admin can explicitly save a final category and priority in separate database fields. The report description is not changed by triage.

This prototype does not train or use a machine-learning model: the project has no suitable labelled issue dataset, and its only application dependency is Flask. The rules are simple heuristics, can miss context or misinterpret wording, and have not been evaluated for accuracy. Treat suggestions as decision support, not emergency assessment or a substitute for administrator review. No issue text is sent to an external service.

## Requirements

- Python 3.10 or newer
- pip

## Run locally

From the project directory, activate your virtual environment and install dependencies:

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Run the application:

```powershell
python app.py
```

Open `http://127.0.0.1:5000/` and sign in with an account seeded for local development. The health check is at `http://127.0.0.1:5000/health`.

To run automated tests:

```powershell
python -m unittest discover -s tests -v
```

The database is created automatically at `instance/campus.db`. Existing databases are migrated automatically to add issue title, location, and separate admin triage decision fields. To use another location, set `DATABASE_PATH`.
Set `APP_ENV=production` when hosting the application. Production startup requires an environment-provided `SECRET_KEY`, enables secure session cookies, and does not create or accept the built-in demo accounts.

## Local development accounts

The default development environment seeds local-only demo users on first initialization; passwords are hashed before insertion. These accounts are for local development and automated tests only. Their credentials are intentionally not published in this guide. Production mode disables demo-user seeding and rejects these built-in accounts even if they already exist in the database. Students can also create an account through the public student sign-up page; registration always assigns the student role.

Local account seed values are maintained in `db.py`; do not reuse them in a public deployment. The reserved production administrator identifier is `admin@123`, and its password is privately set during provisioning. The local demo admin password is for development only and is rejected in production. Existing staff accounts and administrator accounts with any other identifier are converted to students during database initialization. Public registration creates student accounts only; public staff or administrator registration is not available.
The public signup form accepts any non-empty identifier in its email field, including an email address or username. Identifiers are trimmed and case-normalized for login.

## Database overview

- `users`: campus users, role, and password hash.
- `resources`: rooms and equipment.
- `bookings`: requested resource, purpose, date/time, and booking status.
- `facility_issues`: reporter, title, description, campus location, category, priority, status, and timestamps.
- `audit_logs`: actor and details for important actions.

Foreign keys, basic `CHECK` constraints, uniqueness rules, and lookup indexes are defined in `schema.sql`. Initialization is safe to rerun and uses unique keys to avoid duplicate demo records.

## Configuration

Copy `.env.example` as a reference for environment variables. The application reads variables from the process environment; it does not load `.env` files automatically.

- `APP_ENV`: set to `production` when hosting. Production startup fails if `SECRET_KEY` is missing.
- `SECRET_KEY`: Flask signing key. Generate a long random value and configure it in the hosting platform's secret/environment settings; do not commit it.
- `DATABASE_PATH`: SQLite database file path (defaults to `instance/campus.db`).
- `SESSION_COOKIE_SECURE`: optional local HTTPS setting. Production always enables secure session cookies; local HTTP defaults to insecure cookies for development.
- `DEMO_USERS_ENABLED`: local-only option to control demo-user seeding. Production always disables demo-user creation and blocks the built-in demo logins.
- `HOST`, `PORT`, `FLASK_DEBUG`: local server options.

## Render deployment

Create a Render **Web Service** connected to this repository and configure:

- **Build Command:** `pip install -r requirements.txt`
- **Start Command:** `gunicorn app:app --bind 0.0.0.0:$PORT --workers 1`
- **Environment Variables:**
  - `APP_ENV=production`
  - `SECRET_KEY`: a long, randomly generated secret stored in Render, not in the repository.
  - `DATABASE_PATH=/var/data/campus.db`

Attach a persistent Render disk mounted at `/var/data`; SQLite writes must stay under that mount path to survive restarts and redeploys. Without a persistent disk, the database is on ephemeral service storage and may be lost. Keep the service at one Gunicorn worker: SQLite schema initialization and migrations run during application startup, and this project does not coordinate simultaneous migration runs across multiple workers.

Production does not seed demo users. Provision a real administrator account through the private `provision_admin.py` command before relying on admin workflows. Public student sign-up is available, but it cannot create staff or administrator accounts. Never copy a development database to a public deployment because it may contain other local/test data. Demo resource records are still inserted idempotently to make the facility catalog available on a fresh database.

Render's local filesystem is ephemeral unless a persistent disk is attached. This setup is suitable for a small single-instance MVP; SQLite on a single persistent disk is not intended for multi-instance scaling or high write concurrency.

## PythonAnywhere Free deployment

PythonAnywhere serves Flask applications through its configured WSGI file; do not use Gunicorn or `python app.py` as the hosted web process.

1. Upload or clone the project into your home directory, for example `/home/<username>/smart-campus-operations`.
2. In a Bash console, create a virtual environment using a Python version offered by PythonAnywhere, then install the project dependencies:

   ```bash
   mkvirtualenv --python=/usr/bin/python3.13 smart-campus
   workon smart-campus
   cd ~/smart-campus-operations
   pip install -r requirements.txt
   ```

   If Python 3.13 is not offered for your account, use another supported version and select the same version when creating the web app.
3. Create a persistent data directory in your home folder, not `/tmp`, and a private environment file outside the repository:

   ```bash
   mkdir -p ~/smart-campus-data
   umask 077
   nano ~/.smart-campus.env
   chmod 600 ~/.smart-campus.env
   ```

   Put these assignments in the file, replacing the placeholders. Generate a long random `SECRET_KEY` (for example, `python -c "import secrets; print(secrets.token_hex(32))"`); do not put it in the repository or a public command:

   ```text
   APP_ENV=production
   SECRET_KEY=<generated-random-secret>
   DATABASE_PATH=/home/<username>/smart-campus-data/campus.db
   DEMO_USERS_ENABLED=false
   ```

   This project does not load `.env` files automatically. The WSGI configuration below reads this private file and exports the values before importing the application. For one-off Bash console commands, load the same settings with `set -a; source ~/.smart-campus.env; set +a`.
4. In the **Web** tab, add a web app using **Manual configuration** and select the same Python version used by the virtual environment. Set its virtualenv to `~/.virtualenvs/smart-campus`.
5. Edit the WSGI configuration file linked in the Web tab. Replace its application setup with the following, updating the project path and username:

   ```python
   import os
   import sys

   project_path = "/home/<username>/smart-campus-operations"
   if project_path not in sys.path:
       sys.path.insert(0, project_path)

   with open(os.path.expanduser("~/.smart-campus.env"), encoding="utf-8") as config:
       for line in config:
           line = line.strip()
           if line and not line.startswith("#"):
               key, separator, value = line.partition("=")
               if separator:
                   os.environ[key.strip()] = value.strip()

   os.environ["APP_ENV"] = "production"
   from app import app as application
   ```

6. Click **Reload** in the Web tab. The WSGI import initializes the SQLite schema at `DATABASE_PATH` and production mode disables demo-user seeding. The database is stored under your home directory so it persists across application reloads; keep backups and monitor your account's storage quota. Do not place it in `/tmp`.
7. Provision the administrator from a Bash console using the same virtualenv and database configuration:

   ```bash
   workon smart-campus
   set -a; source ~/.smart-campus.env; set +a
   cd ~/smart-campus-operations
   python provision_admin.py
   ```

   The command prompts for the name, then privately prompts (without echoing) for the reserved identifier `admin@123` and password twice. Choose a unique password of at least 12 characters; the identifier cannot be changed. It inserts the admin account with the existing password hash method and refuses to alter an account that already uses `admin@123`. Credentials are never command-line arguments. After successful provisioning, click **Reload** in the Web tab again, then verify the admin can log in. Public registration remains student-only.

PythonAnywhere Free has account and resource limits, and SQLite is intended here only for this single-instance MVP. The WSGI deployment uses the configured web-app process, not Gunicorn's command-line server.

## Test suite

Run all automated tests with:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

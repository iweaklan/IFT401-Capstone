"""Application factory: builds the Flask app, its security settings, pages and the init-db command."""

import os

import click
from flask import Flask, current_app, g
from flask.cli import with_appcontext
from flask_wtf.csrf import CSRFProtect

from .cli import run_price_generator_command
from .config import Config
from . import db as db_module
from .routes.admin import bp as admin_bp
from .routes.auth import bp as auth_bp
from .routes.cash import bp as cash_bp
from .routes.media import bp as media_bp
from .routes.trading import bp as trading_bp

# One CSRF extension instance, initialised against the app inside create_app().
csrf = CSRFProtect()


def create_app(config_object=Config):
    """Build and configure the Flask application."""
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(config_object)

    # Sessions and CSRF tokens are signed with SECRET_KEY, so it is required.
    if not app.config.get("SECRET_KEY"):
        raise RuntimeError(
            "SECRET_KEY is not set. Put a long random value in your .env file "
            "(see .env.example)."
        )

    # Session cookie: hidden from JavaScript and not sent with other sites' requests.
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

    # CSRF protection, so every POST form must carry a valid token.
    csrf.init_app(app)

    # Logos are stored in instance/uploads/logos; requests over 2 MB are refused.
    app.config.setdefault("UPLOAD_FOLDER", os.path.join(app.instance_path, "uploads", "logos"))
    app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)


    app.register_blueprint(auth_bp)
    app.register_blueprint(trading_bp)
    app.register_blueprint(cash_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(media_bp)
    app.cli.add_command(init_db_command)
    app.cli.add_command(run_price_generator_command)

    @app.errorhandler(404)
    def _not_found(error):
        # Routes that abort(404, "Stock not found.") get that message shown.
        return error.description or "Not Found", 404

    @app.errorhandler(413)
    def _too_large(error):
        # 413 = "Payload Too Large": the request was over MAX_CONTENT_LENGTH.
        return "That upload is too large (the limit is 2 MB).", 413

    @app.context_processor
    def _inject_current_user():
        # Makes ``current_user`` available in every template (None if logged out).
        return {"current_user": g.get("current_user")}

    # Jinja filters used by the templates.
    from app.money import format_money as _format_money
    from datetime import datetime as _dt
    from zoneinfo import ZoneInfo as _ZoneInfo

    def _money_filter(value):
        # Format a Decimal (or Decimal-able) as $1,234.56.
        return _format_money(_to_decimal(value))

    def _to_decimal(value):
        from decimal import Decimal, InvalidOperation
        from jinja2 import Undefined
        # Missing values show as $0.00 instead of breaking the page.
        if value is None or isinstance(value, Undefined):
            return Decimal("0")
        if isinstance(value, Decimal):
            return value
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError):
            return Decimal("0")

    def _et_datetime_filter(iso_text, tz_name="America/New_York"):
        # Convert a stored ISO-8601 UTC string to local market time for display.
        if not iso_text:
            return ""
        dt = _dt.fromisoformat(iso_text)
        return dt.astimezone(_ZoneInfo(tz_name)).strftime("%Y-%m-%d %H:%M:%S")

    app.jinja_env.filters["money"] = _money_filter
    app.jinja_env.filters["et_datetime"] = _et_datetime_filter

    return app


# Tables whose row counts init-db prints.
_SUMMARY_TABLES = (
    "users",
    "stocks",
    "holdings",
    "transactions",
    "market_schedule",
    "market_settings",
)


@click.command("init-db")
@with_appcontext
def init_db_command():
    """Drop and recreate the database, seed it, then print per-table counts."""
    # Imported here to avoid a circular import (seed.py imports app).
    from seed import seed_into

    # 1-3. Connect, drop the old tables, recreate the schema.
    conn = db_module.get_connection(current_app.config["DATABASE_URL"])
    try:
        db_module.drop_all_tables(conn)
        db_module.init_db(conn)

        # 4. Insert the demo data in one all-or-nothing transaction.
        with db_module.transaction(conn):
            seed_into(conn)

        # 5. Print the per-table summary. No secret is ever printed.
        click.echo("Database initialized and seeded. Row counts:")
        for table in _SUMMARY_TABLES:
            count = db_module.count_rows(conn, table)
            click.echo(f"  {table}: {count}")
    finally:
        conn.close()

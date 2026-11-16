"""Demo data loaded by ``flask --app app init-db``: two users, six stocks, market hours, settings."""

import os
from decimal import Decimal

from werkzeug.security import generate_password_hash

from app import db
from app.clock import now_utc


# (ticker, company, shares outstanding, starting price). SAGU has only 50 shares and GCNY
# costs more than $10,000, which makes the error messages easy to demo.
_SEED_STOCKS = (
    ("SSOL", "Sonoran Solar Corp", 1000000, Decimal("42.50")),
    ("DSRT", "Desert Data Systems", 500000, Decimal("128.75")),
    ("CPTR", "Copper Trail Mining", 250000, Decimal("17.20")),
    ("MESA", "Mesa Aerospace Inc", 100000, Decimal("310.00")),
    ("SAGU", "Saguaro Rare Earths", 50, Decimal("5.00")),
    ("GCNY", "Grand Canyon Holdings", 10000, Decimal("12500.00")),
)

_WEEKDAYS = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)

# --- Market hours switch: keep ONE line active, then run `flask --app app init-db`
# (which also resets all data). Entry: day -> (open, close, closed all day? 1 = yes).
TEST_SCHEDULE = {day: ("00:00:00", "23:59:59", 0) for day in _WEEKDAYS}  # open 24/7
BUSINESS_SCHEDULE = {  # weekdays 9:00-17:00, closed Saturday and Sunday
    day: ("09:00:00", "17:00:00", 1 if day in ("Saturday", "Sunday") else 0)
    for day in _WEEKDAYS
}

# ------------------------------------------------------------------------
MARKET_SCHEDULE = TEST_SCHEDULE          # <- active: open 24/7
# MARKET_SCHEDULE = BUSINESS_SCHEDULE    # <- weekdays 9-5, closed Sat/Sun
# ------------------------------------------------------------------------

def _require_env(name):
    """Return the value of a required env var, or raise a clear error."""
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"{name} is not set. Set it in the environment (or .env) before "
            f"seeding; the seed loader will not invent a default password."
        )
    return value


def seed_into(conn):
    """Insert all the demo data into an empty (tables-only) database."""
    # One timestamp for the whole seed run.
    now = now_utc().isoformat()

    # Passwords come from .env and are stored only as hashes (as required by handbook).
    admin_password_hash = generate_password_hash(_require_env("SEED_ADMIN_PASSWORD"))
    customer_password_hash = generate_password_hash(_require_env("SEED_CUSTOMER_PASSWORD"))

    # --- Admin user ------------------------------------------------------
    admin_user_id = db.insert_user(
        conn,
        first_name="Ada",
        last_name="Admin",
        email="admin@example.com",
        username="admin",
        password_hash=admin_password_hash,
        user_type="admin",
        cash_balance=Decimal("0.00"),
        is_active=1,
        created_at=now,
        updated_at=now,
    )

    # --- Customer user (the fixed demo trainee) --------------------------
    customer_user_id = db.insert_user(
        conn,
        first_name="Taylor",
        last_name="Trainee",
        email="trainee1@example.com",
        username="trainee1",
        password_hash=customer_password_hash,
        user_type="customer",
        cash_balance=Decimal("10000.00"),
        is_active=1,
        created_at=now,
        updated_at=now,
    )

    # --- Six stocks, all created by the admin ----------------------------
    for ticker, company_name, shares_outstanding, price in _SEED_STOCKS:
        db.insert_stock(
            conn,
            ticker_symbol=ticker,
            company_name=company_name,
            company_shares_outstanding=shares_outstanding,
            current_price=price,
            description=None,
            logo_filename=None,
            created_by=admin_user_id,
            created_at=now,
            price_updated_at=now,
        )

    # --- Market hours (from the MARKET_SCHEDULE switch near the top) ---
    for day in _WEEKDAYS:
        open_time, close_time, closed_all_day = MARKET_SCHEDULE[day]
        db.insert_market_schedule_row(
            conn,
            created_by_admin_id=admin_user_id,
            day_of_week=day,
            market_open_time=open_time,
            market_close_time=close_time,
            is_holiday=closed_all_day,
            created_at=now,
            updated_at=now,
        )

    # --- One market_settings row (no override) ---------------------------
    db.insert_market_settings(
        conn,
        override_mode="none",
        updated_by=None,
        updated_at=now,
    )

    # holdings and transactions are intentionally left empty.

    return {"admin_user_id": admin_user_id, "customer_user_id": customer_user_id}

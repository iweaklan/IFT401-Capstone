"""Settings, read from environment variables or the .env file."""

import os

# Load settings from a local .env file, if there is one.
from dotenv import load_dotenv

load_dotenv()


def _positive_int(name, default):
    """Read a whole-number setting from the environment, with a clear error if it is not one."""
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        value = 0
    if value < 1:
        raise RuntimeError(f"{name} must be a whole number of 1 or more (got {raw!r}).")
    return value


class Config:
    """Flask configuration object built from environment variables."""

    # mysql://user:password@host:port/dbname (the real value belongs in .env file).
    DATABASE_URL = os.environ.get(
        "DATABASE_URL", "mysql://stock_app@localhost:3306/stock_trading"
    )

    # Required: signs sessions and CSRF tokens.
    SECRET_KEY = os.environ.get("SECRET_KEY")

    # Time zone for market hours and displayed times.
    MARKET_TIMEZONE = os.environ.get("MARKET_TIMEZONE", "America/New_York")

    # Price generator: seconds between updates; largest move in basis points (50 = 0.5%).
    PRICE_TICK_SECONDS = _positive_int("PRICE_TICK_SECONDS", 60)
    PRICE_MAX_STEP_BPS = _positive_int("PRICE_MAX_STEP_BPS", 50)

    # Seed passwords, read by seed.py and stored only as Werkzeug hashes.
    SEED_ADMIN_PASSWORD = os.environ.get("SEED_ADMIN_PASSWORD")
    SEED_CUSTOMER_PASSWORD = os.environ.get("SEED_CUSTOMER_PASSWORD")

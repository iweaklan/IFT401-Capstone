"""Data access: the only module that runs SQL.

Every query uses ? placeholders (values are never pasted into SQL text), 
money is Decimal, and timestamps are stored as UTC.
"""

import os
from contextlib import contextmanager
from datetime import datetime, timezone
from urllib.parse import unquote, urlparse

import pymysql
from pymysql.constants import CLIENT, FIELD_TYPE
from pymysql.converters import conversions as _default_conversions

# schema.sql is in the project root, one level above app/.
_SCHEMA_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "schema.sql")


# --- Reading values back from MySQL ---


def _decode_datetime(text):
    """Turn a MySQL DATETIME string into an ISO-8601 UTC string."""
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc).isoformat()


def _decode_time(text):
    """Keep TIME columns as plain ``"HH:MM:SS"`` text (the market service parses it)."""
    return text


# PyMySQL's converters with DATETIME/TIMESTAMP/TIME overridden (DECIMAL already gives Decimal).
_CONVERSIONS = _default_conversions.copy()
_CONVERSIONS[FIELD_TYPE.DATETIME] = _decode_datetime
_CONVERSIONS[FIELD_TYPE.TIMESTAMP] = _decode_datetime
_CONVERSIONS[FIELD_TYPE.TIME] = _decode_time


def _ts(iso_text):
    """Convert an ISO-8601 timestamp string to a MySQL DATETIME(6) value (UTC)."""
    dt = datetime.fromisoformat(iso_text)
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.strftime("%Y-%m-%d %H:%M:%S.%f")


# --- Connection ---


class DuplicateError(Exception):
    """An INSERT/UPDATE broke a UNIQUE rule (e.g. the username or email already exists)."""


class Connection:
    """Thin wrapper around a PyMySQL connection."""

    def __init__(self, raw):
        self._raw = raw

    def execute(self, sql, params=()):
        """Run one statement and return the cursor (fetchone/fetchall/rowcount/lastrowid)."""
        cur = self._raw.cursor()
        try:
            cur.execute(sql.replace("?", "%s"), tuple(params))
        except pymysql.err.IntegrityError as exc:
            if exc.args and exc.args[0] == 1062:
                raise DuplicateError(str(exc)) from exc
            raise
        return cur

    def close(self):
        self._raw.close()


def parse_database_url(url):
    """Split ``mysql://user:password@host:port/dbname`` into its parts."""
    parsed = urlparse(url)
    if not (parsed.scheme or "").startswith("mysql"):
        raise ValueError("DATABASE_URL must start with 'mysql://'")
    database = (parsed.path or "").lstrip("/")
    if not database:
        raise ValueError("DATABASE_URL must name a database, e.g. mysql://user:pw@localhost:3306/stock_trading")
    return {
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 3306,
        "user": unquote(parsed.username or ""),
        "password": unquote(parsed.password or ""),
        "database": database,
    }


def get_connection(database_url):
    """Open a MySQL connection configured the way every rule requires."""
    p = parse_database_url(database_url)
    raw = pymysql.connect(
        host=p["host"],
        port=p["port"],
        user=p["user"],
        password=p["password"],
        database=p["database"],
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
        client_flag=CLIENT.FOUND_ROWS,
        conv=_CONVERSIONS,
        init_command="SET time_zone = '+00:00', SESSION transaction_isolation = 'READ-COMMITTED'",
    )
    return Connection(raw)


@contextmanager
def transaction(conn):
    """Run a block inside one all-or-nothing transaction."""
    conn.execute("START TRANSACTION")
    try:
        yield conn
        conn.execute("COMMIT")
    except Exception as exc:
        # PriceChangedError keeps its one change (deleting the stale order); anything else rolls back.
        if getattr(exc, "keep_changes", False):
            conn.execute("COMMIT")
        else:
            conn.execute("ROLLBACK")
        raise


# --- Schema setup ---

# Fixed statements, children before parents.
_DROP_STATEMENTS = (
    "DROP TABLE IF EXISTS transactions",
    "DROP TABLE IF EXISTS holdings",
    "DROP TABLE IF EXISTS market_schedule",
    "DROP TABLE IF EXISTS market_settings",
    "DROP TABLE IF EXISTS stocks",
    "DROP TABLE IF EXISTS users",
)


def init_db(conn):
    """Create the schema by running every statement in schema.sql."""
    with open(_SCHEMA_PATH, "r", encoding="utf-8") as f:
        schema_sql = f.read()
    for statement in schema_sql.split(";"):
        if statement.strip():
            conn.execute(statement)


def drop_all_tables(conn):
    """Drop every project table (foreign-key checks are paused while doing it)."""
    conn.execute("SET FOREIGN_KEY_CHECKS = 0")
    try:
        for statement in _DROP_STATEMENTS:
            conn.execute(statement)
    finally:
        conn.execute("SET FOREIGN_KEY_CHECKS = 1")


# --- Reads ---


def get_user_by_id(conn, user_id, for_update=False):
    """Return one user row by id, or None if there is no such user."""
    if for_update:
        cur = conn.execute(
            "SELECT * FROM users WHERE user_id = ? FOR UPDATE",
            (user_id,),
        )
    else:
        cur = conn.execute(
            "SELECT * FROM users WHERE user_id = ?",
            (user_id,),
        )
    return cur.fetchone()


def get_user_by_username(conn, username):
    """Return one user row by username, or None. Used by login and signup."""
    cur = conn.execute(
        "SELECT * FROM users WHERE username = ?",
        (username,),
    )
    return cur.fetchone()


def get_user_by_email(conn, email):
    """Return one user row by email, or None. (MySQL compares emails case-insensitively.)"""
    cur = conn.execute(
        "SELECT * FROM users WHERE email = ?",
        (email,),
    )
    return cur.fetchone()


def get_stock(conn, stock_id, for_update=False):
    """Return one stock row by id, or None if there is no such stock."""
    if for_update:
        cur = conn.execute(
            "SELECT * FROM stocks WHERE stock_id = ? FOR UPDATE",
            (stock_id,),
        )
    else:
        cur = conn.execute(
            "SELECT * FROM stocks WHERE stock_id = ?",
            (stock_id,),
        )
    return cur.fetchone()


def list_stocks(conn):
    """Return every stock row, ordered by ticker, for the dashboard table."""
    cur = conn.execute(
        "SELECT * FROM stocks ORDER BY ticker_symbol"
    )
    return cur.fetchall()


def get_shares_held_sum(conn, stock_id):
    """Return the total shares of a stock held across all users."""
    cur = conn.execute(
        "SELECT COALESCE(SUM(quantity), 0) AS held FROM holdings WHERE stock_id = ?",
        (stock_id,),
    )
    row = cur.fetchone()
    return int(row["held"])


def list_holdings_for_user(conn, user_id):
    """Return the user's holdings joined to their stock, for the portfolio panel."""
    cur = conn.execute(
        """
        SELECT
            h.holdings_id,
            h.user_id,
            h.stock_id,
            h.quantity,
            h.created_at,
            h.updated_at,
            s.ticker_symbol,
            s.company_name,
            s.current_price
        FROM holdings AS h
        JOIN stocks AS s ON s.stock_id = h.stock_id
        WHERE h.user_id = ?
        ORDER BY s.ticker_symbol
        """,
        (user_id,),
    )
    return cur.fetchall()


def get_holding(conn, user_id, stock_id):
    """Return the user's holding row for one stock, or None."""
    cur = conn.execute(
        "SELECT * FROM holdings WHERE user_id = ? AND stock_id = ?",
        (user_id, stock_id),
    )
    return cur.fetchone()


def get_recent_executed_transactions(conn, user_id, limit):
    """Return the user's executed transactions, newest first, up to ``limit``."""
    cur = conn.execute(
        """
        SELECT
            t.transaction_id,
            t.user_id,
            t.stock_id,
            t.transaction_type,
            t.quantity,
            t.price_per_share,
            t.total_amount,
            t.status,
            t.created_at,
            t.confirmed_at,
            t.executed_at,
            s.ticker_symbol
        FROM transactions AS t
        LEFT JOIN stocks AS s ON s.stock_id = t.stock_id
        WHERE t.user_id = ? AND t.status = 'executed'
        ORDER BY t.executed_at DESC
        LIMIT ?
        """,
        (user_id, limit),
    )
    return cur.fetchall()


def get_market_settings(conn):
    """Return the single market_settings row (setting_id = 1), or None."""
    cur = conn.execute(
        "SELECT * FROM market_settings WHERE setting_id = ?",
        (1,),
    )
    return cur.fetchone()


def get_schedule_for_day(conn, day_of_week):
    """Return the market_schedule row for a weekday name, or None."""
    cur = conn.execute(
        "SELECT * FROM market_schedule WHERE day_of_week = ?",
        (day_of_week,),
    )
    return cur.fetchone()


# count_rows puts a table NAME into the SQL text, so only these fixed names are allowed.
_COUNT_ROWS_WHITELIST = frozenset(
    {"users", "stocks", "holdings", "transactions", "market_schedule", "market_settings"}
)


def count_rows(conn, table):
    """Return the number of rows in a whitelisted table."""
    if table not in _COUNT_ROWS_WHITELIST:
        raise ValueError(f"count_rows: table {table!r} is not in the whitelist")
    # Safe: ``table`` is proven to be a member of the hard-coded whitelist above.
    cur = conn.execute(f"SELECT COUNT(*) AS n FROM {table}")
    row = cur.fetchone()
    return int(row["n"])

# --- Inserts (callers pass Decimal money and ISO-8601 timestamps) ---


def insert_user(
    conn,
    *,
    first_name,
    last_name,
    email,
    username,
    password_hash,
    user_type,
    cash_balance,
    is_active,
    created_at,
    updated_at,
):
    """Insert one ``users`` row and return its new ``user_id``."""
    cur = conn.execute(
        """
        INSERT INTO users (
            first_name, last_name, email, username, password_hash,
            user_type, cash_balance, is_active, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            first_name,
            last_name,
            email,
            username,
            password_hash,
            user_type,
            cash_balance,
            is_active,
            _ts(created_at),
            _ts(updated_at),
        ),
    )
    return cur.lastrowid


def insert_stock(
    conn,
    *,
    ticker_symbol,
    company_name,
    company_shares_outstanding,
    current_price,
    description,
    logo_filename,
    created_by,
    created_at,
    price_updated_at,
):
    """Insert one ``stocks`` row and return its new ``stock_id``."""
    cur = conn.execute(
        """
        INSERT INTO stocks (
            ticker_symbol, company_name, company_shares_outstanding,
            current_price, description, logo_filename, created_by,
            created_at, price_updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            ticker_symbol,
            company_name,
            company_shares_outstanding,
            current_price,
            description,
            logo_filename,
            created_by,
            _ts(created_at),
            _ts(price_updated_at),
        ),
    )
    return cur.lastrowid


def insert_market_schedule_row(
    conn,
    *,
    created_by_admin_id,
    day_of_week,
    market_open_time,
    market_close_time,
    is_holiday,
    created_at,
    updated_at,
):
    """Insert one ``market_schedule`` row (one weekday) and return its id."""
    cur = conn.execute(
        """
        INSERT INTO market_schedule (
            created_by_admin_id, day_of_week, market_open_time,
            market_close_time, is_holiday, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            created_by_admin_id,
            day_of_week,
            market_open_time,
            market_close_time,
            is_holiday,
            _ts(created_at),
            _ts(updated_at),
        ),
    )
    return cur.lastrowid


def insert_market_settings(
    conn,
    *,
    override_mode,
    updated_by,
    updated_at,
):
    """Insert the single ``market_settings`` row (setting_id is fixed at 1)."""
    conn.execute(
        """
        INSERT INTO market_settings (setting_id, override_mode, updated_by, updated_at)
        VALUES (?, ?, ?, ?)
        """,
        (1, override_mode, updated_by, _ts(updated_at)),
    )

# --- Orders: pending (reviewed) -> confirmed -> executed (money and shares moved) ---
# Confirmed and executed rows are never changed or deleted.


def insert_pending_buy(
    conn,
    user_id,
    stock_id,
    quantity,
    price_per_share,
    total_amount,
    created_at,
):
    """Insert one pending ``buy`` transactions row and return its new id."""
    cur = conn.execute(
        """
        INSERT INTO transactions (
            user_id, stock_id, transaction_type, quantity,
            price_per_share, total_amount, status,
            created_at, confirmed_at, executed_at
        )
        VALUES (?, ?, 'buy', ?, ?, ?, 'pending', ?, NULL, NULL)
        """,
        (
            user_id,
            stock_id,
            quantity,
            price_per_share,
            total_amount,
            _ts(created_at),
        ),
    )
    return cur.lastrowid

# --- Confirm writes: called inside one transaction(); only 'pending' rows can change ---


def get_transaction_for_update(conn, transaction_id):
    """Re-read one transaction row by id inside the confirm transaction."""
    cur = conn.execute(
        "SELECT * FROM transactions WHERE transaction_id = ? FOR UPDATE",
        (transaction_id,),
    )
    return cur.fetchone()


def guarded_confirm_update(conn, transaction_id, confirmed_at):
    """Move a transaction from ``pending`` to ``confirmed`` and report the count."""
    cur = conn.execute(
        """
        UPDATE transactions
        SET status = 'confirmed', confirmed_at = ?
        WHERE transaction_id = ? AND status = 'pending'
        """,
        (_ts(confirmed_at), transaction_id),
    )
    return cur.rowcount


def update_user_cash(conn, user_id, new_balance, updated_at):
    """Write a Python-computed new cash balance for one user."""
    conn.execute(
        "UPDATE users SET cash_balance = ?, updated_at = ? WHERE user_id = ?",
        (new_balance, _ts(updated_at), user_id),
    )


def upsert_holding(conn, user_id, stock_id, quantity, now):
    """Add ``quantity`` shares to the user's holding of a stock, or create it."""
    now = _ts(now)
    existing = get_holding(conn, user_id, stock_id)
    if existing is None:
        conn.execute(
            """
            INSERT INTO holdings (user_id, stock_id, quantity, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user_id, stock_id, quantity, now, now),
        )
    else:
        new_quantity = int(existing["quantity"]) + int(quantity)
        conn.execute(
            "UPDATE holdings SET quantity = ?, updated_at = ? WHERE user_id = ? AND stock_id = ?",
            (new_quantity, now, user_id, stock_id),
        )


def set_transaction_executed(conn, transaction_id, executed_at):
    """Set a transaction's status to ``executed`` and stamp ``executed_at``."""
    conn.execute(
        "UPDATE transactions SET status = 'executed', executed_at = ? WHERE transaction_id = ?",
        (_ts(executed_at), transaction_id),
    )


def delete_pending_transaction(conn, transaction_id):
    """Delete a transaction row only if it is still ``pending``."""
    conn.execute(
        "DELETE FROM transactions WHERE transaction_id = ? AND status = 'pending'",
        (transaction_id,),
    )


# --- Sells (selling the last share deletes the holding row) ---


def insert_pending_sell(
    conn,
    user_id,
    stock_id,
    quantity,
    price_per_share,
    total_amount,
    created_at,
):
    """Insert one pending ``sell`` transactions row and return its new id."""
    cur = conn.execute(
        """
        INSERT INTO transactions (
            user_id, stock_id, transaction_type, quantity,
            price_per_share, total_amount, status,
            created_at, confirmed_at, executed_at
        )
        VALUES (?, ?, 'sell', ?, ?, ?, 'pending', ?, NULL, NULL)
        """,
        (user_id, stock_id, quantity, price_per_share, total_amount, _ts(created_at)),
    )
    return cur.lastrowid


# --- Cash deposits/withdrawals (same life cycle as orders; no stock, quantity or price) ---


def insert_pending_cash(conn, user_id, transaction_type, amount, created_at):
    """Insert one pending ``deposit`` or ``withdrawal`` row and return its new id."""
    cur = conn.execute(
        """
        INSERT INTO transactions (
            user_id, stock_id, transaction_type, quantity,
            price_per_share, total_amount, status,
            created_at, confirmed_at, executed_at
        )
        VALUES (?, NULL, ?, NULL, NULL, ?, 'pending', ?, NULL, NULL)
        """,
        (user_id, transaction_type, amount, _ts(created_at)),
    )
    return cur.lastrowid


def reduce_holding(conn, user_id, stock_id, quantity, now):
    """Decrease a user's holding of a stock by ``quantity`` shares."""
    now = _ts(now)
    existing = get_holding(conn, user_id, stock_id)
    if existing is None:
        raise ValueError("reduce_holding called with no existing holding")
    new_quantity = int(existing["quantity"]) - int(quantity)
    if new_quantity < 0:
        raise ValueError("reduce_holding would make quantity negative")
    if new_quantity == 0:
        conn.execute(
            "DELETE FROM holdings WHERE user_id = ? AND stock_id = ?",
            (user_id, stock_id),
        )
    else:
        conn.execute(
            "UPDATE holdings SET quantity = ?, updated_at = ? WHERE user_id = ? AND stock_id = ?",
            (new_quantity, now, user_id, stock_id),
        )


# --- Administrator functions (none of these can change a price) ---


def get_stock_by_ticker(conn, ticker_symbol):
    """Return one stock row by ticker symbol, or None."""
    cur = conn.execute(
        "SELECT * FROM stocks WHERE ticker_symbol = ?",
        (ticker_symbol,),
    )
    return cur.fetchone()


def update_stock_details(conn, stock_id, company_name, description, logo_filename, shares_outstanding):
    """Change a stock's editable details. There is deliberately no price parameter."""
    conn.execute(
        """
        UPDATE stocks
        SET company_name = ?, description = ?, logo_filename = ?, company_shares_outstanding = ?
        WHERE stock_id = ?
        """,
        (company_name, description, logo_filename, shares_outstanding, stock_id),
    )


def list_market_schedule(conn):
    """Return the seven market_schedule rows in calendar order (Monday first)."""
    cur = conn.execute(
        """
        SELECT * FROM market_schedule
        ORDER BY FIELD(day_of_week, 'Monday', 'Tuesday', 'Wednesday', 'Thursday',
                       'Friday', 'Saturday', 'Sunday')
        """
    )
    return cur.fetchall()


def set_day_hours(conn, day_of_week, open_time, close_time, updated_at):
    """Open a weekday between two 'HH:MM:SS' times (and clear its closed-all-day flag)."""
    conn.execute(
        """
        UPDATE market_schedule
        SET market_open_time = ?, market_close_time = ?, is_holiday = 0, updated_at = ?
        WHERE day_of_week = ?
        """,
        (open_time, close_time, _ts(updated_at), day_of_week),
    )


def set_day_closed(conn, day_of_week, updated_at):
    """Mark a weekday closed all day (is_holiday = 1), keeping its stored hours."""
    conn.execute(
        "UPDATE market_schedule SET is_holiday = 1, updated_at = ? WHERE day_of_week = ?",
        (_ts(updated_at), day_of_week),
    )


def set_market_override(conn, override_mode, updated_by, updated_at):
    """Set the manual market override: 'none', 'force_open' or 'force_closed'."""
    conn.execute(
        "UPDATE market_settings SET override_mode = ?, updated_by = ?, updated_at = ? WHERE setting_id = ?",
        (override_mode, updated_by, _ts(updated_at), 1),
    )


# --- Price generator: the ONLY code that changes a price after creation ---


def update_stock_price(conn, stock_id, new_price, updated_at):
    """Set a stock's new market price (a Decimal computed in Python) and stamp when it changed."""
    conn.execute(
        "UPDATE stocks SET current_price = ?, price_updated_at = ? WHERE stock_id = ?",
        (new_price, _ts(updated_at), stock_id),
    )
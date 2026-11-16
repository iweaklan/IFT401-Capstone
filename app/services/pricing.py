"""Random price generator.

While the market is open, each tick moves every stock's price by a random step of up to
``max_step_bps`` basis points (50 = 0.5%). While it is closed, nothing changes.
"""

import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from app import db
from app.money import MAX_MONEY, quantize, to_money
from app.services import market

# Defaults (.env can override the first two).
DEFAULT_TICK_SECONDS = 60     # how long to wait between rounds of updates
DEFAULT_MAX_STEP_BPS = 50     # biggest single move: 50 bps = 0.5%
MAX_ALLOWED_STEP_BPS = 1000   # safety cap: nobody may configure more than 10% per move

MIN_PRICE = Decimal("0.01")   # the database requires price > 0, so never go below 1 cent
MAX_PRICE = MAX_MONEY         # 99,999,999.99: the largest number the price column can hold

_BPS_BASE = Decimal(10000)    # 10000 bps = 100%; dividing by this turns bps into a fraction


@dataclass(frozen=True)
class TickResult:
    """A small report of what one round (tick) did, printed by the command."""

    market_open: bool     # False means the round was skipped because the market was closed
    stocks_updated: int   # how many stocks were visited (each got a new "price updated" time)
    prices_changed: int   # of those, how many ended up with a DIFFERENT price (a roll of 0 = same price)


def next_price(current, rng, max_step_bps=DEFAULT_MAX_STEP_BPS):
    """Return the stock's next price: ``current`` moved by a random step of up to ``max_step_bps``."""
    # Refuse silly settings (for example a 0% or a 90% step) before doing anything.
    if not 1 <= max_step_bps <= MAX_ALLOWED_STEP_BPS:
        raise ValueError(f"max_step_bps must be between 1 and {MAX_ALLOWED_STEP_BPS}")

    # Make sure we are working with an exact Decimal (it may arrive as text).
    current = to_money(current)

    # Random whole number of basis points, e.g. -50..+50 (up and down equally likely).
    bps = rng.randint(-max_step_bps, max_step_bps)

    # Apply it as a percentage and round to the cent: +37 bps -> x 1.0037.
    moved = quantize(current * (_BPS_BASE + bps) / _BPS_BASE)

    # A non-zero roll that rounded to no change still moves the price one cent.
    if moved == current and bps != 0:
        moved = current + (MIN_PRICE if bps > 0 else -MIN_PRICE)

    # Keep the price between $0.01 and the column maximum.
    return min(max(moved, MIN_PRICE), MAX_PRICE)


def tick(conn, now, tz_name, rng, max_step_bps=DEFAULT_MAX_STEP_BPS):
    """Run one round of price updates. Does NOTHING while the market is closed."""
    # Same check the trading code uses: if closed, change nothing at all.
    if not market.is_market_open(conn, now, tz_name):
        return TickResult(market_open=False, stocks_updated=0, prices_changed=0)

    stamp = now.isoformat()  # e.g. "2026-03-02T15:00:00+00:00"; stored as the update time
    visited = 0
    changed = 0

    # Every stock, including newly created ones.
    for row in db.list_stocks(conn):
        # One short transaction per stock.
        with db.transaction(conn):
            # Lock the row and re-read the price so no trade on this stock runs at the same moment.
            stock = db.get_stock(conn, row["stock_id"], for_update=True)
            if stock is None:
                continue  # defensive: stock removed in the meantime
            old = to_money(stock["current_price"])
            new = next_price(old, rng, max_step_bps)
            # The only call in the app that changes a price after creation.
            db.update_stock_price(conn, stock["stock_id"], new, stamp)
        # (leaving the "with" block commits and releases the lock)
        visited += 1
        if new != old:
            changed += 1
    return TickResult(market_open=True, stocks_updated=visited, prices_changed=changed)


def run_loop(
    open_connection,
    tz_name,
    seconds=DEFAULT_TICK_SECONDS,
    max_step_bps=DEFAULT_MAX_STEP_BPS,
    rng=None,
    sleep=None,
    now_fn=None,
    on_tick=None,
    on_error=None,
    max_ticks=None,
):
    """Call ``tick`` every ``seconds`` seconds, forever (or ``max_ticks`` times)."""
    # Defaults are filled in at call time so tests can replace them.
    rng = rng or random.Random()
    sleep = sleep or time.sleep
    now_fn = now_fn or (lambda: datetime.now(timezone.utc))

    attempted = 0
    while max_ticks is None or attempted < max_ticks:
        attempted += 1
        try:
            conn = open_connection()
            try:
                result = tick(conn, now_fn(), tz_name, rng, max_step_bps)
            finally:
                conn.close()  # always give the connection back, even after an error
            if on_tick:
                on_tick(result)
        except Exception as exc:  # noqa: BLE001 - keep the generator alive whatever goes wrong
            if on_error:
                on_error(exc)
        # No wait after the last round, so --once exits immediately.
        if max_ticks is None or attempted < max_ticks:
            sleep(seconds)
    return attempted

"""Buy and sell rules.

An order is saved as pending at review, then confirmed in one locked transaction that
re-checks the price, market hours, shares and cash before moving anything.
"""

from dataclasses import dataclass
from decimal import Decimal

from app import db
from app.money import MAX_MONEY, quantize, to_money
from app.services import market

# The largest whole-share quantity a single buy may request.
MAX_QUANTITY = 1_000_000


class ValidationError(Exception):
    """Input or business-rule check failed."""


class MarketClosedError(Exception):
    """Trade attempted while the market is closed."""


class InsufficientFundsError(Exception):
    """Cash balance is less than the order total."""

    def __init__(self, total: Decimal, balance: Decimal):
        self.total = total
        self.balance = balance
        super().__init__(
            f"Order total {total} exceeds cash balance {balance}."
        )


class InsufficientSharesError(Exception):
    """Requested quantity exceeds the shares available to buy."""

    def __init__(self, available: int, ticker: str):
        self.available = available
        self.ticker = ticker
        super().__init__(
            f"Only {available} shares of {ticker} are available."
        )


class PriceChangedError(Exception):
    """Stock price changed between review and confirm."""

    keep_changes = True

    def __init__(self, ticker: str, old: Decimal, new: Decimal):
        self.ticker = ticker
        self.old = old
        self.new = new
        super().__init__(
            f"The price of {ticker} changed from {old} to {new}."
        )


def parse_quantity(raw) -> int:
    """Parse a share quantity from raw form input into a whole integer."""
    s = str(raw).strip()
    # Only plain ASCII digits pass (rejects '', '-1', '1.5', '1e3' and look-alike digits).
    if not s.isdigit() or not all(c in "0123456789" for c in s):
        raise ValidationError(
            "Please enter a whole number of shares greater than 0."
        )
    value = int(s)
    if value < 1:
        # Covers "0" (and "000") which are digit strings but not > 0.
        raise ValidationError(
            "Please enter a whole number of shares greater than 0."
        )
    if value > MAX_QUANTITY:
        raise ValidationError(
            "Please enter a whole number of shares no more than 1,000,000."
        )
    return value


def compute_total_cost(price, quantity: int) -> Decimal:
    """Compute the exact buy total: ``quantize(price * quantity)``."""
    total = quantize(to_money(price) * quantity)
    if total > MAX_MONEY:
        raise ValidationError("Order total is too large.")
    return total


@dataclass(frozen=True)
class Quote:
    """The read-only result of pricing a buy order."""

    stock: dict
    quantity: int
    price_per_share: Decimal
    total_cost: Decimal
    cash_before: Decimal
    cash_after: Decimal


def _validate_buy(conn, user_id, stock_id, quantity, now, tz_name):
    """Run the shared buy checks in the fixed order; the first failure is raised."""
    # 1) Stock exists.
    stock = db.get_stock(conn, stock_id)
    if stock is None:
        raise ValidationError("Stock not found.")

    # 2) Quantity valid: the caller passed an already-parsed int. Nothing to do.

    # 3) Market open.
    if not market.is_market_open(conn, now, tz_name):
        raise MarketClosedError()

    # 4) Shares available = outstanding - already-held.
    shares_available = (
        int(stock["company_shares_outstanding"])
        - db.get_shares_held_sum(conn, stock_id)
    )
    if shares_available < quantity:
        raise InsufficientSharesError(
            available=shares_available, ticker=stock["ticker_symbol"]
        )

    # 5) Funds. compute_total_cost may raise ValidationError on overflow.
    total = compute_total_cost(stock["current_price"], quantity)
    user = db.get_user_by_id(conn, user_id)
    cash_before = to_money(user["cash_balance"])
    if cash_before < total:
        raise InsufficientFundsError(total=total, balance=cash_before)

    return stock, user, total, cash_before


def quote_buy(conn, user_id, stock_id, quantity, now,
              tz_name=market.DEFAULT_MARKET_TIMEZONE):
    """Price a buy order without writing anything to the database."""
    stock, _user, total, cash_before = _validate_buy(
        conn, user_id, stock_id, quantity, now, tz_name
    )

    price_per_share = to_money(stock["current_price"])
    return Quote(
        stock=dict(stock),
        quantity=quantity,
        price_per_share=price_per_share,
        total_cost=total,
        cash_before=cash_before,
        cash_after=cash_before - total,
    )


def create_pending_buy(conn, user_id, stock_id, quantity, now,
                       tz_name=market.DEFAULT_MARKET_TIMEZONE):
    """Record a single pending buy order and return its new ``transaction_id``."""
    stock, _user, total, _cash_before = _validate_buy(
        conn, user_id, stock_id, quantity, now, tz_name
    )

    price_per_share = to_money(stock["current_price"])
    return db.insert_pending_buy(
        conn,
        user_id,
        stock_id,
        quantity,
        price_per_share,
        total,
        now.isoformat(),
    )

def confirm_buy(conn, user_id, transaction_id, now,
                tz_name=market.DEFAULT_MARKET_TIMEZONE):
    """Confirm and execute a pending buy as one all-or-nothing operation."""
    with db.transaction(conn):
        tx = db.get_transaction_for_update(conn, transaction_id)
        if (
            tx is None
            or tx["user_id"] != user_id
            or tx["transaction_type"] != "buy"
            or tx["status"] != "pending"
        ):
            raise ValidationError(
                "This order has already been processed or does not exist."
            )

        # 2) Market must still be open.
        if not market.is_market_open(conn, now, tz_name):
            raise MarketClosedError()

        # Lock the stock row (and later the user row) until this transaction ends.
        stock = db.get_stock(conn, tx["stock_id"], for_update=True)
        # Stock could in principle be gone; treat as a validation failure.
        if stock is None:
            raise ValidationError("Stock not found.")

        # 3) Price must be unchanged since review. Compare as Decimals.
        reviewed_price = to_money(tx["price_per_share"])
        current_price = to_money(stock["current_price"])
        if current_price != reviewed_price:
            # Delete the stale order and ask the customer to review again.
            db.delete_pending_transaction(conn, transaction_id)
            raise PriceChangedError(
                ticker=stock["ticker_symbol"],
                old=reviewed_price,
                new=current_price,
            )

        quantity = int(tx["quantity"])
        total = to_money(tx["total_amount"])

        # 4) Re-check shares available and funds inside the transaction.
        shares_available = (
            int(stock["company_shares_outstanding"])
            - db.get_shares_held_sum(conn, tx["stock_id"])
        )
        if shares_available < quantity:
            raise InsufficientSharesError(
                available=shares_available, ticker=stock["ticker_symbol"]
            )

        user = db.get_user_by_id(conn, user_id, for_update=True)
        cash_before = to_money(user["cash_balance"])
        if cash_before < total:
            raise InsufficientFundsError(total=total, balance=cash_before)

        # 5) Guarded confirm: exactly one pending row must flip to confirmed.
        now_iso = now.isoformat()
        affected = db.guarded_confirm_update(conn, transaction_id, now_iso)
        if affected != 1:
            # Already confirmed by another click: change nothing.
            raise ValidationError(
                "This order has already been processed or does not exist."
            )

        # 6) Apply the purchase: debit cash (Python math), add shares, execute.
        new_cash = cash_before - total
        db.update_user_cash(conn, user_id, new_cash, now_iso)
        db.upsert_holding(conn, user_id, tx["stock_id"], quantity, now_iso)
        db.set_transaction_executed(conn, transaction_id, now_iso)
        # Commit happens on clean exit of the transaction() context manager.


def cancel_pending_buy(conn, user_id, transaction_id):
    """Cancel a pending buy that belongs to the user."""
    tx = db.get_transaction_for_update(conn, transaction_id)
    if (
        tx is None
        or tx["user_id"] != user_id
        or tx["transaction_type"] != "buy"
        or tx["status"] != "pending"
    ):
        return
    # delete_pending_transaction is itself scoped to status='pending'.
    db.delete_pending_transaction(conn, transaction_id)


# --- Sell flow (mirrors buy; checks: stock exists, market open, enough shares owned) ---


def _validate_sell(conn, user_id, stock_id, quantity, now, tz_name):
    """Run the shared sell validations in the fixed order."""
    # 1) Stock exists.
    stock = db.get_stock(conn, stock_id)
    if stock is None:
        raise ValidationError("Stock not found.")

    # 2) Quantity already parsed to a valid int by the caller.

    # 3) Market open.
    if not market.is_market_open(conn, now, tz_name):
        raise MarketClosedError()

    # 4) User must own at least `quantity` shares.
    holding = db.get_holding(conn, user_id, stock_id)
    owned = int(holding["quantity"]) if holding is not None else 0
    if owned < quantity:
        raise InsufficientSharesError(available=owned, ticker=stock["ticker_symbol"])

    proceeds = compute_total_cost(stock["current_price"], quantity)
    return stock, owned, proceeds


def quote_sell(conn, user_id, stock_id, quantity, now,
               tz_name=market.DEFAULT_MARKET_TIMEZONE):
    """Price a sell order without writing anything to the database."""
    stock, _owned, proceeds = _validate_sell(
        conn, user_id, stock_id, quantity, now, tz_name
    )
    user = db.get_user_by_id(conn, user_id)
    cash_before = to_money(user["cash_balance"])
    price_per_share = to_money(stock["current_price"])
    return Quote(
        stock=dict(stock),
        quantity=quantity,
        price_per_share=price_per_share,
        total_cost=proceeds,
        cash_before=cash_before,
        cash_after=cash_before + proceeds,
    )


def create_pending_sell(conn, user_id, stock_id, quantity, now,
                        tz_name=market.DEFAULT_MARKET_TIMEZONE):
    """Record one pending sell order and return its new ``transaction_id``."""
    stock, _owned, proceeds = _validate_sell(
        conn, user_id, stock_id, quantity, now, tz_name
    )
    price_per_share = to_money(stock["current_price"])
    return db.insert_pending_sell(
        conn,
        user_id,
        stock_id,
        quantity,
        price_per_share,
        proceeds,
        now.isoformat(),
    )


def confirm_sell(conn, user_id, transaction_id, now,
                 tz_name=market.DEFAULT_MARKET_TIMEZONE):
    """Confirm and execute a pending sell as one all-or-nothing operation."""
    with db.transaction(conn):
        tx = db.get_transaction_for_update(conn, transaction_id)
        if (
            tx is None
            or tx["user_id"] != user_id
            or tx["transaction_type"] != "sell"
            or tx["status"] != "pending"
        ):
            raise ValidationError(
                "This order has already been processed or does not exist."
            )

        if not market.is_market_open(conn, now, tz_name):
            raise MarketClosedError()

        # Lock the stock row (and later the user row) until this transaction ends.
        stock = db.get_stock(conn, tx["stock_id"], for_update=True)
        if stock is None:
            raise ValidationError("Stock not found.")

        # Price unchanged since review.
        reviewed_price = to_money(tx["price_per_share"])
        current_price = to_money(stock["current_price"])
        if current_price != reviewed_price:
            db.delete_pending_transaction(conn, transaction_id)
            raise PriceChangedError(
                ticker=stock["ticker_symbol"],
                old=reviewed_price,
                new=current_price,
            )

        quantity = int(tx["quantity"])
        proceeds = to_money(tx["total_amount"])

        # Re-check the user still owns enough shares.
        holding = db.get_holding(conn, user_id, tx["stock_id"])
        owned = int(holding["quantity"]) if holding is not None else 0
        if owned < quantity:
            raise InsufficientSharesError(
                available=owned, ticker=stock["ticker_symbol"]
            )

        # Guarded confirm: exactly one pending row flips to confirmed.
        now_iso = now.isoformat()
        affected = db.guarded_confirm_update(conn, transaction_id, now_iso)
        if affected != 1:
            raise ValidationError(
                "This order has already been processed or does not exist."
            )

        # Credit cash (Python Decimal); reject if it would overflow the column.
        user = db.get_user_by_id(conn, user_id, for_update=True)
        cash_before = to_money(user["cash_balance"])
        new_cash = cash_before + proceeds
        if new_cash > MAX_MONEY:
            raise ValidationError("Resulting cash balance is too large.")
        db.update_user_cash(conn, user_id, new_cash, now_iso)

        # Return shares to inventory by reducing/removing the holding.
        db.reduce_holding(conn, user_id, tx["stock_id"], quantity, now_iso)
        db.set_transaction_executed(conn, transaction_id, now_iso)


def cancel_pending_sell(conn, user_id, transaction_id):
    """Cancel a pending sell that belongs to the user (no-op otherwise)."""
    tx = db.get_transaction_for_update(conn, transaction_id)
    if (
        tx is None
        or tx["user_id"] != user_id
        or tx["transaction_type"] != "sell"
        or tx["status"] != "pending"
    ):
        return
    db.delete_pending_transaction(conn, transaction_id)

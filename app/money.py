"""Money helpers: exact Decimal values, rounding to cents, and $1,234.56 formatting."""

from decimal import Decimal, ROUND_HALF_UP

# All money is rounded/quantized to this many decimal places.
CENT = Decimal("0.01")

# Largest value any money column (DECIMAL(10,2)) can hold.
MAX_MONEY = Decimal("99999999.99")


def to_money(value) -> Decimal:
    """Build a Decimal from a string (or int). Never from float."""
    return Decimal(str(value))


def quantize(value: Decimal) -> Decimal:
    """Round to 2 decimal places using ROUND_HALF_UP."""
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def format_money(value: Decimal) -> str:
    """Format as $1,234.56 — leading $, comma thousands, exactly 2 decimals."""
    q = quantize(value)
    return "${:,.2f}".format(q)

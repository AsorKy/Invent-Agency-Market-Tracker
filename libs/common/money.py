"""Convert money between numeric(14,2) values and decimal strings.

Implements the E1.3 data taxonomy: money is ``numeric(14,2)`` in Postgres and a
decimal string in APIs, never a float. Used by services that serialize costs
and budgets.
"""

from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation

_CENT = Decimal("0.01")
_MAX_ABS = Decimal("999999999999.99")


def parse_money(value: str | int | Decimal) -> Decimal:
    """Parse a decimal string, int, or Decimal into a 2-place Decimal; floats are rejected."""

    if isinstance(value, bool) or not isinstance(value, str | int | Decimal):
        raise TypeError("money must be a decimal string, int, or Decimal, not float")
    try:
        amount = Decimal(value.strip() if isinstance(value, str) else value)
    except InvalidOperation as exc:
        raise ValueError(f"invalid money value: {value!r}") from exc
    if not amount.is_finite():
        raise ValueError("money must be finite")
    amount = amount.quantize(_CENT, rounding=ROUND_HALF_EVEN)
    if abs(amount) > _MAX_ABS:
        raise ValueError("money exceeds numeric(14,2)")
    return amount


def money_to_str(value: str | int | Decimal) -> str:
    """Return the canonical API string with exactly two decimals, e.g. ``"12.50"``."""

    return format(parse_money(value), "f")

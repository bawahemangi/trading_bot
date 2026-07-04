"""Pure input validation functions for order parameters.

Every function in this module is side-effect free (aside from logging) and
raises :class:`bot.exceptions.ValidationError` on invalid input. Keeping
validation logic here — separate from ``orders.py`` and ``cli.py`` — means
there is exactly one place that decides what a "valid" order field looks
like, satisfying the single-source-of-truth requirement.
"""

from __future__ import annotations

import logging
import re
from decimal import Decimal
from typing import Final

from bot.exceptions import ValidationError

logger = logging.getLogger(__name__)

VALID_SIDES: Final[frozenset[str]] = frozenset({"BUY", "SELL"})
VALID_ORDER_TYPES: Final[frozenset[str]] = frozenset({"MARKET", "LIMIT", "STOP_LIMIT"})
LIMIT_ORDER_TYPE: Final[str] = "LIMIT"
STOP_LIMIT_ORDER_TYPE: Final[str] = "STOP_LIMIT"

# Both LIMIT and STOP_LIMIT orders execute at a specified price; MARKET and
# the stop-trigger side of STOP_LIMIT do not use a plain "price" argument.
PRICE_REQUIRED_ORDER_TYPES: Final[frozenset[str]] = frozenset(
    {LIMIT_ORDER_TYPE, STOP_LIMIT_ORDER_TYPE}
)

# Binance Futures symbols are uppercase alphanumeric, e.g. BTCUSDT, ETHUSDT.
SYMBOL_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[A-Z0-9]+$")


def validate_symbol_format(symbol: str) -> str:
    """Validate and normalize a trading symbol's format.

    This only checks the *shape* of the symbol (uppercase, alphanumeric,
    non-empty). It does not confirm the symbol is actually listed on the
    exchange — see :meth:`bot.orders.OrderManager.ensure_symbol_is_tradable`
    for that authoritative check.

    Args:
        symbol: Raw symbol string supplied by the user (e.g. "btcusdt").

    Returns:
        The normalized, uppercase symbol.

    Raises:
        ValidationError: If the symbol is empty or contains invalid
            characters.

    """
    normalized = symbol.strip().upper()
    if not normalized or not SYMBOL_PATTERN.match(normalized):
        raise ValidationError(
            f"Invalid symbol format: '{symbol}'. Symbols must be "
            "alphanumeric, e.g. BTCUSDT."
        )
    return normalized


def validate_side(side: str) -> str:
    """Validate and normalize an order side.

    Args:
        side: Raw side string supplied by the user (e.g. "buy").

    Returns:
        The normalized side, either "BUY" or "SELL".

    Raises:
        ValidationError: If the side is not BUY or SELL.

    """
    normalized = side.strip().upper()
    if normalized not in VALID_SIDES:
        raise ValidationError(
            f"Invalid side '{side}'. Must be one of {sorted(VALID_SIDES)}."
        )
    return normalized


def validate_order_type(order_type: str) -> str:
    """Validate and normalize an order type.

    Args:
        order_type: Raw order type string supplied by the user (e.g. "limit").

    Returns:
        The normalized order type, either "MARKET" or "LIMIT".

    Raises:
        ValidationError: If the order type is not MARKET or LIMIT.

    """
    normalized = order_type.strip().upper()
    if normalized not in VALID_ORDER_TYPES:
        raise ValidationError(
            f"Invalid order type '{order_type}'. "
            f"Must be one of {sorted(VALID_ORDER_TYPES)}."
        )
    return normalized


def validate_quantity(quantity: Decimal) -> Decimal:
    """Validate that an order quantity is a positive number.

    ``Decimal`` is used (rather than ``float``) throughout the order
    pipeline because binary floating point cannot represent most decimal
    fractions exactly and can render tiny values in scientific notation
    (e.g. ``1e-08``), which Binance's API rejects as a malformed parameter.

    Args:
        quantity: The requested order quantity.

    Returns:
        The validated quantity, unchanged.

    Raises:
        ValidationError: If the quantity is zero or negative.

    """
    if quantity <= 0:
        raise ValidationError(f"Quantity must be positive, got {quantity}.")
    return quantity


def validate_price(price: Decimal | None, order_type: str) -> Decimal | None:
    """Validate an order price against the rules for its order type.

    LIMIT and STOP_LIMIT orders both require a positive price. MARKET
    orders execute at the prevailing market price, so any supplied price
    is ignored (with a warning logged) rather than silently accepted.

    Args:
        price: The requested price, or ``None`` if not supplied.
        order_type: The normalized order type.

    Returns:
        The validated price, or ``None`` for MARKET orders.

    Raises:
        ValidationError: If a price-requiring order is missing a price,
            or the price is not positive.

    """
    if order_type in PRICE_REQUIRED_ORDER_TYPES:
        if price is None:
            raise ValidationError(f"{order_type} orders require a --price argument.")
        if price <= 0:
            raise ValidationError(f"Price must be positive, got {price}.")
        return price

    if price is not None:
        logger.warning("Price %s supplied for a MARKET order; ignoring it.", price)
    return None


def validate_stop_price(stop_price: Decimal | None, order_type: str) -> Decimal | None:
    """Validate the trigger price for a STOP_LIMIT order.

    Args:
        stop_price: The requested stop-trigger price, or ``None``.
        order_type: The normalized order type.

    Returns:
        The validated stop price for STOP_LIMIT orders, or ``None`` for
        every other order type.

    Raises:
        ValidationError: If a STOP_LIMIT order is missing a stop price, or
            the stop price is not positive.

    """
    if order_type == STOP_LIMIT_ORDER_TYPE:
        if stop_price is None:
            raise ValidationError("STOP_LIMIT orders require a --stop-price argument.")
        if stop_price <= 0:
            raise ValidationError(f"Stop price must be positive, got {stop_price}.")
        return stop_price

    if stop_price is not None:
        logger.warning(
            "Stop price %s supplied for a %s order; ignoring it.",
            stop_price,
            order_type,
        )
    return None

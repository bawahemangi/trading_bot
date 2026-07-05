"""Order construction and submission logic.

This module sits between the CLI and the API client:

* :func:`build_order_request` turns raw, unvalidated CLI input into an
  immutable, validated :class:`OrderRequest`.
* :class:`OrderManager` takes a validated request, confirms the symbol is
  actually tradable, submits it via :class:`bot.client.BinanceFuturesClient`,
  and maps the raw API response into a structured :class:`OrderResult`.

Keeping this logic out of ``cli.py`` means it can be reused (e.g. in tests,
a future web API, or a batch script) without any dependency on argparse.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from bot.client import BinanceFuturesClient
from bot.exceptions import InvalidSymbolError
from bot.validators import (
    validate_order_type,
    validate_price,
    validate_quantity,
    validate_side,
    validate_stop_price,
    validate_symbol_format,
)

logger = logging.getLogger(__name__)

LIMIT_ORDER_TYPE = "LIMIT"
STOP_LIMIT_ORDER_TYPE = "STOP_LIMIT"
TIME_IN_FORCE_GTC = "GTC"

# Binance Futures has no order type literally called "STOP_LIMIT" — its
# stop-limit order type is named "STOP" on the wire. Keeping the
# user-facing name distinct from the wire name avoids overloading a single
# string with two different meanings across the CLI and the API layer.
_BINANCE_API_ORDER_TYPE: dict[str, str] = {
    "MARKET": "MARKET",
    "LIMIT": "LIMIT",
    STOP_LIMIT_ORDER_TYPE: "STOP",
}


@dataclass(frozen=True, slots=True)
class OrderRequest:
    """A fully validated, immutable order ready to be submitted.

    Attributes:
        symbol: Uppercase trading pair symbol, e.g. "BTCUSDT".
        side: "BUY" or "SELL".
        order_type: "MARKET", "LIMIT", or "STOP_LIMIT".
        quantity: Positive order quantity.
        price: Positive limit price, or ``None`` for MARKET orders.
        stop_price: Positive stop-trigger price, only for STOP_LIMIT orders.

    """

    symbol: str
    side: str
    order_type: str
    quantity: Decimal
    price: Decimal | None = None
    stop_price: Decimal | None = None


@dataclass(frozen=True, slots=True)
class OrderResult:
    """A structured, human-friendly view of a successful order response.

    Attributes:
        order_id: Exchange-assigned order identifier.
        symbol: Trading pair symbol the order was placed on.
        side: "BUY" or "SELL".
        order_type: "MARKET", "LIMIT", or "STOP_LIMIT".
        status: Exchange order status, e.g. "NEW" or "FILLED".
        executed_qty: Quantity that has been filled so far.
        avg_price: Average fill price, or ``None`` if not yet filled.
        stop_price: Stop-trigger price, or ``None`` for non-stop orders.
        raw_response: The complete, unmodified API response.

    """

    order_id: int
    symbol: str
    side: str
    order_type: str
    status: str
    executed_qty: Decimal
    avg_price: Decimal | None
    stop_price: Decimal | None
    raw_response: dict[str, Any]


def build_order_request(
    symbol: str,
    side: str,
    order_type: str,
    quantity: Decimal,
    price: Decimal | None,
    stop_price: Decimal | None = None,
) -> OrderRequest:
    """Validate raw CLI input and construct an :class:`OrderRequest`.

    Args:
        symbol: Raw symbol string, e.g. "btcusdt".
        side: Raw side string, e.g. "buy".
        order_type: Raw order type string, e.g. "limit".
        quantity: Raw order quantity.
        price: Raw limit price, or ``None``.
        stop_price: Raw stop-trigger price, or ``None`` (only meaningful
            for STOP_LIMIT orders).

    Returns:
        A validated, normalized ``OrderRequest``.

    Raises:
        ValidationError: If any field fails validation.

    """
    validated_type = validate_order_type(order_type)
    return OrderRequest(
        symbol=validate_symbol_format(symbol),
        side=validate_side(side),
        order_type=validated_type,
        quantity=validate_quantity(quantity),
        price=validate_price(price, validated_type),
        stop_price=validate_stop_price(stop_price, validated_type),
    )


def _build_order_params(order: OrderRequest) -> dict[str, Any]:
    """Translate a validated ``OrderRequest`` into Binance API parameters.

    Args:
        order: The validated order to translate.

    Returns:
        A parameter dict ready to be passed to
        :meth:`bot.client.BinanceFuturesClient.place_order`. Numeric fields
        are rendered as fixed-point strings (never scientific notation),
        since Binance's API rejects values like ``1e-08``.

    """
    params: dict[str, Any] = {
        "symbol": order.symbol,
        "side": order.side,
        "type": _BINANCE_API_ORDER_TYPE[order.order_type],
        "quantity": format(order.quantity, "f"),
    }
    if order.order_type == LIMIT_ORDER_TYPE:
        params["price"] = format(order.price, "f")
        params["timeInForce"] = TIME_IN_FORCE_GTC
    return params


def _build_algo_order_params(order: OrderRequest) -> dict[str, Any]:
    """Translate a validated ``OrderRequest`` into Binance Algo API parameters.

    Args:
        order: The validated order to translate.

    Returns:
        A parameter dict ready to be passed to
        :meth:`bot.client.BinanceFuturesClient.place_algo_order`.

    """
    return {
        "algoType": "CONDITIONAL",
        "symbol": order.symbol,
        "side": order.side,
        "type": _BINANCE_API_ORDER_TYPE[order.order_type],
        "quantity": format(order.quantity, "f"),
        "price": format(order.price, "f"),
        "triggerPrice": format(order.stop_price, "f"),
        "timeInForce": TIME_IN_FORCE_GTC,
    }


def _parse_order_response(order: OrderRequest, response: dict[str, Any]) -> OrderResult:
    """Map a raw Binance order response into a structured ``OrderResult``.

    Args:
        order: The order request that produced this response, used as a
            fallback for fields the API response might omit.
        response: The raw, parsed JSON response from Binance.

    Returns:
        A structured ``OrderResult``. Numeric fields are parsed straight
        from the API's string representation into ``Decimal``, never
        through ``float``, so no precision is lost in either direction.

    """
    if "algoId" in response:
        status = response.get("algoStatus")
        if not status:
            status = "NEW" if response.get("success") is True or response.get("msg") == "success" else "FAILED"
        return OrderResult(
            order_id=response["algoId"],
            symbol=response.get("symbol", order.symbol),
            side=response.get("side", order.side),
            order_type=order.order_type,
            status=status,
            executed_qty=Decimal("0"),
            avg_price=None,
            stop_price=order.stop_price,
            raw_response=response,
        )

    avg_price_raw = response.get("avgPrice")
    avg_price = Decimal(avg_price_raw) if avg_price_raw is not None else None
    if avg_price == 0:
        # Binance reports an avgPrice of 0 for orders that have not been
        # filled yet; surface that as "no average price" rather than a
        # misleading price of zero.
        avg_price = None

    stop_price_raw = response.get("stopPrice")
    stop_price = Decimal(stop_price_raw) if stop_price_raw is not None else None
    if stop_price == 0:
        stop_price = None

    return OrderResult(
        order_id=response["orderId"],
        symbol=response.get("symbol", order.symbol),
        side=response.get("side", order.side),
        # Use our own user-facing order_type rather than echoing the API's
        # response, since Binance's wire name for STOP_LIMIT is "STOP" —
        # echoing it back would silently rename the order type in the UI.
        order_type=order.order_type,
        status=response.get("status", "UNKNOWN"),
        executed_qty=Decimal(response.get("executedQty", "0")),
        avg_price=avg_price,
        stop_price=stop_price,
        raw_response=response,
    )


class OrderManager:
    """Coordinates symbol validation and order submission against the API."""

    def __init__(self, client: BinanceFuturesClient) -> None:
        """Initialize the manager.

        Args:
            client: A configured ``BinanceFuturesClient`` instance.

        """
        self._client = client

    def ensure_symbol_is_tradable(self, symbol: str) -> None:
        """Confirm a symbol is currently open for trading on the exchange.

        Args:
            symbol: Normalized, uppercase symbol to check.

        Raises:
            InvalidSymbolError: If the symbol is unknown or not tradable.

        """
        tradable_symbols = self._client.get_tradable_symbols()
        if symbol not in tradable_symbols:
            raise InvalidSymbolError(symbol)

    def submit(self, order: OrderRequest) -> OrderResult:
        """Validate the symbol against the exchange and submit the order.

        Args:
            order: A pre-validated ``OrderRequest``.

        Returns:
            A structured ``OrderResult`` describing the submitted order.

        Raises:
            InvalidSymbolError: If the symbol is not tradable.
            APIConnectionError: On network failure or timeout.
            APIRequestError: If the API rejects the order.
            AuthenticationError: If the API rejects the credentials.

        """
        logger.info("Submitting order: %s", order)
        self.ensure_symbol_is_tradable(order.symbol)

        if order.order_type == STOP_LIMIT_ORDER_TYPE:
            params = _build_algo_order_params(order)
            response = self._client.place_algo_order(params)
        else:
            params = _build_order_params(order)
            response = self._client.place_order(params)

        result = _parse_order_response(order, response)

        logger.info(
            "Order submitted successfully: order_id=%s status=%s",
            result.order_id,
            result.status,
        )
        return result


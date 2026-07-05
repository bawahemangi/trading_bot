"""Command-line entry point for the Binance Futures Testnet trading bot.

Responsibilities are deliberately narrow:

* Parse and type-convert CLI arguments (argparse).
* Load credentials from the environment.
* Delegate all validation and order logic to the ``bot`` package.
* Format output for the terminal and translate exceptions into friendly,
  actionable messages with distinct process exit codes.

No business logic lives here — see ``bot/orders.py`` for that.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import textwrap
from decimal import Decimal, InvalidOperation
from typing import Final

from dotenv import load_dotenv

from bot.client import DEFAULT_BASE_URL, BinanceFuturesClient
from bot.exceptions import (
    APIConnectionError,
    APIRequestError,
    AuthenticationError,
    InvalidSymbolError,
    TradingBotError,
    ValidationError,
)
from bot.logging_config import configure_logging
from bot.orders import OrderManager, OrderRequest, OrderResult, build_order_request

logger = logging.getLogger(__name__)

ENV_API_KEY: Final[str] = "BINANCE_API_KEY"
ENV_API_SECRET: Final[str] = "BINANCE_API_SECRET"
ENV_BASE_URL: Final[str] = "BINANCE_BASE_URL"

EXIT_SUCCESS: Final[int] = 0
EXIT_UNEXPECTED_ERROR: Final[int] = 1
EXIT_VALIDATION_ERROR: Final[int] = 2
EXIT_INVALID_SYMBOL: Final[int] = 3
EXIT_AUTH_ERROR: Final[int] = 4
EXIT_CONNECTION_ERROR: Final[int] = 5
EXIT_API_ERROR: Final[int] = 6

CLI_EXAMPLES: Final[str] = textwrap.dedent("""\
    Examples:
      Market order:
        python cli.py --symbol BTCUSDT --side BUY --type MARKET --quantity 0.01

      Limit order:
        python cli.py --symbol BTCUSDT --side SELL --type LIMIT \\
            --quantity 0.01 --price 65000

      Stop-limit order:
        python cli.py --symbol BTCUSDT --side SELL --type STOP_LIMIT \\
            --quantity 0.01 --price 64000 --stop-price 64500
    """)


def _parse_decimal(raw_value: str) -> Decimal:
    """Parse a CLI argument string into a ``Decimal``.

    Used as the ``type=`` callback for ``--quantity``/``--price``. A plain
    ``Decimal(raw_value)`` call is not enough on its own: argparse only
    catches ``ValueError``/``TypeError`` from a type callback, but an
    invalid decimal string raises ``decimal.InvalidOperation`` (a subclass
    of ``ArithmeticError``), which argparse would let propagate as an ugly
    traceback instead of a clean usage error.

    Args:
        raw_value: The raw string supplied on the command line.

    Returns:
        The parsed ``Decimal``.

    Raises:
        argparse.ArgumentTypeError: If ``raw_value`` is not a valid number.

    """
    try:
        return Decimal(raw_value)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError(
            f"'{raw_value}' is not a valid number."
        ) from exc


def build_arg_parser() -> argparse.ArgumentParser:
    """Construct the CLI argument parser.

    Side/type values are intentionally left as free-form strings here and
    normalized/validated once, in ``bot.validators``, so there is a single
    source of truth for what counts as a valid value. Quantity/price are
    parsed as ``Decimal`` (via ``_parse_decimal``), not ``float``, to avoid
    binary floating-point precision loss on monetary values.

    Returns:
        A configured ``ArgumentParser``.

    """
    parser = argparse.ArgumentParser(
        prog="trading-bot",
        description=(
            "Place MARKET, LIMIT, or STOP_LIMIT orders on Binance Futures Testnet."
        ),
        epilog=CLI_EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--symbol", required=True, help="Trading pair, e.g. BTCUSDT")
    parser.add_argument("--side", required=True, help="Order side: BUY or SELL")
    parser.add_argument(
        "--type",
        dest="order_type",
        required=True,
        help="Order type: MARKET, LIMIT, or STOP_LIMIT",
    )
    parser.add_argument(
        "--quantity",
        required=True,
        type=_parse_decimal,
        help="Order quantity (must be positive)",
    )
    parser.add_argument(
        "--price",
        type=_parse_decimal,
        default=None,
        help="Limit price (required for LIMIT/STOP_LIMIT, ignored for MARKET)",
    )
    parser.add_argument(
        "--stop-price",
        dest="stop_price",
        type=_parse_decimal,
        default=None,
        help="Stop-trigger price (required for STOP_LIMIT only)",
    )
    return parser


def load_credentials() -> tuple[str, str, str]:
    """Load API credentials and the base URL from the environment.

    Reads a ``.env`` file (if present) via ``python-dotenv`` and falls back
    to already-exported environment variables otherwise.

    Returns:
        A ``(api_key, api_secret, base_url)`` tuple.

    Raises:
        AuthenticationError: If either credential is missing.

    """
    load_dotenv()
    api_key = os.getenv(ENV_API_KEY)
    api_secret = os.getenv(ENV_API_SECRET)
    base_url = os.getenv(ENV_BASE_URL, DEFAULT_BASE_URL)

    if not api_key or not api_secret:
        raise AuthenticationError(
            f"Missing credentials. Set {ENV_API_KEY} and {ENV_API_SECRET} "
            "in a .env file in the project root (see .env.example)."
        )
    return api_key, api_secret, base_url


def _format_decimal(value: Decimal | None, if_none: str) -> str:
    """Render a ``Decimal`` for display in fixed-point notation.

    ``Decimal``'s default ``str()`` falls back to scientific notation for
    very small or exactly-zero values (e.g. ``Decimal("0.00000000")``
    displays as ``"0E-8"``), which would be a confusing regression in the
    CLI output. The ``f`` format spec forces fixed-point notation instead.

    Args:
        value: The value to render, or ``None``.
        if_none: Text to display when ``value`` is ``None``.

    Returns:
        A fixed-point string, or ``if_none`` if ``value`` is ``None``.

    """
    return if_none if value is None else format(value, "f")


def print_order_summary(order: OrderRequest) -> None:
    """Print a pre-submission summary of the order about to be placed.

    Args:
        order: The validated order about to be submitted.

    """
    print("=" * 52)
    print("ORDER SUMMARY")
    print("=" * 52)
    print(f"Symbol:      {order.symbol}")
    print(f"Side:        {order.side}")
    print(f"Type:        {order.order_type}")
    print(f"Quantity:    {_format_decimal(order.quantity, if_none='N/A')}")
    print(f"Price:       {_format_decimal(order.price, if_none='MARKET (n/a)')}")
    if order.stop_price is not None:
        print(f"Stop Price:  {_format_decimal(order.stop_price, if_none='N/A')}")
    print("=" * 52)


def print_order_result(result: OrderResult) -> None:
    """Print the outcome of a successfully submitted order.

    Args:
        result: The structured result returned by ``OrderManager.submit``.

    """
    print("\nORDER RESULT")
    print("-" * 52)
    print(f"Order ID:       {result.order_id}")
    print(f"Status:         {result.status}")
    print(f"Executed Qty:   {_format_decimal(result.executed_qty, if_none='N/A')}")
    avg_price = _format_decimal(result.avg_price, if_none="N/A (not yet filled)")
    print(f"Average Price:  {avg_price}")
    if result.stop_price is not None:
        print(f"Stop Price:     {_format_decimal(result.stop_price, if_none='N/A')}")
    print("-" * 52)
    print(f"Raw response: {result.raw_response}")
    print(
        f"\n✅ SUCCESS: {result.side} {result.order_type} order placed "
        f"for {result.symbol} (order ID {result.order_id})."
    )


def _print_error(prefix: str, message: str) -> None:
    """Print a consistently formatted failure message to stderr.

    Args:
        prefix: Short category label, e.g. "Invalid input".
        message: The underlying error message.

    """
    print(f"\n❌ FAILED — {prefix}: {message}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    """Run the CLI: parse arguments, submit an order, report the outcome.

    Args:
        argv: Optional argument list (defaults to ``sys.argv[1:]``); mainly
            useful for testing.

    Returns:
        A process exit code (0 on success, non-zero on failure).

    """
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except AttributeError:
            pass
    configure_logging()
    args = build_arg_parser().parse_args(argv)

    try:
        order = build_order_request(
            symbol=args.symbol,
            side=args.side,
            order_type=args.order_type,
            quantity=args.quantity,
            price=args.price,
            stop_price=args.stop_price,
        )
        print_order_summary(order)

        api_key, api_secret, base_url = load_credentials()
        client = BinanceFuturesClient(api_key, api_secret, base_url=base_url)
        result = OrderManager(client).submit(order)

        print_order_result(result)
        return EXIT_SUCCESS

    except InvalidSymbolError as exc:
        logger.error("Invalid symbol: %s", exc.message)
        _print_error("Invalid symbol", exc.message)
        return EXIT_INVALID_SYMBOL
    except ValidationError as exc:
        logger.error("Validation failed: %s", exc.message)
        _print_error("Invalid input", exc.message)
        return EXIT_VALIDATION_ERROR
    except AuthenticationError as exc:
        logger.error("Authentication failed: %s", exc.message)
        _print_error("Authentication failed", exc.message)
        return EXIT_AUTH_ERROR
    except APIConnectionError as exc:
        logger.error("Connection error: %s", exc.message)
        _print_error("Network error", exc.message)
        return EXIT_CONNECTION_ERROR
    except APIRequestError as exc:
        logger.error("API error: %s", exc.message)
        _print_error("Binance API error", exc.message)
        return EXIT_API_ERROR
    except TradingBotError as exc:
        logger.error("Unhandled trading bot error: %s", exc.message)
        _print_error("Error", exc.message)
        return EXIT_UNEXPECTED_ERROR
    except Exception as exc:  # noqa: BLE001 - last-resort safety net
        logger.exception("Unexpected error")
        _print_error("Unexpected error", str(exc))
        return EXIT_UNEXPECTED_ERROR


if __name__ == "__main__":
    sys.exit(main())

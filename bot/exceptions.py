"""Custom exception hierarchy for the trading bot.

All exceptions raised anywhere in the ``bot`` package inherit from
:class:`TradingBotError`. This allows callers (primarily the CLI layer) to
catch bot-specific failures with a single ``except TradingBotError`` clause,
while still being able to branch on more specific exception types when a
tailored, user-facing error message is required.
"""

from __future__ import annotations


class TradingBotError(Exception):
    """Base exception for all errors raised by the trading bot.

    Attributes:
        message: Human-readable description of the error.

    """

    def __init__(self, message: str) -> None:
        """Initialize the exception.

        Args:
            message: Human-readable description of the error.

        """
        self.message = message
        super().__init__(message)


class ValidationError(TradingBotError):
    """Raised when user-supplied order input fails validation.

    Covers cases such as an invalid order side, an invalid order type,
    a non-positive quantity, a non-positive price, or a LIMIT order
    submitted without a price.
    """


class InvalidSymbolError(ValidationError):
    """Raised when a trading symbol is not tradable on the exchange.

    Attributes:
        symbol: The symbol that failed validation.

    """

    def __init__(self, symbol: str) -> None:
        """Initialize the exception.

        Args:
            symbol: The symbol that failed validation.

        """
        self.symbol = symbol
        super().__init__(
            f"Symbol '{symbol}' is not a valid or tradable symbol on "
            "Binance Futures Testnet."
        )


class APIConnectionError(TradingBotError):
    """Raised when a network-level failure prevents reaching the API.

    Wraps lower-level errors such as ``requests.exceptions.ConnectionError``
    or ``requests.exceptions.Timeout`` so that callers only need to depend
    on bot-specific exception types, never on ``requests`` internals.
    """


class APIRequestError(TradingBotError):
    """Raised when the Binance API returns an error response.

    Attributes:
        code: The Binance-specific error code, if available (e.g. -1121).
        status_code: The HTTP status code returned by the API, if available.

    """

    def __init__(
        self,
        message: str,
        code: int | None = None,
        status_code: int | None = None,
    ) -> None:
        """Initialize the exception.

        Args:
            message: Human-readable description of the error.
            code: The Binance-specific error code, if available.
            status_code: The HTTP status code returned by the API.

        """
        self.code = code
        self.status_code = status_code
        super().__init__(message)


class AuthenticationError(APIRequestError):
    """Raised when the API rejects the provided credentials.

    Typically corresponds to Binance error codes such as -2014 (invalid
    API key format) or -2015 (invalid API key, IP, or permissions for
    the requested action).
    """

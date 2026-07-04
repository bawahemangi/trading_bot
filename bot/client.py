"""Thin, explicit REST client for Binance Futures Testnet (USDT-M).

Implements request signing and HTTP communication directly against the
documented REST endpoints rather than through the ``python-binance``
wrapper. This keeps full control over what gets logged (the exact request
payload and response body) and how errors are translated into the
project's own exception hierarchy.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
from typing import Any, Final
from urllib.parse import urlencode

import requests

from bot.exceptions import (
    APIConnectionError,
    APIRequestError,
    AuthenticationError,
)

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL: Final[str] = "https://testnet.binancefuture.com"
ORDER_ENDPOINT: Final[str] = "/fapi/v1/order"
EXCHANGE_INFO_ENDPOINT: Final[str] = "/fapi/v1/exchangeInfo"

DEFAULT_TIMEOUT_SECONDS: Final[int] = 10
DEFAULT_RECV_WINDOW_MS: Final[int] = 5000
TRADING_STATUS: Final[str] = "TRADING"

# Binance error codes that indicate an authentication/authorization problem
# rather than a generic request error.
AUTH_ERROR_CODES: Final[frozenset[int]] = frozenset({-2014, -2015})
AUTH_HTTP_STATUS_CODES: Final[frozenset[int]] = frozenset({401, 403})

REDACTED_VALUE: Final[str] = "***REDACTED***"


class BinanceFuturesClient:
    """Signed REST client for the Binance Futures Testnet API.

    Attributes:
        base_url: Root URL the client sends requests to.

    """

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        base_url: str = DEFAULT_BASE_URL,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
        session: requests.Session | None = None,
    ) -> None:
        """Initialize the client.

        Args:
            api_key: Binance Futures Testnet API key.
            api_secret: Binance Futures Testnet API secret, used to sign
                requests via HMAC-SHA256.
            base_url: Root URL for the API (defaults to the testnet URL).
            timeout: Per-request timeout, in seconds.
            session: Optional pre-configured ``requests.Session``, mainly
                useful for injecting a mock session in tests.

        Raises:
            AuthenticationError: If either credential is empty.

        """
        if not api_key or not api_secret:
            raise AuthenticationError(
                "API key and API secret must both be provided and non-empty."
            )
        self.base_url = base_url.rstrip("/")
        self._api_secret = api_secret
        self._timeout = timeout
        self._session = session or requests.Session()
        self._session.headers.update({"X-MBX-APIKEY": api_key})

    def _sign(self, params: dict[str, Any]) -> dict[str, Any]:
        """Attach timestamp, recvWindow, and an HMAC-SHA256 signature.

        Args:
            params: Request parameters before signing.

        Returns:
            A new dict containing the original parameters plus
            ``timestamp``, ``recvWindow``, and ``signature``.

        """
        signed_params = dict(params)
        signed_params["timestamp"] = int(time.time() * 1000)
        signed_params["recvWindow"] = DEFAULT_RECV_WINDOW_MS

        query_string = urlencode(signed_params)
        signature = hmac.new(
            self._api_secret.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        signed_params["signature"] = signature
        return signed_params

    @staticmethod
    def _redact(params: dict[str, Any]) -> dict[str, Any]:
        """Return a copy of ``params`` safe to write to logs.

        Args:
            params: Request parameters that may contain a signature.

        Returns:
            A shallow copy with the ``signature`` field masked.

        """
        redacted = dict(params)
        if "signature" in redacted:
            redacted["signature"] = REDACTED_VALUE
        return redacted

    def _request(
        self,
        method: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
        signed: bool = False,
    ) -> dict[str, Any]:
        """Send an HTTP request and translate the outcome or any failure.

        Args:
            method: HTTP method, e.g. "GET" or "POST".
            endpoint: API path, e.g. "/fapi/v1/order".
            params: Query/body parameters to send.
            signed: Whether the request must be signed with the API secret.

        Returns:
            The parsed JSON response body.

        Raises:
            APIConnectionError: On network failure or timeout.
            APIRequestError: If the API returns an error response.
            AuthenticationError: If the API rejects the credentials.

        """
        url = f"{self.base_url}{endpoint}"
        request_params = self._sign(params or {}) if signed else (params or {})

        logger.info(
            "API request | %s %s | payload=%s",
            method,
            endpoint,
            self._redact(request_params),
        )

        try:
            response = self._session.request(
                method, url, params=request_params, timeout=self._timeout
            )
        except requests.exceptions.Timeout as exc:
            logger.error("API request timed out | %s %s", method, endpoint)
            raise APIConnectionError(
                f"Request to {endpoint} timed out after {self._timeout}s."
            ) from exc
        except requests.exceptions.ConnectionError as exc:
            logger.error("API connection failed | %s %s | %s", method, endpoint, exc)
            raise APIConnectionError(
                f"Could not connect to {self.base_url}. Check your network "
                "connection and the base URL."
            ) from exc
        except requests.exceptions.RequestException as exc:
            logger.error("Unexpected network error | %s %s | %s", method, endpoint, exc)
            raise APIConnectionError(
                f"Unexpected network error while calling {endpoint}: {exc}"
            ) from exc

        return self._handle_response(response, endpoint)

    def _handle_response(
        self, response: requests.Response, endpoint: str
    ) -> dict[str, Any]:
        """Parse a response, logging and raising on any error condition.

        Args:
            response: The raw HTTP response.
            endpoint: The API path that was called, for log context.

        Returns:
            The parsed JSON response body, if the request succeeded.

        Raises:
            APIRequestError: If the body is not valid JSON or the API
                reports a generic error.
            AuthenticationError: If the error indicates bad credentials.

        """
        try:
            payload = response.json()
        except ValueError as exc:
            logger.error(
                "Non-JSON response | %s | status=%s", endpoint, response.status_code
            )
            raise APIRequestError(
                f"Received a non-JSON response from {endpoint} "
                f"(HTTP {response.status_code}).",
                status_code=response.status_code,
            ) from exc

        if response.ok:
            logger.info(
                "API response | %s | status=%s | body=%s",
                endpoint,
                response.status_code,
                payload,
            )
            return payload

        code = payload.get("code")
        message = payload.get("msg", "Unknown error")
        logger.error(
            "API error | %s | status=%s | code=%s | msg=%s",
            endpoint,
            response.status_code,
            code,
            message,
        )

        if response.status_code in AUTH_HTTP_STATUS_CODES or code in AUTH_ERROR_CODES:
            raise AuthenticationError(
                f"Authentication failed: {message} (code={code}).",
                code=code,
                status_code=response.status_code,
            )

        raise APIRequestError(
            f"Binance API error: {message} (code={code}).",
            code=code,
            status_code=response.status_code,
        )

    def get_exchange_info(self) -> dict[str, Any]:
        """Fetch exchange trading rules and the full symbol list.

        This is a public endpoint and does not require request signing.

        Returns:
            The parsed exchange info response.

        """
        return self._request("GET", EXCHANGE_INFO_ENDPOINT, signed=False)

    def get_tradable_symbols(self) -> set[str]:
        """Return the set of symbol names currently open for trading.

        Returns:
            A set of uppercase symbol strings, e.g. {"BTCUSDT", "ETHUSDT"}.

        """
        info = self.get_exchange_info()
        return {
            entry["symbol"]
            for entry in info.get("symbols", [])
            if entry.get("status") == TRADING_STATUS
        }

    def place_order(self, params: dict[str, Any]) -> dict[str, Any]:
        """Submit a new order.

        Args:
            params: Order parameters (symbol, side, type, quantity, and
                any type-specific fields such as price/timeInForce).

        Returns:
            The parsed order response from Binance.

        """
        return self._request("POST", ORDER_ENDPOINT, params=params, signed=True)

"""Local mock Binance Futures Testnet server (dev/testing tool only).

This lets the trading bot's full HTTP request → response → logging
pipeline be exercised end-to-end without real network access or real API
credentials. It implements just the two endpoints the bot calls:

* ``GET /fapi/v1/exchangeInfo``  — returns a small, valid symbol list.
* ``POST /fapi/v1/order``        — returns a schema-correct order response.

It does **not** verify signatures, enforce exchange trading rules, or run
real order-matching logic — it exists purely to prove the client, the
order pipeline, and the logging configuration all work correctly, without
depending on this being run against the real
https://testnet.binancefuture.com endpoint.

Usage:
    python scripts/mock_binance_server.py --port 8001

Then, in another terminal, point the bot at it:
    export BINANCE_API_KEY=demo
    export BINANCE_API_SECRET=demo
    export BINANCE_BASE_URL=http://127.0.0.1:8001
    python cli.py --symbol BTCUSDT --side BUY --type MARKET --quantity 0.01
"""

from __future__ import annotations

import argparse
import json
import random
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

TRADABLE_SYMBOLS: tuple[str, ...] = ("BTCUSDT", "ETHUSDT", "BNBUSDT")
DEFAULT_PORT = 8001

_next_order_id = 1_000_000


def _exchange_info_payload() -> dict[str, Any]:
    """Build a minimal, schema-correct ``/fapi/v1/exchangeInfo`` response.

    Returns:
        A dict with the fields the bot actually reads (``symbols`` with
        ``symbol``/``status``).

    """
    return {
        "timezone": "UTC",
        "serverTime": int(time.time() * 1000),
        "symbols": [
            {"symbol": symbol, "status": "TRADING", "pair": symbol}
            for symbol in TRADABLE_SYMBOLS
        ],
    }


def _order_response_payload(params: dict[str, str]) -> dict[str, Any]:
    """Build a schema-correct ``/fapi/v1/order`` response for given params.

    MARKET orders are simulated as filled instantly at a plausible jittered
    price. LIMIT and STOP orders are simulated as resting on the book,
    unfilled — which is how a real testnet order behaves unless the market
    happens to trade through the price.

    Args:
        params: The decoded query parameters from the incoming request.

    Returns:
        A dict shaped like a real Binance Futures order response.

    """
    global _next_order_id
    _next_order_id += 1

    order_type = params.get("type", "MARKET")
    quantity = params.get("quantity", "0")
    price = params.get("price")
    stop_price = params.get("stopPrice")
    symbol = params.get("symbol", "")

    if order_type == "MARKET":
        base_price = 65000.0 if "BTC" in symbol else 3200.0
        fill_price = round(base_price * random.uniform(0.998, 1.002), 2)
        status, executed_qty, avg_price = "FILLED", quantity, f"{fill_price:.2f}"
    else:
        status, executed_qty, avg_price = "NEW", "0.00000000", "0.00000000"

    response: dict[str, Any] = {
        "orderId": _next_order_id,
        "symbol": symbol,
        "status": status,
        "clientOrderId": f"mock-{_next_order_id}",
        "side": params.get("side"),
        "type": order_type,
        "origQty": quantity,
        "executedQty": executed_qty,
        "avgPrice": avg_price,
        "timeInForce": params.get("timeInForce", "GTC"),
        "updateTime": int(time.time() * 1000),
    }
    if price is not None:
        response["price"] = price
    if stop_price is not None:
        response["stopPrice"] = stop_price
    return response


class MockBinanceHandler(BaseHTTPRequestHandler):
    """Handles the small subset of Binance Futures endpoints the bot uses."""

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        """Serialize and send a JSON response.

        Args:
            status: HTTP status code to send.
            payload: JSON-serializable response body.

        """
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        """Handle GET requests (only ``/fapi/v1/exchangeInfo`` is known)."""
        path = urlparse(self.path).path
        if path == "/fapi/v1/exchangeInfo":
            self._send_json(200, _exchange_info_payload())
        else:
            self._send_json(404, {"code": -1, "msg": f"Unknown endpoint {path}"})

    def do_POST(self) -> None:
        """Handle POST requests (only ``/fapi/v1/order`` is known).

        Binance's signed endpoints accept parameters in the query string
        even for POST requests, so parameters are read from ``self.path``
        rather than the request body.
        """
        parsed = urlparse(self.path)
        if parsed.path != "/fapi/v1/order":
            self._send_json(404, {"code": -1, "msg": f"Unknown endpoint {parsed.path}"})
            return
        params = {key: values[0] for key, values in parse_qs(parsed.query).items()}
        self._send_json(200, _order_response_payload(params))

    def log_message(self, format_: str, *args: object) -> None:
        """Silence default request logging; the bot's own log is what matters."""


def main() -> None:
    """Start the mock server and block until interrupted."""
    parser = argparse.ArgumentParser(description="Local mock Binance Futures server.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()

    server = ThreadingHTTPServer(("127.0.0.1", args.port), MockBinanceHandler)
    print(f"Mock Binance server running at http://127.0.0.1:{args.port}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()


if __name__ == "__main__":
    main()

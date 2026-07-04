# Binance Futures Testnet Trading Bot

A clean, production-style CLI application for placing **MARKET**, **LIMIT**,
and **STOP_LIMIT** orders on
[Binance Futures Testnet (USDT-M)](https://testnet.binancefuture.com),
built with a layered architecture that separates CLI concerns from business
logic and API communication.

## Overview

This project implements a signed REST client for Binance Futures Testnet
from first principles (`requests` + HMAC-SHA256), not the `python-binance`
wrapper, so that every request payload, response body, and error path is
fully visible, logged, and testable. It is organized into four independent
layers — CLI, order orchestration, input validation, and the API client —
so the core trading logic can be reused outside a command line (e.g. in
tests or a future service) without any `argparse` dependency.

## Features

- **Order types:** MARKET, LIMIT, and STOP_LIMIT (bonus third order type)
- **Order sides:** BUY and SELL
- **Argument parsing** via `argparse`, with a descriptive `--help` and
  usage examples for every order type
- **Full input validation**: side, order type, positive quantity, positive
  price/stop-price, price-required-for-LIMIT/STOP_LIMIT,
  MARKET-ignores-price, symbol format
- **Live symbol validation** against the exchange's `/fapi/v1/exchangeInfo`
  endpoint before every order, so typos surface as a clear error instead
  of an opaque API failure
- **Exact decimal arithmetic** (`decimal.Decimal`, never `float`) for
  quantity/price/stop-price end-to-end, so tiny values (e.g. `0.00000001`)
  are never sent to — or displayed from — the API in scientific notation
- **Structured console output**: order summary before submission, then
  order ID, status, executed quantity, average fill price, and an explicit
  ✅ SUCCESS / ❌ FAILED message
- **Rotating file logging** of every API request, response, and error to
  `logs/trading_bot.log`, with API secrets/signatures redacted
- **Typed exception hierarchy** mapping cleanly to distinct, non-zero
  process exit codes for scripting
- **No hardcoded secrets** — credentials are read from a `.env` file
- **Local mock server** (`scripts/mock_binance_server.py`) for exercising
  the full request/response/logging pipeline without real credentials

## Folder Structure

```
trading_bot/
│
├── bot/
│   ├── __init__.py
│   ├── client.py          # Signed REST client (requests + HMAC-SHA256)
│   ├── orders.py          # OrderRequest/OrderResult dataclasses + OrderManager
│   ├── validators.py      # Pure input validation functions
│   ├── logging_config.py  # Rotating file + console logging setup
│   └── exceptions.py      # Custom exception hierarchy
│
├── scripts/
│   └── mock_binance_server.py   # Local dev/test server (no real API needed)
│
├── cli.py                 # argparse entry point (no business logic)
├── README.md
├── requirements.txt
├── .env.example
├── .gitignore
└── logs/
    ├── trading_bot.log             # Rotating runtime log (empty placeholder)
    └── sample_runs/
        ├── demo_market_limit_stoplimit.log   # Sample log deliverable
        └── README.md                          # Explains how it was generated
```

## Installation

### 1. Clone / copy the project

```bash
cd trading_bot
```

### 2. Create and activate a virtual environment

```bash
# macOS / Linux
python3 -m venv venv
source venv/bin/activate

# Windows (PowerShell)
python -m venv venv
venv\Scripts\Activate.ps1
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Register for Binance Futures Testnet and generate API credentials

1. Go to <https://testnet.binancefuture.com> and log in (GitHub login).
2. Generate an API key/secret pair from the testnet dashboard.
3. (The testnet account is pre-funded with mock USDT — no real funds are
   ever involved.)

### 5. Create your `.env` file

```bash
cp .env.example .env
```

Then edit `.env` and fill in your Binance Futures Testnet API key and
secret:

```dotenv
BINANCE_API_KEY=your_testnet_api_key_here
BINANCE_API_SECRET=your_testnet_api_secret_here
BINANCE_BASE_URL=https://testnet.binancefuture.com
```

> **Never commit your `.env` file.** It is already excluded via
> `.gitignore`.

## Running Against the Real Testnet

Once `.env` is filled in, run `python cli.py --help` to see the full
option list, then place real testnet orders:

### Market order

```bash
python cli.py --symbol BTCUSDT --side BUY --type MARKET --quantity 0.01
```

### Limit order

```bash
python cli.py --symbol BTCUSDT --side SELL --type LIMIT \
    --quantity 0.01 --price 65000
```

### Stop-limit order (bonus order type)

```bash
python cli.py --symbol BTCUSDT --side SELL --type STOP_LIMIT \
    --quantity 0.01 --price 64000 --stop-price 64500
```

### Sample output

```
====================================================
ORDER SUMMARY
====================================================
Symbol:      BTCUSDT
Side:        SELL
Type:        LIMIT
Quantity:    0.01
Price:       65000
====================================================

ORDER RESULT
----------------------------------------------------
Order ID:       1000002
Status:         NEW
Executed Qty:   0.00000000
Average Price:  N/A (not yet filled)
----------------------------------------------------
Raw response: {...full Binance response...}

✅ SUCCESS: SELL LIMIT order placed for BTCUSDT (order ID 1000002).
```

### Exit codes

| Code | Meaning                                  |
|------|-------------------------------------------|
| 0    | Order placed successfully                 |
| 1    | Unexpected/unhandled error                |
| 2    | Invalid input (validation error)          |
| 3    | Symbol not tradable on the exchange       |
| 4    | Authentication failure                    |
| 5    | Network/connection error                  |
| 6    | Binance API rejected the order            |

## Testing Without Real Credentials (Local Mock Server)

`scripts/mock_binance_server.py` is a small, dependency-free HTTP server
that implements the two endpoints the bot actually calls
(`GET /fapi/v1/exchangeInfo`, `POST /fapi/v1/order`) with schema-correct
responses. It doesn't validate signatures or run real order-matching — it
exists purely so the full client → signing → logging → error-handling
pipeline can be exercised locally, with no real network access or API
keys required. This is how `logs/sample_runs/demo_market_limit_stoplimit.log`
was produced.

```bash
# Terminal 1
python scripts/mock_binance_server.py --port 8001

# Terminal 2
export BINANCE_API_KEY=demo
export BINANCE_API_SECRET=demo
export BINANCE_BASE_URL=http://127.0.0.1:8001
python cli.py --symbol BTCUSDT --side BUY --type MARKET --quantity 0.01
```

MARKET orders come back `FILLED` instantly at a jittered mock price; LIMIT
and STOP_LIMIT orders come back `NEW` (resting, unfilled) — the same
behavior you'd see on the real testnet.

## Logging

Every API request (endpoint + payload), every response, and every error is
written to `logs/trading_bot.log` with a timestamp, log level, and module
name, e.g.:

```
2026-07-04 12:51:38 | INFO  | bot.client | API request | POST /fapi/v1/order | payload={...}
2026-07-04 12:51:38 | INFO  | bot.client | API response | /fapi/v1/order | status=200 | body={...}
2026-07-04 12:52:04 | ERROR | __main__   | Invalid symbol: Symbol 'DOGEUSDT' is not a valid or tradable symbol...
```

API signatures are always redacted before being logged. The log file
rotates automatically at 5 MB (keeping 3 backups), so it never grows
unbounded during long testnet sessions. See `logs/sample_runs/` for a
complete example log covering all three order types plus one error path.

## Assumptions

- **Testnet only.** `BINANCE_BASE_URL` defaults to
  `https://testnet.binancefuture.com`; this bot has not been tested or
  hardened for Binance's production Futures API, and doing so would need
  a review of rate limits, order-filter edge cases, and safety guards
  (e.g. a `--dry-run` flag) that aren't in scope here.
- **USDT-M Futures only** (not COIN-M, not Spot). Symbols are assumed to
  be USDT-margined perpetual/quarterly contracts (e.g. `BTCUSDT`).
- **`GTC` (Good-Til-Canceled)** is used as the `timeInForce` for LIMIT and
  STOP_LIMIT orders, since the assignment didn't specify one.
- **Quantity/price precision** is taken as given from the CLI/API and not
  auto-rounded to each symbol's `LOT_SIZE`/`PRICE_FILTER` step size —
  Binance will reject an order that violates those filters with a clear
  API error (logged and surfaced), rather than the bot silently rounding.
- **Symbol validation** confirms the symbol is *listed and in `TRADING`
  status*, not that the specific order would pass margin/leverage checks
  (e.g. sufficient balance) — those are surfaced as ordinary API errors.
- **`STOP_LIMIT`** is the user-facing name for what Binance's API calls
  order type `STOP` (a stop order with both a trigger `stopPrice` and a
  limit `price`) — the CLI's naming was chosen for clarity over
  matching the wire protocol's exact vocabulary.
- **Single order per invocation.** The CLI is intentionally one-shot
  (one order per process run) rather than an interactive/long-running
  session, matching the "CLI arguments" requirement.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `❌ FAILED — Authentication failed: Missing credentials...` | `.env` missing or not loaded | Confirm `.env` exists in the project root and contains both keys |
| `❌ FAILED — Authentication failed: ... (code=-2015)` | Invalid API key/secret, or IP not whitelisted | Regenerate testnet keys; confirm you're using **testnet**, not production, keys |
| `❌ FAILED — Invalid symbol: Symbol 'XYZ' is not a valid or tradable symbol...` | Typo, or the symbol isn't listed on Futures Testnet | Double-check the symbol on the testnet exchange info endpoint |
| `❌ FAILED — Network error: Could not connect to ...` | No internet access, firewall, or Binance testnet is down | Check connectivity; retry; verify `BINANCE_BASE_URL` |
| `❌ FAILED — Binance API error: ... (code=-1013)` | Quantity/price violates exchange filters (e.g. below minimum notional, wrong step size) | Check the symbol's trading rules via `/fapi/v1/exchangeInfo` |
| LIMIT/STOP_LIMIT order accepted but `Status: NEW` forever | Normal — the order is resting on the book, unfilled | Check status later, or place at a fillable price |

## Future Improvements

- Add a `--dry-run` flag to validate and print the order without submitting it
- Add automated unit tests (`pytest`) with a mocked `requests.Session`,
  committed to CI, rather than the ad-hoc scripts used during development
- Auto-round quantity/price to each symbol's exchange filter step size
- Add retry logic with exponential backoff for transient network errors
- Support reading orders from a batch file (CSV/JSON) for bulk submission
- Add a `--json` output mode for machine-readable results
- Package as an installable CLI (`pip install .`) with a console-script entry point
- Additional order types: OCO, TWAP, Grid

## Requirements

- Python 3.11+
- See `requirements.txt` for pinned dependencies (`requests`, `python-dotenv`)

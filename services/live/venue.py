"""Fixed-origin authorization, eligibility, and read-only account checks."""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import os
import re
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from polymarket import SecureClient

import httpx

from .model import Credentials, require, session_expiry

GEO_URL = "https://polymarket.com/api/geoblock"
SCOPE_PATH = "/v1/user/session-signers"
EXCHANGE = "0xe111180000d2663c0091e4f400237545b87b996b"
UNIT = Decimal(1_000_000)


def read_json(http, url, *, headers=None):
    response = http.get(url, headers=headers)
    require(
        response.status_code == 200
        and response.headers.get("content-type", "").split(";")[0].lower() == "application/json",
        "VENUE_RESPONSE",
    )
    return response.json()


def geoblock(http):
    data = read_json(http, GEO_URL)
    require(
        isinstance(data, dict)
        and data.get("blocked") is False
        and isinstance(data.get("ip"), str)
        and re.fullmatch("[A-Z]{2}", data.get("country", ""))
        and isinstance(data.get("region"), str)
        and re.fullmatch("[A-Za-z0-9-]{0,12}", data["region"]),
        "GEOBLOCK",
    )
    ipaddress.ip_address(data["ip"])


class Venue:
    def __init__(self, credentials: Credentials):
        self.credentials = credentials
        self.client: SecureClient | None = None
        self.http = httpx.Client(
            timeout=3.0, trust_env=False, follow_redirects=False, headers={"Accept": "application/json"}
        )

    def authorize(self, wallet):
        cred = self.credentials
        require(wallet.lower() == cred.wallet_address.lower(), "WALLET_MISMATCH")
        # The SDK's HTTP client reads standard proxy variables. All market and
        # eligibility requests must use the same direct service egress.
        require(
            not any(v for k, v in os.environ.items() if k.lower() in ("http_proxy", "https_proxy", "all_proxy")),
            "PROXY_CONFIGURED",
        )
        geoblock(self.http)
        now = datetime.now(UTC)
        stamp = str(int(now.timestamp()))
        secret = base64.urlsafe_b64decode(cred.owner_api_secret + "=" * (-len(cred.owner_api_secret) % 4))
        digest = hmac.new(secret, (stamp + "GET" + SCOPE_PATH).encode(), hashlib.sha256).digest()
        headers = {
            "POLY_ADDRESS": cred.owner_address,
            "POLY_API_KEY": cred.owner_api_key,
            "POLY_PASSPHRASE": cred.owner_api_passphrase,
            "POLY_TIMESTAMP": stamp,
            "POLY_SIGNATURE": base64.urlsafe_b64encode(digest).decode(),
        }
        proof = read_json(self.http, "https://clob.polymarket.com" + SCOPE_PATH, headers=headers)
        expiry = session_expiry(proof, cred, now)
        if self.client is None:
            from eth_account import Account
            from polymarket import ApiKeyCreds, SecureClient

            require(
                Account.from_key(cred.session_private_key).address.lower() == cred.session_address.lower(),
                "SESSION_IDENTITY",
            )
            keys = ApiKeyCreds.model_validate(
                {
                    "apiKey": cred.session_api_key,
                    "secret": cred.session_api_secret,
                    "passphrase": cred.session_api_passphrase,
                }
            )
            # Public create() can deploy wallets. The pinned SDK's automatic
            # credential validator can also create/derive keys on rejection.
            # Attach the provided keys only, then validate with the read-only
            # authenticated key listing below.
            self.client = SecureClient._create(
                private_key=cred.session_private_key,
                wallet=cred.wallet_address,
                credentials=keys,
                validate_credentials=False,
            )
        require(
            self.client.wallet.lower() == wallet.lower()
            and self.client.wallet_type == "DEPOSIT_WALLET"
            and self.client.signer.lower() == cred.session_address.lower(),
            "SESSION_IDENTITY",
        )
        require(cred.session_api_key in self.client.fetch_api_keys(), "SESSION_CREDENTIALS")
        return expiry

    def account(self, wallet):
        expiry = self.authorize(wallet)
        assert self.client is not None
        require(self.client.get_closed_only_mode() is False, "CLOSED_ONLY")
        require(next(self.client.list_wallet_open_orders().iter_items(), None) is None, "OPEN_ORDERS")
        balance = self.client.get_balance_allowance(asset_type="COLLATERAL")
        allowances = {address.lower(): units for address, units in balance.allowances.items()}
        funds = min(balance.balance, allowances.get(EXCHANGE, 0)) / UNIT
        require(funds > 0, "FUNDS_UNAVAILABLE")
        return expiry, funds

    def market(self, row):
        require(str(row["market_id"]).isdigit(), "MARKET_ID")
        return read_json(self.http, "https://gamma-api.polymarket.com/markets/" + str(row["market_id"]))

    def close(self):
        if self.client:
            self.client.close()
        self.http.close()

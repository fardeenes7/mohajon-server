"""
shipping/couriers/pathao/client.py

Thin HTTP client for the Pathao Courier Merchant API (OAuth 2.0).

Credentials are resolved per-shop from shipping.CourierAccount. Access tokens
are cached in Redis (django.core.cache) keyed by shop so we reuse them across
requests until they near expiry, then re-issue (using the refresh token when
available).

Docs: planning/third-party-api/pathao/README.md
"""

from __future__ import annotations

import logging

import requests
from django.core.cache import cache

logger = logging.getLogger(__name__)

SANDBOX_BASE_URL = "https://courier-api-sandbox.pathao.com"
PRODUCTION_BASE_URL = "https://api-hermes.pathao.com"

# Refresh a little before the real expiry to avoid racing an in-flight request.
TOKEN_EXPIRY_MARGIN = 300  # seconds
DEFAULT_TIMEOUT = 20  # seconds


class PathaoError(Exception):
    """Raised when the Pathao API returns an error or an unexpected response."""


class PathaoClient:
    """
    Stateless-per-request wrapper around the Pathao API for a single shop.

    Instantiate with the shop's decoded credential blob:
        {
            "client_id": "...",
            "client_secret": "...",
            "username": "...",
            "password": "...",
        }
    """

    def __init__(self, *, shop_id: str, credentials: dict, is_test_mode: bool = False):
        self.shop_id = str(shop_id)
        self.creds = credentials or {}
        self.base_url = SANDBOX_BASE_URL if is_test_mode else PRODUCTION_BASE_URL
        self.client_id = self.creds.get("client_id")
        self.client_secret = self.creds.get("client_secret")
        self.username = self.creds.get("username")
        self.password = self.creds.get("password")

    # ── Auth ────────────────────────────────────────────────────────────────

    @property
    def _token_cache_key(self) -> str:
        return f"pathao_token:{self.shop_id}"

    @property
    def _refresh_cache_key(self) -> str:
        return f"pathao_refresh:{self.shop_id}"

    def _issue_token(self) -> dict:
        """POST /aladdin/api/v1/issue-token — password grant."""
        payload = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "grant_type": "password",
            "username": self.username,
            "password": self.password,
        }
        data = self._raw_post("/aladdin/api/v1/issue-token", payload, token=None)
        return data

    def _refresh_token(self, refresh_token: str) -> dict:
        """POST /aladdin/api/v1/issue-token — refresh_token grant."""
        payload = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }
        return self._raw_post("/aladdin/api/v1/issue-token", payload, token=None)

    def _store_token(self, data: dict) -> str:
        access_token = data.get("access_token")
        if not access_token:
            raise PathaoError("Pathao token response missing access_token.")
        expires_in = int(data.get("expires_in") or 0)
        ttl = max(expires_in - TOKEN_EXPIRY_MARGIN, 60)
        cache.set(self._token_cache_key, access_token, timeout=ttl)
        if data.get("refresh_token"):
            # Refresh tokens live longer than access tokens; keep beyond the access TTL.
            cache.set(self._refresh_cache_key, data["refresh_token"], timeout=ttl + 86400)
        return access_token

    def get_access_token(self, *, force: bool = False) -> str:
        """
        Return a valid access token, using the cached one when possible.

        Tries: cache → refresh grant → password grant.
        """
        if not force:
            cached = cache.get(self._token_cache_key)
            if cached:
                return cached

        refresh_token = cache.get(self._refresh_cache_key)
        if refresh_token:
            try:
                return self._store_token(self._refresh_token(refresh_token))
            except PathaoError:
                logger.info("Pathao refresh token failed for shop %s; re-issuing.", self.shop_id)

        return self._store_token(self._issue_token())

    # ── HTTP plumbing ─────────────────────────────────────────────────────────

    def _raw_post(self, path: str, payload: dict, token: str | None) -> dict:
        return self._request("POST", path, token=token, json=payload)

    def _request(
        self,
        method: str,
        path: str,
        *,
        token: str | None = "AUTO",
        json: dict | None = None,
        retry_on_auth: bool = True,
    ) -> dict:
        if token == "AUTO":
            token = self.get_access_token()

        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        url = f"{self.base_url}{path}"
        try:
            response = requests.request(
                method, url, json=json, headers=headers, timeout=DEFAULT_TIMEOUT
            )
        except requests.RequestException as exc:
            raise PathaoError(f"Pathao request failed: {exc}") from exc

        # Token expired mid-flight → re-issue once and retry.
        if response.status_code == 401 and retry_on_auth and token is not None:
            fresh = self.get_access_token(force=True)
            return self._request(
                method, path, token=fresh, json=json, retry_on_auth=False
            )

        try:
            data = response.json()
        except ValueError:
            data = {}

        if response.status_code >= 400:
            message = data.get("message") or data.get("error") or response.text
            raise PathaoError(f"Pathao API error ({response.status_code}): {message}")
        return data

    # ── Stores ──────────────────────────────────────────────────────────────

    def create_store(self, store: dict) -> dict:
        """POST /aladdin/api/v1/stores"""
        return self._request("POST", "/aladdin/api/v1/stores", json=store)

    def list_stores(self) -> dict:
        """GET /aladdin/api/v1/stores"""
        return self._request("GET", "/aladdin/api/v1/stores")

    # ── Orders ────────────────────────────────────────────────────────────────

    def create_order(self, order: dict) -> dict:
        """POST /aladdin/api/v1/orders"""
        return self._request("POST", "/aladdin/api/v1/orders", json=order)

    def create_bulk_orders(self, orders: list[dict]) -> dict:
        """POST /aladdin/api/v1/orders/bulk"""
        return self._request("POST", "/aladdin/api/v1/orders/bulk", json={"orders": orders})

    def get_order_info(self, consignment_id: str) -> dict:
        """GET /aladdin/api/v1/orders/{consignment_id}/info"""
        return self._request("GET", f"/aladdin/api/v1/orders/{consignment_id}/info")

    # ── Locations ─────────────────────────────────────────────────────────────

    def get_cities(self) -> dict:
        """GET /aladdin/api/v1/city-list"""
        return self._request("GET", "/aladdin/api/v1/city-list")

    def get_zones(self, city_id: int) -> dict:
        """GET /aladdin/api/v1/cities/{city_id}/zone-list"""
        return self._request("GET", f"/aladdin/api/v1/cities/{city_id}/zone-list")

    def get_areas(self, zone_id: int) -> dict:
        """GET /aladdin/api/v1/zones/{zone_id}/area-list"""
        return self._request("GET", f"/aladdin/api/v1/zones/{zone_id}/area-list")

    # ── Pricing ───────────────────────────────────────────────────────────────

    def calculate_price(self, price_request: dict) -> dict:
        """POST /aladdin/api/v1/merchant/price-plan"""
        return self._request(
            "POST", "/aladdin/api/v1/merchant/price-plan", json=price_request
        )

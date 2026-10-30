"""PayPal sandbox client. Only the gateway process constructs this; agents never see the credentials."""
import os
import time
import uuid

import httpx

SANDBOX = "https://api-m.sandbox.paypal.com"
# PayPal's published sandbox test card. It stands in for the business's payment method.
TEST_CARD = {"number": "4111111111111111", "expiry": "2030-12", "security_code": "123", "name": "Probation Cafe"}


class PayPalError(Exception):
    def __init__(self, status: int, body: str):
        super().__init__(f"PayPal {status}: {body[:300]}")
        self.status = status


class PayPal:
    def __init__(self, client_id: str | None = None, secret: str | None = None, base: str = SANDBOX):
        self.client_id = client_id or os.environ.get("PAYPAL_CLIENT_ID", "")
        self.secret = secret or os.environ.get("PAYPAL_CLIENT_SECRET", "")
        self.base, self._token, self._expires = base, None, 0.0

    def _headers(self) -> dict:
        if not self._token or time.time() > self._expires:
            r = httpx.post(f"{self.base}/v1/oauth2/token", data={"grant_type": "client_credentials"},
                           auth=(self.client_id, self.secret), timeout=30)
            if r.status_code != 200:
                raise PayPalError(r.status_code, r.text)
            self._token, self._expires = r.json()["access_token"], time.time() + r.json()["expires_in"] - 60
        return {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}

    def _call(self, method: str, path: str, **kw) -> dict:
        r = httpx.request(method, f"{self.base}{path}", headers={**self._headers(), **kw.pop("headers", {})},
                          timeout=60, **kw)
        if r.status_code >= 400:
            raise PayPalError(r.status_code, r.text)
        return r.json() if r.content else {}

    def purchase(self, amount_usd: float, description: str, custom_id: str) -> dict:
        """Create and capture one order. custom_id carries the agent, so attribution can be read back from PayPal."""
        order = self._call("POST", "/v2/checkout/orders", headers={"PayPal-Request-Id": str(uuid.uuid4())}, json={
            "intent": "CAPTURE",
            "purchase_units": [{"description": description[:127], "custom_id": custom_id[:127],
                                "amount": {"currency_code": "USD", "value": f"{amount_usd:.2f}"}}],
            "payment_source": {"card": TEST_CARD}})
        capture = order["purchase_units"][0]["payments"]["captures"][0]
        return {"order_id": order["id"], "capture_id": capture["id"], "status": capture["status"]}

    def refund(self, capture_id: str) -> dict:
        r = self._call("POST", f"/v2/payments/captures/{capture_id}/refund", json={},
                       headers={"PayPal-Request-Id": str(uuid.uuid4())})
        return {"refund_id": r["id"], "status": r["status"]}

    def get_capture(self, capture_id: str) -> dict:
        return self._call("GET", f"/v2/payments/captures/{capture_id}")

    def get_refund(self, refund_id: str) -> dict:
        return self._call("GET", f"/v2/payments/refunds/{refund_id}")

    def list_disputes(self) -> list:
        return self._call("GET", "/v1/customer/disputes").get("items", [])

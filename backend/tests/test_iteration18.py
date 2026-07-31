"""Iteration 18 targeted email-history compatibility and Stripe configuration tests."""
import asyncio
import uuid
from datetime import datetime, timezone

import pytest
import requests
import stripe
from bson import ObjectId
from dotenv import dotenv_values
from motor.motor_asyncio import AsyncIOMotorClient

from conftest import BASE_URL

BACKEND_ENV = dotenv_values("/app/backend/.env")
ENTRY_PAYLOAD = {
    "entry_date": datetime.now(timezone.utc).isoformat(),
    "notarization_type": "Acknowledgment",
    "document_type": "Affidavit",
    "document_description": "TEST iteration 18 email history",
    "signer_name": f"TEST Iteration18 {uuid.uuid4().hex[:10]}",
    "signer_address": "18 QA Avenue",
    "signer_phone": "",
    "signer_email": "",
    "id_type": "State ID",
    "id_number": "TEST-I18-ID",
    "id_expiration": "2029-07-31",
    "id_issued_by": "PA",
    "witness_name": "",
    "fee": 1.0,
    "notes": "TEST iteration 18",
    "signature_data": "",
}


def _db_call(operation):
    async def execute():
        client = AsyncIOMotorClient(BACKEND_ENV["MONGO_URL"])
        try:
            return await operation(client[BACKEND_ENV["DB_NAME"]])
        finally:
            client.close()

    return asyncio.run(execute())


class TestEmailHistorySchemaCompatibility:
    """Email history maps new and legacy writer fields and gives new fields precedence."""

    @pytest.mark.parametrize(
        "row_index,expected_to,expected_error",
        [
            (0, "new@x.com", "new err"),
            (1, "legacy@x.com", "old err"),
            (2, "preferred@x.com", "preferred err"),
        ],
        ids=["new-writer-schema", "legacy-schema", "new-fields-take-precedence"],
    )
    def test_new_legacy_and_mixed_rows_serialize_to_stable_contract(
        self,
        authenticated_client,
        credentials,
        row_index,
        expected_to,
        expected_error,
    ):
        created = authenticated_client.post(
            f"{BASE_URL}/api/entries", json=ENTRY_PAYLOAD, timeout=20
        )
        assert created.status_code == 200, created.text
        entry_id = created.json()["id"]
        row_ids = [ObjectId(), ObjectId(), ObjectId()]

        async def seed(database):
            admin = await database.users.find_one({"email": credentials["email"].lower()})
            assert admin is not None
            common = {
                "user_id": str(admin["_id"]),
                "entry_id": entry_id,
                "subject": "TEST iteration 18",
                "status": "failed",
                "sent_at": datetime.now(timezone.utc).isoformat(),
            }
            await database.certificate_email_log.insert_many([
                {
                    "_id": row_ids[0],
                    **common,
                    "recipient": "new@x.com",
                    "error_msg": "new err",
                },
                {
                    "_id": row_ids[1],
                    **common,
                    "to": "legacy@x.com",
                    "error": "old err",
                },
                {
                    "_id": row_ids[2],
                    **common,
                    "recipient": "preferred@x.com",
                    "error_msg": "preferred err",
                    "to": "fallback@x.com",
                    "error": "fallback err",
                },
            ])

        _db_call(seed)
        try:
            response = authenticated_client.get(
                f"{BASE_URL}/api/entries/{entry_id}/email-history", timeout=20
            )
            assert response.status_code == 200, response.text
            by_id = {row["id"]: row for row in response.json()}
            actual = by_id[str(row_ids[row_index])]
            assert actual["to"] == expected_to
            assert actual["error"] == expected_error
        finally:
            authenticated_client.delete(f"{BASE_URL}/api/entries/{entry_id}", timeout=20)

            async def cleanup(database):
                await database.certificate_email_log.delete_many(
                    {"_id": {"$in": row_ids}}
                )
                await database.clients.delete_many(
                    {"name": ENTRY_PAYLOAD["signer_name"]}
                )

            _db_call(cleanup)


class TestStripeSecretConfiguration:
    """Billing registration fails immediately when its required Stripe secret is absent."""

    def test_register_billing_requires_stripe_secret_key(self, monkeypatch):
        monkeypatch.syspath_prepend("/app/backend")
        from routes.billing import register_billing

        monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
        with pytest.raises(KeyError, match="STRIPE_SECRET_KEY"):
            register_billing(None, None, None)



class TestBillingPortalRegression:
    """Portal rejects foreign origins and still accepts the configured app origin."""

    def test_checkout_customer_portal_rejects_foreign_origin_and_accepts_valid_origin(self):
        email = f"test.iter18.portal.{uuid.uuid4().hex}@gmail.com"
        session = requests.Session()
        session.headers.update({"Content-Type": "application/json"})
        registered = session.post(
            f"{BASE_URL}/api/auth/register",
            json={
                "email": email,
                "password": "TEST_Iteration18_2026!",
                "name": "TEST Iteration18 Portal",
            },
            timeout=20,
        )
        assert registered.status_code == 200, registered.text
        user_id = registered.json()["id"]
        customer_id = None
        try:
            checkout = session.post(
                f"{BASE_URL}/api/billing/checkout",
                json={"lookup_key": "notarium_pro_monthly", "origin_url": BASE_URL},
                timeout=60,
            )
            assert checkout.status_code == 200, checkout.text
            assert checkout.json()["session_id"].startswith("cs_test_")

            portal = session.post(
                f"{BASE_URL}/api/billing/portal",
                json={"origin_url": BASE_URL},
                timeout=60,
            )
            assert portal.status_code == 200, portal.text
            assert portal.json()["portal_url"].startswith("https://billing.stripe.com/")

            rejected = session.post(
                f"{BASE_URL}/api/billing/portal",
                json={"origin_url": "https://evil.com"},
                timeout=60,
            )
            assert rejected.status_code == 400, rejected.text
            assert rejected.json()["detail"] == "origin_url host not allowed"

            async def fetch_customer(database):
                user = await database.users.find_one({"_id": ObjectId(user_id)})
                return user.get("stripe_customer_id") if user else None

            customer_id = _db_call(fetch_customer)
            assert isinstance(customer_id, str) and customer_id.startswith("cus_")
        finally:
            async def cleanup(database):
                await database.payment_transactions.delete_many({"user_id": user_id})
                await database.login_attempts.delete_many(
                    {"identifier": f"email:{email}"}
                )
                await database.users.delete_one({"_id": ObjectId(user_id)})

            _db_call(cleanup)
            if customer_id:
                stripe.api_key = BACKEND_ENV["STRIPE_SECRET_KEY"]
                try:
                    stripe.Customer.delete(customer_id)
                except stripe.error.StripeError:
                    pass

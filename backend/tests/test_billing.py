"""Stripe billing, subscription gate, admin bypass, isolation, and regression API tests."""
import asyncio
import re
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import bcrypt
import pytest
import requests
import stripe
from bson import ObjectId
from dotenv import dotenv_values
from motor.motor_asyncio import AsyncIOMotorClient

from conftest import BASE_URL

BACKEND_ENV = dotenv_values("/app/backend/.env")
TEST_PASSWORD = "TEST_Billing_2026!"
ENTRY_PAYLOAD = {
    "entry_date": datetime.now(timezone.utc).isoformat(),
    "notarization_type": "Acknowledgment",
    "document_type": "Affidavit",
    "document_description": "TEST billing gate entry",
    "signer_name": "TEST Billing Signer",
    "signer_address": "123 QA Avenue",
    "signer_phone": "",
    "signer_email": "",
    "id_type": "State ID",
    "id_number": "TEST-BILLING-ID",
    "id_expiration": "2029-07-31",
    "id_issued_by": "PA",
    "witness_name": "",
    "fee": 1.0,
    "notes": "TEST billing subscription gate",
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


def _register_account(label):
    session = requests.Session()
    session.headers.update({"Content-Type": "application/json"})
    email = f"test.billing.{label}.{uuid.uuid4().hex}@gmail.com"
    response = session.post(
        f"{BASE_URL}/api/auth/register",
        json={"email": email, "password": TEST_PASSWORD, "name": f"TEST Billing {label}"},
        timeout=30,
    )
    assert response.status_code == 200, response.text
    return {"session": session, "email": email, "id": response.json()["id"]}


def _cleanup_account(account):
    async def cleanup(database):
        user = await database.users.find_one({"email": account["email"]})
        if not user:
            return None
        user_id = str(user["_id"])
        customer_id = user.get("stripe_customer_id")
        await database.payment_transactions.delete_many({"user_id": user_id})
        await database.entries.delete_many({"user_id": user_id})
        await database.clients.delete_many({"user_id": user_id})
        await database.document_types.delete_many({"user_id": user_id})
        await database.signer_intakes.delete_many({"user_id": user_id})
        await database.login_attempts.delete_many({"identifier": {"$regex": f"{re.escape(account['email'])}$"}})
        await database.users.delete_one({"_id": user["_id"]})
        return customer_id

    customer_id = _db_call(cleanup)
    if customer_id:
        stripe.api_key = BACKEND_ENV["STRIPE_SECRET_KEY"]
        try:
            stripe.Customer.delete(customer_id)
        except stripe.error.StripeError:
            pass


@pytest.fixture(scope="class")
def checkout_account():
    account = _register_account("checkout")
    yield account
    _cleanup_account(account)


@pytest.fixture
def never_subscribed_account():
    account = _register_account("portal")
    yield account
    _cleanup_account(account)


@pytest.fixture(scope="class")
def foreign_billing_account():
    account = _register_account("foreign")
    yield account
    _cleanup_account(account)


@pytest.fixture(scope="class")
def expired_trial_account():
    email = f"test.billing.expired.{uuid.uuid4().hex}@gmail.com"
    user_id = ObjectId()
    entry_id = ObjectId()
    created_at = (datetime.now(timezone.utc) - timedelta(days=20)).isoformat()

    async def seed(database):
        await database.users.insert_one({
            "_id": user_id,
            "email": email,
            "password_hash": bcrypt.hashpw(TEST_PASSWORD.encode(), bcrypt.gensalt()).decode(),
            "name": "TEST Expired Trial Notary",
            "role": "notary",
            "created_at": created_at,
        })
        await database.entries.insert_one({
            "_id": entry_id,
            **ENTRY_PAYLOAD,
            "user_id": str(user_id),
            "created_at": created_at,
        })

    _db_call(seed)
    session = requests.Session()
    session.headers.update({"Content-Type": "application/json"})
    login = session.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": email, "password": TEST_PASSWORD},
        timeout=30,
    )
    assert login.status_code == 200, login.text
    yield {"session": session, "email": email, "id": str(user_id), "entry_id": str(entry_id)}
    _cleanup_account({"email": email})


@pytest.fixture(scope="class")
def admin_billing_state(credentials):
    email = credentials["email"].lower()

    async def snapshot(database):
        return await database.users.find_one({"email": email})

    original = _db_call(snapshot)
    assert original is not None and original.get("role") == "admin"
    yield original

    billing_fields = (
        "created_at", "subscription_status", "subscription_plan_key",
        "subscription_current_period_end", "subscription_cancel_at_period_end",
    )

    async def restore(database):
        set_values = {field: original[field] for field in billing_fields if field in original}
        unset_values = {field: "" for field in billing_fields if field not in original}
        update = {}
        if set_values:
            update["$set"] = set_values
        if unset_values:
            update["$unset"] = unset_values
        if update:
            await database.users.update_one({"_id": original["_id"]}, update)

    _db_call(restore)


def _assert_subscription_required(response):
    assert response.status_code == 402, response.text
    detail = response.json()["detail"]
    assert detail["error"] == "subscription_required"
    assert detail["billing_status"] == "expired"
    assert isinstance(detail["message"], str) and detail["message"]


class TestBillingCheckout:
    """Fresh trial status, checkout plans, transaction persistence, auth and ownership."""

    def test_fresh_user_status_has_trial_and_exact_plan_catalog(self, checkout_account):
        response = checkout_account["session"].get(f"{BASE_URL}/api/billing/status", timeout=30)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["provider"] == "stripe"
        assert data["effective_status"] == "trialing"
        assert data["raw_status"] is None
        assert data["role"] == "notary" and data["is_admin_bypass"] is False
        assert data["trial_days_left"] in (13, 14)
        assert datetime.fromisoformat(data["trial_ends_at"]) > datetime.now(timezone.utc)
        assert isinstance(data["publishable_key"], str) and data["publishable_key"].startswith("pk_test_")
        assert data["plans"] == [
            {
                "lookup_key": "notarium_pro_monthly", "label": "Monthly",
                "amount": 19.0, "currency": "usd", "interval": "month",
            },
            {
                "lookup_key": "notarium_pro_yearly", "label": "Yearly",
                "amount": 190.0, "currency": "usd", "interval": "year",
                "savings_label": "Save $38 (2 months free)",
            },
        ]

    def test_checkout_requires_auth_and_rejects_unknown_plan(self, checkout_account):
        payload = {"lookup_key": "notarium_pro_monthly", "origin_url": BASE_URL}
        unauthorized = requests.post(f"{BASE_URL}/api/billing/checkout", json=payload, timeout=30)
        assert unauthorized.status_code == 401
        assert unauthorized.json()["detail"] == "Not authenticated"

        unknown = checkout_account["session"].post(
            f"{BASE_URL}/api/billing/checkout",
            json={"lookup_key": "notarium_pro_unknown", "origin_url": BASE_URL},
            timeout=30,
        )
        assert unknown.status_code == 400
        assert unknown.json()["detail"] == "Unknown plan"

    @pytest.mark.parametrize(
        "lookup_key,expected_amount",
        [("notarium_pro_monthly", 1900), ("notarium_pro_yearly", 19000)],
    )
    def test_checkout_returns_stripe_url_and_persists_transaction(
        self, checkout_account, lookup_key, expected_amount
    ):
        response = checkout_account["session"].post(
            f"{BASE_URL}/api/billing/checkout",
            json={"lookup_key": lookup_key, "origin_url": BASE_URL},
            timeout=60,
        )
        assert response.status_code == 200, response.text
        data = response.json()
        parsed = urlparse(data["checkout_url"])
        assert parsed.scheme == "https" and parsed.netloc.endswith("stripe.com")
        assert isinstance(data["session_id"], str) and data["session_id"].startswith("cs_test_")

        async def fetch(database):
            return await database.payment_transactions.find_one({"session_id": data["session_id"]})

        record = _db_call(fetch)
        assert record is not None
        assert record["user_id"] == checkout_account["id"]
        assert record["lookup_key"] == lookup_key
        assert record["amount"] == expected_amount and record["currency"] == "usd"
        assert record["status"] == "initiated" and record["payment_status"] == "pending"
        assert isinstance(record["created_at"], datetime) and isinstance(record["updated_at"], datetime)
        checkout_account[f"{lookup_key}_session_id"] = data["session_id"]

    def test_session_status_is_isolated_between_users(self, checkout_account, foreign_billing_account):
        session_id = checkout_account["notarium_pro_monthly_session_id"]
        foreign = foreign_billing_account["session"].get(
            f"{BASE_URL}/api/billing/session/{session_id}", timeout=30
        )
        assert foreign.status_code == 404
        assert foreign.json()["detail"] == "Session not found"

        owner = checkout_account["session"].get(
            f"{BASE_URL}/api/billing/session/{session_id}", timeout=30
        )
        assert owner.status_code == 200, owner.text
        assert owner.json() == {
            "session_id": session_id,
            "status": "initiated",
            "payment_status": "pending",
        }

    def test_portal_without_customer_is_400(self, never_subscribed_account):
        response = never_subscribed_account["session"].post(
            f"{BASE_URL}/api/billing/portal", json={"origin_url": BASE_URL}, timeout=30
        )
        assert response.status_code == 400
        assert response.json()["detail"] == "No Stripe customer for this account"


class TestExpiredTrialGate:
    """Expired notaries are read-only and receive the structured 402 subscription gate."""

    def test_status_is_expired_and_entries_remain_readable(self, expired_trial_account):
        session = expired_trial_account["session"]
        status = session.get(f"{BASE_URL}/api/billing/status", timeout=30)
        assert status.status_code == 200
        data = status.json()
        assert data["effective_status"] == "expired"
        assert data["trial_days_left"] == 0 and data["is_admin_bypass"] is False

        entries = session.get(f"{BASE_URL}/api/entries", timeout=30)
        assert entries.status_code == 200
        assert any(row["id"] == expired_trial_account["entry_id"] for row in entries.json())

    def test_all_required_write_routes_return_structured_402(self, expired_trial_account):
        session = expired_trial_account["session"]
        entry_id = expired_trial_account["entry_id"]
        calls = [
            session.post(f"{BASE_URL}/api/entries", json=ENTRY_PAYLOAD, timeout=30),
            session.post(f"{BASE_URL}/api/clients", json={"name": "TEST Blocked Client"}, timeout=30),
            session.post(
                f"{BASE_URL}/api/document-types",
                json={"name": "TEST Blocked Type", "description": "billing gate"},
                timeout=30,
            ),
            session.post(
                f"{BASE_URL}/api/intakes",
                json={"label": "TEST Blocked Intake", "expires_hours": 24},
                timeout=30,
            ),
            session.put(f"{BASE_URL}/api/entries/{entry_id}", json=ENTRY_PAYLOAD, timeout=30),
        ]
        for response in calls:
            _assert_subscription_required(response)


class TestAdminBillingBypassAndRegression:
    """Admin bypass and existing authenticated read endpoint regression checks."""

    def test_admin_bypasses_expired_trial_and_can_create_entry(
        self, authenticated_client, credentials, admin_billing_state
    ):
        old_created_at = (datetime.now(timezone.utc) - timedelta(days=20)).isoformat()

        async def expire(database):
            await database.users.update_one(
                {"email": credentials["email"].lower()},
                {
                    "$set": {"created_at": old_created_at},
                    "$unset": {
                        "subscription_status": "", "subscription_plan_key": "",
                        "subscription_current_period_end": "", "subscription_cancel_at_period_end": "",
                    },
                },
            )

        _db_call(expire)
        status = authenticated_client.get(f"{BASE_URL}/api/billing/status", timeout=30)
        assert status.status_code == 200
        data = status.json()
        assert data["role"] == "admin" and data["is_admin_bypass"] is True
        assert data["effective_status"] == "expired" and data["trial_days_left"] == 0

        payload = {**ENTRY_PAYLOAD, "signer_name": f"TEST Admin Bypass {uuid.uuid4().hex[:8]}"}
        created = authenticated_client.post(f"{BASE_URL}/api/entries", json=payload, timeout=30)
        assert created.status_code == 200, created.text
        entry = created.json()
        assert entry["signer_name"] == payload["signer_name"]
        try:
            fetched = authenticated_client.get(f"{BASE_URL}/api/entries/{entry['id']}", timeout=30)
            assert fetched.status_code == 200 and fetched.json()["signer_name"] == payload["signer_name"]
        finally:
            deleted = authenticated_client.delete(f"{BASE_URL}/api/entries/{entry['id']}", timeout=30)
            assert deleted.status_code == 200

            async def cleanup_client(database):
                await database.clients.delete_many({
                    "user_id": str(admin_billing_state["_id"]), "name": payload["signer_name"]
                })

            _db_call(cleanup_client)

    @pytest.mark.parametrize(
        "path,validator",
        [
            ("/api/entries", lambda data: isinstance(data, list)),
            ("/api/clients", lambda data: isinstance(data, list)),
            ("/api/document-types", lambda data: isinstance(data, list)),
            ("/api/auth/me", lambda data: data.get("role") == "admin" and isinstance(data.get("id"), str)),
            (
                "/api/stats/dashboard",
                lambda data: isinstance(data.get("total_entries"), int)
                and isinstance(data.get("monthly_series"), list),
            ),
            (
                "/api/admin/roster",
                lambda data: isinstance(data.get("total"), int)
                and isinstance(data.get("notaries"), list),
            ),
        ],
    )
    def test_existing_read_endpoints_regression(self, authenticated_client, path, validator):
        response = authenticated_client.get(f"{BASE_URL}{path}", timeout=30)
        assert response.status_code == 200, response.text
        assert validator(response.json()), response.text

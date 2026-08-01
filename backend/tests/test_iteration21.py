"""Iteration 21 backend tests for signer-intake follow-up reminders and regressions."""
import asyncio
import time
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
import pytest
import requests
from bson import ObjectId
from dotenv import dotenv_values
from motor.motor_asyncio import AsyncIOMotorClient

from conftest import BASE_URL

BACKEND_ENV = dotenv_values("/app/backend/.env")


def _db_call(operation):
    async def execute():
        client = AsyncIOMotorClient(BACKEND_ENV["MONGO_URL"])
        try:
            return await operation(client[BACKEND_ENV["DB_NAME"]])
        finally:
            client.close()

    return asyncio.run(execute())


def _admin(credentials):
    async def fetch(database):
        return await database.users.find_one({"email": credentials["email"].lower()})

    return _db_call(fetch)


def _intake(intake_id):
    async def fetch(database):
        return await database.signer_intakes.find_one({"_id": ObjectId(intake_id)})

    return _db_call(fetch)


@pytest.fixture
def iteration21_cleanup():
    intake_ids = []
    run_ids = []
    login_identifiers = []
    yield {"intake_ids": intake_ids, "run_ids": run_ids, "login_identifiers": login_identifiers}

    async def cleanup(database):
        valid_ids = [ObjectId(value) for value in intake_ids if ObjectId.is_valid(value)]
        if valid_ids:
            await database.signer_intakes.delete_many({"_id": {"$in": valid_ids}})
        if run_ids:
            await database.cron_runs.delete_many({"run_id": {"$in": run_ids}})
        if login_identifiers:
            await database.login_attempts.delete_many({"identifier": {"$in": login_identifiers}})

    _db_call(cleanup)


class TestIntakeReminderFields:
    """Create/list contracts for optional follow-up fields and backwards compatibility."""

    def test_create_with_reminder_fields_echoes_and_persists(
        self, authenticated_client, credentials, iteration21_cleanup
    ):
        appointment_at = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
        payload = {
            "label": f"TEST_Iteration21_Fields_{uuid.uuid4().hex[:8]}",
            "appointment_note": "TEST Follow-up appointment",
            "expires_hours": 168,
            "signer_email": credentials["email"].upper(),
            "appointment_at": appointment_at,
        }
        response = authenticated_client.post(
            f"{BASE_URL}/api/intakes", json=payload, timeout=30
        )
        assert response.status_code == 200, response.text
        data = response.json()
        iteration21_cleanup["intake_ids"].append(data["id"])
        assert data["signer_email"] == credentials["email"].lower()
        assert data["appointment_at"] == appointment_at
        assert data["reminder_sent_at"] is None
        assert data["notary_name"] == "Matthew Yurksaitis"
        assert data["status"] == "pending"

        listed = authenticated_client.get(f"{BASE_URL}/api/intakes", timeout=30)
        assert listed.status_code == 200, listed.text
        row = next(item for item in listed.json() if item["id"] == data["id"])
        assert row["signer_email"] == credentials["email"].lower()
        assert row["appointment_at"] == appointment_at
        assert row["reminder_sent_at"] is None
        assert row["notary_name"] == "Matthew Yurksaitis"

    def test_create_without_new_fields_remains_backwards_compatible(
        self, authenticated_client, iteration21_cleanup
    ):
        response = authenticated_client.post(
            f"{BASE_URL}/api/intakes",
            json={"label": f"TEST_Iteration21_Legacy_{uuid.uuid4().hex[:8]}", "expires_hours": 24},
            timeout=30,
        )
        assert response.status_code == 200, response.text
        data = response.json()
        iteration21_cleanup["intake_ids"].append(data["id"])
        assert data["signer_email"] == ""
        assert data["appointment_at"] == ""
        assert data["reminder_sent_at"] is None
        assert data["status"] == "pending"
        assert isinstance(data["notary_name"], str) and data["notary_name"]

    def test_malformed_appointment_datetime_is_rejected(
        self, authenticated_client, credentials, iteration21_cleanup
    ):
        response = authenticated_client.post(
            f"{BASE_URL}/api/intakes",
            json={
                "label": "TEST_Iteration21_Invalid_Date",
                "signer_email": credentials["email"],
                "appointment_at": "tomorrow around lunch",
            },
            timeout=30,
        )
        if response.status_code == 200:
            iteration21_cleanup["intake_ids"].append(response.json()["id"])
        assert response.status_code in (400, 422), response.text
    def test_malformed_signer_email_is_rejected(
        self, authenticated_client, iteration21_cleanup
    ):
        response = authenticated_client.post(
            f"{BASE_URL}/api/intakes",
            json={
                "label": "TEST_Iteration21_Invalid_Email",
                "signer_email": "not-an-email-address",
                "appointment_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            },
            timeout=30,
        )
        if response.status_code == 200:
            iteration21_cleanup["intake_ids"].append(response.json()["id"])
        assert response.status_code in (400, 422), response.text


class TestManualIntakeReminder:
    """Cookie authentication, validation errors, email delivery, and reminder metadata."""

    def test_requires_cookie_and_handles_missing_email_status_and_unknown_id(
        self, authenticated_client, iteration21_cleanup
    ):
        created = authenticated_client.post(
            f"{BASE_URL}/api/intakes",
            json={"label": "TEST_Iteration21_No_Email", "expires_hours": 24},
            timeout=30,
        )
        assert created.status_code == 200, created.text
        intake_id = created.json()["id"]
        iteration21_cleanup["intake_ids"].append(intake_id)
        endpoint = f"{BASE_URL}/api/intakes/{intake_id}/send-reminder"

        unauthenticated = requests.post(endpoint, timeout=30)
        assert unauthenticated.status_code == 401
        assert unauthenticated.json()["detail"] == "Not authenticated"

        missing_email = authenticated_client.post(endpoint, timeout=30)
        assert missing_email.status_code == 400
        assert "signer email" in missing_email.json()["detail"].lower()

        marked = authenticated_client.post(
            f"{BASE_URL}/api/intakes/{intake_id}/mark-used", timeout=30
        )
        assert marked.status_code == 200 and marked.json() == {"ok": True}
        non_pending = authenticated_client.post(endpoint, timeout=30)
        assert non_pending.status_code == 400
        assert "no longer pending" in non_pending.json()["detail"].lower()

        unknown = authenticated_client.post(
            f"{BASE_URL}/api/intakes/000000000000000000000000/send-reminder", timeout=30
        )
        assert unknown.status_code == 404
        assert unknown.json()["detail"] == "Intake not found"

    def test_success_sets_sent_timestamp_and_email_id(
        self, authenticated_client, credentials, iteration21_cleanup
    ):
        appointment_at = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
        created = authenticated_client.post(
            f"{BASE_URL}/api/intakes",
            json={
                "label": "TEST_Iteration21_Manual – Signer",
                "appointment_note": "TEST Please bring identification",
                "expires_hours": 168,
                "signer_email": credentials["email"],
                "appointment_at": appointment_at,
            },
            timeout=30,
        )
        assert created.status_code == 200, created.text
        intake_id = created.json()["id"]
        iteration21_cleanup["intake_ids"].append(intake_id)

        sent = authenticated_client.post(
            f"{BASE_URL}/api/intakes/{intake_id}/send-reminder", timeout=45
        )
        assert sent.status_code == 200, sent.text
        payload = sent.json()
        assert payload["ok"] is True
        assert payload["sent_to"] == credentials["email"].lower()
        assert isinstance(payload["email_id"], str) and payload["email_id"]

        persisted = _intake(intake_id)
        assert datetime.fromisoformat(persisted["reminder_sent_at"]) <= datetime.now(timezone.utc)
        assert persisted["reminder_email_id"] == payload["email_id"]


class TestIntakeReminderCron:
    """Cron authorization, webhook idempotency, and 20h-32h selection behavior."""

    @staticmethod
    def _headers(run_id):
        return {
            "Authorization": f"Bearer {BACKEND_ENV['WEBHOOK_CRON_SECRET']}",
            "X-Webhook-Id": run_id,
        }

    def test_authentication_and_same_webhook_id_is_idempotent(self, iteration21_cleanup):
        endpoint = f"{BASE_URL}/api/cron/intake-reminders"
        no_auth = requests.post(endpoint, timeout=30)
        wrong_auth = requests.post(
            endpoint, headers={"Authorization": "Bearer TEST_WRONG"}, timeout=30
        )
        assert no_auth.status_code == 401 and no_auth.json()["detail"] == "Unauthorized"
        assert wrong_auth.status_code == 401 and wrong_auth.json()["detail"] == "Unauthorized"

        run_id = f"TEST_INTAKE_AUTH_{uuid.uuid4().hex}"
        iteration21_cleanup["run_ids"].append(run_id)
        first = requests.post(endpoint, headers=self._headers(run_id), timeout=45)
        assert first.status_code == 200, first.text
        assert first.json() == {"accepted": True, "run_id": run_id}
        second = requests.post(endpoint, headers=self._headers(run_id), timeout=30)
        assert second.status_code == 200, second.text
        assert second.json() == {"accepted": True, "duplicate": True, "run_id": run_id}

    def test_cron_only_updates_pending_in_window_with_email(
        self, credentials, iteration21_cleanup
    ):
        admin = _admin(credentials)
        assert admin is not None
        now = datetime.now(timezone.utc)
        common = {
            "user_id": str(admin["_id"]),
            "appointment_note": "TEST cron selection",
            "expires_at": (now + timedelta(days=10)).isoformat(),
            "created_at": now.isoformat(),
            "submitted_at": None,
            "data": None,
            "status": "pending",
            "reminder_sent_at": None,
        }
        docs = [
            {
                **common,
                "token": f"TEST_IN_WINDOW_{uuid.uuid4().hex}",
                "label": "TEST In Window – Signer",
                "signer_email": credentials["email"],
                "appointment_at": (now + timedelta(hours=24)).isoformat(),
            },
            {
                **common,
                "token": f"TEST_OUT_WINDOW_{uuid.uuid4().hex}",
                "label": "TEST Out Window – Signer",
                "signer_email": credentials["email"],
                "appointment_at": (now + timedelta(days=5)).isoformat(),
            },
            {
                **common,
                "token": f"TEST_NO_EMAIL_{uuid.uuid4().hex}",
                "label": "TEST No Email – Signer",
                "signer_email": "",
                "appointment_at": (now + timedelta(hours=24)).isoformat(),
            },
        ]

        async def seed(database):
            result = await database.signer_intakes.insert_many(docs)
            return [str(value) for value in result.inserted_ids]

        in_window_id, out_window_id, no_email_id = _db_call(seed)
        iteration21_cleanup["intake_ids"].extend(
            [in_window_id, out_window_id, no_email_id]
        )
        run_id = f"TEST_INTAKE_WINDOW_{uuid.uuid4().hex}"
        iteration21_cleanup["run_ids"].append(run_id)
        fired = requests.post(
            f"{BASE_URL}/api/cron/intake-reminders",
            headers=self._headers(run_id),
            timeout=45,
        )
        assert fired.status_code == 200, fired.text
        assert fired.json() == {"accepted": True, "run_id": run_id}

        deadline = time.monotonic() + 12
        while time.monotonic() < deadline and not _intake(in_window_id).get("reminder_sent_at"):
            time.sleep(0.5)
        selected = _intake(in_window_id)
        outside = _intake(out_window_id)
        missing_email = _intake(no_email_id)
        assert datetime.fromisoformat(selected["reminder_sent_at"]) <= datetime.now(timezone.utc)
        assert isinstance(selected.get("reminder_email_id"), str) and selected["reminder_email_id"]
        assert outside.get("reminder_sent_at") is None
        assert not outside.get("reminder_email_id")
        assert missing_email.get("reminder_sent_at") is None
        assert not missing_email.get("reminder_email_id")


class TestRequestedRegressions:
    """Existing authenticated reads and commission/bounce cron acknowledgements."""

    @pytest.mark.parametrize(
        "path,validator",
        [
            ("/api/auth/me", lambda body: isinstance(body.get("email"), str)),
            ("/api/entries", lambda body: isinstance(body, list)),
            ("/api/journal-presets", lambda body: isinstance(body, list)),
            ("/api/alerts/status", lambda body: isinstance(body.get("enabled"), bool)),
        ],
    )
    def test_authenticated_read_regressions(self, authenticated_client, path, validator):
        response = authenticated_client.get(f"{BASE_URL}{path}", timeout=30)
        assert response.status_code == 200, response.text
        assert validator(response.json()), response.json()

    @pytest.mark.parametrize("cron_name", ["commission-alerts", "delivery-bounces"])
    def test_existing_cron_regressions(self, cron_name, iteration21_cleanup):
        run_id = f"TEST_ITER21_{cron_name}_{uuid.uuid4().hex}"
        iteration21_cleanup["run_ids"].append(run_id)
        response = requests.post(
            f"{BASE_URL}/api/cron/{cron_name}",
            headers={
                "Authorization": f"Bearer {BACKEND_ENV['WEBHOOK_CRON_SECRET']}",
                "X-Webhook-Id": run_id,
            },
            timeout=45,
        )
        assert response.status_code == 200, response.text
        assert response.json() == {"accepted": True, "run_id": run_id}


class TestAuthPlaybookRegression:
    """Required cookie, bcrypt, CORS, lockout, and configured-admin checks."""

    def test_bcrypt_and_configured_admin_password(self, credentials):
        admin = _admin(credentials)
        assert admin is not None
        password_hash = admin.get("password_hash")
        assert isinstance(password_hash, str) and password_hash.startswith("$2b$")
        assert bcrypt.checkpw(credentials["password"].encode(), password_hash.encode())
        assert admin["name"] == BACKEND_ENV["ADMIN_NAME"]

    def test_login_sets_secure_httponly_cookies(self, credentials):
        response = requests.post(
            f"{BASE_URL}/api/auth/login", json=credentials, timeout=30
        )
        assert response.status_code == 200, response.text
        cookies = response.headers.get("set-cookie", "").lower()
        assert "access_token=" in cookies and "refresh_token=" in cookies
        assert cookies.count("httponly") >= 2
        assert cookies.count("secure") >= 2

    def test_cors_allows_credentials_for_explicit_origin(self):
        response = requests.options(
            f"{BASE_URL}/api/auth/me",
            headers={
                "Origin": BASE_URL,
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "content-type",
            },
            timeout=30,
        )
        assert response.status_code in (200, 204), response.text
        assert response.headers.get("access-control-allow-origin") == BASE_URL
        assert response.headers.get("access-control-allow-credentials") == "true"

    def test_bruteforce_lockout_after_five_failures(self, iteration21_cleanup):
        email = f"test.iter21.missing.{uuid.uuid4().hex}@example.com"
        identifier = f"email:{email}"
        iteration21_cleanup["login_identifiers"].append(identifier)
        statuses = []
        for _ in range(6):
            response = requests.post(
                f"{BASE_URL}/api/auth/login",
                json={"email": email, "password": "TEST_Wrong_Password"},
                timeout=30,
            )
            statuses.append(response.status_code)
        assert statuses[:5] == [401] * 5
        assert statuses[5] == 429

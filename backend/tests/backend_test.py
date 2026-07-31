"""API regression tests for commission alerts, signer intakes, authentication, dashboard, journal and PDF export."""
import asyncio
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
import requests
import bcrypt
import yaml
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import dotenv_values

from conftest import BASE_URL

sys.path.insert(0, "/app/backend")


VALID_SIGNER = {
    "signer_name": "TEST_Intake Signer",
    "signer_address": "123 QA Avenue",
    "signer_phone": "555-0107",
    "signer_email": "intake.qa@example.test",
    "id_type": "State ID",
    "id_number": "TEST-ID-0726",
    "id_expiration": "2029-07-31",
    "id_issued_by": "PA",
    "notes": "TEST intake end-to-end",
}


class TestAuthSecurity:
    """Authentication and security playbook checks."""

    def test_login_sets_secure_httponly_cookies(self, credentials):
        response = requests.post(f"{BASE_URL}/api/auth/login", json=credentials, timeout=20)
        assert response.status_code == 200
        assert response.json()["email"] == credentials["email"]
        raw = response.headers.get("set-cookie", "").lower()
        assert "access_token=" in raw and "refresh_token=" in raw
        assert "httponly" in raw and "secure" in raw

    def test_admin_bcrypt_hash_is_2b(self, credentials):
        env = dotenv_values("/app/backend/.env")

        async def fetch_admin():
            client = AsyncIOMotorClient(env["MONGO_URL"])
            try:
                return await client[env["DB_NAME"]].users.find_one({"email": credentials["email"].lower()})
            finally:
                client.close()

        admin = asyncio.run(fetch_admin())
        password_hash = admin.get("password_hash") if admin else None
        assert isinstance(password_hash, str)
        assert password_hash.startswith("$2b$")
        assert bcrypt.checkpw(env["ADMIN_PASSWORD"].encode(), password_hash.encode())
        assert admin["name"] == env["ADMIN_NAME"]

    def test_bruteforce_lockout_after_five_failures(self):
        unique_email = f"missing-{uuid.uuid4().hex}@gmail.com"
        statuses = []
        for _ in range(6):
            response = requests.post(
                f"{BASE_URL}/api/auth/login",
                json={"email": unique_email, "password": "DefinitelyWrong!"},
                timeout=20,
            )
            statuses.append(response.status_code)

        env = dotenv_values("/app/backend/.env")

        async def cleanup_attempts():
            client = AsyncIOMotorClient(env["MONGO_URL"])
            try:
                await client[env["DB_NAME"]].login_attempts.delete_many(
                    {"identifier": {"$regex": f"{unique_email}$"}}
                )
            finally:
                client.close()

        asyncio.run(cleanup_attempts())
        assert statuses[:5] == [401] * 5
        assert statuses[5] == 429

    def test_cors_uses_explicit_origin_and_allows_credentials(self):
        response = requests.options(
            f"{BASE_URL}/api/auth/me",
            headers={
                "Origin": "https://qa-origin.example.test",
                "Access-Control-Request-Method": "GET",
            },
            timeout=20,
        )
        assert response.status_code in (200, 204)
        assert response.headers.get("access-control-allow-origin") != "*"
        assert response.headers.get("access-control-allow-credentials") == "true"


# Commission alert API, cron authorization/idempotency, persistence, and bucket behavior.
ALERT_STATE_FIELDS = (
    "commission_expires", "alerts_enabled", "last_alert_bucket",
    "last_alert_sent_at", "last_alert_email_id",
)


def _db_call(operation):
    env = dotenv_values("/app/backend/.env")

    async def execute():
        client = AsyncIOMotorClient(env["MONGO_URL"])
        try:
            return await operation(client[env["DB_NAME"]])
        finally:
            client.close()

    return asyncio.run(execute())


def _get_admin(email):
    async def operation(database):
        return await database.users.find_one({"email": email.lower()})

    return _db_call(operation)


def _update_admin(email, set_values=None, unset_fields=()):
    update = {}
    if set_values:
        update["$set"] = set_values
    if unset_fields:
        update["$unset"] = {field: "" for field in unset_fields}

    async def operation(database):
        return await database.users.update_one({"email": email.lower()}, update)

    return _db_call(operation)


@pytest.fixture
def isolated_commission_state(credentials):
    """Snapshot the owner and pause other users so cron tests cannot email unrelated accounts."""
    email = credentials["email"].lower()

    async def prepare(database):
        owner = await database.users.find_one({"email": email})
        assert owner is not None
        other_states = []
        async for user in database.users.find({"email": {"$ne": email}}):
            other_states.append((user["_id"], "alerts_enabled" in user, user.get("alerts_enabled")))
        await database.users.update_many({"email": {"$ne": email}}, {"$set": {"alerts_enabled": False}})
        return owner, other_states

    owner, other_states = _db_call(prepare)
    yield owner
    time.sleep(1)

    async def restore(database):
        set_values = {field: owner[field] for field in ALERT_STATE_FIELDS if field in owner}
        unset_values = {field: "" for field in ALERT_STATE_FIELDS if field not in owner}
        update = {}
        if set_values:
            update["$set"] = set_values
        if unset_values:
            update["$unset"] = unset_values
        if update:
            await database.users.update_one({"_id": owner["_id"]}, update)
        for user_id, existed, value in other_states:
            if existed:
                await database.users.update_one({"_id": user_id}, {"$set": {"alerts_enabled": value}})
            else:
                await database.users.update_one({"_id": user_id}, {"$unset": {"alerts_enabled": ""}})
        await database.cron_runs.delete_many({"run_id": {"$regex": "^TEST_COMMISSION_"}})

    _db_call(restore)


def _wait_for_admin_field(email, field, expected, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        user = _get_admin(email)
        if user and user.get(field) == expected:
            return user
        time.sleep(0.4)
    user = _get_admin(email)
    pytest.fail(f"Timed out waiting for {field}={expected!r}; current={user.get(field) if user else None!r}")


class TestCommissionAlerts:
    """Commission alert status, profile, email, cron, bucket and schedule tests."""

    def test_bucket_mapping_covers_every_required_threshold(self):
        from server import _current_alert_bucket

        today = date(2026, 7, 31)
        cases = {
            61: None, 60: 60, 45: 60, 30: 30, 29: 30,
            14: 14, 13: 14, 7: 7, 6: 7, 1: 1, 0: 0, -1: -1,
        }
        actual = {
            days_left: _current_alert_bucket(today + timedelta(days=days_left), today)
            for days_left in cases
        }
        assert actual == cases

    def test_cron_schedule_contract(self):
        schedule = yaml.safe_load(Path("/app/.emergent/crons.yml").read_text(encoding="utf-8"))
        assert isinstance(schedule, dict) and len(schedule.get("crons", [])) == 1
        cron = schedule["crons"][0]
        assert cron["name"] == "commission-alerts" and len(cron["name"]) <= 21
        assert cron["cron"] == "0 8 * * *"
        assert cron["endpoint"] == "{{BASE_URL}}/api/cron/commission-alerts"
        assert cron["method"] == "POST" and cron["enabled"] is True

    def test_profile_alert_default_toggle_and_status_persist(self, authenticated_client, credentials,
                                                             isolated_commission_state):
        email = credentials["email"]
        _update_admin(email, unset_fields=("alerts_enabled",))
        me_default = authenticated_client.get(f"{BASE_URL}/api/auth/me", timeout=20)
        assert me_default.status_code == 200 and me_default.json()["alerts_enabled"] is True

        disabled = authenticated_client.put(
            f"{BASE_URL}/api/auth/profile", json={"alerts_enabled": False}, timeout=20
        )
        assert disabled.status_code == 200 and disabled.json()["alerts_enabled"] is False
        persisted_off = authenticated_client.get(f"{BASE_URL}/api/auth/me", timeout=20)
        assert persisted_off.status_code == 200 and persisted_off.json()["alerts_enabled"] is False

        enabled = authenticated_client.put(
            f"{BASE_URL}/api/auth/profile", json={"alerts_enabled": True}, timeout=20
        )
        assert enabled.status_code == 200 and enabled.json()["alerts_enabled"] is True
        persisted_on = authenticated_client.get(f"{BASE_URL}/api/auth/me", timeout=20)
        assert persisted_on.status_code == 200 and persisted_on.json()["alerts_enabled"] is True

    def test_alert_status_unset_and_configured_upcoming_counts(self, authenticated_client, credentials,
                                                               isolated_commission_state):
        email = credentials["email"]
        _update_admin(email, set_values={"alerts_enabled": True}, unset_fields=("commission_expires",))
        unset_status = authenticated_client.get(f"{BASE_URL}/api/alerts/status", timeout=20)
        assert unset_status.status_code == 200
        assert unset_status.json() == {"configured": False, "enabled": True}

        expiry = (datetime.now(timezone.utc).date() + timedelta(days=45)).isoformat()
        _update_admin(email, set_values={"commission_expires": expiry})
        configured = authenticated_client.get(f"{BASE_URL}/api/alerts/status", timeout=20)
        assert configured.status_code == 200
        data = configured.json()
        assert data["configured"] is True and data["enabled"] is True
        assert data["commission_expires"] == expiry and data["days_left"] == 45
        assert [item["days_out"] for item in data["upcoming_alerts"]] == [30, 14, 7, 1, 0]
        assert all(isinstance(item["on"], str) for item in data["upcoming_alerts"])

    def test_preview_email_sends_to_current_user(self, authenticated_client, credentials,
                                                 isolated_commission_state):
        expiry = (datetime.now(timezone.utc).date() + timedelta(days=45)).isoformat()
        _update_admin(credentials["email"], set_values={"commission_expires": expiry, "alerts_enabled": True})
        response = authenticated_client.post(f"{BASE_URL}/api/alerts/test", timeout=40)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["ok"] is True
        assert data["sent_to"] == credentials["email"]
        assert isinstance(data["email_id"], str) and data["email_id"]

    def test_cron_auth_immediate_ack_and_webhook_idempotency(self, credentials,
                                                             isolated_commission_state):
        env = dotenv_values("/app/backend/.env")
        endpoint = f"{BASE_URL}/api/cron/commission-alerts"
        no_auth = requests.post(endpoint, timeout=20)
        wrong_auth = requests.post(endpoint, headers={"Authorization": "Bearer wrong"}, timeout=20)
        assert no_auth.status_code == 401 and no_auth.json()["detail"] == "Unauthorized"
        assert wrong_auth.status_code == 401 and wrong_auth.json()["detail"] == "Unauthorized"

        expiry = (datetime.now(timezone.utc).date() + timedelta(days=100)).isoformat()
        _update_admin(
            credentials["email"],
            set_values={"commission_expires": expiry, "alerts_enabled": True},
            unset_fields=("last_alert_bucket", "last_alert_sent_at", "last_alert_email_id"),
        )
        run_id = f"TEST_COMMISSION_AUTH_{uuid.uuid4().hex}"
        headers = {
            "Authorization": f"Bearer {env['WEBHOOK_CRON_SECRET']}",
            "X-Webhook-Id": run_id,
        }
        started = time.monotonic()
        accepted = requests.post(endpoint, headers=headers, timeout=20)
        elapsed = time.monotonic() - started
        assert accepted.status_code == 200
        assert accepted.json() == {"accepted": True, "run_id": run_id}
        assert elapsed < 3, f"Cron acknowledgement took {elapsed:.2f}s"

        duplicate = requests.post(endpoint, headers=headers, timeout=20)
        assert duplicate.status_code == 200
        assert duplicate.json() == {"accepted": True, "duplicate": True, "run_id": run_id}
        time.sleep(1)
        assert _get_admin(credentials["email"]).get("last_alert_email_id") is None

    def test_cron_45_day_send_dedup_far_future_and_expired(self, credentials,
                                                           isolated_commission_state):
        env = dotenv_values("/app/backend/.env")
        email = credentials["email"]
        endpoint = f"{BASE_URL}/api/cron/commission-alerts"
        auth_header = {"Authorization": f"Bearer {env['WEBHOOK_CRON_SECRET']}"}

        def fire(label):
            run_id = f"TEST_COMMISSION_{label}_{uuid.uuid4().hex}"
            response = requests.post(
                endpoint,
                headers={**auth_header, "X-Webhook-Id": run_id},
                timeout=20,
            )
            assert response.status_code == 200
            assert response.json() == {"accepted": True, "run_id": run_id}

        today = datetime.now(timezone.utc).date()
        _update_admin(
            email,
            set_values={"commission_expires": (today + timedelta(days=45)).isoformat(), "alerts_enabled": True},
            unset_fields=("last_alert_bucket", "last_alert_sent_at", "last_alert_email_id"),
        )
        fire("45DAY")
        first = _wait_for_admin_field(email, "last_alert_bucket", 60)
        first_id, first_sent_at = first["last_alert_email_id"], first["last_alert_sent_at"]
        assert isinstance(first_id, str) and first_id

        fire("DEDUP")
        time.sleep(1.5)
        deduped = _get_admin(email)
        assert deduped["last_alert_bucket"] == 60
        assert deduped["last_alert_email_id"] == first_id
        assert deduped["last_alert_sent_at"] == first_sent_at

        _update_admin(email, set_values={"commission_expires": (today + timedelta(days=100)).isoformat()})
        fire("FAR")
        time.sleep(1.5)
        far = _get_admin(email)
        assert far["last_alert_email_id"] == first_id and far["last_alert_sent_at"] == first_sent_at

        _update_admin(
            email,
            set_values={"commission_expires": (today - timedelta(days=1)).isoformat()},
            unset_fields=("last_alert_bucket", "last_alert_sent_at", "last_alert_email_id"),
        )
        fire("EXPIRED")
        expired = _wait_for_admin_field(email, "last_alert_bucket", -1)
        assert isinstance(expired["last_alert_email_id"], str) and expired["last_alert_email_id"]
        assert expired["last_alert_email_id"] != first_id

        from server import _render_commission_email
        subject, html = _render_commission_email("Matthew", -1, today - timedelta(days=1), BASE_URL)
        assert "EXPIRED" in subject
        assert "Domini Business Services" in html and "Commission Alerts" in html


class TestSignerIntakes:
    """Authenticated creation/list/use/delete and unauthenticated signer submission."""

    def test_public_seeded_pending_intake_requires_no_auth(self):
        response = requests.get(
            f"{BASE_URL}/api/public/intake/sU3c4C39cti1XVFBAkodwlPo", timeout=20
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "pending"
        assert isinstance(data["notary_name"], str) and data["notary_name"]
        assert "appointment_note" in data

    def test_full_intake_lifecycle(self, authenticated_client):
        label = f"TEST_UI_Intake_{uuid.uuid4().hex[:8]}"
        create_payload = {
            "label": label,
            "appointment_note": "TEST Bring the original identification.",
            "expires_hours": 24,
        }
        created = authenticated_client.post(
            f"{BASE_URL}/api/intakes", json=create_payload, timeout=20
        )
        assert created.status_code == 200
        intake = created.json()
        intake_id, token = intake["id"], intake["token"]
        assert isinstance(intake_id, str) and len(intake_id) == 24
        assert isinstance(token, str) and len(token) >= 20
        assert intake["status"] == "pending"
        assert intake["label"] == label
        assert intake["notary_name"] == "Matthew Yurksaitis"
        assert datetime.fromisoformat(intake["expires_at"]) > datetime.now(timezone.utc)

        try:
            listed = authenticated_client.get(f"{BASE_URL}/api/intakes", timeout=20)
            assert listed.status_code == 200
            matches = [row for row in listed.json() if row["id"] == intake_id]
            assert len(matches) == 1 and matches[0]["label"] == label
            assert matches[0]["notary_name"] == "Matthew Yurksaitis"

            # Deliberately use a brand-new session: public GET/POST must not rely on auth cookies.
            public_client = requests.Session()
            public_get = public_client.get(f"{BASE_URL}/api/public/intake/{token}", timeout=20)
            assert public_get.status_code == 200
            context = public_get.json()
            assert context["status"] == "pending"
            assert context["appointment_note"] == create_payload["appointment_note"]
            assert isinstance(context["notary_name"], str) and context["notary_name"]

            submit = public_client.post(
                f"{BASE_URL}/api/public/intake/{token}", json=VALID_SIGNER, timeout=20
            )
            assert submit.status_code == 200 and submit.json() == {"ok": True}

            second = public_client.post(
                f"{BASE_URL}/api/public/intake/{token}", json=VALID_SIGNER, timeout=20
            )
            assert second.status_code == 400
            assert "already" in second.json()["detail"].lower()

            completed_get = public_client.get(f"{BASE_URL}/api/public/intake/{token}", timeout=20)
            assert completed_get.status_code == 200
            assert completed_get.json()["status"] == "completed"

            refreshed = authenticated_client.get(f"{BASE_URL}/api/intakes", timeout=20).json()
            persisted = next(row for row in refreshed if row["id"] == intake_id)
            assert persisted["status"] == "completed"
            assert persisted["data"]["signer_name"] == VALID_SIGNER["signer_name"]
            assert persisted["data"]["id_number"] == VALID_SIGNER["id_number"]

            used = authenticated_client.post(
                f"{BASE_URL}/api/intakes/{intake_id}/mark-used", timeout=20
            )
            assert used.status_code == 200 and used.json() == {"ok": True}
            refreshed = authenticated_client.get(f"{BASE_URL}/api/intakes", timeout=20).json()
            assert next(row for row in refreshed if row["id"] == intake_id)["status"] == "used"
        finally:
            deleted = authenticated_client.delete(f"{BASE_URL}/api/intakes/{intake_id}", timeout=20)
            assert deleted.status_code == 200 and deleted.json() == {"ok": True}

        after_delete = authenticated_client.get(f"{BASE_URL}/api/intakes", timeout=20).json()
        assert all(row["id"] != intake_id for row in after_delete)
        assert requests.get(f"{BASE_URL}/api/public/intake/{token}", timeout=20).status_code == 404

    def test_completed_public_intake_does_not_expose_signer_pii(self):
        response = requests.get(
            f"{BASE_URL}/api/public/intake/mU77L1JQpluEcT0dgfkBJ-xL", timeout=20
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] in ("completed", "used")
        assert set(data) == {
            "status", "label", "appointment_note", "notary_name",
            "commission_number", "state", "submitted_at",
        }, "Public status response contains fields beyond its explicit safe allowlist"
        assert "data" not in data, "Public status endpoint exposes submitted signer PII"

    def test_public_bad_token_is_404_without_auth(self):
        response = requests.get(
            f"{BASE_URL}/api/public/intake/TEST_DOES_NOT_EXIST_{uuid.uuid4().hex}", timeout=20
        )
        assert response.status_code == 404
        assert response.json()["detail"] == "Link not found"

    def test_blank_required_signer_fields_are_rejected(self, authenticated_client):
        created = authenticated_client.post(
            f"{BASE_URL}/api/intakes",
            json={"label": "TEST_Invalid_Fields", "expires_hours": 24},
            timeout=20,
        )
        assert created.status_code == 200
        intake = created.json()
        try:
            invalid = requests.post(
                f"{BASE_URL}/api/public/intake/{intake['token']}",
                json={"signer_name": "", "id_type": "", "id_number": ""},
                timeout=20,
            )
            assert invalid.status_code == 422
            detail = invalid.json()["detail"]
            assert {tuple(error["loc"]) for error in detail} == {
                ("body", "signer_name"), ("body", "id_type"), ("body", "id_number")
            }
        finally:
            authenticated_client.delete(f"{BASE_URL}/api/intakes/{intake['id']}", timeout=20)

    def test_atomic_concurrent_submit_accepts_only_one_and_never_overwrites(self, authenticated_client):
        created = authenticated_client.post(
            f"{BASE_URL}/api/intakes",
            json={"label": "TEST_Atomic_Submit", "expires_hours": 24},
            timeout=20,
        )
        assert created.status_code == 200
        intake = created.json()
        payload_a = {**VALID_SIGNER, "signer_name": "TEST_Atomic Signer A", "id_number": "ATOMIC-A"}
        payload_b = {**VALID_SIGNER, "signer_name": "TEST_Atomic Signer B", "id_number": "ATOMIC-B"}

        def submit(payload):
            return requests.post(
                f"{BASE_URL}/api/public/intake/{intake['token']}", json=payload, timeout=20
            )

        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                responses = list(pool.map(submit, (payload_a, payload_b)))
            assert sorted(response.status_code for response in responses) == [200, 400]
            rejected = next(response for response in responses if response.status_code == 400)
            assert "already been submitted" in rejected.json()["detail"].lower()

            listed = authenticated_client.get(f"{BASE_URL}/api/intakes", timeout=20)
            assert listed.status_code == 200
            persisted = next(row for row in listed.json() if row["id"] == intake["id"])
            winning_data = persisted["data"]
            assert winning_data in (payload_a, payload_b)

            third = submit({**VALID_SIGNER, "signer_name": "TEST_Overwrite Attempt", "id_number": "OVERWRITE"})
            assert third.status_code == 400
            after_third = authenticated_client.get(f"{BASE_URL}/api/intakes", timeout=20).json()
            still_persisted = next(row for row in after_third if row["id"] == intake["id"])
            assert still_persisted["data"] == winning_data
        finally:
            deleted = authenticated_client.delete(
                f"{BASE_URL}/api/intakes/{intake['id']}", timeout=20
            )
            assert deleted.status_code == 200

    def test_mark_used_unknown_intake_returns_404(self, authenticated_client):
        response = authenticated_client.post(
            f"{BASE_URL}/api/intakes/000000000000000000000000/mark-used", timeout=20
        )
        assert response.status_code == 404

    @pytest.mark.parametrize("method,path", [
        ("delete", "/api/intakes/not-an-object-id"),
        ("post", "/api/intakes/not-an-object-id/mark-used"),
    ])
    def test_malformed_intake_id_is_controlled_4xx(self, authenticated_client, method, path):
        response = getattr(authenticated_client, method)(f"{BASE_URL}{path}", timeout=20)
        assert response.status_code == 400
        assert response.json()["detail"] == "Invalid identifier"


class TestRegression:
    """Existing dashboard, journal persistence and PDF export regression checks."""

    def test_dashboard_stats_schema(self, authenticated_client):
        response = authenticated_client.get(f"{BASE_URL}/api/stats/dashboard", timeout=20)
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data["total_entries"], int)
        assert isinstance(data["monthly_series"], list) and len(data["monthly_series"]) == 6
        assert isinstance(data["type_breakdown"], list)

    def test_journal_create_get_delete_and_pdf(self, authenticated_client):
        existing = authenticated_client.get(f"{BASE_URL}/api/entries", timeout=20)
        assert existing.status_code == 200
        signer_name = existing.json()[0]["signer_name"] if existing.json() else "TEST_Regression Signer"
        payload = {
            "entry_date": datetime.now(timezone.utc).isoformat(),
            "notarization_type": "Acknowledgment",
            "document_type": "Affidavit",
            "document_description": "TEST intake feature regression",
            "signer_name": signer_name,
            "signer_address": "",
            "signer_phone": "",
            "signer_email": "",
            "id_type": "State ID",
            "id_number": "TEST-REG-0726",
            "fee": 1.25,
        }
        created = authenticated_client.post(f"{BASE_URL}/api/entries", json=payload, timeout=20)
        assert created.status_code == 200
        entry = created.json()
        assert entry["signer_name"] == signer_name and entry["fee"] == 1.25
        try:
            fetched = authenticated_client.get(f"{BASE_URL}/api/entries/{entry['id']}", timeout=20)
            assert fetched.status_code == 200
            assert fetched.json()["document_description"] == payload["document_description"]

            pdf = authenticated_client.get(f"{BASE_URL}/api/entries/export/pdf", timeout=30)
            assert pdf.status_code == 200
            assert pdf.headers["content-type"].startswith("application/pdf")
            assert pdf.content.startswith(b"%PDF") and len(pdf.content) > 1000
        finally:
            deleted = authenticated_client.delete(f"{BASE_URL}/api/entries/{entry['id']}", timeout=20)
            assert deleted.status_code == 200
        assert authenticated_client.get(
            f"{BASE_URL}/api/entries/{entry['id']}", timeout=20
        ).status_code == 404

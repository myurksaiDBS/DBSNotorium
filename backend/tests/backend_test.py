"""API regression tests for commission alerts, signer intakes, authentication, dashboard, journal and PDF export."""
import asyncio
import io
import re
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
import requests
import bcrypt
import fitz
import yaml
from bson import ObjectId
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
            100: None,
            60: 60,
            45: 60,
            31: 60,
            30: 30,
            15: 30,
            14: 14,
            8: 14,
            7: 7,
            2: 7,
            1: 1,
            0: 0,
            -1: -1,
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

    def test_profile_renewal_resets_dedup_and_allows_new_cycle_alert(
            self, authenticated_client, credentials, isolated_commission_state):
        env = dotenv_values("/app/backend/.env")
        email = credentials["email"]
        today = datetime.now(timezone.utc).date()
        old_expiry = (today + timedelta(days=30)).isoformat()
        new_expiry = (today + timedelta(days=45)).isoformat()
        old_email_id = f"TEST_OLD_EMAIL_{uuid.uuid4().hex}"
        _update_admin(email, set_values={
            "commission_expires": old_expiry,
            "alerts_enabled": True,
            "last_alert_bucket": 60,
            "last_alert_sent_at": "2026-01-01T00:00:00+00:00",
            "last_alert_email_id": old_email_id,
        })

        renewed = authenticated_client.put(
            f"{BASE_URL}/api/auth/profile",
            json={"commission_expires": new_expiry},
            timeout=20,
        )
        assert renewed.status_code == 200, renewed.text
        assert renewed.json()["commission_expires"] == new_expiry
        persisted = _get_admin(email)
        assert persisted["commission_expires"] == new_expiry
        assert persisted.get("last_alert_bucket") is None
        assert persisted.get("last_alert_sent_at") is None
        assert persisted.get("last_alert_email_id") is None

        run_id = f"TEST_COMMISSION_RENEWAL_{uuid.uuid4().hex}"
        fired = requests.post(
            f"{BASE_URL}/api/cron/commission-alerts",
            headers={
                "Authorization": f"Bearer {env['WEBHOOK_CRON_SECRET']}",
                "X-Webhook-Id": run_id,
            },
            timeout=20,
        )
        assert fired.status_code == 200
        assert fired.json() == {"accepted": True, "run_id": run_id}
        alerted = _wait_for_admin_field(email, "last_alert_bucket", 60)
        assert isinstance(alerted.get("last_alert_email_id"), str)
        assert alerted["last_alert_email_id"] and alerted["last_alert_email_id"] != old_email_id

    def test_concurrent_duplicate_webhook_enqueues_exactly_one_scan(
            self, credentials, isolated_commission_state):
        env = dotenv_values("/app/backend/.env")
        email = credentials["email"]
        today = datetime.now(timezone.utc).date()
        _update_admin(
            email,
            set_values={
                "commission_expires": (today + timedelta(days=100)).isoformat(),
                "alerts_enabled": True,
            },
            unset_fields=("last_alert_bucket", "last_alert_sent_at", "last_alert_email_id"),
        )
        run_id = f"TEST_COMMISSION_CONCURRENT_{uuid.uuid4().hex}"
        endpoint = f"{BASE_URL}/api/cron/commission-alerts"
        headers = {
            "Authorization": f"Bearer {env['WEBHOOK_CRON_SECRET']}",
            "X-Webhook-Id": run_id,
        }

        def deliver(_):
            return requests.post(endpoint, headers=headers, timeout=20)

        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(deliver, range(2)))
        assert [response.status_code for response in responses] == [200, 200]
        payloads = [response.json() for response in responses]
        assert sum(payload.get("duplicate") is True for payload in payloads) == 1
        assert sum("duplicate" not in payload for payload in payloads) == 1
        assert all(payload["accepted"] is True and payload["run_id"] == run_id for payload in payloads)

        async def inspect(database):
            indexes = await database.cron_runs.index_information()
            run_id_indexes = [
                spec for spec in indexes.values()
                if spec.get("key") == [("run_id", 1)]
            ]
            return await database.cron_runs.count_documents({"run_id": run_id}), run_id_indexes

        count, indexes = _db_call(inspect)
        assert count == 1
        assert len(indexes) == 1 and indexes[0].get("unique") is True
        time.sleep(1)
        assert _get_admin(email).get("last_alert_email_id") is None

    def test_full_sequential_cron_sends_each_new_bucket_and_dedups_immediate_refire(
            self, credentials, isolated_commission_state):
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
        sequence = [(45, 60), (29, 30), (13, 14), (5, 7), (0, 0), (-1, -1)]
        _update_admin(
            email,
            set_values={"alerts_enabled": True},
            unset_fields=("last_alert_bucket", "last_alert_sent_at", "last_alert_email_id"),
        )
        previous_email_id = None
        delivered_ids = []
        for days_left, expected_bucket in sequence:
            _update_admin(
                email,
                set_values={"commission_expires": (today + timedelta(days=days_left)).isoformat()},
            )
            fire(f"{expected_bucket}_FIRST")
            sent = _wait_for_admin_field(email, "last_alert_bucket", expected_bucket)
            current_id = sent.get("last_alert_email_id")
            current_sent_at = sent.get("last_alert_sent_at")
            assert isinstance(current_id, str) and current_id
            assert current_id != previous_email_id
            delivered_ids.append(current_id)

            fire(f"{expected_bucket}_DEDUP")
            time.sleep(1.2)
            deduped = _get_admin(email)
            assert deduped["last_alert_bucket"] == expected_bucket
            assert deduped["last_alert_email_id"] == current_id
            assert deduped["last_alert_sent_at"] == current_sent_at
            previous_email_id = current_id

        assert len(delivered_ids) == 6
        assert len(set(delivered_ids)) == 6

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

# Entry Certificate PDF rendering, public-link, email-provider and persistence checks.
CERTIFICATE_FIELDS = (
    "certificate_token", "certificate_token_created_at", "certificate_emailed_at",
    "certificate_emailed_to", "certificate_email_id",
)


@pytest.fixture(scope="class")
def isolated_certificate_entry(authenticated_client):
    """Snapshot one seeded entry and restore its certificate metadata after tests."""
    listed = authenticated_client.get(f"{BASE_URL}/api/entries", timeout=20)
    assert listed.status_code == 200 and listed.json(), "Certificate tests require a seeded journal entry"
    entry = listed.json()[0]
    entry_oid = ObjectId(entry["id"])

    async def snapshot_and_reset(database):
        original = await database.entries.find_one({"_id": entry_oid})
        assert original is not None
        await database.entries.update_one(
            {"_id": entry_oid}, {"$unset": {field: "" for field in CERTIFICATE_FIELDS}}
        )
        return original

    original = _db_call(snapshot_and_reset)
    yield entry

    async def restore(database):
        set_values = {field: original[field] for field in CERTIFICATE_FIELDS if field in original}
        unset_values = {field: "" for field in CERTIFICATE_FIELDS if field not in original}
        update = {}
        if set_values:
            update["$set"] = set_values
        if unset_values:
            update["$unset"] = unset_values
        await database.entries.update_one({"_id": entry_oid}, update)

    _db_call(restore)


class TestEntryCertificate:
    """Authenticated PDF, public token, validation, Resend and metadata lifecycle tests."""

    def test_certificate_auth_id_validation_and_pdf_contract(
            self, authenticated_client, isolated_certificate_entry):
        entry = isolated_certificate_entry
        endpoint = f"{BASE_URL}/api/entries/{entry['id']}/certificate.pdf"

        unauthorized = requests.get(endpoint, timeout=30)
        assert unauthorized.status_code == 401
        assert unauthorized.json()["detail"] == "Not authenticated"

        malformed = authenticated_client.get(
            f"{BASE_URL}/api/entries/not-an-object-id/certificate.pdf", timeout=30
        )
        assert malformed.status_code == 400
        assert malformed.json()["detail"] == "Invalid identifier"

        missing = authenticated_client.get(
            f"{BASE_URL}/api/entries/000000000000000000000000/certificate.pdf", timeout=30
        )
        assert missing.status_code == 404
        assert missing.json()["detail"] == "Entry not found"

        response = authenticated_client.get(endpoint, timeout=30)
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/pdf")
        disposition = response.headers.get("content-disposition", "")
        assert disposition.startswith("attachment;") and ".pdf" in disposition
        assert response.content.startswith(b"%PDF")
        assert 15_000 <= len(response.content) <= 100_000, len(response.content)
        print(f"Certificate PDF size: {len(response.content)} bytes")

        pdf = fitz.open(stream=response.content, filetype="pdf")
        assert pdf.page_count == 1
        page = pdf[0]
        assert round(page.rect.width) == 612 and round(page.rect.height) == 792
        text = page.get_text()
        required_text = (
            "CERTIFICATE OF NOTARIAL ACT", entry["notarization_type"],
            entry["entry_date"][:10], entry["signer_name"], entry["id_number"],
            "NOTARY ATTESTATION", "A Division of YFI Enterprises, LLC",
        )
        for expected in required_text:
            assert expected in text, f"Missing certificate text: {expected!r}"
        assert re.search(r"NOT-[A-F0-9]{8}", text)
        assert f"${float(entry.get('fee') or 0):.2f}" in text
        assert len(page.get_images(full=True)) >= 1, "Domini shield image/watermark missing"
        drawing_colors = []
        for drawing in page.get_drawings():
            drawing_colors.extend(color for color in (drawing.get("color"), drawing.get("fill")) if color)
        expected_red = (0.875, 0.051, 0.106)
        expected_navy = (0.0, 0.192, 0.478)
        assert any(sum(abs(actual[i] - expected_red[i]) for i in range(3)) < 0.03
                   for actual in drawing_colors), "Red top rule missing"
        assert any(sum(abs(actual[i] - expected_navy[i]) for i in range(3)) < 0.03
                   for actual in drawing_colors), "Navy attestation block missing"

    def test_email_validation_token_reuse_persistence_and_public_pdf(
            self, authenticated_client, credentials, isolated_certificate_entry):
        entry = isolated_certificate_entry
        endpoint = f"{BASE_URL}/api/entries/{entry['id']}/email-certificate"

        for payload in ({}, {"to": "not-an-email", "message": "TEST invalid"}):
            invalid = authenticated_client.post(endpoint, json=payload, timeout=30)
            assert invalid.status_code == 422
            assert isinstance(invalid.json().get("detail"), list)

        first_to = credentials["email"]
        first = authenticated_client.post(
            endpoint,
            json={"to": first_to, "message": "TEST certificate delivery — first send."},
            timeout=40,
        )
        assert first.status_code == 200, first.text
        first_data = first.json()
        assert first_data["ok"] is True and first_data["sent_to"] == first_to
        assert isinstance(first_data["email_id"], str) and first_data["email_id"]
        assert first_data["certificate_url"].startswith(f"{BASE_URL}/api/public/certificate/")
        first_token = first_data["certificate_url"].rsplit("/", 1)[-1]
        assert len(first_token) >= 20

        async def fetch(database):
            return await database.entries.find_one({"_id": ObjectId(entry["id"])})

        persisted = _db_call(fetch)
        assert persisted["certificate_token"] == first_token
        assert persisted["certificate_emailed_to"] == first_to
        assert persisted["certificate_email_id"] == first_data["email_id"]
        assert datetime.fromisoformat(persisted["certificate_emailed_at"])

        public = requests.get(first_data["certificate_url"], timeout=30)
        assert public.status_code == 200
        assert public.headers["content-type"].startswith("application/pdf")
        assert public.headers.get("content-disposition", "").startswith("inline;")
        public_pdf = fitz.open(stream=public.content, filetype="pdf")
        assert public_pdf.page_count == 1
        assert public_pdf[0].get_text() == fitz.open(
            stream=authenticated_client.get(
                f"{BASE_URL}/api/entries/{entry['id']}/certificate.pdf", timeout=30
            ).content,
            filetype="pdf",
        )[0].get_text()

        second_to_input = credentials["email"].upper()
        second_to = second_to_input.split("@", 1)[0] + "@" + second_to_input.split("@", 1)[1].lower()
        second = authenticated_client.post(
            endpoint,
            json={"to": second_to_input, "message": "TEST certificate delivery — token reuse."},
            timeout=40,
        )
        assert second.status_code == 200, second.text
        second_data = second.json()
        assert second_data["sent_to"] == second_to
        assert second_data["certificate_url"].rsplit("/", 1)[-1] == first_token
        assert second_data["email_id"] != first_data["email_id"]
        persisted_second = _db_call(fetch)
        assert persisted_second["certificate_token"] == first_token
        assert persisted_second["certificate_emailed_to"] == second_to
        assert persisted_second["certificate_email_id"] == second_data["email_id"]

        undeliverable = authenticated_client.post(
            endpoint,
            json={"to": "delivered@resend.dev", "message": "TEST expected provider rejection."},
            timeout=40,
        )
        assert undeliverable.status_code == 400, undeliverable.text
        detail = undeliverable.json()["detail"]
        assert isinstance(detail, str) and detail
        assert "undeliverable" in detail.lower(), detail
        after_failure = _db_call(fetch)
        assert after_failure["certificate_token"] == first_token
        assert after_failure["certificate_emailed_to"] == second_to
        assert after_failure["certificate_email_id"] == second_data["email_id"]

    def test_public_bogus_token_is_404_without_auth(self):
        response = requests.get(
            f"{BASE_URL}/api/public/certificate/TEST_BOGUS_{uuid.uuid4().hex}", timeout=20
        )
        assert response.status_code == 404
        assert response.json()["detail"] == "Certificate not found"


class TestRegression:
    """Existing dashboard, journal persistence and PDF export regression checks."""
    def test_certificate_handles_user_entered_markup_safely(self, authenticated_client):
        """User-entered journal text must not crash ReportLab's XML-like Paragraph parser."""
        payload = {
            "entry_date": datetime.now(timezone.utc).isoformat(),
            "notarization_type": "Acknowledgment",
            "document_type": "Affidavit <copy>",
            "document_description": "TEST A & B <script>alert('x')</script>",
            "signer_name": "TEST Alice <Signer> & Bob",
            "signer_email": "markup.qa@example.test",
            "id_type": "State ID",
            "id_number": "TEST-<ID>&-0726",
            "fee": 2.50,
        }
        created = authenticated_client.post(f"{BASE_URL}/api/entries", json=payload, timeout=20)
        assert created.status_code == 200, created.text
        entry_id = created.json()["id"]
        try:
            response = authenticated_client.get(
                f"{BASE_URL}/api/entries/{entry_id}/certificate.pdf", timeout=30
            )
            assert response.status_code == 200, response.text
            pdf = fitz.open(stream=response.content, filetype="pdf")
            assert pdf.page_count == 1
            text = pdf[0].get_text()
            assert "TEST Alice" in text and "Bob" in text
            assert "TEST-<ID>&-0726" in text
        finally:
            deleted = authenticated_client.delete(f"{BASE_URL}/api/entries/{entry_id}", timeout=20)
            _cleanup_iteration7_certificate_clients()
            assert deleted.status_code == 200



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



# Iteration 7: certificate output encoding, atomic token creation, alert ordering, and branded PDF regression.
def _cleanup_iteration7_certificate_clients():
    async def operation(database):
        return await database.clients.delete_many({
            "name": {"$regex": r"^TEST Alice <Signer> & Bob"}
        })

    return _db_call(operation)


class TestEntryCertificateFixesIteration7:
    """Focused verification of the four fixes requested after iteration 6."""

    @staticmethod
    def _entry_payload(label):
        return {
            "entry_date": datetime.now(timezone.utc).isoformat(),
            "notarization_type": "Acknowledgment",
            "document_type": "Deed",
            "document_description": "House at <123 Main St> & Co",
            "signer_name": "TEST Alice <Signer> & Bob",
            "signer_address": "123 QA Avenue",
            "signer_phone": "",
            "signer_email": "matthew.yurksaitis@dominibusservices.com",
            "id_type": "State ID",
            "id_number": "TEST-<ID>&-0726",
            "id_expiration": "2029-07-31",
            "id_issued_by": "PA",
            "witness_name": "",
            "fee": 2.50,
            "notes": "TEST iteration 7 certificate verification",
            "signature_data": "",
        }

    def test_pdf_preserves_literal_markup_and_branding(self, authenticated_client):
        payload = self._entry_payload("PDF")
        created = authenticated_client.post(f"{BASE_URL}/api/entries", json=payload, timeout=20)
        assert created.status_code == 200, created.text
        entry_id = created.json()["id"]
        try:
            response = authenticated_client.get(
                f"{BASE_URL}/api/entries/{entry_id}/certificate.pdf", timeout=30
            )
            assert response.status_code == 200, response.text
            assert response.headers["content-type"].startswith("application/pdf")
            print(f"Iteration 7 certificate PDF size: {len(response.content)} bytes")
            assert 38_000 <= len(response.content) <= 42_000, len(response.content)
            pdf = fitz.open(stream=response.content, filetype="pdf")
            assert pdf.page_count == 1
            page = pdf[0]
            assert round(page.rect.width) == 612 and round(page.rect.height) == 792
            text = page.get_text()
            for literal in (
                payload["signer_name"], payload["id_number"], payload["document_description"],
                "NOTARY ATTESTATION", "A Division of YFI Enterprises, LLC",
            ):
                assert literal in text, f"PDF did not preserve required text: {literal!r}\n{text}"
            assert len(page.get_images(full=True)) >= 1, "Domini shield watermark/image missing"
            colors_found = [
                color
                for drawing in page.get_drawings()
                for color in (drawing.get("color"), drawing.get("fill"))
                if color
            ]
            assert any(sum(abs(c[i] - (0.875, 0.051, 0.106)[i]) for i in range(3)) < 0.03
                       for c in colors_found), "Red top rule missing"
            assert any(sum(abs(c[i] - (0.0, 0.192, 0.478)[i]) for i in range(3)) < 0.03
                       for c in colors_found), "Navy attestation block missing"
        finally:
            deleted = authenticated_client.delete(f"{BASE_URL}/api/entries/{entry_id}", timeout=20)
            _cleanup_iteration7_certificate_clients()
            assert deleted.status_code == 200

    def test_certificate_email_html_escapes_all_interpolated_values(
            self, authenticated_client, credentials, monkeypatch):
        payload = self._entry_payload("EMAIL<script>signer</script>")
        payload["document_type"] = "Deed <script>doctype</script>"
        created = authenticated_client.post(f"{BASE_URL}/api/entries", json=payload, timeout=20)
        assert created.status_code == 200, created.text
        entry_id = created.json()["id"]
        captured = {}

        async def capture_email(to_email, subject, html, reply_to=None):
            captured.update(to=to_email, subject=subject, html=html, reply_to=reply_to)
            return "TEST_FAKE_EMAIL_ID", None

        import server
        monkeypatch.setattr(server, "_send_email", capture_email)
        admin = _get_admin(credentials["email"])
        user = {
            **admin,
            "_id": str(admin["_id"]),
            "name": "Notary <script>name</script> & Co",
            "commission_number": "COMM-<script>number</script>&",
            "state": "PA<script>state</script>&",
        }
        note = "Personal <script>alert(1)</script> & <b>bold</b>"
        try:
            result = asyncio.run(server.email_certificate(
                entry_id,
                server.CertificateEmailIn(to=credentials["email"], message=note),
                user,
            ))
            assert result["ok"] is True
            html = captured["html"]
            assert "<script>" not in html and "</script>" not in html
            for escaped_fragment in (
                "TEST Alice &lt;Signer&gt; &amp; Bob",
                "Deed &lt;script&gt;doctype&lt;/script&gt; — House at &lt;123 Main St&gt; &amp; Co",
                "&lt;script&gt;alert(1)&lt;/script&gt; &amp; &lt;b&gt;bold&lt;/b&gt;",
                "Notary &lt;script&gt;name&lt;/script&gt; &amp; Co",
                "COMM-&lt;script&gt;number&lt;/script&gt;&amp;",
                "PA&lt;script&gt;state&lt;/script&gt;&amp;",
            ):
                assert escaped_fragment in html, f"Missing escaped email value: {escaped_fragment}"
        finally:
            deleted = authenticated_client.delete(f"{BASE_URL}/api/entries/{entry_id}", timeout=20)
            _cleanup_iteration7_certificate_clients()
            assert deleted.status_code == 200

    def test_parallel_first_email_calls_share_one_persisted_token(
            self, authenticated_client, credentials):
        payload = self._entry_payload("RACE")
        created = authenticated_client.post(f"{BASE_URL}/api/entries", json=payload, timeout=20)
        assert created.status_code == 200, created.text
        entry_id = created.json()["id"]
        endpoint = f"{BASE_URL}/api/entries/{entry_id}/email-certificate"
        cookies = authenticated_client.cookies.get_dict()

        def send(index):
            return requests.post(
                endpoint,
                cookies=cookies,
                json={
                    "to": credentials["email"],
                    "message": f"TEST iteration 7 parallel token request {index}",
                },
                timeout=45,
            )

        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                responses = list(pool.map(send, (1, 2)))
            assert [response.status_code for response in responses] == [200, 200], [
                response.text for response in responses
            ]
            bodies = [response.json() for response in responses]
            tokens = [body["certificate_url"].rsplit("/", 1)[-1] for body in bodies]
            assert tokens[0] == tokens[1], f"Parallel callers received different tokens: {tokens}"

            async def fetch(database):
                return await database.entries.find_one({"_id": ObjectId(entry_id)})

            persisted = _db_call(fetch)
            assert persisted["certificate_token"] == tokens[0]
            assert all(body["sent_to"] == credentials["email"] for body in bodies)
            assert all(isinstance(body["email_id"], str) and body["email_id"] for body in bodies)
        finally:
            deleted = authenticated_client.delete(f"{BASE_URL}/api/entries/{entry_id}", timeout=20)
            _cleanup_iteration7_certificate_clients()
            assert deleted.status_code == 200

    def test_alert_status_is_chronological_without_changing_bucket_order(
            self, authenticated_client, credentials, isolated_commission_state):
        import server
        assert server.ALERT_BUCKETS == [0, 1, 7, 14, 30, 60]
        expiry = (datetime.now(timezone.utc).date() + timedelta(days=45)).isoformat()
        _update_admin(credentials["email"], set_values={
            "commission_expires": expiry,
            "alerts_enabled": True,
        })
        response = authenticated_client.get(f"{BASE_URL}/api/alerts/status", timeout=20)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["days_left"] == 45
        assert [item["days_out"] for item in data["upcoming_alerts"]] == [30, 14, 7, 1, 0]
        assert [item["on"] for item in data["upcoming_alerts"]] == sorted(
            item["on"] for item in data["upcoming_alerts"]
        )


# Iteration 9: renewal checklist, certificate email audit history, and intake QR poster APIs.
@pytest.fixture(scope="class")
def foreign_notary_account():
    """Create a second notary to verify owner isolation, then remove all test data."""
    session = requests.Session()
    session.headers.update({"Content-Type": "application/json"})
    email = f"test.foreign.{uuid.uuid4().hex}@gmail.com"
    response = session.post(
        f"{BASE_URL}/api/auth/register",
        json={"email": email, "password": "TEST_Notary_2026!", "name": "TEST Foreign Notary"},
        timeout=20,
    )
    assert response.status_code == 200, response.text
    account = {**response.json(), "session": session}
    yield account

    async def cleanup(database):
        user = await database.users.find_one({"email": email})
        if not user:
            return
        user_id = str(user["_id"])
        foreign_entries = await database.entries.find({"user_id": user_id}, {"_id": 1}).to_list(100)
        entry_ids = [str(row["_id"]) for row in foreign_entries]
        if entry_ids:
            await database.certificate_email_log.delete_many({"entry_id": {"$in": entry_ids}})
        await database.entries.delete_many({"user_id": user_id})
        await database.clients.delete_many({"user_id": user_id})
        await database.document_types.delete_many({"user_id": user_id})
        await database.signer_intakes.delete_many({"user_id": user_id})
        await database.login_attempts.delete_many({"identifier": {"$regex": f"{re.escape(email)}$"}})
        await database.users.delete_one({"_id": user["_id"]})

    _db_call(cleanup)


class TestNotariumIteration9Features:
    """Renewal checklist, email audit log, QR image/poster, ownership and validation tests."""

    @staticmethod
    def _entry_payload(signer_name):
        return {
            "entry_date": datetime.now(timezone.utc).isoformat(),
            "notarization_type": "Acknowledgment",
            "document_type": "Affidavit",
            "document_description": "TEST iteration 9 certificate history",
            "signer_name": signer_name,
            "signer_address": "123 QA Avenue",
            "signer_phone": "",
            "signer_email": "",
            "id_type": "State ID",
            "id_number": "TEST-HISTORY-0726",
            "id_expiration": "2029-07-31",
            "id_issued_by": "PA",
            "witness_name": "",
            "fee": 1.00,
            "notes": "TEST audit trail",
            "signature_data": "",
        }

    def test_renewal_lock_unlock_toggle_persistence_and_validation(
            self, authenticated_client, credentials):
        email = credentials["email"]
        original = _get_admin(email)
        assert original is not None
        original_progress_exists = "renewal_checklist_progress" in original
        original_progress = original.get("renewal_checklist_progress", [])
        today = datetime.now(timezone.utc).date()
        locked_expiry = (today + timedelta(days=45)).isoformat()
        unlocked_expiry = (today + timedelta(days=25)).isoformat()
        endpoint = f"{BASE_URL}/api/renewal-checklist"
        try:
            unauthenticated = requests.get(endpoint, timeout=20)
            assert unauthenticated.status_code == 401
            assert unauthenticated.json()["detail"] == "Not authenticated"

            locked_profile = authenticated_client.put(
                f"{BASE_URL}/api/auth/profile",
                json={"commission_expires": locked_expiry},
                timeout=20,
            )
            assert locked_profile.status_code == 200, locked_profile.text
            locked = authenticated_client.get(endpoint, timeout=20)
            assert locked.status_code == 200, locked.text
            locked_data = locked.json()
            assert locked_data["unlocked"] is False
            assert locked_data["days_left"] == 45
            assert locked_data["commission_expires"] == locked_expiry
            assert locked_data["state"] == locked_profile.json()["state"]
            assert locked_data["total_count"] == 10
            assert len(locked_data["items"]) == 10
            assert len({item["id"] for item in locked_data["items"]}) == 10
            assert all(set(item) == {"id", "title", "detail", "completed"}
                       for item in locked_data["items"])

            unlocked_profile = authenticated_client.put(
                f"{BASE_URL}/api/auth/profile",
                json={"commission_expires": unlocked_expiry},
                timeout=20,
            )
            assert unlocked_profile.status_code == 200, unlocked_profile.text
            unlocked = authenticated_client.get(endpoint, timeout=20)
            assert unlocked.status_code == 200, unlocked.text
            unlocked_data = unlocked.json()
            assert unlocked_data["unlocked"] is True
            assert unlocked_data["days_left"] == 25
            assert unlocked_data["state"] == unlocked_profile.json()["state"]
            assert len(unlocked_data["items"]) == unlocked_data["total_count"] == 10

            item_id = unlocked_data["items"][0]["id"]
            enabled = authenticated_client.post(
                f"{endpoint}/toggle", json={"item_id": item_id, "done": True}, timeout=20
            )
            assert enabled.status_code == 200 and enabled.json() == {"ok": True}
            persisted_on = authenticated_client.get(endpoint, timeout=20).json()
            assert next(item for item in persisted_on["items"] if item["id"] == item_id)["completed"] is True
            assert item_id in _get_admin(email).get("renewal_checklist_progress", [])

            unknown = authenticated_client.post(
                f"{endpoint}/toggle",
                json={"item_id": "TEST_unknown_item", "done": True},
                timeout=20,
            )
            assert unknown.status_code == 400
            assert unknown.json()["detail"] == "Unknown checklist item"

            disabled = authenticated_client.post(
                f"{endpoint}/toggle", json={"item_id": item_id, "done": False}, timeout=20
            )
            assert disabled.status_code == 200 and disabled.json() == {"ok": True}
            persisted_off = authenticated_client.get(endpoint, timeout=20).json()
            assert next(item for item in persisted_off["items"] if item["id"] == item_id)["completed"] is False
            assert item_id not in _get_admin(email).get("renewal_checklist_progress", [])
        finally:
            # The requested shared admin baseline is 45 days out after testing.
            authenticated_client.put(
                f"{BASE_URL}/api/auth/profile",
                json={"commission_expires": locked_expiry},
                timeout=20,
            )
            if original_progress_exists:
                _update_admin(email, set_values={"renewal_checklist_progress": original_progress})
            else:
                _update_admin(email, unset_fields=("renewal_checklist_progress",))

    def test_certificate_email_audit_history_write_sort_security_and_validation(
            self, authenticated_client, credentials, foreign_notary_account):
        signer_name = f"TEST_History_{uuid.uuid4().hex[:8]}"
        created = authenticated_client.post(
            f"{BASE_URL}/api/entries",
            json=self._entry_payload(signer_name),
            timeout=20,
        )
        assert created.status_code == 200, created.text
        entry_id = created.json()["id"]
        history_url = f"{BASE_URL}/api/entries/{entry_id}/email-history"

        foreign_created = foreign_notary_account["session"].post(
            f"{BASE_URL}/api/entries",
            json=self._entry_payload(f"TEST_Foreign_History_{uuid.uuid4().hex[:8]}"),
            timeout=20,
        )
        assert foreign_created.status_code == 200, foreign_created.text
        foreign_entry_id = foreign_created.json()["id"]
        try:
            empty = authenticated_client.get(history_url, timeout=20)
            assert empty.status_code == 200 and empty.json() == []

            unauthenticated = requests.get(history_url, timeout=20)
            assert unauthenticated.status_code == 401
            assert unauthenticated.json()["detail"] == "Not authenticated"

            malformed = authenticated_client.get(
                f"{BASE_URL}/api/entries/not-an-object-id/email-history", timeout=20
            )
            assert malformed.status_code == 400
            assert malformed.json()["detail"] == "Invalid identifier"

            foreign = authenticated_client.get(
                f"{BASE_URL}/api/entries/{foreign_entry_id}/email-history", timeout=20
            )
            assert foreign.status_code == 404
            assert foreign.json()["detail"] == "Entry not found"

            send_url = f"{BASE_URL}/api/entries/{entry_id}/email-certificate"
            sent = authenticated_client.post(
                send_url,
                json={"to": credentials["email"], "message": "TEST iteration 9 sent audit row"},
                timeout=45,
            )
            assert sent.status_code == 200, sent.text
            sent_payload = sent.json()
            assert sent_payload["ok"] is True
            assert isinstance(sent_payload["email_id"], str) and sent_payload["email_id"]

            after_sent = authenticated_client.get(history_url, timeout=20)
            assert after_sent.status_code == 200
            sent_rows = after_sent.json()
            assert len(sent_rows) == 1
            sent_row = sent_rows[0]
            assert sent_row["to"] == credentials["email"]
            assert sent_row["status"] == "sent"
            assert sent_row["email_id"] == sent_payload["email_id"]
            assert sent_row["error"] is None
            assert sent_row["subject"].startswith("Your notarization certificate")
            assert datetime.fromisoformat(sent_row["sent_at"])

            failed = authenticated_client.post(
                send_url,
                json={"to": "delivered@resend.dev", "message": "TEST expected provider rejection audit row"},
                timeout=45,
            )
            assert failed.status_code == 400, failed.text
            assert isinstance(failed.json().get("detail"), str) and failed.json()["detail"]

            history = authenticated_client.get(history_url, timeout=20)
            assert history.status_code == 200, history.text
            rows = history.json()
            assert len(rows) == 2
            assert [row["sent_at"] for row in rows] == sorted(
                (row["sent_at"] for row in rows), reverse=True
            )
            assert [row["status"] for row in rows] == ["failed", "sent"]
            failed_row = rows[0]
            assert failed_row["to"] == "delivered@resend.dev"
            assert failed_row["email_id"] is None
            assert isinstance(failed_row["error"], str) and failed_row["error"]
            assert datetime.fromisoformat(failed_row["sent_at"])
            assert all(set(row) == {
                "id", "to", "subject", "status", "email_id", "error", "sent_at"
            } for row in rows)
        finally:
            async def cleanup(database):
                await database.certificate_email_log.delete_many({"entry_id": entry_id})
                await database.clients.delete_many({"name": signer_name})

            _db_call(cleanup)
            deleted = authenticated_client.delete(
                f"{BASE_URL}/api/entries/{entry_id}", timeout=20
            )
            assert deleted.status_code == 200


    def test_email_history_accepts_equivalent_uppercase_object_id(
            self, authenticated_client, credentials):
        """A valid ObjectId is hex-case-insensitive and should address the same entry/history."""
        signer_name = f"TEST_History_Case_{uuid.uuid4().hex[:8]}"
        created = authenticated_client.post(
            f"{BASE_URL}/api/entries", json=self._entry_payload(signer_name), timeout=20
        )
        assert created.status_code == 200, created.text
        entry_id = created.json()["id"]
        admin = _get_admin(credentials["email"])
        test_email_id = f"TEST_CASE_{uuid.uuid4().hex}"

        async def seed_log(database):
            await database.certificate_email_log.insert_one({
                "user_id": str(admin["_id"]),
                "entry_id": entry_id,
                "to": credentials["email"],
                "subject": "TEST uppercase ObjectId history",
                "email_id": test_email_id,
                "error": None,
                "status": "sent",
                "sent_at": datetime.now(timezone.utc).isoformat(),
            })

        _db_call(seed_log)
        try:
            lowercase_response = authenticated_client.get(
                f"{BASE_URL}/api/entries/{entry_id}/email-history", timeout=20
            )
            uppercase_response = authenticated_client.get(
                f"{BASE_URL}/api/entries/{entry_id.upper()}/email-history", timeout=20
            )
            assert lowercase_response.status_code == uppercase_response.status_code == 200
            lowercase_rows = lowercase_response.json()
            uppercase_rows = uppercase_response.json()
            assert len(lowercase_rows) == len(uppercase_rows) == 1
            assert [row["id"] for row in uppercase_rows] == [row["id"] for row in lowercase_rows]
            assert uppercase_rows[0]["email_id"] == test_email_id
            assert uppercase_rows[0]["status"] == "sent"
        finally:
            async def cleanup(database):
                await database.certificate_email_log.delete_many({"entry_id": entry_id})
                await database.clients.delete_many({"name": signer_name})

            _db_call(cleanup)
            deleted = authenticated_client.delete(
                f"{BASE_URL}/api/entries/{entry_id}", timeout=20
            )
            assert deleted.status_code == 200


    def test_intake_qr_png_poster_contract_security_and_validation(
            self, authenticated_client, foreign_notary_account):
        from PIL import Image
        import qrcode

        created = authenticated_client.post(
            f"{BASE_URL}/api/intakes",
            json={
                "label": "TEST Iteration 9 QR",
                "appointment_note": "TEST Bring your original photo ID.",
                "expires_hours": 24,
            },
            timeout=20,
        )
        assert created.status_code == 200, created.text
        intake = created.json()

        foreign_created = foreign_notary_account["session"].post(
            f"{BASE_URL}/api/intakes",
            json={"label": "TEST Foreign QR", "expires_hours": 24},
            timeout=20,
        )
        assert foreign_created.status_code == 200, foreign_created.text
        foreign_intake_id = foreign_created.json()["id"]
        try:
            for suffix in ("qr.png", "qr-poster.pdf"):
                unauthenticated = requests.get(
                    f"{BASE_URL}/api/intakes/{intake['id']}/{suffix}", timeout=30
                )
                assert unauthenticated.status_code == 401

                malformed = authenticated_client.get(
                    f"{BASE_URL}/api/intakes/not-an-object-id/{suffix}", timeout=30
                )
                assert malformed.status_code == 400
                assert malformed.json()["detail"] == "Invalid identifier"

                foreign = authenticated_client.get(
                    f"{BASE_URL}/api/intakes/{foreign_intake_id}/{suffix}", timeout=30
                )
                assert foreign.status_code == 404
                assert foreign.json()["detail"] == "Intake not found"

            png = authenticated_client.get(
                f"{BASE_URL}/api/intakes/{intake['id']}/qr.png", timeout=30
            )
            assert png.status_code == 200
            assert png.headers["content-type"].startswith("image/png")
            assert png.content.startswith(b"\x89PNG\r\n\x1a\n")
            image = Image.open(io.BytesIO(png.content))
            image.verify()
            image = Image.open(io.BytesIO(png.content)).convert("RGB")
            assert image.width == image.height and image.width >= 400
            colors_in_qr = set(image.getdata())
            assert (255, 255, 255) in colors_in_qr
            assert any(r < 20 and 35 < g < 70 and 90 < b < 150 for r, g, b in colors_in_qr)

            backend_env = dotenv_values("/app/backend/.env")
            expected_url = f"{backend_env['ALERT_APP_URL'].rstrip('/')}/intake/{intake['token']}"
            expected_qr = qrcode.QRCode(
                version=None,
                error_correction=qrcode.constants.ERROR_CORRECT_H,
                box_size=12,
                border=2,
            )
            expected_qr.add_data(expected_url)
            expected_qr.make(fit=True)
            expected_img = expected_qr.make_image(
                fill_color="#00317A", back_color="white"
            ).convert("RGB")
            assert list(image.getdata()) == list(expected_img.getdata()), \
                "QR image does not encode the expected public intake URL"

            poster = authenticated_client.get(
                f"{BASE_URL}/api/intakes/{intake['id']}/qr-poster.pdf", timeout=40
            )
            assert poster.status_code == 200, poster.text
            assert poster.headers["content-type"].startswith("application/pdf")
            assert poster.headers.get("content-disposition", "").startswith("attachment;")
            assert poster.content.startswith(b"%PDF")
            assert 45_000 <= len(poster.content) <= 90_000, len(poster.content)
            print(f"Intake QR poster size: {len(poster.content)} bytes")

            pdf = fitz.open(stream=poster.content, filetype="pdf")
            assert pdf.page_count == 1
            page = pdf[0]
            assert round(page.rect.width) == 612 and round(page.rect.height) == 792
            text = page.get_text()
            for expected_text in (
                "Scan to complete your details",
                "HOW IT WORKS",
                "A Division of YFI Enterprises, LLC",
                expected_url,
                "TEST Bring your original photo ID.",
            ):
                assert expected_text in text, f"Missing poster content: {expected_text!r}"
            assert len(page.get_images(full=True)) >= 2, "Shield watermark or QR image missing"
            square_rects = [
                rect for image_info in page.get_images(full=True)
                for rect in page.get_image_rects(image_info[0])
                if abs(rect.width - rect.height) < 1 and rect.width >= 250
            ]
            assert any(abs(rect.x0 + rect.width / 2 - 306) < 5 for rect in square_rects), \
                "Large QR image is not centered on the Letter page"
            drawing_colors = [
                color
                for drawing in page.get_drawings()
                for color in (drawing.get("color"), drawing.get("fill"))
                if color
            ]
            assert any(sum(abs(c[i] - (0.875, 0.051, 0.106)[i]) for i in range(3)) < 0.03
                       for c in drawing_colors), "Red top rule missing"
            assert any(sum(abs(c[i] - (0.0, 0.192, 0.478)[i]) for i in range(3)) < 0.03
                       for c in drawing_colors), "Navy HOW IT WORKS bar missing"
        finally:
            deleted = authenticated_client.delete(
                f"{BASE_URL}/api/intakes/{intake['id']}", timeout=20
            )
            assert deleted.status_code == 200


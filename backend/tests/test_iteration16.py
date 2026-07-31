"""Iteration 16 backend tests for firm profiles and delivery-bounce digests."""
import asyncio
import uuid
import time
from datetime import datetime, timedelta, timezone

import fitz
import pytest
import requests
from bson import ObjectId
from dotenv import dotenv_values
from motor.motor_asyncio import AsyncIOMotorClient

from conftest import BASE_URL

BACKEND_ENV = dotenv_values("/app/backend/.env")
FIRM_FIELDS = (
    "firm_name",
    "firm_address",
    "firm_phone",
    "firm_website",
    "firm_license_number",
)


def _db_call(operation):
    async def execute():
        client = AsyncIOMotorClient(BACKEND_ENV["MONGO_URL"])
        try:
            return await operation(client[BACKEND_ENV["DB_NAME"]])
        finally:
            client.close()

    return asyncio.run(execute())


def _wait_for_log(log_id, predicate, timeout=12):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        async def fetch(database):
            return await database.certificate_email_log.find_one({"_id": log_id})

        row = _db_call(fetch)
        if row and predicate(row):
            return row
        time.sleep(0.4)
    pytest.fail(f"Timed out waiting for certificate email log {log_id}")


@pytest.fixture(scope="class")
def admin_profile_snapshot(credentials):
    async def snapshot(database):
        return await database.users.find_one({"email": credentials["email"].lower()})

    original = _db_call(snapshot)
    assert original is not None
    yield original

    async def restore(database):
        set_values = {field: original[field] for field in FIRM_FIELDS if field in original}
        unset_values = {field: "" for field in FIRM_FIELDS if field not in original}
        update = {}
        if set_values:
            update["$set"] = set_values
        if unset_values:
            update["$unset"] = unset_values
        if update:
            await database.users.update_one({"_id": original["_id"]}, update)

    _db_call(restore)


class TestFirmProfile:
    """Firm contact details persist independently and conditionally render in certificates."""

    def test_all_fields_persist_and_null_only_clears_target(
        self, authenticated_client, admin_profile_snapshot
    ):
        values = {
            "firm_name": "TEST Keystone Signing Group",
            "firm_address": "1600 QA Avenue, Philadelphia, PA 19103",
            "firm_phone": "+1 215 555 0160",
            "firm_website": "https://test-keystone.example.com",
            "firm_license_number": "TEST-LIC-PA-1600",
        }
        saved = authenticated_client.put(
            f"{BASE_URL}/api/auth/profile", json=values, timeout=20
        )
        assert saved.status_code == 200, saved.text
        for field, expected in values.items():
            assert saved.json()[field] == expected

        fetched = authenticated_client.get(f"{BASE_URL}/api/auth/me", timeout=20)
        assert fetched.status_code == 200, fetched.text
        for field, expected in values.items():
            assert fetched.json()[field] == expected

        nulled = authenticated_client.put(
            f"{BASE_URL}/api/auth/profile", json={"firm_phone": None}, timeout=20
        )
        assert nulled.status_code == 200, nulled.text
        assert nulled.json()["firm_phone"] is None
        for field in ("firm_name", "firm_address", "firm_website", "firm_license_number"):
            assert nulled.json()[field] == values[field]

        persisted = authenticated_client.get(f"{BASE_URL}/api/auth/me", timeout=20)
        assert persisted.status_code == 200
        assert persisted.json()["firm_phone"] is None
        for field in ("firm_name", "firm_address", "firm_website", "firm_license_number"):
            assert persisted.json()[field] == values[field]

    def test_firm_footer_increases_same_certificate_and_contains_values(
        self, authenticated_client, admin_profile_snapshot
    ):
        listed = authenticated_client.get(f"{BASE_URL}/api/entries", timeout=20)
        assert listed.status_code == 200 and listed.json(), "A seeded entry is required"
        entry_id = listed.json()[0]["id"]
        endpoint = f"{BASE_URL}/api/entries/{entry_id}/certificate.pdf"

        cleared = {field: "" for field in FIRM_FIELDS}
        clear_response = authenticated_client.put(
            f"{BASE_URL}/api/auth/profile", json=cleared, timeout=20
        )
        assert clear_response.status_code == 200
        without_firm = authenticated_client.get(endpoint, timeout=30)
        assert without_firm.status_code == 200
        assert without_firm.headers["content-type"].startswith("application/pdf")
        assert without_firm.content.startswith(b"%PDF")

        values = {
            "firm_name": "TEST Certificate Footer Firm",
            "firm_address": "16 Footer Lane, Philadelphia, PA",
            "firm_phone": "215-555-0116",
            "firm_website": "footer-test.example.com",
            "firm_license_number": "TEST-FOOTER-LIC-16",
        }
        saved = authenticated_client.put(
            f"{BASE_URL}/api/auth/profile", json=values, timeout=20
        )
        assert saved.status_code == 200
        with_firm = authenticated_client.get(endpoint, timeout=30)
        assert with_firm.status_code == 200
        assert with_firm.headers["content-type"].startswith("application/pdf")
        assert with_firm.content.startswith(b"%PDF")
        assert len(with_firm.content) > len(without_firm.content), (
            len(without_firm.content), len(with_firm.content)
        )

        document = fitz.open(stream=with_firm.content, filetype="pdf")
        text = "\n".join(page.get_text() for page in document)
        for expected in values.values():
            assert expected in text


@pytest.fixture
def isolated_bounce_rows(credentials):
    run_tag = uuid.uuid4().hex
    now = datetime.now(timezone.utc)

    async def seed(database):
        admin = await database.users.find_one({"email": credentials["email"].lower()})
        foreign = await database.users.find_one({"email": "jane.notary@example.com"})
        assert admin is not None and foreign is not None
        rows = [
            {
                "_id": ObjectId(),
                "user_id": str(admin["_id"]),
                "entry_id": f"TEST-I16-ADMIN-{run_tag}",
                "recipient": "test.admin.bounce@example.com",
                "error_msg": "TEST mailbox does not exist",
                "status": "failed",
                "sent_at": (now - timedelta(hours=1)).isoformat(),
            },
            {
                "_id": ObjectId(),
                "user_id": str(foreign["_id"]),
                "entry_id": f"TEST-I16-FOREIGN-{run_tag}",
                "recipient": "test.foreign.bounce@example.com",
                "error_msg": "TEST foreign mailbox does not exist",
                "status": "failed",
                "sent_at": (now - timedelta(hours=1)).isoformat(),
            },
            {
                "_id": ObjectId(),
                "user_id": str(admin["_id"]),
                "entry_id": f"TEST-I16-OLD-{run_tag}",
                "recipient": "test.old.bounce@example.com",
                "error_msg": "TEST old bounce",
                "status": "failed",
                "sent_at": (now - timedelta(days=8)).isoformat(),
            },
        ]
        await database.certificate_email_log.insert_many(rows)
        return rows

    rows = _db_call(seed)
    yield rows

    async def cleanup(database):
        await database.certificate_email_log.delete_many(
            {"_id": {"$in": [row["_id"] for row in rows]}}
        )

    _db_call(cleanup)


class TestBounceList:
    """Bounce summaries expose the required contract and remain owner-scoped."""

    def test_list_contract_date_filter_and_owner_isolation(
        self, authenticated_client, isolated_bounce_rows
    ):
        admin_row, foreign_row, old_row = isolated_bounce_rows
        unauthenticated = requests.get(f"{BASE_URL}/api/alerts/bounces", timeout=20)
        assert unauthenticated.status_code == 401

        response = authenticated_client.get(f"{BASE_URL}/api/alerts/bounces", timeout=20)
        assert response.status_code == 200, response.text
        data = response.json()
        assert isinstance(data["total"], int)
        assert isinstance(data["bounces"], list)
        assert data["total"] == len(data["bounces"])
        by_id = {row["id"]: row for row in data["bounces"]}
        assert str(admin_row["_id"]) in by_id
        assert str(foreign_row["_id"]) not in by_id
        assert str(old_row["_id"]) not in by_id
        actual = by_id[str(admin_row["_id"])]
        assert actual == {
            "id": str(admin_row["_id"]),
            "entry_id": admin_row["entry_id"],
            "recipient": admin_row["recipient"],
            "error_msg": admin_row["error_msg"],
            "sent_at": admin_row["sent_at"],
            "digest_notified": False,
        }

    def test_app_generated_log_schema_maps_to_bounce_contract(
        self, authenticated_client, credentials
    ):
        """The certificate sender currently persists to/error; alerts must still expose recipient/error_msg."""
        test_id = ObjectId()
        now = datetime.now(timezone.utc).isoformat()

        async def seed(database):
            admin = await database.users.find_one({"email": credentials["email"].lower()})
            await database.certificate_email_log.insert_one({
                "_id": test_id,
                "user_id": str(admin["_id"]),
                "entry_id": f"TEST-I16-LEGACY-{uuid.uuid4().hex}",
                "to": "test.writer-schema@example.com",
                "error": "TEST writer schema mailbox failure",
                "status": "failed",
                "sent_at": now,
            })

        _db_call(seed)
        try:
            response = authenticated_client.get(f"{BASE_URL}/api/alerts/bounces", timeout=20)
            assert response.status_code == 200
            row = next(item for item in response.json()["bounces"] if item["id"] == str(test_id))
            assert row["recipient"] == "test.writer-schema@example.com"
            assert row["error_msg"] == "TEST writer schema mailbox failure"
        finally:
            async def cleanup(database):
                await database.certificate_email_log.delete_one({"_id": test_id})

            _db_call(cleanup)


@pytest.fixture
def fresh_preview_account():
    email = f"test.iter16.preview.{uuid.uuid4().hex}@example.com"
    password = "TEST_Iteration16_2026!"
    session = requests.Session()
    session.headers.update({"Content-Type": "application/json"})
    registered = session.post(
        f"{BASE_URL}/api/auth/register",
        json={"email": email, "password": password, "name": "TEST Iteration16 Preview"},
        timeout=20,
    )
    assert registered.status_code == 200, registered.text
    account = {"email": email, "id": registered.json()["id"], "session": session}
    yield account

    async def cleanup(database):
        await database.certificate_email_log.delete_many({"user_id": account["id"]})
        await database.document_types.delete_many({"user_id": account["id"]})
        await database.login_attempts.delete_many({"identifier": f"email:{email}"})
        await database.users.delete_one({"_id": ObjectId(account["id"])})

    _db_call(cleanup)


class TestBouncePreview:
    """Preview rejects an empty digest and sends a populated digest to its owner."""

    def test_zero_recent_bounces_returns_400(self, fresh_preview_account):
        response = fresh_preview_account["session"].post(
            f"{BASE_URL}/api/cron/delivery-bounces/preview", timeout=30
        )
        assert response.status_code == 400, response.text
        assert response.json()["detail"] == "No recent bounces to preview."

    def test_recent_bounce_sends_preview(self, authenticated_client, credentials):
        row_id = ObjectId()

        async def seed(database):
            admin = await database.users.find_one({"email": credentials["email"].lower()})
            await database.certificate_email_log.insert_one({
                "_id": row_id,
                "user_id": str(admin["_id"]),
                "entry_id": f"TEST-I16-PREVIEW-{uuid.uuid4().hex}",
                "recipient": "test.preview.bounce@example.com",
                "error_msg": "TEST preview mailbox does not exist",
                "status": "failed",
                "sent_at": datetime.now(timezone.utc).isoformat(),
                "bounce_digest_sent": True,
            })

        _db_call(seed)
        try:
            response = authenticated_client.post(
                f"{BASE_URL}/api/cron/delivery-bounces/preview", timeout=45
            )
            assert response.status_code == 200, response.text
            data = response.json()
            assert data["ok"] is True
            assert isinstance(data["email_id"], str) and data["email_id"]
            assert data["sent_to"] == credentials["email"]
            assert isinstance(data["count"], int) and data["count"] >= 1
        finally:
            async def cleanup(database):
                await database.certificate_email_log.delete_one({"_id": row_id})

            _db_call(cleanup)


class TestDeliveryBounceCron:
    """Cron authentication, idempotency, background processing and send deduplication."""

    def test_auth_background_marking_webhook_dedup_and_no_double_send(
        self, credentials
    ):
        endpoint = f"{BASE_URL}/api/cron/delivery-bounces"
        no_auth = requests.post(endpoint, timeout=20)
        wrong_auth = requests.post(
            endpoint, headers={"Authorization": "Bearer wrong"}, timeout=20
        )
        assert no_auth.status_code == 401 and no_auth.json()["detail"] == "Unauthorized"
        assert wrong_auth.status_code == 401 and wrong_auth.json()["detail"] == "Unauthorized"

        row_id = ObjectId()
        run_id = f"TEST_I16_BOUNCE_{uuid.uuid4().hex}"
        second_run_id = f"TEST_I16_BOUNCE_SECOND_{uuid.uuid4().hex}"

        async def seed(database):
            admin = await database.users.find_one({"email": credentials["email"].lower()})
            await database.certificate_email_log.insert_one({
                "_id": row_id,
                "user_id": str(admin["_id"]),
                "entry_id": f"TEST-I16-CRON-{uuid.uuid4().hex}",
                "recipient": "test@example.com",
                "error_msg": "Mailbox does not exist",
                "status": "failed",
                "sent_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
            })

        _db_call(seed)
        headers = {
            "Authorization": f"Bearer {BACKEND_ENV['WEBHOOK_CRON_SECRET']}",
            "X-Webhook-Id": run_id,
        }
        try:
            accepted = requests.post(endpoint, headers=headers, timeout=20)
            assert accepted.status_code == 200, accepted.text
            assert accepted.json() == {"accepted": True, "run_id": run_id}

            duplicate = requests.post(endpoint, headers=headers, timeout=20)
            assert duplicate.status_code == 200
            assert duplicate.json() == {
                "accepted": True,
                "duplicate": True,
                "run_id": run_id,
            }

            processed = _wait_for_log(row_id, lambda row: row.get("bounce_digest_sent") is True)
            first_sent_at = processed.get("bounce_digest_sent_at")
            assert isinstance(first_sent_at, str) and datetime.fromisoformat(first_sent_at)

            second = requests.post(
                endpoint,
                headers={
                    "Authorization": f"Bearer {BACKEND_ENV['WEBHOOK_CRON_SECRET']}",
                    "X-Webhook-Id": second_run_id,
                },
                timeout=20,
            )
            assert second.status_code == 200
            assert second.json() == {"accepted": True, "run_id": second_run_id}
            time.sleep(2.5)

            async def inspect(database):
                return await database.certificate_email_log.find_one({"_id": row_id})

            after_second = _db_call(inspect)
            assert after_second["bounce_digest_sent"] is True
            assert after_second["bounce_digest_sent_at"] == first_sent_at
        finally:
            async def cleanup(database):
                await database.certificate_email_log.delete_one({"_id": row_id})
                await database.cron_runs.delete_many(
                    {"run_id": {"$in": [run_id, second_run_id]}}
                )

            _db_call(cleanup)


class TestDeliveryBounceCodeContracts:
    """Schedule and email HTML escaping contracts for the delivery digest."""

    def test_schedule_and_digest_renderer_escape_untrusted_values(self):
        import sys
        import yaml

        sys.path.insert(0, "/app/backend")
        import server

        schedule = yaml.safe_load(open("/app/.emergent/crons.yml", encoding="utf-8"))
        cron = next(item for item in schedule["crons"] if item["name"] == "delivery-bounces")
        assert cron == {
            "name": "delivery-bounces",
            "description": cron["description"],
            "cron": "30 8 * * *",
            "endpoint": "{{BASE_URL}}/api/cron/delivery-bounces",
            "method": "POST",
            "enabled": True,
        }

        subject, html = server._render_bounce_digest_email(
            "TEST <script>Notary</script>",
            [{
                "entry_id": "abc123",
                "signer_name": "TEST <b>Signer</b>",
                "recipient": "bad<script>@example.com",
                "error_msg": "<script>alert(1)</script>",
            }],
            "https://notarium.example.test",
        )
        assert subject == "[Notarium] 1 certificate email bounced overnight"
        assert "<script>" not in html
        assert "&lt;script&gt;Notary&lt;/script&gt;" in html
        assert "&lt;b&gt;Signer&lt;/b&gt;" in html
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html

"""Iteration 17 targeted tests for firm-field clearing and certificate bounce schema compatibility."""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone

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
ENTRY_PAYLOAD = {
    "entry_date": datetime.now(timezone.utc).isoformat(),
    "notarization_type": "Acknowledgment",
    "document_type": "Affidavit",
    "document_description": "TEST iteration 17 certificate schema",
    "signer_name": "TEST Iteration17 Signer",
    "signer_address": "17 QA Avenue",
    "signer_phone": "",
    "signer_email": "",
    "id_type": "State ID",
    "id_number": "TEST-I17-ID",
    "id_expiration": "2029-07-31",
    "id_issued_by": "PA",
    "witness_name": "",
    "fee": 1.0,
    "notes": "TEST iteration 17",
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


@pytest.fixture
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


class TestFirmProfileNullClearing:
    """Explicit null clears only the requested firm field and a later value persists."""

    def test_phone_clear_preserves_address_and_phone_can_be_set_again(
        self, authenticated_client, admin_profile_snapshot
    ):
        address = "1700 Iteration Avenue, Philadelphia, PA 19103"
        initial_phone = "(555) 111-2222"
        saved = authenticated_client.put(
            f"{BASE_URL}/api/auth/profile",
            json={"firm_address": address, "firm_phone": initial_phone},
            timeout=20,
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["firm_address"] == address
        assert saved.json()["firm_phone"] == initial_phone

        cleared = authenticated_client.put(
            f"{BASE_URL}/api/auth/profile",
            json={"firm_phone": None, "firm_address": address},
            timeout=20,
        )
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["firm_phone"] is None
        assert cleared.json()["firm_address"] == address

        fetched = authenticated_client.get(f"{BASE_URL}/api/auth/me", timeout=20)
        assert fetched.status_code == 200, fetched.text
        assert fetched.json()["firm_phone"] is None
        assert fetched.json()["firm_address"] == address

        replacement = "(555) 999-0000"
        reset = authenticated_client.put(
            f"{BASE_URL}/api/auth/profile", json={"firm_phone": replacement}, timeout=20
        )
        assert reset.status_code == 200, reset.text
        assert reset.json()["firm_phone"] == replacement
        assert reset.json()["firm_address"] == address

        persisted = authenticated_client.get(f"{BASE_URL}/api/auth/me", timeout=20)
        assert persisted.status_code == 200
        assert persisted.json()["firm_phone"] == replacement
        assert persisted.json()["firm_address"] == address


class TestCertificateLogSchema:
    """Real failed sends use the unified DB shape and all read contracts remain populated."""

    def test_failed_email_writes_unified_schema_and_is_visible_in_bounces_and_history(
        self, authenticated_client
    ):
        recipient = f"undeliverable@notarium-nxdomain-{uuid.uuid4().hex}.com"
        created = authenticated_client.post(
            f"{BASE_URL}/api/entries", json=ENTRY_PAYLOAD, timeout=20
        )
        assert created.status_code == 200, created.text
        entry_id = created.json()["id"]
        log_id = None
        try:
            sent = authenticated_client.post(
                f"{BASE_URL}/api/entries/{entry_id}/email-certificate",
                json={"to": recipient, "message": "TEST force sandbox delivery failure"},
                timeout=45,
            )
            assert sent.status_code == 400, (
                "The sandbox address must produce an immediate failed audit row",
                sent.status_code,
                sent.text[:500],
            )
            provider_error = sent.json().get("detail")
            assert isinstance(provider_error, str) and provider_error.strip()

            async def fetch_log(database):
                return await database.certificate_email_log.find_one(
                    {"entry_id": entry_id, "recipient": recipient}, sort=[("sent_at", -1)]
                )

            log = _db_call(fetch_log)
            assert log is not None
            log_id = log["_id"]
            assert log["status"] == "failed"
            assert log["recipient"] == recipient
            assert isinstance(log["error_msg"], str) and log["error_msg"].strip()
            assert log["error_msg"] == provider_error
            assert "to" not in log and "error" not in log

            bounces = authenticated_client.get(f"{BASE_URL}/api/alerts/bounces", timeout=20)
            assert bounces.status_code == 200, bounces.text
            bounce = next(
                row for row in bounces.json()["bounces"] if row["id"] == str(log_id)
            )
            assert bounce["recipient"] == recipient
            assert bounce["error_msg"] == provider_error

            history = authenticated_client.get(
                f"{BASE_URL}/api/entries/{entry_id}/email-history", timeout=20
            )
            assert history.status_code == 200, history.text
            history_row = next(row for row in history.json() if row["id"] == str(log_id))
            assert history_row["to"] == recipient
            assert history_row["error"] == provider_error
        finally:
            authenticated_client.delete(f"{BASE_URL}/api/entries/{entry_id}", timeout=20)

            async def cleanup(database):
                await database.certificate_email_log.delete_many({"entry_id": entry_id})
                await database.clients.delete_many({"name": ENTRY_PAYLOAD["signer_name"]})

            _db_call(cleanup)


class TestLegacyBounceCompatibility:
    """Legacy to/error rows map in the API and render with real recipient/reason details."""

    def test_legacy_row_maps_and_preview_renderer_includes_recipient_and_reason(
        self, authenticated_client, credentials
    ):
        row_id = ObjectId()
        recipient = "legacy@example.com"
        reason = "Old error"

        async def seed(database):
            admin = await database.users.find_one({"email": credentials["email"].lower()})
            await database.certificate_email_log.insert_one({
                "_id": row_id,
                "user_id": str(admin["_id"]),
                "entry_id": f"TEST-I17-LEGACY-{uuid.uuid4().hex}",
                "signer_name": "TEST Legacy Signer",
                "status": "failed",
                "to": recipient,
                "error": reason,
                "sent_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
                "bounce_digest_sent": True,
            })

        _db_call(seed)
        try:
            response = authenticated_client.get(f"{BASE_URL}/api/alerts/bounces", timeout=20)
            assert response.status_code == 200, response.text
            row = next(item for item in response.json()["bounces"] if item["id"] == str(row_id))
            assert row["recipient"] == recipient
            assert row["error_msg"] == reason

            preview = authenticated_client.post(
                f"{BASE_URL}/api/cron/delivery-bounces/preview", timeout=45
            )
            assert preview.status_code == 200, preview.text
            assert preview.json()["ok"] is True
            assert preview.json()["count"] >= 1

            import sys
            sys.path.insert(0, "/app/backend")
            import server

            subject, html = server._render_bounce_digest_email(
                "TEST Notary",
                [{
                    "entry_id": "legacy-entry",
                    "signer_name": "TEST Legacy Signer",
                    "to": recipient,
                    "error": reason,
                }],
                BASE_URL,
            )
            assert subject == "[Notarium] 1 certificate email bounced overnight"
            assert recipient in html
            assert reason in html
            assert "delivery failed" not in html
        finally:
            async def cleanup(database):
                await database.certificate_email_log.delete_one({"_id": row_id})

            _db_call(cleanup)

"""API regression tests for signer intakes, authentication, dashboard, journal and PDF export."""
import asyncio
import uuid
from datetime import datetime, timezone

import pytest
import requests
import bcrypt
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import dotenv_values

from conftest import BASE_URL


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
        assert datetime.fromisoformat(intake["expires_at"]) > datetime.now(timezone.utc)

        try:
            listed = authenticated_client.get(f"{BASE_URL}/api/intakes", timeout=20)
            assert listed.status_code == 200
            matches = [row for row in listed.json() if row["id"] == intake_id]
            assert len(matches) == 1 and matches[0]["label"] == label

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
        finally:
            authenticated_client.delete(f"{BASE_URL}/api/intakes/{intake['id']}", timeout=20)

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
        assert response.status_code in (400, 404, 422)


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

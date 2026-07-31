"""Iteration 14 backend tests for login lockout, firm logos/PDFs, invitations, and regressions."""
import asyncio
import base64
import io
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import bcrypt
import fitz
import pytest
import requests
from bson import ObjectId
from dotenv import dotenv_values
from motor.motor_asyncio import AsyncIOMotorClient
from PIL import Image

from conftest import BASE_URL

BACKEND_ENV = dotenv_values("/app/backend/.env")
TEST_PASSWORD = "TEST_Invite_2026!"
ENTRY_PAYLOAD = {
    "entry_date": datetime.now(timezone.utc).isoformat(),
    "notarization_type": "Acknowledgment",
    "document_type": "Affidavit",
    "document_description": "TEST iteration 14 journal entry",
    "signer_name": f"TEST Iteration14 {uuid.uuid4().hex[:8]}",
    "signer_address": "14 QA Avenue",
    "signer_phone": "",
    "signer_email": "",
    "id_type": "State ID",
    "id_number": "TEST-I14-ID",
    "id_expiration": "2029-07-31",
    "id_issued_by": "PA",
    "witness_name": "",
    "fee": 1.0,
    "notes": "TEST iteration 14",
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


def _tiny_png_data_url():
    image = Image.new("RGB", (24, 12), (210, 20, 40))
    output = io.BytesIO()
    image.save(output, format="PNG")
    return "data:image/png;base64," + base64.b64encode(output.getvalue()).decode()


def _assert_pdf(response):
    assert response.status_code == 200, response.text[:500]
    assert response.headers["content-type"].startswith("application/pdf")
    assert response.content.startswith(b"%PDF") and len(response.content) > 1_000
    document = fitz.open(stream=response.content, filetype="pdf")
    assert document.page_count >= 1
    return document


class TestLoginLockoutFix:
    """Email-keyed brute-force lockout and auth playbook checks."""

    def test_valid_admin_locks_on_sixth_attempt_and_recovers_after_cleanup(self, credentials):
        email = credentials["email"].lower()
        identifier = f"email:{email}"

        async def clear(database):
            await database.login_attempts.delete_many({"identifier": identifier})

        _db_call(clear)
        try:
            failures = []
            for _ in range(5):
                response = requests.post(
                    f"{BASE_URL}/api/auth/login",
                    json={"email": email, "password": "TEST_Wrong_Password!"},
                    timeout=20,
                )
                failures.append(response.status_code)
                assert response.json()["detail"] == "Invalid email or password"
            assert failures == [401] * 5

            sixth = requests.post(
                f"{BASE_URL}/api/auth/login",
                json={"email": email, "password": "TEST_Wrong_Password!"},
                timeout=20,
            )
            assert sixth.status_code == 429
            assert "Too many attempts" in sixth.json()["detail"]

            correct_while_locked = requests.post(
                f"{BASE_URL}/api/auth/login", json=credentials, timeout=20
            )
            assert correct_while_locked.status_code == 429
            assert "Too many attempts" in correct_while_locked.json()["detail"]

            async def inspect(database):
                return await database.login_attempts.find_one({"identifier": identifier})

            attempt = _db_call(inspect)
            assert attempt["count"] == 5
            assert datetime.fromisoformat(attempt["locked_until"]) > datetime.now(timezone.utc)
        finally:
            _db_call(clear)

        recovered = requests.post(f"{BASE_URL}/api/auth/login", json=credentials, timeout=20)
        assert recovered.status_code == 200, recovered.text
        assert recovered.json()["email"] == email
        cookies = recovered.headers.get("set-cookie", "").lower()
        assert "access_token=" in cookies and "refresh_token=" in cookies
        assert "httponly" in cookies and "secure" in cookies

    def test_admin_hash_and_seed_configuration(self, credentials):
        async def fetch(database):
            return await database.users.find_one({"email": credentials["email"].lower()})

        admin = _db_call(fetch)
        assert admin is not None
        assert admin["password_hash"].startswith("$2b$")
        assert bcrypt.checkpw(credentials["password"].encode(), admin["password_hash"].encode())
        assert admin["name"] == BACKEND_ENV["ADMIN_NAME"]

        source = open("/app/backend/server.py", encoding="utf-8").read()
        startup_block = source[source.index("async def startup()") : source.index("async def shutdown()")]
        assert "elif not verify_password(admin_password, existing[\"password_hash\"])" in startup_block
        assert '"password_hash": hash_password(admin_password)' in startup_block

    def test_cookie_auth_cors_uses_explicit_origin_and_credentials(self):
        response = requests.options(
            f"{BASE_URL}/api/auth/me",
            headers={"Origin": BASE_URL, "Access-Control-Request-Method": "GET"},
            timeout=20,
        )
        assert response.status_code in (200, 204)
        assert response.headers.get("access-control-allow-origin") == BASE_URL
        assert response.headers.get("access-control-allow-credentials") == "true"


@pytest.fixture(scope="class")
def admin_brand_snapshot(credentials):
    email = credentials["email"].lower()
    fields = ("firm_logo_data", "firm_logo_updated_at", "seal_data", "seal_updated_at")

    async def snapshot(database):
        return await database.users.find_one({"email": email})

    original = _db_call(snapshot)
    assert original is not None
    yield original

    async def restore(database):
        set_values = {field: original[field] for field in fields if field in original}
        unset_values = {field: "" for field in fields if field not in original}
        update = {}
        if set_values:
            update["$set"] = set_values
        if unset_values:
            update["$unset"] = unset_values
        await database.users.update_one({"_id": original["_id"]}, update)

    _db_call(restore)


class TestFirmLogoAndPdfs:
    """Firm-logo validation, persistence, deletion, PDF embedding, and seal regression."""

    def test_rejects_non_image_and_oversized_logo(self, authenticated_client, admin_brand_snapshot):
        non_image = authenticated_client.put(
            f"{BASE_URL}/api/auth/firm-logo",
            json={"firm_logo_data": "data:text/plain;base64,SGVsbG8="},
            timeout=20,
        )
        assert non_image.status_code == 400
        assert "PNG or JPEG" in non_image.json()["detail"]

        oversized = "data:image/png;base64," + ("A" * 700_001)
        too_large = authenticated_client.put(
            f"{BASE_URL}/api/auth/firm-logo",
            json={"firm_logo_data": oversized},
            timeout=30,
        )
        assert too_large.status_code == 400
        assert "too large" in too_large.json()["detail"].lower()

    def test_rejects_spoofed_image_data_url_with_non_image_bytes(
        self, authenticated_client, admin_brand_snapshot
    ):
        spoofed = "data:image/png;base64," + base64.b64encode(b"this is not a PNG").decode()
        response = authenticated_client.put(
            f"{BASE_URL}/api/auth/firm-logo",
            json={"firm_logo_data": spoofed},
            timeout=20,
        )
        assert response.status_code == 400, "An image MIME prefix must not bypass image decoding"

    def test_malformed_stored_logo_never_breaks_certificate_pdf(
        self, authenticated_client, admin_brand_snapshot
    ):
        authenticated_client.delete(f"{BASE_URL}/api/auth/firm-logo", timeout=20)
        spoofed = "data:image/png;base64," + base64.b64encode(b"this is not a PNG").decode()
        uploaded = authenticated_client.put(
            f"{BASE_URL}/api/auth/firm-logo", json={"firm_logo_data": spoofed}, timeout=20
        )
        assert uploaded.status_code in (200, 400)
        entries = authenticated_client.get(f"{BASE_URL}/api/entries", timeout=20)
        assert entries.status_code == 200 and entries.json()
        response = authenticated_client.get(
            f"{BASE_URL}/api/entries/{entries.json()[0]['id']}/certificate.pdf", timeout=30
        )
        assert response.status_code == 200, response.text[:500]
        assert response.headers["content-type"].startswith("application/pdf")

    def test_logo_crud_and_both_pdfs_with_and_without_logo(
        self, authenticated_client, admin_brand_snapshot
    ):
        authenticated_client.delete(f"{BASE_URL}/api/auth/firm-logo", timeout=20)
        empty = authenticated_client.get(f"{BASE_URL}/api/auth/firm-logo", timeout=20)
        assert empty.status_code == 200
        assert empty.json()["has_logo"] is False and empty.json()["firm_logo_data"] is None

        created_entry = authenticated_client.post(
            f"{BASE_URL}/api/entries", json=ENTRY_PAYLOAD, timeout=20
        )
        assert created_entry.status_code == 200, created_entry.text
        entry_id = created_entry.json()["id"]
        created_intake = authenticated_client.post(
            f"{BASE_URL}/api/intakes",
            json={"label": "TEST iteration 14 poster", "expires_hours": 24},
            timeout=20,
        )
        assert created_intake.status_code == 200, created_intake.text
        intake_id = created_intake.json()["id"]
        try:
            certificate_without = _assert_pdf(authenticated_client.get(
                f"{BASE_URL}/api/entries/{entry_id}/certificate.pdf", timeout=30
            ))
            poster_without = _assert_pdf(authenticated_client.get(
                f"{BASE_URL}/api/intakes/{intake_id}/qr-poster.pdf", timeout=30
            ))
            certificate_without_images = len(certificate_without[0].get_images(full=True))
            poster_without_images = len(poster_without[0].get_images(full=True))

            logo = _tiny_png_data_url()
            uploaded = authenticated_client.put(
                f"{BASE_URL}/api/auth/firm-logo", json={"firm_logo_data": logo}, timeout=20
            )
            assert uploaded.status_code == 200 and uploaded.json() == {"ok": True}
            fetched = authenticated_client.get(f"{BASE_URL}/api/auth/firm-logo", timeout=20)
            assert fetched.status_code == 200
            assert fetched.json()["has_logo"] is True
            assert fetched.json()["firm_logo_data"] == logo
            assert datetime.fromisoformat(fetched.json()["updated_at"])

            certificate_with = _assert_pdf(authenticated_client.get(
                f"{BASE_URL}/api/entries/{entry_id}/certificate.pdf", timeout=30
            ))
            poster_with = _assert_pdf(authenticated_client.get(
                f"{BASE_URL}/api/intakes/{intake_id}/qr-poster.pdf", timeout=30
            ))
            assert len(certificate_with[0].get_images(full=True)) > certificate_without_images
            assert len(poster_with[0].get_images(full=True)) > poster_without_images

            deleted = authenticated_client.delete(f"{BASE_URL}/api/auth/firm-logo", timeout=20)
            assert deleted.status_code == 200 and deleted.json() == {"ok": True}
            cleared = authenticated_client.get(f"{BASE_URL}/api/auth/firm-logo", timeout=20)
            assert cleared.json()["has_logo"] is False and cleared.json()["firm_logo_data"] is None

            _assert_pdf(authenticated_client.get(
                f"{BASE_URL}/api/entries/{entry_id}/certificate.pdf", timeout=30
            ))
            _assert_pdf(authenticated_client.get(
                f"{BASE_URL}/api/intakes/{intake_id}/qr-poster.pdf", timeout=30
            ))
        finally:
            authenticated_client.delete(f"{BASE_URL}/api/entries/{entry_id}", timeout=20)
            authenticated_client.delete(f"{BASE_URL}/api/intakes/{intake_id}", timeout=20)

    def test_existing_seal_crud_still_works(self, authenticated_client, admin_brand_snapshot):
        seal = _tiny_png_data_url()
        uploaded = authenticated_client.put(
            f"{BASE_URL}/api/auth/seal", json={"seal_data": seal}, timeout=20
        )
        assert uploaded.status_code == 200 and uploaded.json() == {"ok": True}
        fetched = authenticated_client.get(f"{BASE_URL}/api/auth/seal", timeout=20)
        assert fetched.status_code == 200
        assert fetched.json()["has_seal"] is True and fetched.json()["seal_data"] == seal
        deleted = authenticated_client.delete(f"{BASE_URL}/api/auth/seal", timeout=20)
        assert deleted.status_code == 200 and deleted.json() == {"ok": True}
        cleared = authenticated_client.get(f"{BASE_URL}/api/auth/seal", timeout=20).json()
        assert cleared["has_seal"] is False and cleared["seal_data"] is None


@pytest.fixture(scope="class")
def invite_context(authenticated_client):
    run_id = uuid.uuid4().hex
    context = {
        "run_id": run_id,
        "emails": set(),
        "tokens": set(),
        "owner": authenticated_client,
    }
    foreign_email = f"inv.owner.{run_id}@example.com"
    foreign_session = requests.Session()
    foreign_session.headers.update({"Content-Type": "application/json"})
    registered = foreign_session.post(
        f"{BASE_URL}/api/auth/register",
        json={"email": foreign_email, "password": TEST_PASSWORD, "name": "TEST Invite Foreign Owner"},
        timeout=20,
    )
    assert registered.status_code == 200, registered.text
    context.update(foreign_email=foreign_email, foreign=foreign_session)
    context["emails"].add(foreign_email)
    yield context

    async def cleanup(database):
        if context["tokens"]:
            await database.invites.delete_many({"token": {"$in": list(context["tokens"])}})
        users = await database.users.find({"email": {"$in": list(context["emails"])}}).to_list(100)
        for user in users:
            user_id = str(user["_id"])
            await database.entries.delete_many({"user_id": user_id})
            await database.clients.delete_many({"user_id": user_id})
            await database.document_types.delete_many({"user_id": user_id})
            await database.signer_intakes.delete_many({"user_id": user_id})
            await database.login_attempts.delete_many({"identifier": f"email:{user['email']}"})
            await database.users.delete_one({"_id": user["_id"]})

    _db_call(cleanup)


def _create_invite(context, session, label, firm="TEST QA Firm", state="PA"):
    email = f"inv.test.{label}.{context['run_id']}@example.com"
    response = session.post(
        f"{BASE_URL}/api/invites",
        json={
            "email": email,
            "firm_name": firm,
            "state": state,
            "message": f"TEST invitation {label}",
        },
        timeout=45,
    )
    assert response.status_code == 200, response.text
    data = response.json()
    context["emails"].add(email)
    context["tokens"].add(data["token"])
    return {**data, "session": session}


@pytest.fixture(scope="class")
def admin_invite(invite_context):
    return _create_invite(invite_context, invite_context["owner"], "accept")


@pytest.fixture(scope="class")
def foreign_invite(invite_context):
    return _create_invite(invite_context, invite_context["foreign"], "foreign")


@pytest.fixture(scope="class")
def expired_invite(invite_context):
    invite = _create_invite(invite_context, invite_context["owner"], "expired")
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()

    async def expire(database):
        await database.invites.update_one({"token": invite["token"]}, {"$set": {"expires_at": past}})

    _db_call(expire)
    return invite


class TestInvitations:
    """Invite creation, isolation, public states, acceptance, ownership, and revocation."""

    def test_create_response_contract_and_registered_email_rejection(
        self, invite_context, admin_invite
    ):
        assert admin_invite["email"].startswith("inv.test.accept.")
        assert admin_invite["firm_name"] == "TEST QA Firm"
        assert datetime.fromisoformat(admin_invite["expires_at"]) > datetime.now(timezone.utc)
        assert isinstance(admin_invite["token"], str) and len(admin_invite["token"]) >= 24
        assert admin_invite["invite_url"].startswith(BASE_URL + "/invite/")
        assert admin_invite["invite_url"].endswith(admin_invite["token"])
        assert isinstance(admin_invite["email_sent"], bool)
        assert "email_error" in admin_invite

        duplicate_user = invite_context["owner"].post(
            f"{BASE_URL}/api/invites",
            json={"email": invite_context["foreign_email"], "firm_name": "TEST QA Firm", "state": "PA"},
            timeout=20,
        )
        assert duplicate_user.status_code == 400
        assert "already has" in duplicate_user.json()["detail"]

    def test_list_is_array_and_isolated_to_current_owner(
        self, invite_context, admin_invite, foreign_invite
    ):
        owner_response = invite_context["owner"].get(f"{BASE_URL}/api/invites", timeout=20)
        foreign_response = invite_context["foreign"].get(f"{BASE_URL}/api/invites", timeout=20)
        assert owner_response.status_code == foreign_response.status_code == 200
        owner_data = owner_response.json()
        foreign_data = foreign_response.json()
        owner_rows = owner_data if isinstance(owner_data, list) else owner_data.get("invites", [])
        foreign_rows = foreign_data if isinstance(foreign_data, list) else foreign_data.get("invites", [])
        assert admin_invite["token"] in {row["token"] for row in owner_rows}
        assert foreign_invite["token"] not in {row["token"] for row in owner_rows}
        assert foreign_invite["token"] in {row["token"] for row in foreign_rows}
        assert admin_invite["token"] not in {row["token"] for row in foreign_rows}
        assert isinstance(owner_data, dict) and isinstance(owner_data.get("invites"), list), (
            "GET /api/invites contract requires {invites: [...]}"
        )
        assert isinstance(foreign_data, dict) and isinstance(foreign_data.get("invites"), list), (
            "GET /api/invites contract requires {invites: [...]}"
        )

    def test_authenticated_get_invite_by_id(self, invite_context, admin_invite):
        listed = invite_context["owner"].get(f"{BASE_URL}/api/invites", timeout=20).json()
        rows = listed if isinstance(listed, list) else listed.get("invites", [])
        invite_id = next(row["id"] for row in rows if row["token"] == admin_invite["token"])
        response = invite_context["owner"].get(
            f"{BASE_URL}/api/invites/{invite_id}", timeout=20
        )
        assert response.status_code == 200, response.text
        assert response.json()["token"] == admin_invite["token"]
        assert response.json()["email"] == admin_invite["email"]

    def test_public_unknown_and_expired_invites(self, expired_invite):
        unknown = requests.get(
            f"{BASE_URL}/api/public/invite/TEST_UNKNOWN_{uuid.uuid4().hex}", timeout=20
        )
        assert unknown.status_code == 404 and unknown.json()["detail"] == "Invitation not found"

        expired = requests.get(
            f"{BASE_URL}/api/public/invite/{expired_invite['token']}", timeout=20
        )
        assert expired.status_code == 410
        assert "expired" in expired.json()["detail"].lower()
        expired_accept = requests.post(
            f"{BASE_URL}/api/public/invite/{expired_invite['token']}/accept",
            json={"password": TEST_PASSWORD, "name": "TEST Expired"},
            timeout=20,
        )
        assert expired_accept.status_code == 410

    def test_accept_prefills_user_sets_cookies_and_prevents_reuse(
        self, invite_context, admin_invite
    ):
        public_info = requests.get(
            f"{BASE_URL}/api/public/invite/{admin_invite['token']}", timeout=20
        )
        assert public_info.status_code == 200
        assert public_info.json() == {
            "email": admin_invite["email"],
            "firm_name": "TEST QA Firm",
            "state": "PA",
            "invited_by_name": "Matthew Yurksaitis",
        }

        short = requests.post(
            f"{BASE_URL}/api/public/invite/{admin_invite['token']}/accept",
            json={"password": "short", "name": "TEST Accepted Invite"},
            timeout=20,
        )
        assert short.status_code == 400
        assert "at least 8" in short.json()["detail"]

        accepted_session = requests.Session()
        accepted = accepted_session.post(
            f"{BASE_URL}/api/public/invite/{admin_invite['token']}/accept",
            json={"password": TEST_PASSWORD, "name": "TEST Accepted Invite"},
            timeout=30,
        )
        assert accepted.status_code == 200, accepted.text
        assert accepted.json() == {
            "id": accepted.json()["id"],
            "email": admin_invite["email"],
            "name": "TEST Accepted Invite",
            "role": "notary",
            "firm_name": "TEST QA Firm",
        }
        assert ObjectId.is_valid(accepted.json()["id"])
        set_cookie = accepted.headers.get("set-cookie", "").lower()
        assert "access_token=" in set_cookie and "refresh_token=" in set_cookie
        assert "httponly" in set_cookie and "secure" in set_cookie

        me = accepted_session.get(f"{BASE_URL}/api/auth/me", timeout=20)
        assert me.status_code == 200, me.text
        assert me.json()["email"] == admin_invite["email"]
        assert me.json()["firm_name"] == "TEST QA Firm" and me.json()["state"] == "PA"

        async def persisted(database):
            invite = await database.invites.find_one({"token": admin_invite["token"]})
            user = await database.users.find_one({"email": admin_invite["email"]})
            return invite, user

        invite_doc, user_doc = _db_call(persisted)
        assert datetime.fromisoformat(invite_doc["used_at"])
        assert invite_doc["accepted_user_id"] == str(user_doc["_id"])
        assert user_doc["firm_name"] == "TEST QA Firm" and user_doc["state"] == "PA"

        second = requests.post(
            f"{BASE_URL}/api/public/invite/{admin_invite['token']}/accept",
            json={"password": TEST_PASSWORD, "name": "TEST Second Acceptance"},
            timeout=20,
        )
        assert second.status_code == 410
        used_info = requests.get(
            f"{BASE_URL}/api/public/invite/{admin_invite['token']}", timeout=20
        )
        assert used_info.status_code == 410
        assert "already been accepted" in used_info.json()["detail"]

    def test_non_owner_cannot_revoke_then_owner_can_revoke(
        self, invite_context, foreign_invite
    ):
        listed = invite_context["foreign"].get(f"{BASE_URL}/api/invites", timeout=20).json()
        rows = listed if isinstance(listed, list) else listed.get("invites", [])
        invite_id = next(row["id"] for row in rows if row["token"] == foreign_invite["token"])
        forbidden = invite_context["owner"].delete(
            f"{BASE_URL}/api/invites/{invite_id}", timeout=20
        )
        assert forbidden.status_code == 404 and forbidden.json()["detail"] == "Invite not found"

        deleted = invite_context["foreign"].delete(
            f"{BASE_URL}/api/invites/{invite_id}", timeout=20
        )
        assert deleted.status_code == 200 and deleted.json() == {"ok": True}
        assert requests.get(
            f"{BASE_URL}/api/public/invite/{foreign_invite['token']}", timeout=20
        ).status_code == 404

    def test_concurrent_acceptance_allows_exactly_one_success(self, invite_context):
        invite = _create_invite(invite_context, invite_context["owner"], "race")
        endpoint = f"{BASE_URL}/api/public/invite/{invite['token']}/accept"

        def accept(index):
            return requests.post(
                endpoint,
                json={"password": TEST_PASSWORD, "name": f"TEST Race {index}"},
                timeout=30,
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(accept, (1, 2)))
        assert sorted(response.status_code for response in responses) == [200, 410], [
            (response.status_code, response.text[:300]) for response in responses
        ]


@pytest.fixture
def expired_trial_user():
    email = f"test.iter14.expired.{uuid.uuid4().hex}@example.com"
    user_id = ObjectId()

    async def seed(database):
        await database.users.insert_one({
            "_id": user_id,
            "email": email,
            "password_hash": bcrypt.hashpw(TEST_PASSWORD.encode(), bcrypt.gensalt()).decode(),
            "name": "TEST Iteration14 Expired",
            "role": "notary",
            "created_at": (datetime.now(timezone.utc) - timedelta(days=20)).isoformat(),
        })

    _db_call(seed)
    session = requests.Session()
    login = session.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": email, "password": TEST_PASSWORD},
        timeout=20,
    )
    assert login.status_code == 200, login.text
    yield session

    async def cleanup(database):
        await database.entries.delete_many({"user_id": str(user_id)})
        await database.clients.delete_many({"user_id": str(user_id)})
        await database.document_types.delete_many({"user_id": str(user_id)})
        await database.login_attempts.delete_many({"identifier": f"email:{email}"})
        await database.users.delete_one({"_id": user_id})

    _db_call(cleanup)


class TestIteration14Regressions:
    """Billing status, admin journal CRUD, and expired-trial write gate regressions."""

    def test_billing_status_contract(self, authenticated_client):
        response = authenticated_client.get(f"{BASE_URL}/api/billing/status", timeout=20)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["effective_status"] in ("trialing", "active") or data.get("is_admin_bypass") is True
        assert isinstance(data["plans"], list) and len(data["plans"]) >= 2
        assert {plan["lookup_key"] for plan in data["plans"]} >= {
            "notarium_pro_monthly", "notarium_pro_yearly"
        }

    def test_admin_entry_full_crud(self, authenticated_client):
        payload = {**ENTRY_PAYLOAD, "signer_name": f"TEST CRUD {uuid.uuid4().hex[:8]}"}
        created = authenticated_client.post(f"{BASE_URL}/api/entries", json=payload, timeout=20)
        assert created.status_code == 200, created.text
        entry_id = created.json()["id"]
        assert created.json()["signer_name"] == payload["signer_name"]
        try:
            fetched = authenticated_client.get(f"{BASE_URL}/api/entries/{entry_id}", timeout=20)
            assert fetched.status_code == 200 and fetched.json()["notes"] == payload["notes"]
            updated_payload = {**payload, "notes": "TEST updated iteration 14", "fee": 14.0}
            updated = authenticated_client.put(
                f"{BASE_URL}/api/entries/{entry_id}", json=updated_payload, timeout=20
            )
            assert updated.status_code == 200, updated.text
            assert updated.json()["notes"] == "TEST updated iteration 14"
            persisted = authenticated_client.get(f"{BASE_URL}/api/entries/{entry_id}", timeout=20)
            assert persisted.json()["notes"] == "TEST updated iteration 14"
            assert persisted.json()["fee"] == 14.0
        finally:
            deleted = authenticated_client.delete(f"{BASE_URL}/api/entries/{entry_id}", timeout=20)
            assert deleted.status_code == 200
        assert authenticated_client.get(
            f"{BASE_URL}/api/entries/{entry_id}", timeout=20
        ).status_code == 404

    def test_expired_trial_entry_create_is_402(self, expired_trial_user):
        status = expired_trial_user.get(f"{BASE_URL}/api/billing/status", timeout=20)
        assert status.status_code == 200 and status.json()["effective_status"] == "expired"
        response = expired_trial_user.post(
            f"{BASE_URL}/api/entries", json=ENTRY_PAYLOAD, timeout=20
        )
        assert response.status_code == 402, response.text
        detail = response.json()["detail"]
        assert detail["error"] == "subscription_required" and detail["billing_status"] == "expired"

"""Iteration 20 backend tests for custom renewal steps, intake branding, and invite extraction."""
import asyncio
import base64
import io
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest
import requests
from bson import ObjectId
from dotenv import dotenv_values
from motor.motor_asyncio import AsyncIOMotorClient
from PIL import Image

from conftest import BASE_URL

BACKEND_ENV = dotenv_values("/app/backend/.env")
TEST_PASSWORD = "TEST_Iteration20_2026!"


def _db_call(operation):
    async def execute():
        client = AsyncIOMotorClient(BACKEND_ENV["MONGO_URL"])
        try:
            return await operation(client[BACKEND_ENV["DB_NAME"]])
        finally:
            client.close()

    return asyncio.run(execute())


def _tiny_png_data_url():
    image = Image.new("RGB", (2, 2), (0, 49, 122))
    output = io.BytesIO()
    image.save(output, format="PNG")
    return "data:image/png;base64," + base64.b64encode(output.getvalue()).decode()


def _register_account(label):
    email = f"test.iter20.{label}.{uuid.uuid4().hex}@example.com"
    session = requests.Session()
    session.headers.update({"Content-Type": "application/json"})
    response = session.post(
        f"{BASE_URL}/api/auth/register",
        json={"email": email, "password": TEST_PASSWORD, "name": f"TEST Iteration20 {label}"},
        timeout=30,
    )
    assert response.status_code == 200, response.text
    return {"email": email, "id": response.json()["id"], "session": session}


def _cleanup_accounts(emails):
    async def cleanup(database):
        users = await database.users.find({"email": {"$in": list(emails)}}).to_list(100)
        user_ids = [str(user["_id"]) for user in users]
        object_ids = [user["_id"] for user in users]
        if user_ids:
            await database.entries.delete_many({"user_id": {"$in": user_ids}})
            await database.clients.delete_many({"user_id": {"$in": user_ids}})
            await database.document_types.delete_many({"user_id": {"$in": user_ids}})
            await database.signer_intakes.delete_many({"user_id": {"$in": user_ids}})
            await database.payment_transactions.delete_many({"user_id": {"$in": user_ids}})
        await database.invites.delete_many({"email": {"$in": list(emails)}})
        await database.login_attempts.delete_many(
            {"identifier": {"$in": [f"email:{email}" for email in emails]}}
        )
        if object_ids:
            await database.users.delete_many({"_id": {"$in": object_ids}})

    _db_call(cleanup)


@pytest.fixture
def checklist_account():
    account = _register_account("checklist")
    yield account
    _cleanup_accounts({account["email"]})


class TestCustomRenewalChecklist:
    """Custom step validation, merge/count, toggle persistence, deletion, and limits."""

    def test_create_merge_toggle_delete_and_completion_cleanup(self, checklist_account):
        session = checklist_account["session"]
        baseline = session.get(f"{BASE_URL}/api/renewal-checklist", timeout=20)
        assert baseline.status_code == 200, baseline.text
        initial = baseline.json()
        assert initial["total_count"] == len(initial["items"])
        assert all(item["custom"] is False for item in initial["items"])

        created = session.post(
            f"{BASE_URL}/api/renewal-checklist/custom",
            json={"title": "  TEST Upload bond receipt  ", "detail": "  Keep a PDF copy  "},
            timeout=20,
        )
        assert created.status_code == 200, created.text
        item = created.json()["item"]
        assert item["id"].startswith("custom:")
        assert item["title"] == "TEST Upload bond receipt"
        assert item["detail"] == "Keep a PDF copy"
        assert item["completed"] is False
        assert item["custom"] is True
        assert item["state_specific"] is False
        custom_id = item["id"]

        merged = session.get(f"{BASE_URL}/api/renewal-checklist", timeout=20).json()
        merged_item = next(row for row in merged["items"] if row["id"] == custom_id)
        assert merged_item == {
            "id": custom_id,
            "title": "TEST Upload bond receipt",
            "detail": "Keep a PDF copy",
            "completed": False,
            "state_specific": False,
            "custom": True,
        }
        assert merged["total_count"] == initial["total_count"] + 1
        assert merged["completed_count"] == initial["completed_count"]

        checked = session.post(
            f"{BASE_URL}/api/renewal-checklist/toggle",
            json={"item_id": custom_id, "done": True},
            timeout=20,
        )
        assert checked.status_code == 200 and checked.json() == {"ok": True}
        after_check = session.get(f"{BASE_URL}/api/renewal-checklist", timeout=20).json()
        assert next(row for row in after_check["items"] if row["id"] == custom_id)["completed"] is True
        assert after_check["completed_count"] == initial["completed_count"] + 1

        unchecked = session.post(
            f"{BASE_URL}/api/renewal-checklist/toggle",
            json={"item_id": custom_id, "done": False},
            timeout=20,
        )
        assert unchecked.status_code == 200 and unchecked.json() == {"ok": True}
        after_uncheck = session.get(f"{BASE_URL}/api/renewal-checklist", timeout=20).json()
        assert next(row for row in after_uncheck["items"] if row["id"] == custom_id)["completed"] is False

        session.post(
            f"{BASE_URL}/api/renewal-checklist/toggle",
            json={"item_id": custom_id, "done": True},
            timeout=20,
        )
        deleted = session.delete(
            f"{BASE_URL}/api/renewal-checklist/custom/{custom_id}", timeout=20
        )
        assert deleted.status_code == 200 and deleted.json() == {"ok": True}
        final = session.get(f"{BASE_URL}/api/renewal-checklist", timeout=20).json()
        assert custom_id not in {row["id"] for row in final["items"]}
        assert final["total_count"] == initial["total_count"]
        assert final["completed_count"] == initial["completed_count"]

        async def inspect(database):
            return await database.users.find_one({"_id": ObjectId(checklist_account["id"])})

        user = _db_call(inspect)
        assert custom_id not in user.get("renewal_checklist_progress", [])
        assert custom_id not in {row["id"] for row in user.get("custom_checklist_items", [])}

    @pytest.mark.parametrize("title", ["", "   ", "X" * 121])
    def test_title_validation(self, checklist_account, title):
        response = checklist_account["session"].post(
            f"{BASE_URL}/api/renewal-checklist/custom",
            json={"title": title, "detail": "TEST invalid"},
            timeout=20,
        )
        assert response.status_code == 400, response.text
        assert isinstance(response.json()["detail"], str) and response.json()["detail"]

    def test_unknown_toggle_and_invalid_deletes(self, checklist_account):
        session = checklist_account["session"]
        unknown_id = f"custom:TEST_UNKNOWN_{uuid.uuid4().hex}"
        unknown_toggle = session.post(
            f"{BASE_URL}/api/renewal-checklist/toggle",
            json={"item_id": unknown_id, "done": True},
            timeout=20,
        )
        assert unknown_toggle.status_code == 400
        assert unknown_toggle.json()["detail"] == "Unknown checklist item"

        builtin_delete = session.delete(
            f"{BASE_URL}/api/renewal-checklist/custom/notify_state", timeout=20
        )
        assert builtin_delete.status_code == 400
        assert "Only custom" in builtin_delete.json()["detail"]

        missing_delete = session.delete(
            f"{BASE_URL}/api/renewal-checklist/custom/{unknown_id}", timeout=20
        )
        assert missing_delete.status_code == 404
        assert missing_delete.json()["detail"] == "Custom step not found."

    def test_sequential_twenty_step_limit(self, checklist_account):
        session = checklist_account["session"]
        ids = []
        for index in range(20):
            response = session.post(
                f"{BASE_URL}/api/renewal-checklist/custom",
                json={"title": f"TEST Sequential step {index + 1}", "detail": "limit"},
                timeout=20,
            )
            assert response.status_code == 200, (index, response.text)
            ids.append(response.json()["item"]["id"])
        rejected = session.post(
            f"{BASE_URL}/api/renewal-checklist/custom",
            json={"title": "TEST Sequential step 21", "detail": "limit"},
            timeout=20,
        )
        assert rejected.status_code == 400
        assert rejected.json()["detail"] == "Maximum 20 custom steps."
        checklist = session.get(f"{BASE_URL}/api/renewal-checklist", timeout=20).json()
        assert len([item for item in checklist["items"] if item["custom"]]) == 20
        assert set(ids) == {item["id"] for item in checklist["items"] if item["custom"]}

    def test_concurrent_creates_never_exceed_twenty_step_limit(self, checklist_account):
        cookies = checklist_account["session"].cookies.get_dict()

        def add(index):
            return requests.post(
                f"{BASE_URL}/api/renewal-checklist/custom",
                json={"title": f"TEST Concurrent step {index}", "detail": "atomic limit"},
                cookies=cookies,
                timeout=30,
            )

        with ThreadPoolExecutor(max_workers=25) as pool:
            responses = list(pool.map(add, range(25)))
        statuses = [response.status_code for response in responses]
        assert statuses.count(200) == 20, [(r.status_code, r.text[:150]) for r in responses]
        assert statuses.count(400) == 5, [(r.status_code, r.text[:150]) for r in responses]
        checklist = checklist_account["session"].get(
            f"{BASE_URL}/api/renewal-checklist", timeout=20
        ).json()
        assert len([item for item in checklist["items"] if item["custom"]]) == 20


@pytest.fixture
def admin_brand_snapshot(credentials):
    fields = ("firm_name", "firm_website", "firm_logo_data", "firm_logo_updated_at")

    async def snapshot(database):
        return await database.users.find_one({"email": credentials["email"].lower()})

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
        if update:
            await database.users.update_one({"_id": original["_id"]}, update)

    _db_call(restore)


class TestPublicIntakeBranding:
    """Public intake exposes owner branding but never submitted signer PII."""

    def test_pending_and_completed_public_intake_branding_without_pii(
        self, authenticated_client, admin_brand_snapshot
    ):
        firm_name = "TEST Iteration20 Branded Notary"
        firm_website = "https://iteration20.example.test"
        logo = _tiny_png_data_url()
        profile = authenticated_client.put(
            f"{BASE_URL}/api/auth/profile",
            json={"firm_name": firm_name, "firm_website": firm_website},
            timeout=20,
        )
        assert profile.status_code == 200, profile.text
        uploaded = authenticated_client.put(
            f"{BASE_URL}/api/auth/firm-logo",
            json={"firm_logo_data": logo},
            timeout=20,
        )
        assert uploaded.status_code == 200, uploaded.text

        created = authenticated_client.post(
            f"{BASE_URL}/api/intakes",
            json={
                "label": "TEST Iteration20 intake",
                "appointment_note": "TEST branded appointment",
                "expires_hours": 24,
            },
            timeout=20,
        )
        assert created.status_code == 200, created.text
        intake = created.json()
        intake_id = intake["id"]
        token = intake["token"]
        try:
            pending = requests.get(f"{BASE_URL}/api/public/intake/{token}", timeout=20)
            assert pending.status_code == 200, pending.text
            pending_data = pending.json()
            assert pending_data["status"] == "pending"
            assert pending_data["firm_name"] == firm_name
            assert pending_data["firm_website"] == firm_website
            assert pending_data["firm_logo_data"] == logo
            assert "data" not in pending_data

            pii = {
                "signer_name": f"TEST_SECRET_NAME_{uuid.uuid4().hex}",
                "signer_address": "TEST_SECRET_ADDRESS_20",
                "signer_phone": "+1-555-0200",
                "signer_email": f"secret-{uuid.uuid4().hex}@example.com",
                "id_type": "Passport",
                "id_number": f"TEST_SECRET_ID_{uuid.uuid4().hex}",
                "id_expiration": "2030-01-01",
                "id_issued_by": "TEST",
                "notes": "TEST_SECRET_NOTES_20",
            }
            submitted = requests.post(
                f"{BASE_URL}/api/public/intake/{token}", json=pii, timeout=20
            )
            assert submitted.status_code == 200, submitted.text
            completed = requests.get(f"{BASE_URL}/api/public/intake/{token}", timeout=20)
            assert completed.status_code == 200, completed.text
            completed_data = completed.json()
            assert completed_data["status"] == "completed"
            assert completed_data["firm_name"] == firm_name
            assert completed_data["firm_website"] == firm_website
            assert completed_data["firm_logo_data"] == logo
            forbidden_keys = {
                "data", "signer_name", "signer_address", "signer_phone", "signer_email",
                "id_type", "id_number", "id_expiration", "id_issued_by", "notes",
            }
            assert forbidden_keys.isdisjoint(completed_data)
            serialized = completed.text
            for field in (
                "signer_name", "signer_address", "signer_phone", "signer_email",
                "id_number", "id_expiration", "notes",
            ):
                assert pii[field] not in serialized
        finally:
            deleted = authenticated_client.delete(
                f"{BASE_URL}/api/intakes/{intake_id}", timeout=20
            )
            assert deleted.status_code == 200, deleted.text


class TestExtractedInviteRoutes:
    """Extracted invite router retains CRUD, public info/accept, and atomic acceptance."""

    def test_create_list_detail_public_accept_and_delete(self, authenticated_client):
        run_id = uuid.uuid4().hex
        accepted_email = f"test.iter20.invite.accept.{run_id}@example.com"
        revoked_email = f"test.iter20.invite.revoke.{run_id}@example.com"
        emails = {accepted_email, revoked_email}
        tokens = []
        try:
            accepted_create = authenticated_client.post(
                f"{BASE_URL}/api/invites",
                json={
                    "email": accepted_email,
                    "firm_name": "TEST Iteration20 Firm",
                    "state": "PA",
                    "message": "TEST extracted route",
                },
                timeout=45,
            )
            assert accepted_create.status_code == 200, accepted_create.text
            accepted_invite = accepted_create.json()
            tokens.append(accepted_invite["token"])
            assert accepted_invite["email"] == accepted_email
            assert accepted_invite["firm_name"] == "TEST Iteration20 Firm"

            listed = authenticated_client.get(f"{BASE_URL}/api/invites", timeout=20)
            assert listed.status_code == 200, listed.text
            listed_data = listed.json()
            assert set(listed_data) == {"invites"}
            assert isinstance(listed_data["invites"], list)
            accepted_row = next(
                row for row in listed_data["invites"] if row["token"] == accepted_invite["token"]
            )
            detail = authenticated_client.get(
                f"{BASE_URL}/api/invites/{accepted_row['id']}", timeout=20
            )
            assert detail.status_code == 200, detail.text
            assert detail.json()["token"] == accepted_invite["token"]
            assert detail.json()["email"] == accepted_email

            public = requests.get(
                f"{BASE_URL}/api/public/invite/{accepted_invite['token']}", timeout=20
            )
            assert public.status_code == 200, public.text
            assert public.json()["email"] == accepted_email
            assert public.json()["firm_name"] == "TEST Iteration20 Firm"
            assert public.json()["state"] == "PA"

            accepted_session = requests.Session()
            accepted = accepted_session.post(
                f"{BASE_URL}/api/public/invite/{accepted_invite['token']}/accept",
                json={"password": TEST_PASSWORD, "name": "TEST Iteration20 Accepted"},
                timeout=30,
            )
            assert accepted.status_code == 200, accepted.text
            assert accepted.json()["email"] == accepted_email
            assert accepted.json()["firm_name"] == "TEST Iteration20 Firm"
            assert "httponly" in accepted.headers.get("set-cookie", "").lower()
            me = accepted_session.get(f"{BASE_URL}/api/auth/me", timeout=20)
            assert me.status_code == 200 and me.json()["email"] == accepted_email
            reused = requests.post(
                f"{BASE_URL}/api/public/invite/{accepted_invite['token']}/accept",
                json={"password": TEST_PASSWORD, "name": "TEST Reuse"},
                timeout=20,
            )
            assert reused.status_code == 410

            revoked_create = authenticated_client.post(
                f"{BASE_URL}/api/invites",
                json={"email": revoked_email, "firm_name": "TEST Revoke", "state": "NJ"},
                timeout=45,
            )
            assert revoked_create.status_code == 200, revoked_create.text
            revoked_token = revoked_create.json()["token"]
            tokens.append(revoked_token)
            rows = authenticated_client.get(f"{BASE_URL}/api/invites", timeout=20).json()["invites"]
            revoked_id = next(row["id"] for row in rows if row["token"] == revoked_token)
            deleted = authenticated_client.delete(
                f"{BASE_URL}/api/invites/{revoked_id}", timeout=20
            )
            assert deleted.status_code == 200 and deleted.json() == {"ok": True}
            assert requests.get(
                f"{BASE_URL}/api/public/invite/{revoked_token}", timeout=20
            ).status_code == 404
        finally:
            async def cleanup_invites(database):
                await database.invites.delete_many({"token": {"$in": tokens}})

            _db_call(cleanup_invites)
            _cleanup_accounts(emails)

    def test_six_concurrent_accepts_have_exactly_one_winner(self, authenticated_client):
        email = f"test.iter20.invite.race.{uuid.uuid4().hex}@example.com"
        token = None
        try:
            created = authenticated_client.post(
                f"{BASE_URL}/api/invites",
                json={"email": email, "firm_name": "TEST Iteration20 Race", "state": "TX"},
                timeout=45,
            )
            assert created.status_code == 200, created.text
            token = created.json()["token"]
            endpoint = f"{BASE_URL}/api/public/invite/{token}/accept"

            def accept(index):
                return requests.post(
                    endpoint,
                    json={"password": TEST_PASSWORD, "name": f"TEST Race Winner {index}"},
                    timeout=30,
                )

            with ThreadPoolExecutor(max_workers=6) as pool:
                responses = list(pool.map(accept, range(6)))
            statuses = [response.status_code for response in responses]
            assert statuses.count(200) == 1, [(r.status_code, r.text) for r in responses]
            assert statuses.count(410) == 5, [(r.status_code, r.text) for r in responses]

            async def inspect(database):
                users = await database.users.find({"email": email}).to_list(10)
                invite = await database.invites.find_one({"token": token})
                return users, invite

            users, invite = _db_call(inspect)
            assert len(users) == 1
            assert invite["accepted_user_id"] == str(users[0]["_id"])
            assert datetime.fromisoformat(invite["used_at"]) <= datetime.now(timezone.utc)
        finally:
            async def cleanup_invite(database):
                if token:
                    await database.invites.delete_many({"token": token})

            _db_call(cleanup_invite)
            _cleanup_accounts({email})

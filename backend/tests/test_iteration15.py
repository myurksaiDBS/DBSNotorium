"""Iteration 15 targeted atomicity, image validation, invite detail, and lockout tests."""
import asyncio
import base64
import io
import uuid
from datetime import datetime, timezone

import aiohttp
import bcrypt
import pytest
import requests
from bson import ObjectId
from dotenv import dotenv_values
from motor.motor_asyncio import AsyncIOMotorClient
from PIL import Image

from conftest import BASE_URL

BACKEND_ENV = dotenv_values("/app/backend/.env")
TEST_PASSWORD = "TEST_Iteration15_2026!"


def _db_call(operation):
    async def execute():
        client = AsyncIOMotorClient(BACKEND_ENV["MONGO_URL"])
        try:
            return await operation(client[BACKEND_ENV["DB_NAME"]])
        finally:
            client.close()

    return asyncio.run(execute())


def _image_data_url(image_format, mime_type):
    image = Image.new("RGB", (1, 1), (31, 97, 173))
    output = io.BytesIO()
    image.save(output, format=image_format)
    return f"data:{mime_type};base64," + base64.b64encode(output.getvalue()).decode()


def _register_test_user(label):
    email = f"test.iter15.{label}.{uuid.uuid4().hex}@example.com"
    session = requests.Session()
    session.headers.update({"Content-Type": "application/json"})
    response = session.post(
        f"{BASE_URL}/api/auth/register",
        json={"email": email, "password": TEST_PASSWORD, "name": f"TEST Iteration15 {label}"},
        timeout=30,
    )
    assert response.status_code == 200, response.text
    return {"email": email, "id": response.json()["id"], "session": session}


def _cleanup_emails(emails):
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
        await database.login_attempts.delete_many(
            {"identifier": {"$in": [f"email:{email}" for email in emails]}}
        )
        if object_ids:
            await database.users.delete_many({"_id": {"$in": object_ids}})

    _db_call(cleanup)


@pytest.fixture
def admin_logo_snapshot(credentials):
    email = credentials["email"].lower()

    async def snapshot(database):
        return await database.users.find_one({"email": email})

    original = _db_call(snapshot)
    assert original is not None
    yield

    async def restore(database):
        fields = ("firm_logo_data", "firm_logo_updated_at")
        update = {}
        set_values = {field: original[field] for field in fields if field in original}
        unset_values = {field: "" for field in fields if field not in original}
        if set_values:
            update["$set"] = set_values
        if unset_values:
            update["$unset"] = unset_values
        if update:
            await database.users.update_one({"_id": original["_id"]}, update)

    _db_call(restore)


class TestFirmLogoValidationFix:
    """Firm logos must decode as genuine PNG/JPEG image bytes."""

    def test_spoofed_png_rejected_and_real_png_jpeg_accepted(
        self, authenticated_client, admin_logo_snapshot
    ):
        cases = [
            (
                "spoofed PNG",
                "data:image/png;base64,SGVsbG8gV29ybGQ=",
                400,
                "could not be decoded",
            ),
            ("real 1x1 PNG", _image_data_url("PNG", "image/png"), 200, None),
            ("real 1x1 JPEG", _image_data_url("JPEG", "image/jpeg"), 200, None),
            ("GIF", _image_data_url("GIF", "image/gif"), 400, "PNG or JPEG"),
            ("BMP", _image_data_url("BMP", "image/bmp"), 400, "PNG or JPEG"),
        ]
        try:
            for label, data_url, expected_status, expected_detail in cases:
                response = authenticated_client.put(
                    f"{BASE_URL}/api/auth/firm-logo",
                    json={"firm_logo_data": data_url},
                    timeout=30,
                )
                assert response.status_code == expected_status, (
                    label,
                    response.status_code,
                    response.text[:300],
                )
                if expected_status == 200:
                    assert response.json() == {"ok": True}
                    fetched = authenticated_client.get(
                        f"{BASE_URL}/api/auth/firm-logo", timeout=20
                    )
                    assert fetched.status_code == 200
                    assert fetched.json()["has_logo"] is True
                    assert fetched.json()["firm_logo_data"] == data_url
                else:
                    assert expected_detail.lower() in response.json()["detail"].lower()
        finally:
            authenticated_client.delete(f"{BASE_URL}/api/auth/firm-logo", timeout=20)


class TestInviteFixes:
    """Invite detail ownership and concurrent one-winner acceptance."""

    def test_get_invite_contract_owner_isolation_and_bad_id(self, authenticated_client):
        target_email = f"test.iter15.detail.{uuid.uuid4().hex}@example.com"
        foreign = _register_test_user("foreign-owner")
        token = None
        try:
            created = authenticated_client.post(
                f"{BASE_URL}/api/invites",
                json={
                    "email": target_email,
                    "firm_name": "TEST Iteration15 Firm",
                    "state": "PA",
                    "message": "TEST invite detail contract",
                },
                timeout=45,
            )
            assert created.status_code == 200, created.text
            token = created.json()["token"]

            listed = authenticated_client.get(f"{BASE_URL}/api/invites", timeout=20)
            assert listed.status_code == 200
            rows = listed.json().get("invites", [])
            invite_id = next(row["id"] for row in rows if row["token"] == token)
            foreign_listed = foreign["session"].get(f"{BASE_URL}/api/invites", timeout=20)
            assert foreign_listed.status_code == 200
            foreign_rows = foreign_listed.json().get("invites", [])
            assert token not in {row["token"] for row in foreign_rows}

            owner = authenticated_client.get(
                f"{BASE_URL}/api/invites/{invite_id}", timeout=20
            )
            assert owner.status_code == 200, owner.text
            data = owner.json()
            assert set(data) == {
                "id",
                "token",
                "email",
                "firm_name",
                "state",
                "created_at",
                "expires_at",
                "used_at",
                "email_status",
                "email_error",
                "invite_url",
            }
            assert data["id"] == invite_id
            assert data["token"] == token and data["email"] == target_email
            assert data["firm_name"] == "TEST Iteration15 Firm" and data["state"] == "PA"
            assert data["used_at"] is None
            assert datetime.fromisoformat(data["created_at"]) <= datetime.now(timezone.utc)
            assert datetime.fromisoformat(data["expires_at"]) > datetime.now(timezone.utc)
            assert data["email_status"] in ("sent", "failed")
            assert data["invite_url"] == f"{BASE_URL}/invite/{token}"

            non_owner = foreign["session"].get(
                f"{BASE_URL}/api/invites/{invite_id}", timeout=20
            )
            assert non_owner.status_code == 404
            assert non_owner.json()["detail"] == "Invite not found"

            malformed = authenticated_client.get(
                f"{BASE_URL}/api/invites/not-a-valid-object-id", timeout=20
            )
            assert malformed.status_code == 404
            assert malformed.json()["detail"] == "Invite not found"
        finally:
            async def cleanup_invite(database):
                if token:
                    await database.invites.delete_many({"token": token})

            _db_call(cleanup_invite)
            _cleanup_emails({target_email, foreign["email"]})

    def test_six_concurrent_accepts_have_one_winner_and_five_410s(
        self, authenticated_client
    ):
        target_email = f"test.iter15.race.{uuid.uuid4().hex}@example.com"
        token = None
        try:
            created = authenticated_client.post(
                f"{BASE_URL}/api/invites",
                json={
                    "email": target_email,
                    "firm_name": "TEST Atomic Invite Firm",
                    "state": "NJ",
                },
                timeout=45,
            )
            assert created.status_code == 200, created.text
            token = created.json()["token"]
            endpoint = f"{BASE_URL}/api/public/invite/{token}/accept"

            async def race_accepts():
                timeout = aiohttp.ClientTimeout(total=60)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async def accept(index):
                        async with session.post(
                            endpoint,
                            json={"password": TEST_PASSWORD, "name": f"TEST Atomic Winner {index}"},
                        ) as response:
                            return response.status, await response.json()

                    return await asyncio.gather(*(accept(index) for index in range(6)))

            results = asyncio.run(race_accepts())
            statuses = [status for status, _ in results]
            assert statuses.count(200) == 1, results
            assert statuses.count(410) == 5, results
            assert 400 not in statuses, results
            for status, body in results:
                if status == 410:
                    assert "already" in body["detail"].lower()

            async def inspect(database):
                users = await database.users.find({"email": target_email}).to_list(10)
                invite = await database.invites.find_one({"token": token})
                return users, invite

            users, invite = _db_call(inspect)
            assert len(users) == 1
            assert invite["used_at"] is not None
            assert invite["accepted_user_id"] == str(users[0]["_id"])
        finally:
            async def cleanup(database):
                if token:
                    await database.invites.delete_many({"token": token})

            _db_call(cleanup)
            _cleanup_emails({target_email})


class TestConcurrentLoginLockoutFix:
    """Concurrent invalid logins must atomically increment the shared email counter."""

    def test_seven_concurrent_wrong_passwords_do_not_lose_increments(self):
        account = _register_test_user("lockout")
        identifier = f"email:{account['email']}"
        try:
            async def clear(database):
                await database.login_attempts.delete_many({"identifier": identifier})

            _db_call(clear)
            endpoint = f"{BASE_URL}/api/auth/login"

            async def race_logins():
                timeout = aiohttp.ClientTimeout(total=60)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async def wrong_login():
                        async with session.post(
                            endpoint,
                            json={"email": account["email"], "password": "TEST_Wrong_Password!"},
                        ) as response:
                            return response.status, await response.json()

                    return await asyncio.gather(*(wrong_login() for _ in range(7)))

            results = asyncio.run(race_logins())
            statuses = [status for status, _ in results]
            assert set(statuses).issubset({401, 429}), results
            assert statuses.count(401) >= 5, results

            async def inspect(database):
                return await database.login_attempts.find(
                    {"identifier": identifier}
                ).to_list(10)

            attempt_docs = _db_call(inspect)
            assert len(attempt_docs) == 1, (
                "A shared identifier must have exactly one counter document",
                attempt_docs,
            )
            attempt = attempt_docs[0]
            assert attempt["count"] >= 5
            assert attempt["count"] == statuses.count(401), (
                "Every request that returned 401 must contribute exactly one atomic increment",
                results,
                attempt,
            )
            assert datetime.fromisoformat(attempt["locked_until"]) > datetime.now(timezone.utc)

            subsequent = requests.post(
                endpoint,
                json={"email": account["email"], "password": TEST_PASSWORD},
                timeout=20,
            )
            assert subsequent.status_code == 429
            assert "Too many attempts" in subsequent.json()["detail"]
        finally:
            _cleanup_emails({account["email"]})

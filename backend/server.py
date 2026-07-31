from dotenv import load_dotenv
from pathlib import Path
ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

import os
import io
import logging
import base64
import asyncio
import hmac
import httpx
import html as _html
import qrcode
from qrcode.image.pil import PilImage
from datetime import datetime, timezone, timedelta, date
from typing import List, Optional, Annotated

import bcrypt
import jwt
from bson import ObjectId
from pymongo.errors import DuplicateKeyError
from fastapi import FastAPI, APIRouter, HTTPException, Depends, Request, Response, Query, Header, BackgroundTasks
from fastapi.responses import StreamingResponse
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, Field, EmailStr, BeforeValidator, ConfigDict
from starlette.middleware.cors import CORSMiddleware

from reportlab.lib.pagesizes import letter, landscape
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image as RLImage
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.utils import ImageReader

import stripe

LOGO_PATH = ROOT_DIR / "assets" / "domini-logo.png"

# -------- Stripe --------
stripe.api_key = os.environ.get("STRIPE_SECRET_KEY") or "sk_test_emergent"
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
STRIPE_PUBLISHABLE_KEY = os.environ.get("STRIPE_PUBLISHABLE_KEY", "")
TRIAL_DAYS = 14
BILLING_PLANS = {
    "notarium_pro_monthly": {"label": "Monthly", "amount": 19.00, "interval": "month"},
    "notarium_pro_yearly":  {"label": "Yearly",  "amount": 190.00, "interval": "year"},
}

# -------- Mongo --------
mongo_url = os.environ['MONGO_URL']
mongo_client = AsyncIOMotorClient(mongo_url)
db = mongo_client[os.environ['DB_NAME']]

JWT_ALGORITHM = "HS256"

def get_jwt_secret() -> str:
    return os.environ["JWT_SECRET"]

# -------- Utils --------
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except Exception:
        return False

def create_access_token(user_id: str, email: str) -> str:
    payload = {"sub": user_id, "email": email,
               "exp": datetime.now(timezone.utc) + timedelta(hours=8),
               "type": "access"}
    return jwt.encode(payload, get_jwt_secret(), algorithm=JWT_ALGORITHM)

def create_refresh_token(user_id: str) -> str:
    payload = {"sub": user_id,
               "exp": datetime.now(timezone.utc) + timedelta(days=7),
               "type": "refresh"}
    return jwt.encode(payload, get_jwt_secret(), algorithm=JWT_ALGORITHM)

def set_auth_cookies(response: Response, access: str, refresh: str):
    response.set_cookie("access_token", access, httponly=True, secure=True,
                        samesite="none", max_age=8*3600, path="/")
    response.set_cookie("refresh_token", refresh, httponly=True, secure=True,
                        samesite="none", max_age=7*24*3600, path="/")

def oid(v) -> str:
    return str(v)

PyObjectId = Annotated[str, BeforeValidator(oid)]

# -------- Models --------
class UserOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    id: PyObjectId = Field(alias="_id")
    email: EmailStr
    name: str
    role: str = "notary"
    commission_number: Optional[str] = None
    commission_expires: Optional[str] = None
    state: Optional[str] = None

class RegisterIn(BaseModel):
    email: EmailStr
    password: str
    name: str

class LoginIn(BaseModel):
    email: EmailStr
    password: str

class ProfileIn(BaseModel):
    name: Optional[str] = None
    commission_number: Optional[str] = None
    commission_expires: Optional[str] = None
    state: Optional[str] = None
    alerts_enabled: Optional[bool] = None
    firm_name: Optional[str] = None

# Journal entry
class EntryIn(BaseModel):
    entry_date: str  # ISO datetime string
    notarization_type: str
    document_type: str
    document_description: Optional[str] = ""
    signer_name: str
    signer_address: Optional[str] = ""
    signer_phone: Optional[str] = ""
    signer_email: Optional[str] = ""
    id_type: str
    id_number: str
    id_expiration: Optional[str] = ""
    id_issued_by: Optional[str] = ""
    witness_name: Optional[str] = ""
    fee: float = 0.0
    notes: Optional[str] = ""
    signature_data: Optional[str] = ""  # base64 PNG
    client_id: Optional[str] = None

# Client
class ClientIn(BaseModel):
    name: str
    address: Optional[str] = ""
    phone: Optional[str] = ""
    email: Optional[str] = ""
    id_type: Optional[str] = ""
    id_number: Optional[str] = ""
    notes: Optional[str] = ""

# Document Type
class DocTypeIn(BaseModel):
    name: str
    description: Optional[str] = ""

# Signer Intake (pre-fill link)
class IntakeCreateIn(BaseModel):
    label: Optional[str] = ""          # e.g., "Smith closing – Thu 3pm"
    appointment_note: Optional[str] = ""
    expires_hours: int = 168           # 7 days default

class IntakeSubmitIn(BaseModel):
    signer_name: str = Field(min_length=1)
    signer_address: Optional[str] = ""
    signer_phone: Optional[str] = ""
    signer_email: Optional[str] = ""
    id_type: str = Field(min_length=1)
    id_number: str = Field(min_length=1)
    id_expiration: Optional[str] = ""
    id_issued_by: Optional[str] = ""
    notes: Optional[str] = ""

# -------- Auth Dependency --------
async def get_current_user(request: Request) -> dict:
    token = request.cookies.get("access_token")
    if not token:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:]
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = jwt.decode(token, get_jwt_secret(), algorithms=[JWT_ALGORITHM])
        if payload.get("type") != "access":
            raise HTTPException(status_code=401, detail="Invalid token type")
        user = await db.users.find_one({"_id": ObjectId(payload["sub"])})
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        user["_id"] = str(user["_id"])
        user.pop("password_hash", None)
        return user
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")

def serialize_entry(doc: dict) -> dict:
    d = dict(doc)
    d["id"] = str(d.pop("_id"))
    d.pop("user_id", None)
    return d

def serialize_generic(doc: dict) -> dict:
    d = dict(doc)
    d["id"] = str(d.pop("_id"))
    d.pop("user_id", None)
    return d

# -------- App --------
app = FastAPI(title="Notary Journal API")
api = APIRouter(prefix="/api")

@api.get("/")
async def root():
    return {"message": "Notary Journal API", "status": "ok"}

# ---- Auth Routes ----
@api.post("/auth/register")
async def register(payload: RegisterIn, response: Response):
    email = payload.email.lower().strip()
    if await db.users.find_one({"email": email}):
        raise HTTPException(status_code=400, detail="Email already registered")
    user_doc = {
        "email": email,
        "password_hash": hash_password(payload.password),
        "name": payload.name,
        "role": "notary",
        "commission_number": None,
        "commission_expires": None,
        "state": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    result = await db.users.insert_one(user_doc)
    uid = str(result.inserted_id)
    access = create_access_token(uid, email)
    refresh = create_refresh_token(uid)
    set_auth_cookies(response, access, refresh)
    return {"id": uid, "email": email, "name": payload.name, "role": "notary"}

@api.post("/auth/login")
async def login(payload: LoginIn, response: Response, request: Request):
    email = payload.email.lower().strip()
    identifier = f"{request.client.host}:{email}"
    now = datetime.now(timezone.utc)
    attempt_doc = await db.login_attempts.find_one({"identifier": identifier})
    if attempt_doc and attempt_doc.get("count", 0) >= 5:
        locked_until = attempt_doc.get("locked_until")
        if locked_until and datetime.fromisoformat(locked_until) > now:
            raise HTTPException(status_code=429, detail="Too many attempts. Try again in 15 minutes.")

    user = await db.users.find_one({"email": email})
    if not user or not verify_password(payload.password, user["password_hash"]):
        count = (attempt_doc.get("count", 0) if attempt_doc else 0) + 1
        update = {"count": count, "updated_at": now.isoformat()}
        if count >= 5:
            update["locked_until"] = (now + timedelta(minutes=15)).isoformat()
        await db.login_attempts.update_one(
            {"identifier": identifier}, {"$set": update, "$setOnInsert": {"identifier": identifier}},
            upsert=True)
        raise HTTPException(status_code=401, detail="Invalid email or password")

    await db.login_attempts.delete_one({"identifier": identifier})
    uid = str(user["_id"])
    access = create_access_token(uid, email)
    refresh = create_refresh_token(uid)
    set_auth_cookies(response, access, refresh)
    return {
        "id": uid, "email": user["email"], "name": user["name"],
        "role": user.get("role", "notary"),
        "commission_number": user.get("commission_number"),
        "commission_expires": user.get("commission_expires"),
        "state": user.get("state"),
        "alerts_enabled": user.get("alerts_enabled", True),
    }

@api.post("/auth/logout")
async def logout(response: Response, _user=Depends(get_current_user)):
    response.delete_cookie("access_token", path="/")
    response.delete_cookie("refresh_token", path="/")
    return {"ok": True}

@api.get("/auth/me")
async def me(user=Depends(get_current_user)):
    return {
        "id": user["_id"], "email": user["email"], "name": user["name"],
        "role": user.get("role", "notary"),
        "commission_number": user.get("commission_number"),
        "commission_expires": user.get("commission_expires"),
        "state": user.get("state"),
        "alerts_enabled": user.get("alerts_enabled", True),
        "last_alert_sent_at": user.get("last_alert_sent_at"),
        "has_seal": bool(user.get("seal_data")),
        "firm_name": user.get("firm_name"),
    }

@api.post("/auth/refresh")
async def refresh(request: Request, response: Response):
    rt = request.cookies.get("refresh_token")
    if not rt:
        raise HTTPException(status_code=401, detail="No refresh token")
    try:
        payload = jwt.decode(rt, get_jwt_secret(), algorithms=[JWT_ALGORITHM])
        if payload.get("type") != "refresh":
            raise HTTPException(status_code=401, detail="Invalid token type")
        user = await db.users.find_one({"_id": ObjectId(payload["sub"])})
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        access = create_access_token(str(user["_id"]), user["email"])
        response.set_cookie("access_token", access, httponly=True, secure=True,
                            samesite="none", max_age=8*3600, path="/")
        return {"ok": True}
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

@api.put("/auth/profile")
async def update_profile(payload: ProfileIn, user=Depends(get_current_user)):
    updates = {k: v for k, v in payload.model_dump(exclude_unset=True).items() if v is not None}
    # If the notary changes their commission expiration (e.g. after renewing),
    # clear alert-dedup state so the new cycle can trigger notices again.
    if "commission_expires" in updates and updates["commission_expires"] != user.get("commission_expires"):
        updates["last_alert_bucket"] = None
        updates["last_alert_sent_at"] = None
        updates["last_alert_email_id"] = None
    if updates:
        await db.users.update_one({"_id": ObjectId(user["_id"])}, {"$set": updates})
    doc = await db.users.find_one({"_id": ObjectId(user["_id"])})
    return {
        "id": str(doc["_id"]), "email": doc["email"], "name": doc["name"],
        "role": doc.get("role", "notary"),
        "commission_number": doc.get("commission_number"),
        "commission_expires": doc.get("commission_expires"),
        "state": doc.get("state"),
        "alerts_enabled": doc.get("alerts_enabled", True),
        "firm_name": doc.get("firm_name"),
        "has_seal": bool(doc.get("seal_data")),
    }

# ---- Admin Roster (super-admin only) ----
def _require_admin(user: dict):
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin only")

@api.get("/admin/roster")
async def admin_roster(user=Depends(get_current_user)):
    _require_admin(user)
    users = await db.users.find({}).sort("created_at", -1).to_list(1000)
    rows = []
    for u in users:
        uid = str(u["_id"])
        entry_count = await db.entries.count_documents({"user_id": uid})
        client_count = await db.clients.count_documents({"user_id": uid})
        last_entry = await db.entries.find_one({"user_id": uid}, sort=[("entry_date", -1)])
        rows.append({
            "id": uid,
            "email": u.get("email"),
            "name": u.get("name"),
            "firm_name": u.get("firm_name"),
            "role": u.get("role", "notary"),
            "state": u.get("state"),
            "commission_expires": u.get("commission_expires"),
            "created_at": u.get("created_at"),
            "entry_count": entry_count,
            "client_count": client_count,
            "last_activity": (last_entry or {}).get("entry_date"),
            "subscription_status": u.get("subscription_status", "trial"),
            "subscription_plan": u.get("subscription_plan", "Starter"),
        })
    return {"total": len(rows), "notaries": rows}

# -------- Billing helpers --------
def _trial_end(user: dict) -> datetime:
    created = user.get("created_at")
    try:
        base = datetime.fromisoformat(created) if created else datetime.now(timezone.utc)
    except (ValueError, TypeError):
        base = datetime.now(timezone.utc)
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)
    return base + timedelta(days=TRIAL_DAYS)


def _subscription_snapshot(user: dict) -> dict:
    """Compute the user's effective billing state.
    States: active | trialing | expired | canceled
    """
    role = user.get("role", "notary")
    status = user.get("subscription_status")
    if status == "active":
        effective = "active"
    else:
        end = _trial_end(user)
        now = datetime.now(timezone.utc)
        if now < end:
            effective = "trialing"
        else:
            effective = status if status in ("canceled", "past_due") else "expired"
    trial_end = _trial_end(user)
    now = datetime.now(timezone.utc)
    return {
        "effective_status": effective,
        "raw_status": status,
        "plan_lookup_key": user.get("subscription_plan_key"),
        "plan_label": BILLING_PLANS.get(user.get("subscription_plan_key") or "", {}).get("label"),
        "trial_days_left": max(0, (trial_end - now).days) if effective == "trialing" else 0,
        "trial_ends_at": trial_end.isoformat(),
        "current_period_end": user.get("subscription_current_period_end"),
        "cancel_at_period_end": user.get("subscription_cancel_at_period_end", False),
        "role": role,
        "is_admin_bypass": role == "admin",
    }


async def require_active_subscription(user=Depends(get_current_user)) -> dict:
    """Dependency: gate write actions when trial expired and no active plan.
    Admins always bypass."""
    if user.get("role") == "admin":
        return user
    snap = _subscription_snapshot(user)
    if snap["effective_status"] in ("active", "trialing"):
        return user
    raise HTTPException(
        status_code=402,
        detail={
            "error": "subscription_required",
            "message": "Your free trial has ended. Subscribe to continue using Notarium.",
            "billing_status": snap["effective_status"],
        },
    )


@api.get("/billing/status")
async def billing_status(user=Depends(get_current_user)):
    snap = _subscription_snapshot(user)
    return {
        "provider": "stripe",
        "publishable_key": STRIPE_PUBLISHABLE_KEY,
        "plans": [
            {"lookup_key": "notarium_pro_monthly", "label": "Monthly",
             "amount": 19.00, "currency": "usd", "interval": "month"},
            {"lookup_key": "notarium_pro_yearly", "label": "Yearly",
             "amount": 190.00, "currency": "usd", "interval": "year",
             "savings_label": "Save $38 (2 months free)"},
        ],
        **snap,
    }


class CheckoutIn(BaseModel):
    lookup_key: str
    origin_url: str


def _validate_origin_url(origin_url: str) -> str:
    """Only allow the configured app URL host to prevent open-redirect via Stripe return."""
    from urllib.parse import urlparse
    allowed = os.environ.get("ALERT_APP_URL", "").rstrip("/")
    try:
        p = urlparse(origin_url)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid origin_url")
    if p.scheme not in ("http", "https") or not p.netloc:
        raise HTTPException(status_code=400, detail="Invalid origin_url")
    if allowed:
        a = urlparse(allowed)
        if p.netloc != a.netloc:
            raise HTTPException(status_code=400, detail="origin_url host not allowed")
    return f"{p.scheme}://{p.netloc}"


@api.post("/billing/checkout")
async def billing_checkout(payload: CheckoutIn, user=Depends(get_current_user)):
    if payload.lookup_key not in BILLING_PLANS:
        raise HTTPException(status_code=400, detail="Unknown plan")

    prices = stripe.Price.list(lookup_keys=[payload.lookup_key], active=True, limit=1).data
    if not prices:
        raise HTTPException(status_code=500, detail=f"Price not found: {payload.lookup_key}")
    price = prices[0]

    # Reuse or create Stripe customer
    customer_id = user.get("stripe_customer_id")
    if not customer_id:
        cust = stripe.Customer.create(
            email=user.get("email"),
            name=user.get("name"),
            metadata={"user_id": user["_id"]},
        )
        customer_id = cust.id
        await db.users.update_one(
            {"_id": ObjectId(user["_id"])},
            {"$set": {"stripe_customer_id": customer_id}},
        )

    origin = _validate_origin_url(payload.origin_url)
    session_kwargs = dict(
        customer=customer_id,
        line_items=[{"price": price.id, "quantity": 1}],
        mode="subscription",
        success_url=f"{origin}/settings?tab=billing&session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{origin}/settings?tab=billing&canceled=1",
        metadata={"user_id": user["_id"], "lookup_key": payload.lookup_key},
        subscription_data={
            "trial_period_days": TRIAL_DAYS,
            "metadata": {"user_id": user["_id"], "lookup_key": payload.lookup_key},
        },
    )
    # SMP-eligible (US SaaS): use managed_payments; fall back to Stripe Tax if ineligible
    try:
        session = stripe.checkout.Session.create(**session_kwargs, managed_payments={"enabled": True})
    except stripe.error.InvalidRequestError as e:
        msg = (getattr(e, "user_message", "") or "").lower()
        if "managed payments" in msg or "ineligible" in msg:
            session = stripe.checkout.Session.create(
                **session_kwargs,
                automatic_tax={"enabled": True},
                billing_address_collection="required",
            )
        else:
            raise HTTPException(status_code=500, detail=str(e))

    await db.payment_transactions.insert_one({
        "session_id": session.id,
        "user_id": user["_id"],
        "customer_id": customer_id,
        "lookup_key": payload.lookup_key,
        "amount": (price.unit_amount or 0),
        "currency": price.currency,
        "status": "initiated",
        "payment_status": "pending",
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    })
    return {"checkout_url": session.url, "session_id": session.id}


@api.get("/billing/session/{session_id}")
async def billing_session_status(session_id: str, user=Depends(get_current_user)):
    record = await db.payment_transactions.find_one({"session_id": session_id})
    if not record or record.get("user_id") != user["_id"]:
        raise HTTPException(status_code=404, detail="Session not found")
    # Poll Stripe live if still pending — webhook may lag
    if record.get("payment_status") != "paid":
        try:
            s = stripe.checkout.Session.retrieve(session_id)
            if s.payment_status == "paid" or s.status == "complete":
                await _apply_subscription_from_session(s)
                record = await db.payment_transactions.find_one({"session_id": session_id})
        except stripe.error.StripeError:
            pass
    return {
        "session_id": record["session_id"],
        "status": record.get("status"),
        "payment_status": record.get("payment_status"),
    }


@api.post("/billing/portal")
async def billing_portal(request: Request, user=Depends(get_current_user)):
    customer_id = user.get("stripe_customer_id")
    if not customer_id:
        raise HTTPException(status_code=400, detail="No Stripe customer for this account")
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    origin = (body or {}).get("origin_url") or os.environ.get("ALERT_APP_URL", "")
    try:
        portal = stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=f"{origin.rstrip('/')}/settings?tab=billing",
        )
    except stripe.error.InvalidRequestError as e:
        raise HTTPException(status_code=500, detail=f"Stripe portal error: {e.user_message or str(e)}")
    return {"portal_url": portal.url}


async def _apply_subscription_from_session(session_obj) -> None:
    """Update DB with subscription info from a completed checkout session."""
    session_id = session_obj["id"] if isinstance(session_obj, dict) else session_obj.id
    metadata = session_obj.get("metadata") if isinstance(session_obj, dict) else (session_obj.metadata or {})
    user_id = (metadata or {}).get("user_id")
    lookup_key = (metadata or {}).get("lookup_key")
    subscription_id = session_obj.get("subscription") if isinstance(session_obj, dict) else session_obj.subscription

    period_end = None
    sub_status = "active"
    cancel_at_period_end = False
    if subscription_id:
        try:
            sub = stripe.Subscription.retrieve(subscription_id)
            sub_status = sub.status  # trialing | active | ...
            cancel_at_period_end = bool(sub.cancel_at_period_end)
            if sub.current_period_end:
                period_end = datetime.fromtimestamp(sub.current_period_end, tz=timezone.utc).isoformat()
        except stripe.error.StripeError:
            pass

    await db.payment_transactions.update_one(
        {"session_id": session_id, "payment_status": {"$ne": "paid"}},
        {"$set": {
            "status": "completed",
            "payment_status": "paid",
            "stripe_subscription_id": subscription_id,
            "updated_at": datetime.now(timezone.utc),
        }},
    )
    if user_id:
        try:
            await db.users.update_one(
                {"_id": ObjectId(user_id)},
                {"$set": {
                    "subscription_status": "active" if sub_status in ("active", "trialing") else sub_status,
                    "subscription_plan_key": lookup_key,
                    "stripe_subscription_id": subscription_id,
                    "subscription_current_period_end": period_end,
                    "subscription_cancel_at_period_end": cancel_at_period_end,
                }},
            )
        except Exception as e:
            logging.error(f"Failed to update user subscription: {e}")


async def _sync_subscription(subscription_obj) -> None:
    """Sync subscription state to user record (used by customer.subscription.* webhooks)."""
    sub_id = subscription_obj.get("id") if isinstance(subscription_obj, dict) else subscription_obj.id
    status = subscription_obj.get("status") if isinstance(subscription_obj, dict) else subscription_obj.status
    cancel = subscription_obj.get("cancel_at_period_end") if isinstance(subscription_obj, dict) else subscription_obj.cancel_at_period_end
    cpe = subscription_obj.get("current_period_end") if isinstance(subscription_obj, dict) else subscription_obj.current_period_end
    period_end = datetime.fromtimestamp(cpe, tz=timezone.utc).isoformat() if cpe else None

    # Map status: trialing/active → active; canceled/incomplete_expired → canceled; past_due → past_due
    if status in ("active", "trialing"):
        mapped = "active"
    elif status in ("canceled", "incomplete_expired", "unpaid"):
        mapped = "canceled"
    else:
        mapped = status

    await db.users.update_one(
        {"stripe_subscription_id": sub_id},
        {"$set": {
            "subscription_status": mapped,
            "subscription_current_period_end": period_end,
            "subscription_cancel_at_period_end": bool(cancel),
        }},
    )


@api.post("/stripe/webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    try:
        event = stripe.Webhook.construct_event(payload, sig, STRIPE_WEBHOOK_SECRET)
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid signature")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Webhook error: {e}")

    t = event["type"]
    obj = event["data"]["object"]

    if t == "checkout.session.completed":
        await _apply_subscription_from_session(obj)
    elif t == "customer.subscription.created":
        await _sync_subscription(obj)
    elif t == "customer.subscription.updated":
        await _sync_subscription(obj)
    elif t == "customer.subscription.deleted":
        await _sync_subscription(obj)
    elif t == "invoice.payment_failed":
        sub_id = obj.get("subscription")
        if sub_id:
            await db.users.update_one(
                {"stripe_subscription_id": sub_id},
                {"$set": {"subscription_status": "past_due"}},
            )
    return {"status": "ok"}

# ---- Commission Alerts (Resend email) ----
EMAIL_BASE_URL = "https://integrations.emergentagent.com"
# Ordered smallest → largest so we pick the tightest still-valid threshold.
# e.g. at 5 days left we want the 7-day notice, not the 60-day notice.
ALERT_BUCKETS = [0, 1, 7, 14, 30, 60]

def _parse_commission_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value[:10]).date()
    except (ValueError, TypeError):
        return None

def _current_alert_bucket(expires_on: date, today: date) -> Optional[int]:
    """Return the smallest bucket (in days) still >= days-remaining, or None."""
    days_left = (expires_on - today).days
    if days_left < 0:
        return -1  # expired-notice bucket
    for b in ALERT_BUCKETS:
        if days_left <= b:
            return b
    return None

def _render_commission_email(notary_name: str, days_left: int, expires_on: date, app_url: str) -> tuple[str, str]:
    if days_left < 0:
        subject = "Notary commission has EXPIRED — action required"
        headline = "Your commission has lapsed"
        body_text = ("Records show your notary commission has expired. "
                     "Please renew immediately before performing any further notarial acts.")
        cta_color = "#DF0D1B"
    elif days_left == 0:
        subject = "Notary commission expires TODAY"
        headline = "Your commission expires today"
        body_text = ("Today is the last day your current notary commission is valid. "
                     "Confirm your renewal is filed and effective before your next appointment.")
        cta_color = "#DF0D1B"
    else:
        subject = f"Notary commission expires in {days_left} days"
        headline = f"{days_left} days left on your commission"
        body_text = (f"Your notary commission is scheduled to expire on "
                     f"{expires_on.strftime('%B %d, %Y')}. Start your renewal now "
                     f"so your journal never goes dark.")
        cta_color = "#00317A"

    html = f"""<!doctype html>
<html><body style="margin:0;padding:0;background:#F4F7F9;font-family:Helvetica,Arial,sans-serif;color:#111827;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#F4F7F9;padding:32px 0;">
  <tr><td align="center">
    <table role="presentation" width="560" cellpadding="0" cellspacing="0" style="background:#ffffff;border:1px solid #E2E8F0;border-radius:4px;overflow:hidden;">
      <tr>
        <td style="background:#00317A;padding:20px 28px;">
          <div style="color:#ffffff;font-family:Georgia,'Times New Roman',serif;font-size:20px;font-weight:700;">Domini Business Services</div>
          <div style="color:rgba(255,255,255,0.7);font-size:10px;letter-spacing:2.5px;text-transform:uppercase;margin-top:2px;">Notarium · Commission Alerts</div>
        </td>
      </tr>
      <tr><td style="background:#DF0D1B;height:3px;line-height:3px;font-size:0;">&nbsp;</td></tr>
      <tr>
        <td style="padding:32px 28px 8px;">
          <div style="font-size:10px;letter-spacing:2.5px;text-transform:uppercase;color:{cta_color};margin-bottom:8px;">Commission alert</div>
          <h1 style="font-family:Georgia,'Times New Roman',serif;font-size:26px;color:#00317A;margin:0 0 12px;">{headline}</h1>
          <p style="font-size:14px;line-height:1.6;color:#4B5563;margin:0 0 18px;">Hi {notary_name or 'there'},</p>
          <p style="font-size:14px;line-height:1.6;color:#111827;margin:0 0 22px;">{body_text}</p>
          <table role="presentation" cellpadding="0" cellspacing="0"><tr><td style="background:{cta_color};border-radius:3px;">
            <a href="{app_url}/settings" style="display:inline-block;padding:12px 22px;color:#ffffff;text-decoration:none;font-size:13px;font-weight:600;letter-spacing:0.5px;">Update commission details</a>
          </td></tr></table>
        </td>
      </tr>
      <tr>
        <td style="padding:22px 28px;border-top:1px solid #E2E8F0;background:#F8FAFC;">
          <div style="font-family:'Courier New',monospace;font-size:11px;color:#4B5563;">
            Commission expires: <span style="color:#111827;">{expires_on.strftime('%B %d, %Y')}</span>
          </div>
          <div style="font-family:'Courier New',monospace;font-size:11px;color:#4B5563;margin-top:4px;">
            Days remaining: <span style="color:#111827;">{days_left}</span>
          </div>
        </td>
      </tr>
      <tr>
        <td style="padding:16px 28px;background:#00317A;color:rgba(255,255,255,0.55);font-size:10px;letter-spacing:2px;text-transform:uppercase;text-align:center;font-family:'Courier New',monospace;">
          A Division of YFI Enterprises, LLC
        </td>
      </tr>
    </table>
    <div style="font-size:11px;color:#94A3B8;margin-top:14px;">
      You are receiving this because commission alerts are enabled on your Notarium account.
    </div>
  </td></tr>
</table></body></html>"""
    return subject, html

async def _send_email(to_email: str, subject: str, html: str,
                      reply_to: Optional[str] = None) -> tuple[Optional[str], Optional[str]]:
    """Return (email_id, error_message). email_id set on success; error_message on failure."""
    key = os.environ.get("EMERGENT_EMAIL_KEY")
    from_name = os.environ.get("EMAIL_FROM_NAME", "Domini Business Services")
    if not key:
        logging.warning("EMERGENT_EMAIL_KEY not configured; skipping email send")
        return None, "Email is not configured on this server."
    payload = {"to": [to_email], "subject": subject, "html": html, "from_name": from_name}
    if reply_to:
        payload["contact_email"] = reply_to
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{EMAIL_BASE_URL}/api/v1/email/send",
                headers={"X-Email-Key": key},
                json=payload,
            )
        resp.raise_for_status()
        return resp.json().get("id"), None
    except httpx.HTTPStatusError as e:
        detail = e.response.text
        try:
            data = e.response.json()
            detail = data.get("message") or data.get("error") or detail
        except Exception:
            pass
        logging.error(f"Email send failed: {e.response.status_code} {detail}")
        return None, detail or f"Email provider returned {e.response.status_code}."
    except Exception as e:
        logging.error(f"Email send error: {e}")
        return None, "Could not reach the email service."

async def _scan_and_send_alerts() -> dict:
    """Iterate users, send alerts for those hitting a new bucket. Returns summary."""
    today = datetime.now(timezone.utc).date()
    app_url = os.environ.get("ALERT_APP_URL", "").rstrip("/")
    sent, skipped, errors = 0, 0, 0
    async for u in db.users.find({}):
        if not u.get("alerts_enabled", True):
            skipped += 1; continue
        expires_on = _parse_commission_date(u.get("commission_expires"))
        if not expires_on:
            skipped += 1; continue
        bucket = _current_alert_bucket(expires_on, today)
        if bucket is None:
            skipped += 1; continue
        if u.get("last_alert_bucket") == bucket:
            skipped += 1; continue  # already alerted for this threshold
        subject, html = _render_commission_email(
            u.get("name", ""), (expires_on - today).days, expires_on, app_url)
        email_id, _ = await _send_email(u["email"], subject, html)
        if email_id is not None:
            await db.users.update_one(
                {"_id": u["_id"]},
                {"$set": {
                    "last_alert_bucket": bucket,
                    "last_alert_sent_at": datetime.now(timezone.utc).isoformat(),
                    "last_alert_email_id": email_id,
                }})
            sent += 1
        else:
            errors += 1
    return {"sent": sent, "skipped": skipped, "errors": errors, "date": today.isoformat()}

@api.post("/cron/commission-alerts")
async def cron_commission_alerts(
    background: BackgroundTasks,
    authorization: Optional[str] = Header(default=None),
    x_webhook_id: Optional[str] = Header(default=None, alias="X-Webhook-Id"),
):
    # Cron endpoints must ack 2xx immediately; enqueue/background the actual work.
    expected = os.environ.get("WEBHOOK_CRON_SECRET", "")
    provided = ""
    if authorization and authorization.startswith("Bearer "):
        provided = authorization[7:]
    if not expected or not hmac.compare_digest(expected, provided):
        raise HTTPException(status_code=401, detail="Unauthorized")
    # Idempotency: skip re-processing same webhook run. Insert-first with a
    # unique index makes duplicate concurrent deliveries safe.
    if x_webhook_id:
        try:
            await db.cron_runs.insert_one({
                "run_id": x_webhook_id,
                "cron": "commission-alerts",
                "received_at": datetime.now(timezone.utc).isoformat(),
            })
        except DuplicateKeyError:
            # Another worker already accepted this delivery.
            return {"accepted": True, "duplicate": True, "run_id": x_webhook_id}
    background.add_task(_scan_and_send_alerts)
    return {"accepted": True, "run_id": x_webhook_id}

@api.post("/alerts/test")
async def send_test_alert(user=Depends(get_current_user)):
    """Send a preview commission alert to the notary immediately."""
    today = datetime.now(timezone.utc).date()
    expires_on = _parse_commission_date(user.get("commission_expires")) or (today + timedelta(days=45))
    days_left = (expires_on - today).days
    app_url = os.environ.get("ALERT_APP_URL", "").rstrip("/")
    subject, html = _render_commission_email(user.get("name", ""), days_left, expires_on, app_url)
    subject = "[Preview] " + subject
    email_id, err = await _send_email(user["email"], subject, html)
    if not email_id:
        raise HTTPException(status_code=400, detail=err or "Could not send email.")
    return {"ok": True, "email_id": email_id, "sent_to": user["email"]}

@api.get("/alerts/status")
async def alerts_status(user=Depends(get_current_user)):
    """Preview: which upcoming buckets remain, and when the next alert is expected."""
    today = datetime.now(timezone.utc).date()
    expires_on = _parse_commission_date(user.get("commission_expires"))
    if not expires_on:
        return {"configured": False, "enabled": user.get("alerts_enabled", True)}
    days_left = (expires_on - today).days
    upcoming = []
    for b in ALERT_BUCKETS:
        alert_date = expires_on - timedelta(days=b)
        if alert_date >= today:
            upcoming.append({"days_out": b, "on": alert_date.isoformat()})
    # ALERT_BUCKETS is ascending for tightest-threshold selection, but users
    # expect the soonest reminder at the top of the list.
    upcoming.sort(key=lambda a: a["days_out"], reverse=True)
    return {
        "configured": True,
        "enabled": user.get("alerts_enabled", True),
        "commission_expires": expires_on.isoformat(),
        "days_left": days_left,
        "last_alert_bucket": user.get("last_alert_bucket"),
        "last_alert_sent_at": user.get("last_alert_sent_at"),
        "upcoming_alerts": upcoming,
    }

# ---- Journal Entries ----
@api.get("/entries")
async def list_entries(
    user=Depends(get_current_user),
    q: Optional[str] = None,
    notarization_type: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: int = 500,
):
    filt = {"user_id": user["_id"]}
    if q:
        filt["$or"] = [
            {"signer_name": {"$regex": q, "$options": "i"}},
            {"document_description": {"$regex": q, "$options": "i"}},
            {"id_number": {"$regex": q, "$options": "i"}},
        ]
    if notarization_type and notarization_type != "all":
        filt["notarization_type"] = notarization_type
    if start_date or end_date:
        rng = {}
        if start_date: rng["$gte"] = start_date
        if end_date: rng["$lte"] = end_date + "T23:59:59"
        filt["entry_date"] = rng
    cursor = db.entries.find(filt).sort("entry_date", -1).limit(limit)
    docs = await cursor.to_list(limit)
    return [serialize_entry(d) for d in docs]

@api.post("/entries")
async def create_entry(payload: EntryIn, user=Depends(require_active_subscription)):
    doc = payload.model_dump()
    doc["user_id"] = user["_id"]
    doc["created_at"] = datetime.now(timezone.utc).isoformat()
    result = await db.entries.insert_one(doc)
    doc["_id"] = result.inserted_id
    # auto-add / update client
    if payload.signer_name:
        existing = await db.clients.find_one(
            {"user_id": user["_id"], "name": payload.signer_name})
        if not existing:
            await db.clients.insert_one({
                "user_id": user["_id"],
                "name": payload.signer_name,
                "address": payload.signer_address,
                "phone": payload.signer_phone,
                "email": payload.signer_email,
                "id_type": payload.id_type,
                "id_number": payload.id_number,
                "notes": "",
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
    return serialize_entry(doc)

@api.get("/entries/{entry_id}")
async def get_entry(entry_id: str, user=Depends(get_current_user)):
    doc = await db.entries.find_one({"_id": ObjectId(entry_id), "user_id": user["_id"]})
    if not doc:
        raise HTTPException(status_code=404, detail="Entry not found")
    return serialize_entry(doc)

@api.put("/entries/{entry_id}")
async def update_entry(entry_id: str, payload: EntryIn, user=Depends(require_active_subscription)):
    updates = payload.model_dump()
    updates["updated_at"] = datetime.now(timezone.utc).isoformat()
    result = await db.entries.update_one(
        {"_id": ObjectId(entry_id), "user_id": user["_id"]},
        {"$set": updates})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Entry not found")
    doc = await db.entries.find_one({"_id": ObjectId(entry_id)})
    return serialize_entry(doc)

@api.delete("/entries/{entry_id}")
async def delete_entry(entry_id: str, user=Depends(require_active_subscription)):
    result = await db.entries.delete_one({"_id": ObjectId(entry_id), "user_id": user["_id"]})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Entry not found")
    return {"ok": True}

# ---- Certificate rendering + helpers ----
def _draw_domini_stamp(canvas, page_w, page_h, doc_page: int):
    """Draw watermark shield + Domini footer (portrait or landscape aware)."""
    canvas.saveState()
    if LOGO_PATH.exists():
        try:
            canvas.setFillAlpha(0.06)
            img = ImageReader(str(LOGO_PATH))
            size = min(page_w, page_h) * 0.55
            canvas.drawImage(img, (page_w - size) / 2, (page_h - size) / 2,
                             width=size, height=size, mask="auto",
                             preserveAspectRatio=True)
            canvas.setFillAlpha(1.0)
            foot = 0.4 * inch
            canvas.drawImage(img, 0.4 * inch, 0.2 * inch,
                             width=foot, height=foot, mask="auto",
                             preserveAspectRatio=True)
        except Exception:
            pass
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.HexColor("#4B5563"))
    canvas.drawString(0.95 * inch, 0.35 * inch,
                      "Domini Business Services  ·  Notary Journal")
    canvas.setFont("Helvetica-Oblique", 7)
    canvas.setFillColor(colors.HexColor("#94A3B8"))
    canvas.drawString(0.95 * inch, 0.22 * inch,
                      "A Division of YFI Enterprises, LLC")
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.HexColor("#4B5563"))
    canvas.drawRightString(page_w - 0.4 * inch, 0.3 * inch, f"Page {doc_page}")
    canvas.setStrokeColor(colors.HexColor("#DF0D1B"))
    canvas.setLineWidth(2)
    canvas.line(0.4 * inch, page_h - 0.28 * inch,
                page_w - 0.4 * inch, page_h - 0.28 * inch)
    canvas.restoreState()

def _render_certificate_pdf(entry: dict, notary: dict) -> bytes:
    """Single-page letter portrait branded certificate for one entry."""
    from reportlab.lib.pagesizes import letter as _letter
    # ReportLab's Paragraph parses a mini-HTML dialect: unescaped < > & from user
    # input silently drops content. Escape everything that flows into Paragraph.
    esc = lambda v: _html.escape(str(v)) if v is not None else ""
    buf = io.BytesIO()
    page_w, page_h = _letter

    def stamp(canvas, _doc):
        _draw_domini_stamp(canvas, page_w, page_h, _doc.page)

    doc = SimpleDocTemplate(buf, pagesize=_letter,
                            rightMargin=0.75 * inch, leftMargin=0.75 * inch,
                            topMargin=0.55 * inch, bottomMargin=0.7 * inch)
    styles = getSampleStyleSheet()
    eyebrow = ParagraphStyle('eyebrow', parent=styles['Normal'], fontSize=8,
                             textColor=colors.HexColor("#DF0D1B"),
                             fontName="Helvetica-Bold", spaceAfter=4, leading=10)
    title_style = ParagraphStyle('title', parent=styles['Title'], fontSize=24,
                                 alignment=TA_LEFT, textColor=colors.HexColor("#00317A"),
                                 fontName="Helvetica-Bold", spaceAfter=6, leading=28)
    body = ParagraphStyle('body', parent=styles['Normal'], fontSize=10.5,
                          textColor=colors.HexColor("#111827"), leading=15,
                          spaceAfter=10)
    label = ParagraphStyle('lbl', parent=styles['Normal'], fontSize=7.5,
                           textColor=colors.HexColor("#4B5563"),
                           fontName="Helvetica-Bold", leading=10)
    value = ParagraphStyle('val', parent=styles['Normal'], fontSize=10.5,
                           textColor=colors.HexColor("#111827"),
                           leading=13)
    mono = ParagraphStyle('mono', parent=value, fontName="Courier")

    date_str = (entry.get("entry_date") or "")[:10]
    cert_no = f"NOT-{str(entry.get('_id', ''))[-8:].upper()}"

    elems = []
    elems.append(Paragraph("CERTIFICATE OF NOTARIAL ACT", eyebrow))
    elems.append(Paragraph(entry.get("notarization_type") or "Notarial Act", title_style))
    # Cert meta bar
    meta = Table(
        [[
            Paragraph(f"<b>Certificate No.</b>", label),
            Paragraph(cert_no, mono),
            Paragraph(f"<b>Date</b>", label),
            Paragraph(date_str, mono),
        ]],
        colWidths=[0.9 * inch, 1.9 * inch, 0.6 * inch, 1.4 * inch],
    )
    meta.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor("#F4F7F9")),
        ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 10),
        ('RIGHTPADDING', (0, 0), (-1, -1), 10),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
    ]))
    elems.append(meta)
    elems.append(Spacer(1, 0.22 * inch))

    doc_desc = entry.get("document_type", "")
    if entry.get("document_description"):
        doc_desc += f" — {entry.get('document_description')}"
    intro = (f"This document certifies that on <b>{esc(date_str)}</b>, "
             f"<b>{esc(notary.get('name', 'the undersigned Notary Public'))}</b> witnessed "
             f"the <b>{esc(entry.get('notarization_type', 'notarial act'))}</b> described below.")
    elems.append(Paragraph(intro, body))

    # Info grid — every value is escaped before entering Paragraph
    def cell(l, v, is_mono=False):
        return [Paragraph(l.upper(), label),
                Paragraph(esc(v) if v not in (None, "") else "—",
                          mono if is_mono else value)]

    info_rows = [
        [cell("Signer Name", entry.get("signer_name", ""))[0],
         cell("Signer Name", entry.get("signer_name", ""))[1],
         cell("Fee Charged", f"${float(entry.get('fee') or 0):.2f}", True)[0],
         cell("Fee Charged", f"${float(entry.get('fee') or 0):.2f}", True)[1]],
        [cell("Signer Address", entry.get("signer_address") or "—")[0],
         cell("Signer Address", entry.get("signer_address") or "—")[1], "", ""],
        [cell("Document Type", entry.get("document_type") or "—")[0],
         cell("Document Type", entry.get("document_type") or "—")[1],
         cell("Description", entry.get("document_description") or "—")[0],
         cell("Description", entry.get("document_description") or "—")[1]],
        [cell("ID Type", entry.get("id_type") or "—")[0],
         cell("ID Type", entry.get("id_type") or "—")[1],
         cell("ID Number", entry.get("id_number") or "—", True)[0],
         cell("ID Number", entry.get("id_number") or "—", True)[1]],
        [cell("ID Issued By", entry.get("id_issued_by") or "—")[0],
         cell("ID Issued By", entry.get("id_issued_by") or "—")[1],
         cell("ID Expiration", entry.get("id_expiration") or "—", True)[0],
         cell("ID Expiration", entry.get("id_expiration") or "—", True)[1]],
    ]
    # Restructure rows: each row = [label1, value1, label2, value2] pairs stacked
    grid_rows = []
    for r in info_rows:
        grid_rows.append([r[0], r[2]])
        grid_rows.append([r[1], r[3]])

    grid = Table(grid_rows, colWidths=[3.4 * inch, 3.4 * inch])
    grid.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
        ('TOPPADDING', (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
        ('LINEBELOW', (0, 1), (-1, 1), 0.25, colors.HexColor("#E2E8F0")),
        ('LINEBELOW', (0, 3), (-1, 3), 0.25, colors.HexColor("#E2E8F0")),
        ('LINEBELOW', (0, 5), (-1, 5), 0.25, colors.HexColor("#E2E8F0")),
        ('LINEBELOW', (0, 7), (-1, 7), 0.25, colors.HexColor("#E2E8F0")),
        ('TOPPADDING', (0, 2), (-1, 2), 8),
        ('TOPPADDING', (0, 4), (-1, 4), 8),
        ('TOPPADDING', (0, 6), (-1, 6), 8),
        ('TOPPADDING', (0, 8), (-1, 8), 8),
    ]))
    elems.append(grid)
    elems.append(Spacer(1, 0.25 * inch))

    # Signature panel
    sig_data = entry.get("signature_data") or ""
    if sig_data and sig_data.startswith("data:image/") and "base64," in sig_data:
        try:
            sig_bytes = base64.b64decode(sig_data.split("base64,", 1)[1])
            sig_img = RLImage(io.BytesIO(sig_bytes), width=3.0 * inch, height=0.95 * inch)
            sig_img.hAlign = 'LEFT'
            sig_row = Table(
                [[Paragraph("SIGNER'S SIGNATURE", label)],
                 [sig_img]],
                colWidths=[6.8 * inch],
            )
            sig_row.setStyle(TableStyle([
                ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#F4F7F9")),
                ('TOPPADDING', (0, 0), (-1, -1), 8),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
                ('LEFTPADDING', (0, 0), (-1, -1), 10),
                ('RIGHTPADDING', (0, 0), (-1, -1), 10),
            ]))
            elems.append(sig_row)
            elems.append(Spacer(1, 0.2 * inch))
        except Exception:
            pass

    # Notary attestation block
    exp = notary.get("commission_expires") or ""
    exp_str = exp[:10] if exp else "—"
    notary_html = (
        f"<b>Notary Public:</b> {esc(notary.get('name', '—'))}<br/>"
        f"<b>Commission No.:</b> {esc(notary.get('commission_number') or '—')} "
        f"&nbsp;&nbsp;·&nbsp;&nbsp; "
        f"<b>State:</b> {esc(notary.get('state') or '—')} "
        f"&nbsp;&nbsp;·&nbsp;&nbsp; "
        f"<b>Commission Expires:</b> {esc(exp_str)}"
    )
    notary_para = ParagraphStyle('nota', parent=value, fontSize=9.5, leading=14,
                                 textColor=colors.HexColor("#111827"))
    notary_bar = Table(
        [[Paragraph("NOTARY ATTESTATION", label)],
         [Paragraph(notary_html, notary_para)]],
        colWidths=[6.8 * inch],
    )
    notary_bar.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 1, colors.HexColor("#00317A")),
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#00317A")),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('LEFTPADDING', (0, 0), (-1, -1), 12),
        ('RIGHTPADDING', (0, 0), (-1, -1), 12),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
    ]))
    # override the header cell padding manually via a stronger override
    notary_bar.setStyle(TableStyle([
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('BOX', (0, 0), (-1, -1), 0.75, colors.HexColor("#00317A")),
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#00317A")),
        ('TOPPADDING', (0, 0), (-1, 0), 6),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 6),
        ('TOPPADDING', (0, 1), (-1, 1), 10),
        ('BOTTOMPADDING', (0, 1), (-1, 1), 12),
        ('LEFTPADDING', (0, 0), (-1, -1), 12),
        ('RIGHTPADDING', (0, 0), (-1, -1), 12),
    ]))
    elems.append(notary_bar)

    # Optional custom notary seal stamped beside the attestation block. Stored
    # as a base64 data URL on the user doc; keep this small and non-fatal so
    # a bad blob never breaks certificate rendering.
    seal_data = notary.get("seal_data") or ""
    if seal_data.startswith("data:image/") and "base64," in seal_data:
        try:
            seal_bytes = base64.b64decode(seal_data.split("base64,", 1)[1])
            seal_img = RLImage(io.BytesIO(seal_bytes), width=1.2*inch, height=1.2*inch)
            seal_row = Table(
                [[Paragraph("OFFICIAL NOTARY SEAL", label), seal_img]],
                colWidths=[3.0*inch, 3.8*inch],
            )
            seal_row.setStyle(TableStyle([
                ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
                ('ALIGN', (1,0), (1,0), 'RIGHT'),
                ('LEFTPADDING', (0,0), (-1,-1), 12),
                ('RIGHTPADDING', (0,0), (-1,-1), 12),
                ('TOPPADDING', (0,0), (-1,-1), 10),
                ('BOTTOMPADDING', (0,0), (-1,-1), 10),
                ('LINEABOVE', (0,0), (-1,0), 0.5, colors.HexColor("#E2E8F0")),
            ]))
            elems.append(seal_row)
        except Exception:
            pass

    doc.build(elems, onFirstPage=stamp, onLaterPages=stamp)
    buf.seek(0)
    return buf.read()

async def _get_entry_certificate_token(entry_id_obj: ObjectId) -> str:
    """Ensure the entry has a certificate_token; return it. Atomic under concurrency."""
    # Conditional update: only set the token if none exists yet. If another
    # caller wrote first, this returns None and we re-read the winning token.
    token = _secrets.token_urlsafe(20)
    updated = await db.entries.find_one_and_update(
        {"_id": entry_id_obj, "certificate_token": {"$exists": False}},
        {"$set": {"certificate_token": token,
                  "certificate_token_created_at": datetime.now(timezone.utc).isoformat()}},
    )
    if updated is not None:
        return token
    # A concurrent writer won; re-read whatever token actually persisted.
    doc = await db.entries.find_one({"_id": entry_id_obj}, {"certificate_token": 1})
    return (doc or {}).get("certificate_token") or token

@api.get("/entries/{entry_id}/certificate.pdf")
async def download_certificate(entry_id: str, user=Depends(get_current_user)):
    doc = await db.entries.find_one({"_id": _to_oid(entry_id), "user_id": user["_id"]})
    if not doc:
        raise HTTPException(status_code=404, detail="Entry not found")
    pdf = _render_certificate_pdf(doc, user)
    return StreamingResponse(
        io.BytesIO(pdf), media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="notary-certificate-{entry_id[-8:]}.pdf"'})

@api.get("/public/certificate/{token}")
async def public_download_certificate(token: str):
    doc = await db.entries.find_one({"certificate_token": token})
    if not doc:
        raise HTTPException(status_code=404, detail="Certificate not found")
    notary = await db.users.find_one({"_id": ObjectId(doc["user_id"])})
    if not notary:
        raise HTTPException(status_code=404, detail="Notary not found")
    pdf = _render_certificate_pdf(doc, notary)
    return StreamingResponse(
        io.BytesIO(pdf), media_type="application/pdf",
        headers={"Content-Disposition":
                 f'inline; filename="notary-certificate-{str(doc["_id"])[-8:]}.pdf"'})

class CertificateEmailIn(BaseModel):
    to: EmailStr
    message: Optional[str] = ""

@api.post("/entries/{entry_id}/email-certificate")
async def email_certificate(entry_id: str, payload: CertificateEmailIn,
                            user=Depends(require_active_subscription)):
    entry_oid = _to_oid(entry_id)
    doc = await db.entries.find_one({"_id": entry_oid, "user_id": user["_id"]})
    if not doc:
        raise HTTPException(status_code=404, detail="Entry not found")
    token = await _get_entry_certificate_token(entry_oid)
    app_url = os.environ.get("ALERT_APP_URL", "").rstrip("/")
    cert_url = f"{app_url}/api/public/certificate/{token}"
    date_str = (doc.get("entry_date") or "")[:10]
    doc_desc = doc.get("document_type", "")
    if doc.get("document_description"):
        doc_desc += f" — {doc.get('document_description')}"
    signer = doc.get("signer_name") or "there"
    custom_note = (payload.message or "").strip()
    # Escape every user- or DB-supplied value before it enters the HTML body.
    esc = _html.escape
    signer_e = esc(signer)
    doc_desc_e = esc(doc_desc) if doc_desc else "the document you presented"
    date_e = esc(date_str)
    notary_name_e = esc(user.get('name', 'Your notary'))
    notary_comm_e = esc(user.get('commission_number') or '—')
    notary_state_e = esc(user.get('state') or '—')
    cert_id_e = esc(str(doc['_id'])[-8:].upper())
    custom_html = (f'<div style="padding:12px 14px;background:#F4F7F9;border-left:3px solid #DF0D1B;'
                   f'font-size:13px;color:#111827;margin:16px 0;white-space:pre-wrap;">'
                   f'{esc(custom_note)}</div>') if custom_note else ""
    subject = f"Your notarization certificate — {date_str}"
    html = f"""<!doctype html>
<html><body style="margin:0;padding:0;background:#F4F7F9;font-family:Helvetica,Arial,sans-serif;color:#111827;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#F4F7F9;padding:32px 0;">
  <tr><td align="center">
    <table role="presentation" width="560" cellpadding="0" cellspacing="0" style="background:#ffffff;border:1px solid #E2E8F0;border-radius:4px;overflow:hidden;">
      <tr>
        <td style="background:#00317A;padding:20px 28px;">
          <div style="color:#ffffff;font-family:Georgia,'Times New Roman',serif;font-size:20px;font-weight:700;">Domini Business Services</div>
          <div style="color:rgba(255,255,255,0.7);font-size:10px;letter-spacing:2.5px;text-transform:uppercase;margin-top:2px;">Notarium · Certificate</div>
        </td>
      </tr>
      <tr><td style="background:#DF0D1B;height:3px;line-height:3px;font-size:0;">&nbsp;</td></tr>
      <tr>
        <td style="padding:32px 28px 8px;">
          <div style="font-size:10px;letter-spacing:2.5px;text-transform:uppercase;color:#DF0D1B;margin-bottom:8px;">Certificate of Notarial Act</div>
          <h1 style="font-family:Georgia,'Times New Roman',serif;font-size:24px;color:#00317A;margin:0 0 12px;">Your notarization is on record</h1>
          <p style="font-size:14px;line-height:1.6;color:#4B5563;margin:0 0 18px;">Hi {signer_e},</p>
          <p style="font-size:14px;line-height:1.6;color:#111827;margin:0 0 8px;">
            {notary_name_e} has completed the notarial act on <b>{date_e}</b>
            for <b>{doc_desc_e}</b>.
          </p>
          <p style="font-size:14px;line-height:1.6;color:#111827;margin:0 0 22px;">
            Your personal, single-page branded certificate is ready to view and save below.
          </p>
          {custom_html}
          <table role="presentation" cellpadding="0" cellspacing="0"><tr><td style="background:#00317A;border-radius:3px;">
            <a href="{cert_url}" style="display:inline-block;padding:12px 22px;color:#ffffff;text-decoration:none;font-size:13px;font-weight:600;letter-spacing:0.5px;">Download your certificate (PDF)</a>
          </td></tr></table>
          <p style="font-size:11px;color:#94A3B8;margin:18px 0 0;">Certificate link: <a href="{cert_url}" style="color:#00317A;">{cert_url}</a></p>
        </td>
      </tr>
      <tr>
        <td style="padding:20px 28px;border-top:1px solid #E2E8F0;background:#F8FAFC;font-family:'Courier New',monospace;font-size:11px;color:#4B5563;">
          Certificate No. <span style="color:#111827;">NOT-{cert_id_e}</span><br/>
          Notary: <span style="color:#111827;">{notary_name_e}</span> ·
          Commission: <span style="color:#111827;">{notary_comm_e}</span> ·
          {notary_state_e}
        </td>
      </tr>
      <tr>
        <td style="padding:16px 28px;background:#00317A;color:rgba(255,255,255,0.55);font-size:10px;letter-spacing:2px;text-transform:uppercase;text-align:center;font-family:'Courier New',monospace;">
          A Division of YFI Enterprises, LLC
        </td>
      </tr>
    </table>
  </td></tr>
</table></body></html>"""
    email_id, err = await _send_email(payload.to, subject, html, reply_to=user["email"])
    # Persist every attempt to the audit trail — successes AND failures.
    log_entry = {
        "user_id": user["_id"],
        "entry_id": str(entry_oid),
        "to": payload.to,
        "subject": subject,
        "email_id": email_id,
        "error": err,
        "status": "sent" if email_id else "failed",
        "sent_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.certificate_email_log.insert_one(log_entry)
    if not email_id:
        raise HTTPException(status_code=400, detail=err or "Could not send certificate email.")
    await db.entries.update_one(
        {"_id": entry_oid},
        {"$set": {
            "certificate_emailed_to": payload.to,
            "certificate_emailed_at": datetime.now(timezone.utc).isoformat(),
            "certificate_email_id": email_id,
        }})
    return {"ok": True, "email_id": email_id, "sent_to": payload.to,
            "certificate_url": cert_url}

@api.get("/entries/{entry_id}/email-history")
async def entry_email_history(entry_id: str, user=Depends(get_current_user)):
    entry_oid = _to_oid(entry_id)
    # Make sure the entry belongs to this notary before exposing history.
    entry = await db.entries.find_one({"_id": entry_oid, "user_id": user["_id"]},
                                      {"_id": 1})
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    docs = await db.certificate_email_log.find(
        {"user_id": user["_id"], "entry_id": str(entry_oid)}
    ).sort("sent_at", -1).to_list(200)
    return [{
        "id": str(d["_id"]),
        "to": d.get("to"),
        "subject": d.get("subject"),
        "status": d.get("status"),
        "email_id": d.get("email_id"),
        "error": d.get("error"),
        "sent_at": d.get("sent_at"),
    } for d in docs]

# ---- Renewal Checklist ----
# Ten shared steps applicable to any US notary renewal. Descriptions reference
# the notary's own state at render-time. Items unlock once the notary hits the
# 30-day (or tighter) alert bucket so the reminder feels earned, not nagged.
RENEWAL_CHECKLIST_ITEMS = [
    {"id": "window",       "title": "Confirm your renewal window",
     "detail": "Most Secretary of State offices allow renewals up to 60 days before your commission expires."},
    {"id": "education",    "title": "Complete required continuing education or refresher course",
     "detail": "Check your state's mandated CE hours and preferred provider list."},
    {"id": "exam",         "title": "Pass the state-mandated notary exam (if applicable)",
     "detail": "Some states require a proctored exam on every renewal."},
    {"id": "bond",         "title": "Order or renew your notary bond",
     "detail": "Bond amounts vary by state — verify current requirements."},
    {"id": "insurance",    "title": "Renew your Errors & Omissions (E&O) insurance",
     "detail": "Not required by every state but strongly recommended."},
    {"id": "application",  "title": "Submit renewal application and fee to your Secretary of State",
     "detail": "Include background check documents where required."},
    {"id": "certificate",  "title": "Receive your new commission certificate and file it with your county clerk (if required)",
     "detail": "Filing rules vary by county — verify locally."},
    {"id": "seal",         "title": "Order a new notary seal / stamp with the new commission dates",
     "detail": "Do not use your old seal after the new commission takes effect."},
    {"id": "journal",      "title": "Note the new commission expiration in your paper journal",
     "detail": "Some states require carrying over case numbers to a fresh journal."},
    {"id": "notarium",     "title": "Update Notarium with the new commission number and expiration",
     "detail": "This keeps your PDF exports and certificates accurate."},
]
_CHECKLIST_IDS = {i["id"] for i in RENEWAL_CHECKLIST_ITEMS}

# Per-state overlays. Keys are matched case-insensitively against user.state
# and override title/detail on existing items only — item ids don't change,
# so completion state stays comparable across state moves.
STATE_CHECKLIST_OVERRIDES = {
    "california": {
        "education": {
            "title": "Complete the 3-hour California-approved renewal course",
            "detail": "California requires 3 hours of state-approved continuing education for every renewal.",
        },
        "exam": {
            "title": "Pass the California notary exam (mandatory every renewal)",
            "detail": "California requires a proctored 45-question exam every renewal cycle.",
        },
        "bond": {
            "title": "Obtain a $15,000 California notary bond",
            "detail": "Required by California Government Code §8212 for the 4-year commission.",
        },
        "application": {
            "title": "Submit form NP-30 with Live Scan fingerprints and $20 fee to CA Secretary of State",
            "detail": "Include the required Live Scan fingerprint clearance receipt.",
        },
        "certificate": {
            "title": "File your oath of office with your county clerk within 30 days",
            "detail": "California Government Code §8213 — miss the window and your commission is void.",
        },
    },
    "texas": {
        "education": {
            "title": "Continuing education is optional in Texas",
            "detail": "Texas does not require CE for renewal, but a refresher is smart.",
        },
        "exam": {
            "title": "No state exam required in Texas",
            "detail": "Texas does not require a notary exam for renewal.",
        },
        "bond": {
            "title": "Obtain a $10,000 Texas notary bond",
            "detail": "Required for the 4-year Texas notary commission.",
        },
        "application": {
            "title": "Submit renewal Form 2301 to the Texas Secretary of State",
            "detail": "$21 filing fee; term is four years.",
        },
        "certificate": {
            "title": "Receive your commission directly from the Texas Secretary of State",
            "detail": "No county clerk filing required in Texas.",
        },
    },
    "florida": {
        "education": {
            "title": "3-hour education course (first-time commissions only in Florida)",
            "detail": "Florida requires the 3-hour course only for first-time notaries, not renewals.",
        },
        "exam": {
            "title": "No state exam required in Florida",
            "detail": "Florida does not require an exam for renewal.",
        },
        "bond": {
            "title": "Obtain a $7,500 Florida notary bond",
            "detail": "Required by Florida Statute §117.01(7).",
        },
        "application": {
            "title": "Submit renewal application through a bonding agency to the Florida Governor's office",
            "detail": "Florida notaries are appointed by the Governor via a bonding agency.",
        },
        "certificate": {
            "title": "Receive your new commission certificate from the Governor",
            "detail": "No county clerk filing required in Florida.",
        },
    },
}

def _state_key(state: Optional[str]) -> Optional[str]:
    if not state:
        return None
    s = state.strip().lower()
    abbrev = {"ca": "california", "tx": "texas", "fl": "florida"}
    return abbrev.get(s, s if s in STATE_CHECKLIST_OVERRIDES else None)

def _renewal_unlocked(user: dict) -> tuple[bool, Optional[int]]:
    """Unlock once notary reaches the 30-day (or tighter) alert bucket."""
    expires_on = _parse_commission_date(user.get("commission_expires"))
    if not expires_on:
        return False, None
    days_left = (expires_on - datetime.now(timezone.utc).date()).days
    return (days_left <= 30), days_left

@api.get("/renewal-checklist")
async def renewal_checklist(user=Depends(get_current_user)):
    unlocked, days_left = _renewal_unlocked(user)
    completed = set(user.get("renewal_checklist_progress", []))
    overrides = STATE_CHECKLIST_OVERRIDES.get(_state_key(user.get("state")), {})
    items = []
    for it in RENEWAL_CHECKLIST_ITEMS:
        merged = {**it, **overrides.get(it["id"], {})}
        items.append({
            "id": merged["id"],
            "title": merged["title"],
            "detail": merged["detail"],
            "completed": merged["id"] in completed,
            "state_specific": merged["id"] in overrides,
        })
    return {
        "unlocked": unlocked,
        "days_left": days_left,
        "state": user.get("state") or "your state",
        "state_customized": bool(overrides),
        "commission_expires": user.get("commission_expires"),
        "items": items,
        "completed_count": len([i for i in items if i["completed"]]),
        "total_count": len(items),
    }

class ChecklistToggleIn(BaseModel):
    item_id: str
    done: bool

@api.post("/renewal-checklist/toggle")
async def toggle_renewal_item(payload: ChecklistToggleIn, user=Depends(get_current_user)):
    if payload.item_id not in _CHECKLIST_IDS:
        raise HTTPException(status_code=400, detail="Unknown checklist item")
    op = "$addToSet" if payload.done else "$pull"
    await db.users.update_one(
        {"_id": ObjectId(user["_id"])},
        {op: {"renewal_checklist_progress": payload.item_id}})
    return {"ok": True}

# ---- Intake QR poster ----
def _make_qr_png(url: str, box_size: int = 12, border: int = 2) -> bytes:
    """Return PNG bytes of a QR code encoding url. High error correction so a
    printed poster can lose some ink and still scan."""
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=box_size, border=border,
    )
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#00317A", back_color="white",
                        image_factory=PilImage)
    buf = io.BytesIO()
    img.get_image().save(buf, format="PNG")
    return buf.getvalue()

def _render_intake_poster_pdf(intake: dict, notary: dict, url: str,
                              headline_override: Optional[str] = None,
                              note_override: Optional[str] = None) -> bytes:
    from reportlab.lib.pagesizes import letter as _letter
    esc = _html.escape
    page_w, page_h = _letter
    buf = io.BytesIO()

    def stamp(canvas, _doc):
        _draw_domini_stamp(canvas, page_w, page_h, _doc.page)

    doc = SimpleDocTemplate(buf, pagesize=_letter,
                            rightMargin=0.75*inch, leftMargin=0.75*inch,
                            topMargin=0.6*inch, bottomMargin=0.75*inch)
    styles = getSampleStyleSheet()
    eyebrow = ParagraphStyle('e', parent=styles['Normal'], fontSize=9,
                             textColor=colors.HexColor("#DF0D1B"),
                             fontName="Helvetica-Bold", spaceAfter=6, leading=11,
                             alignment=TA_CENTER)
    headline = ParagraphStyle('h', parent=styles['Title'], fontSize=34,
                              textColor=colors.HexColor("#00317A"),
                              fontName="Helvetica-Bold", alignment=TA_CENTER,
                              leading=38, spaceAfter=10)
    sub = ParagraphStyle('s', parent=styles['Normal'], fontSize=14,
                         textColor=colors.HexColor("#111827"),
                         alignment=TA_CENTER, leading=20, spaceAfter=20)
    step = ParagraphStyle('st', parent=styles['Normal'], fontSize=12,
                          textColor=colors.HexColor("#111827"), leading=17,
                          spaceAfter=6, alignment=TA_LEFT)
    url_style = ParagraphStyle('u', parent=styles['Normal'], fontSize=9,
                               textColor=colors.HexColor("#4B5563"),
                               alignment=TA_CENTER, fontName="Courier")

    # Custom headline / note override the defaults so notaries can tailor a
    # printed poster to a specific appointment (e.g. "Smith Closing – Tue 3pm").
    hl_text = (headline_override or "Scan to complete your details").strip() or "Scan to complete your details"
    subtitle = (f"Before we meet, please share your name, address and ID with "
                f"{esc(notary.get('name','your notary'))}.")

    elems = []
    elems.append(Paragraph("SIGNER INTAKE", eyebrow))
    elems.append(Paragraph(esc(hl_text), headline))
    elems.append(Paragraph(subtitle, sub))

    # QR image inside a bordered card
    qr_bytes = _make_qr_png(url, box_size=14, border=2)
    qr_img = RLImage(io.BytesIO(qr_bytes), width=3.8*inch, height=3.8*inch)
    qr_card = Table([[qr_img]], colWidths=[6.5*inch])
    qr_card.setStyle(TableStyle([
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('BOX', (0,0), (-1,-1), 1, colors.HexColor("#00317A")),
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor("#F4F7F9")),
        ('TOPPADDING', (0,0), (-1,-1), 22),
        ('BOTTOMPADDING', (0,0), (-1,-1), 22),
    ]))
    elems.append(qr_card)
    elems.append(Spacer(1, 0.15*inch))
    elems.append(Paragraph(f"Or open: {esc(url)}", url_style))
    elems.append(Spacer(1, 0.25*inch))

    # Steps
    steps_bar = Table(
        [[Paragraph("HOW IT WORKS", ParagraphStyle('lbl', parent=styles['Normal'],
                                                    fontSize=8.5, textColor=colors.white,
                                                    fontName="Helvetica-Bold",
                                                    alignment=TA_LEFT, leading=11))]],
        colWidths=[6.5*inch],
    )
    steps_bar.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor("#00317A")),
        ('LEFTPADDING', (0,0), (-1,-1), 12),
        ('TOPPADDING', (0,0), (-1,-1), 6),
        ('BOTTOMPADDING', (0,0), (-1,-1), 6),
    ]))
    elems.append(steps_bar)
    steps_html = (
        '1. &nbsp; Point your phone camera at the QR code above.<br/>'
        '2. &nbsp; Fill in your legal name, address and ID details.<br/>'
        '3. &nbsp; Tap "Send to my Notary" — you\'re done. Bring your ID to the appointment.'
    )
    steps_table = Table(
        [[Paragraph(steps_html, step)]],
        colWidths=[6.5*inch],
    )
    steps_table.setStyle(TableStyle([
        ('BOX', (0,0), (-1,-1), 0.5, colors.HexColor("#E2E8F0")),
        ('TOPPADDING', (0,0), (-1,-1), 14),
        ('BOTTOMPADDING', (0,0), (-1,-1), 14),
        ('LEFTPADDING', (0,0), (-1,-1), 16),
    ]))
    elems.append(steps_table)
    # Prefer the custom appointment note passed via query param; fall back to
    # the note the notary saved when they created the intake link.
    poster_note = (note_override.strip() if note_override else "") or intake.get("appointment_note", "")
    if poster_note:
        elems.append(Spacer(1, 0.2*inch))
        note = Table(
            [[Paragraph(f"<b>Note:</b> {esc(poster_note)}", step)]],
            colWidths=[6.5*inch],
        )
        note.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,-1), colors.HexColor("#FEF9EC")),
            ('BOX', (0,0), (-1,-1), 0.5, colors.HexColor("#F0D28A")),
            ('TOPPADDING', (0,0), (-1,-1), 12),
            ('BOTTOMPADDING', (0,0), (-1,-1), 12),
            ('LEFTPADDING', (0,0), (-1,-1), 14),
            ('RIGHTPADDING', (0,0), (-1,-1), 14),
        ]))
        elems.append(note)

    doc.build(elems, onFirstPage=stamp, onLaterPages=stamp)
    buf.seek(0)
    return buf.read()

@api.get("/intakes/{intake_id}/qr-poster.pdf")
async def intake_qr_poster(intake_id: str,
                           headline: Optional[str] = None,
                           note: Optional[str] = None,
                           user=Depends(get_current_user)):
    intake = await db.signer_intakes.find_one(
        {"_id": _to_oid(intake_id), "user_id": user["_id"]})
    if not intake:
        raise HTTPException(status_code=404, detail="Intake not found")
    app_url = os.environ.get("ALERT_APP_URL", "").rstrip("/")
    public_url = f"{app_url}/intake/{intake['token']}"
    pdf = _render_intake_poster_pdf(
        intake, user, public_url,
        headline_override=headline, note_override=note,
    )
    return StreamingResponse(
        io.BytesIO(pdf), media_type="application/pdf",
        headers={"Content-Disposition":
                 f'attachment; filename="intake-poster-{intake_id[-8:]}.pdf"'})

@api.get("/intakes/{intake_id}/qr.png")
async def intake_qr_png(intake_id: str, user=Depends(get_current_user)):
    intake = await db.signer_intakes.find_one(
        {"_id": _to_oid(intake_id), "user_id": user["_id"]})
    if not intake:
        raise HTTPException(status_code=404, detail="Intake not found")
    app_url = os.environ.get("ALERT_APP_URL", "").rstrip("/")
    public_url = f"{app_url}/intake/{intake['token']}"
    png = _make_qr_png(public_url)
    return StreamingResponse(io.BytesIO(png), media_type="image/png")

# ---- Dashboard stats ----
@api.get("/stats/dashboard")
async def dashboard_stats(user=Depends(get_current_user)):
    now = datetime.now(timezone.utc)
    start_of_month = datetime(now.year, now.month, 1, tzinfo=timezone.utc).isoformat()

    total_entries = await db.entries.count_documents({"user_id": user["_id"]})
    month_entries_cursor = db.entries.find(
        {"user_id": user["_id"], "entry_date": {"$gte": start_of_month}})
    month_docs = await month_entries_cursor.to_list(10000)
    month_revenue = sum(float(d.get("fee") or 0) for d in month_docs)
    total_revenue_cursor = db.entries.find({"user_id": user["_id"]})
    all_docs = await total_revenue_cursor.to_list(100000)
    total_revenue = sum(float(d.get("fee") or 0) for d in all_docs)

    # Monthly time series (last 6 months)
    months = []
    for i in range(5, -1, -1):
        year = now.year
        month = now.month - i
        while month <= 0:
            month += 12
            year -= 1
        m_start = datetime(year, month, 1, tzinfo=timezone.utc)
        if month == 12:
            m_end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
        else:
            m_end = datetime(year, month + 1, 1, tzinfo=timezone.utc)
        docs_m = [d for d in all_docs
                  if d.get("entry_date") and m_start.isoformat() <= d["entry_date"] < m_end.isoformat()]
        rev = sum(float(d.get("fee") or 0) for d in docs_m)
        months.append({
            "month": m_start.strftime("%b %Y"),
            "revenue": round(rev, 2),
            "count": len(docs_m),
        })

    # Notarization type breakdown
    type_counts = {}
    for d in all_docs:
        t = d.get("notarization_type", "Unknown")
        type_counts[t] = type_counts.get(t, 0) + 1
    type_breakdown = [{"type": k, "count": v} for k, v in type_counts.items()]

    total_clients = await db.clients.count_documents({"user_id": user["_id"]})

    # ---- Delivery stats (last 30 days of certificate emails) ----
    month_start = (now - timedelta(days=30)).isoformat()
    email_docs = await db.certificate_email_log.find(
        {"user_id": user["_id"], "sent_at": {"$gte": month_start}}
    ).to_list(5000)
    sent_count = sum(1 for e in email_docs if e.get("status") == "sent")
    failed_count = sum(1 for e in email_docs if e.get("status") == "failed")
    # Top signers this month — join to entries so we surface the signer name,
    # not just their email address.
    signer_totals: dict = {}
    for e in email_docs:
        if e.get("status") != "sent":
            continue
        eid = e.get("entry_id")
        if not eid:
            continue
        signer_totals[eid] = signer_totals.get(eid, 0) + 1
    top_signers = []
    if signer_totals:
        ordered_ids = sorted(signer_totals, key=signer_totals.get, reverse=True)[:5]
        oid_list = [ObjectId(i) for i in ordered_ids if len(i) == 24]
        entries_by_id = {}
        async for ent in db.entries.find({"_id": {"$in": oid_list},
                                          "user_id": user["_id"]}):
            entries_by_id[str(ent["_id"])] = ent.get("signer_name") or "—"
        for eid in ordered_ids:
            top_signers.append({
                "name": entries_by_id.get(eid, "—"),
                "count": signer_totals[eid],
            })

    return {
        "total_entries": total_entries,
        "month_entries": len(month_docs),
        "month_revenue": round(month_revenue, 2),
        "total_revenue": round(total_revenue, 2),
        "total_clients": total_clients,
        "monthly_series": months,
        "type_breakdown": type_breakdown,
        "delivery": {
            "sent": sent_count,
            "failed": failed_count,
            "total": len(email_docs),
            "top_signers": top_signers,
        },
    }

# ---- Account customization: password + seal ----
class PasswordChangeIn(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)

@api.post("/auth/change-password")
async def change_password(payload: PasswordChangeIn, user=Depends(get_current_user)):
    # Re-fetch the raw doc so we still have the bcrypt hash (get_current_user strips it).
    doc = await db.users.find_one({"_id": ObjectId(user["_id"])})
    if not doc or not verify_password(payload.current_password, doc["password_hash"]):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    await db.users.update_one(
        {"_id": ObjectId(user["_id"])},
        {"$set": {"password_hash": hash_password(payload.new_password)}})
    return {"ok": True}

class SealIn(BaseModel):
    seal_data: str  # data:image/png;base64,... — kept small (< 500KB)

@api.put("/auth/seal")
async def upload_seal(payload: SealIn, user=Depends(get_current_user)):
    if not payload.seal_data.startswith("data:image/"):
        raise HTTPException(status_code=400, detail="Seal must be a PNG or JPEG image (data URL).")
    if len(payload.seal_data) > 700_000:  # ~500KB binary after base64 overhead
        raise HTTPException(status_code=400, detail="Seal image is too large. Try under 500 KB.")
    await db.users.update_one(
        {"_id": ObjectId(user["_id"])},
        {"$set": {"seal_data": payload.seal_data,
                  "seal_updated_at": datetime.now(timezone.utc).isoformat()}})
    return {"ok": True}

@api.delete("/auth/seal")
async def delete_seal(user=Depends(get_current_user)):
    await db.users.update_one(
        {"_id": ObjectId(user["_id"])},
        {"$unset": {"seal_data": "", "seal_updated_at": ""}})
    return {"ok": True}

@api.get("/auth/seal")
async def get_seal(user=Depends(get_current_user)):
    doc = await db.users.find_one({"_id": ObjectId(user["_id"])},
                                  {"seal_data": 1, "seal_updated_at": 1})
    return {
        "has_seal": bool(doc and doc.get("seal_data")),
        "seal_data": (doc or {}).get("seal_data"),
        "updated_at": (doc or {}).get("seal_updated_at"),
    }

# ---- Clients ----
@api.get("/clients")
async def list_clients(q: Optional[str] = None, user=Depends(get_current_user)):
    filt = {"user_id": user["_id"]}
    if q:
        filt["$or"] = [
            {"name": {"$regex": q, "$options": "i"}},
            {"phone": {"$regex": q, "$options": "i"}},
            {"email": {"$regex": q, "$options": "i"}},
        ]
    docs = await db.clients.find(filt).sort("name", 1).to_list(2000)
    return [serialize_generic(d) for d in docs]

@api.post("/clients")
async def create_client(payload: ClientIn, user=Depends(require_active_subscription)):
    doc = payload.model_dump()
    doc["user_id"] = user["_id"]
    doc["created_at"] = datetime.now(timezone.utc).isoformat()
    result = await db.clients.insert_one(doc)
    doc["_id"] = result.inserted_id
    return serialize_generic(doc)

@api.put("/clients/{client_id}")
async def update_client(client_id: str, payload: ClientIn, user=Depends(require_active_subscription)):
    result = await db.clients.update_one(
        {"_id": ObjectId(client_id), "user_id": user["_id"]},
        {"$set": payload.model_dump()})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Client not found")
    doc = await db.clients.find_one({"_id": ObjectId(client_id)})
    return serialize_generic(doc)

@api.delete("/clients/{client_id}")
async def delete_client(client_id: str, user=Depends(require_active_subscription)):
    result = await db.clients.delete_one({"_id": ObjectId(client_id), "user_id": user["_id"]})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Client not found")
    return {"ok": True}

# ---- Document Types ----
DEFAULT_DOC_TYPES = [
    {"name": "Power of Attorney", "description": "General or durable POA"},
    {"name": "Affidavit", "description": "Sworn written statement"},
    {"name": "Deed", "description": "Real estate deed"},
    {"name": "Loan Document", "description": "Mortgage/loan closing docs"},
    {"name": "Vehicle Title", "description": "Auto title transfer"},
    {"name": "Passport Application", "description": "Passport DS-11 form"},
    {"name": "Trust Document", "description": "Living trust or amendment"},
    {"name": "Living Will", "description": "Advance directive"},
]

@api.get("/document-types")
async def list_doc_types(user=Depends(get_current_user)):
    docs = await db.document_types.find({"user_id": user["_id"]}).sort("name", 1).to_list(500)
    return [serialize_generic(d) for d in docs]

@api.post("/document-types")
async def create_doc_type(payload: DocTypeIn, user=Depends(require_active_subscription)):
    doc = payload.model_dump()
    doc["user_id"] = user["_id"]
    doc["created_at"] = datetime.now(timezone.utc).isoformat()
    result = await db.document_types.insert_one(doc)
    doc["_id"] = result.inserted_id
    return serialize_generic(doc)

@api.delete("/document-types/{type_id}")
async def delete_doc_type(type_id: str, user=Depends(require_active_subscription)):
    result = await db.document_types.delete_one({"_id": ObjectId(type_id), "user_id": user["_id"]})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Type not found")
    return {"ok": True}

# ---- Signer Intakes (pre-fill portal) ----
import secrets as _secrets
from bson.errors import InvalidId

def _to_oid(value: str) -> ObjectId:
    """Validate an ObjectId string and raise 400 if malformed."""
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        raise HTTPException(status_code=400, detail="Invalid identifier")

def _serialize_intake(doc: dict, notary_name: str = "") -> dict:
    d = dict(doc)
    d["id"] = str(d.pop("_id"))
    d.pop("user_id", None)
    if notary_name:
        d["notary_name"] = notary_name
    return d

@api.get("/intakes")
async def list_intakes(user=Depends(get_current_user)):
    docs = await db.signer_intakes.find({"user_id": user["_id"]}).sort("created_at", -1).to_list(500)
    now = datetime.now(timezone.utc).isoformat()
    for d in docs:
        if d.get("status") == "pending" and d.get("expires_at") and d["expires_at"] < now:
            d["status"] = "expired"
    return [_serialize_intake(d, user.get("name", "")) for d in docs]

@api.post("/intakes")
async def create_intake(payload: IntakeCreateIn, user=Depends(require_active_subscription)):
    token = _secrets.token_urlsafe(18)
    now = datetime.now(timezone.utc)
    expires = now + timedelta(hours=max(1, min(payload.expires_hours, 24 * 30)))
    doc = {
        "user_id": user["_id"],
        "token": token,
        "label": payload.label or "",
        "appointment_note": payload.appointment_note or "",
        "status": "pending",   # pending | completed | used | expired
        "data": None,
        "created_at": now.isoformat(),
        "expires_at": expires.isoformat(),
        "submitted_at": None,
    }
    result = await db.signer_intakes.insert_one(doc)
    doc["_id"] = result.inserted_id
    return _serialize_intake(doc, user.get("name", ""))

@api.delete("/intakes/{intake_id}")
async def delete_intake(intake_id: str, user=Depends(require_active_subscription)):
    result = await db.signer_intakes.delete_one({"_id": _to_oid(intake_id), "user_id": user["_id"]})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Intake not found")
    return {"ok": True}

@api.post("/intakes/{intake_id}/mark-used")
async def mark_intake_used(intake_id: str, user=Depends(get_current_user)):
    result = await db.signer_intakes.update_one(
        {"_id": _to_oid(intake_id), "user_id": user["_id"]},
        {"$set": {"status": "used", "used_at": datetime.now(timezone.utc).isoformat()}})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Intake not found")
    return {"ok": True}

# --- Public routes (no auth) ---
@api.get("/public/intake/{token}")
async def public_get_intake(token: str):
    doc = await db.signer_intakes.find_one({"token": token})
    if not doc:
        raise HTTPException(status_code=404, detail="Link not found")
    now = datetime.now(timezone.utc).isoformat()
    if doc.get("expires_at") and doc["expires_at"] < now and doc.get("status") == "pending":
        return {"status": "expired"}
    notary = await db.users.find_one({"_id": ObjectId(doc["user_id"])})
    # Never leak submitted PII back through the public endpoint —
    # only surface status + display fields.
    return {
        "status": doc.get("status", "pending"),
        "label": doc.get("label", ""),
        "appointment_note": doc.get("appointment_note", ""),
        "notary_name": notary.get("name") if notary else "",
        "commission_number": notary.get("commission_number") if notary else "",
        "state": notary.get("state") if notary else "",
        "submitted_at": doc.get("submitted_at"),
    }

@api.post("/public/intake/{token}")
async def public_submit_intake(token: str, payload: IntakeSubmitIn):
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    # Atomic: only submit if still pending and not expired — prevents
    # double-submit races and overwriting completed data.
    updated = await db.signer_intakes.find_one_and_update(
        {
            "token": token,
            "status": "pending",
            "expires_at": {"$gt": now_iso},
        },
        {"$set": {
            "data": payload.model_dump(),
            "status": "completed",
            "submitted_at": now_iso,
        }},
    )
    if updated:
        return {"ok": True}
    # Diagnose why the update didn't apply so the signer gets a useful message.
    doc = await db.signer_intakes.find_one({"token": token})
    if not doc:
        raise HTTPException(status_code=404, detail="Link not found")
    if doc.get("status") in ("completed", "used"):
        raise HTTPException(status_code=400, detail="This intake has already been submitted.")
    raise HTTPException(status_code=400, detail="This link has expired. Ask your notary for a new one.")

# ---- PDF Export ----
@api.get("/entries/export/pdf")
async def export_pdf(
    user=Depends(get_current_user),
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
):
    filt = {"user_id": user["_id"]}
    if start_date or end_date:
        rng = {}
        if start_date: rng["$gte"] = start_date
        if end_date: rng["$lte"] = end_date + "T23:59:59"
        filt["entry_date"] = rng
    docs = await db.entries.find(filt).sort("entry_date", 1).to_list(10000)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(letter),
                            rightMargin=0.4*inch, leftMargin=0.4*inch,
                            topMargin=0.4*inch, bottomMargin=0.4*inch)

    def stamp(canvas, _doc):
        canvas.saveState()
        page_w, page_h = landscape(letter)
        # Center watermark shield
        if LOGO_PATH.exists():
            try:
                canvas.setFillAlpha(0.06)
                img = ImageReader(str(LOGO_PATH))
                size = 4.5 * inch
                canvas.drawImage(img, (page_w - size) / 2, (page_h - size) / 2,
                                 width=size, height=size, mask="auto",
                                 preserveAspectRatio=True)
                canvas.setFillAlpha(1.0)
                # Small footer logo (opaque)
                foot = 0.4 * inch
                canvas.drawImage(img, 0.4 * inch, 0.2 * inch,
                                 width=foot, height=foot, mask="auto",
                                 preserveAspectRatio=True)
            except Exception:
                pass
        # Footer text
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#4B5563"))
        canvas.drawString(0.95 * inch, 0.35 * inch,
                          "Domini Business Services  ·  Notary Journal")
        canvas.setFont("Helvetica-Oblique", 7)
        canvas.setFillColor(colors.HexColor("#94A3B8"))
        canvas.drawString(0.95 * inch, 0.22 * inch,
                          "A Division of YFI Enterprises, LLC")
        # Page number
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#4B5563"))
        canvas.drawRightString(page_w - 0.4 * inch, 0.3 * inch,
                               f"Page {_doc.page}")
        # Top red rule
        canvas.setStrokeColor(colors.HexColor("#DF0D1B"))
        canvas.setLineWidth(2)
        canvas.line(0.4 * inch, page_h - 0.28 * inch,
                    page_w - 0.4 * inch, page_h - 0.28 * inch)
        canvas.restoreState()
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle('t', parent=styles['Title'], fontSize=18,
                                 alignment=TA_CENTER, textColor=colors.HexColor("#00317A"))
    header_style = ParagraphStyle('h', parent=styles['Normal'], fontSize=9,
                                  textColor=colors.HexColor("#4B5563"), alignment=TA_CENTER)
    elems = []
    elems.append(Paragraph("Notary Public Journal", title_style))
    subtitle = f"{user['name']}"
    if user.get("commission_number"):
        subtitle += f" &nbsp;&nbsp;|&nbsp;&nbsp; Commission #: {user['commission_number']}"
    if user.get("state"):
        subtitle += f" &nbsp;&nbsp;|&nbsp;&nbsp; State: {user['state']}"
    elems.append(Paragraph(subtitle, header_style))
    period = ""
    if start_date or end_date:
        period = f"Period: {start_date or 'Beginning'} to {end_date or 'Present'}"
    else:
        period = f"All Entries (Generated: {datetime.now().strftime('%Y-%m-%d')})"
    elems.append(Paragraph(period, header_style))
    elems.append(Spacer(1, 0.2*inch))

    data = [["#", "Date", "Type", "Signer", "Document", "ID Type / #", "Fee"]]
    for i, d in enumerate(docs, 1):
        date_str = d.get("entry_date", "")[:10] if d.get("entry_date") else ""
        row = [
            str(i), date_str,
            d.get("notarization_type", ""),
            d.get("signer_name", ""),
            (d.get("document_type", "") + (" - " + d.get("document_description", "") if d.get("document_description") else ""))[:60],
            f"{d.get('id_type','')} / {d.get('id_number','')}",
            f"${float(d.get('fee') or 0):.2f}",
        ]
        data.append(row)

    total_fees = sum(float(d.get("fee") or 0) for d in docs)
    data.append(["", "", "", "", "", "TOTAL", f"${total_fees:.2f}"])

    table = Table(data, colWidths=[0.4*inch, 0.9*inch, 1.4*inch, 1.7*inch, 2.6*inch, 1.8*inch, 0.8*inch])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#00317A")),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,-1), 8),
        ('BOTTOMPADDING', (0,0), (-1,0), 8),
        ('TOPPADDING', (0,0), (-1,0), 8),
        ('GRID', (0,0), (-1,-1), 0.25, colors.HexColor("#E2E8F0")),
        ('ALIGN', (-1,1), (-1,-1), 'RIGHT'),
        ('ALIGN', (0,0), (0,-1), 'CENTER'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('ROWBACKGROUNDS', (0,1), (-1,-2), [colors.white, colors.HexColor("#F4F7F9")]),
        ('FONTNAME', (0,-1), (-1,-1), 'Helvetica-Bold'),
        ('BACKGROUND', (0,-1), (-1,-1), colors.HexColor("#F4F7F9")),
    ]))
    elems.append(table)
    doc.build(elems, onFirstPage=stamp, onLaterPages=stamp)
    buf.seek(0)
    return StreamingResponse(buf, media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="notary-journal-{datetime.now().strftime("%Y%m%d")}.pdf"'})

# ---- Startup ----
@app.on_event("startup")
async def startup():
    await db.users.create_index("email", unique=True)
    await db.entries.create_index([("user_id", 1), ("entry_date", -1)])
    await db.clients.create_index([("user_id", 1), ("name", 1)])
    await db.document_types.create_index([("user_id", 1), ("name", 1)])
    await db.signer_intakes.create_index("token", unique=True)
    await db.signer_intakes.create_index([("user_id", 1), ("created_at", -1)])
    # Certificate tokens are optional; use a sparse index so many nulls are fine.
    await db.entries.create_index("certificate_token", unique=True, sparse=True)
    await db.certificate_email_log.create_index([("user_id", 1), ("entry_id", 1), ("sent_at", -1)])
    await db.cron_runs.create_index("run_id", unique=True)
    await db.login_attempts.create_index("identifier")

    admin_email = os.environ.get("ADMIN_EMAIL", "admin@example.com").lower()
    admin_password = os.environ.get("ADMIN_PASSWORD", "admin123")
    admin_name = os.environ.get("ADMIN_NAME", "Admin")
    existing = await db.users.find_one({"email": admin_email})
    if not existing:
        await db.users.insert_one({
            "email": admin_email,
            "password_hash": hash_password(admin_password),
            "name": admin_name,
            "role": "admin",
            "commission_number": None,
            "commission_expires": None,
            "state": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        # seed default doc types for admin
        admin = await db.users.find_one({"email": admin_email})
        user_id = str(admin["_id"])
        for t in DEFAULT_DOC_TYPES:
            await db.document_types.insert_one({
                **t, "user_id": user_id,
                "created_at": datetime.now(timezone.utc).isoformat()
            })
    elif not verify_password(admin_password, existing["password_hash"]):
        await db.users.update_one(
            {"email": admin_email},
            {"$set": {"password_hash": hash_password(admin_password), "name": admin_name}})

@app.on_event("shutdown")
async def shutdown():
    mongo_client.close()

app.include_router(api)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

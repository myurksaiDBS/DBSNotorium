from dotenv import load_dotenv
from pathlib import Path
ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

import os
import io
import logging
import base64
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Annotated

import bcrypt
import jwt
from bson import ObjectId
from fastapi import FastAPI, APIRouter, HTTPException, Depends, Request, Response, Query
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

LOGO_PATH = ROOT_DIR / "assets" / "domini-logo.png"

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
    signer_name: str
    signer_address: Optional[str] = ""
    signer_phone: Optional[str] = ""
    signer_email: Optional[str] = ""
    id_type: str
    id_number: str
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
    if updates:
        await db.users.update_one({"_id": ObjectId(user["_id"])}, {"$set": updates})
    doc = await db.users.find_one({"_id": ObjectId(user["_id"])})
    return {
        "id": str(doc["_id"]), "email": doc["email"], "name": doc["name"],
        "role": doc.get("role", "notary"),
        "commission_number": doc.get("commission_number"),
        "commission_expires": doc.get("commission_expires"),
        "state": doc.get("state"),
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
async def create_entry(payload: EntryIn, user=Depends(get_current_user)):
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
async def update_entry(entry_id: str, payload: EntryIn, user=Depends(get_current_user)):
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
async def delete_entry(entry_id: str, user=Depends(get_current_user)):
    result = await db.entries.delete_one({"_id": ObjectId(entry_id), "user_id": user["_id"]})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Entry not found")
    return {"ok": True}

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

    return {
        "total_entries": total_entries,
        "month_entries": len(month_docs),
        "month_revenue": round(month_revenue, 2),
        "total_revenue": round(total_revenue, 2),
        "total_clients": total_clients,
        "monthly_series": months,
        "type_breakdown": type_breakdown,
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
async def create_client(payload: ClientIn, user=Depends(get_current_user)):
    doc = payload.model_dump()
    doc["user_id"] = user["_id"]
    doc["created_at"] = datetime.now(timezone.utc).isoformat()
    result = await db.clients.insert_one(doc)
    doc["_id"] = result.inserted_id
    return serialize_generic(doc)

@api.put("/clients/{client_id}")
async def update_client(client_id: str, payload: ClientIn, user=Depends(get_current_user)):
    result = await db.clients.update_one(
        {"_id": ObjectId(client_id), "user_id": user["_id"]},
        {"$set": payload.model_dump()})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Client not found")
    doc = await db.clients.find_one({"_id": ObjectId(client_id)})
    return serialize_generic(doc)

@api.delete("/clients/{client_id}")
async def delete_client(client_id: str, user=Depends(get_current_user)):
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
async def create_doc_type(payload: DocTypeIn, user=Depends(get_current_user)):
    doc = payload.model_dump()
    doc["user_id"] = user["_id"]
    doc["created_at"] = datetime.now(timezone.utc).isoformat()
    result = await db.document_types.insert_one(doc)
    doc["_id"] = result.inserted_id
    return serialize_generic(doc)

@api.delete("/document-types/{type_id}")
async def delete_doc_type(type_id: str, user=Depends(get_current_user)):
    result = await db.document_types.delete_one({"_id": ObjectId(type_id), "user_id": user["_id"]})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Type not found")
    return {"ok": True}

# ---- Signer Intakes (pre-fill portal) ----
import secrets as _secrets

def _serialize_intake(doc: dict) -> dict:
    d = dict(doc)
    d["id"] = str(d.pop("_id"))
    d.pop("user_id", None)
    return d

@api.get("/intakes")
async def list_intakes(user=Depends(get_current_user)):
    docs = await db.signer_intakes.find({"user_id": user["_id"]}).sort("created_at", -1).to_list(500)
    now = datetime.now(timezone.utc).isoformat()
    for d in docs:
        if d.get("status") == "pending" and d.get("expires_at") and d["expires_at"] < now:
            d["status"] = "expired"
    return [_serialize_intake(d) for d in docs]

@api.post("/intakes")
async def create_intake(payload: IntakeCreateIn, user=Depends(get_current_user)):
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
    return _serialize_intake(doc)

@api.delete("/intakes/{intake_id}")
async def delete_intake(intake_id: str, user=Depends(get_current_user)):
    result = await db.signer_intakes.delete_one({"_id": ObjectId(intake_id), "user_id": user["_id"]})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Intake not found")
    return {"ok": True}

@api.post("/intakes/{intake_id}/mark-used")
async def mark_intake_used(intake_id: str, user=Depends(get_current_user)):
    await db.signer_intakes.update_one(
        {"_id": ObjectId(intake_id), "user_id": user["_id"]},
        {"$set": {"status": "used", "used_at": datetime.now(timezone.utc).isoformat()}})
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
    return {
        "status": doc.get("status", "pending"),
        "label": doc.get("label", ""),
        "appointment_note": doc.get("appointment_note", ""),
        "notary_name": notary.get("name") if notary else "",
        "commission_number": notary.get("commission_number") if notary else "",
        "state": notary.get("state") if notary else "",
        "submitted_at": doc.get("submitted_at"),
        "data": doc.get("data"),
    }

@api.post("/public/intake/{token}")
async def public_submit_intake(token: str, payload: IntakeSubmitIn):
    doc = await db.signer_intakes.find_one({"token": token})
    if not doc:
        raise HTTPException(status_code=404, detail="Link not found")
    now = datetime.now(timezone.utc)
    if doc.get("status") in ("completed", "used"):
        raise HTTPException(status_code=400, detail="This intake has already been submitted.")
    if doc.get("expires_at") and doc["expires_at"] < now.isoformat():
        raise HTTPException(status_code=400, detail="This link has expired. Ask your notary for a new one.")
    await db.signer_intakes.update_one(
        {"_id": doc["_id"]},
        {"$set": {
            "data": payload.model_dump(),
            "status": "completed",
            "submitted_at": now.isoformat(),
        }})
    return {"ok": True}

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

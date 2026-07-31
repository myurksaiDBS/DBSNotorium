"""Invitation routes for Notarium.

Colleagues onboard via a one-time link with the firm details pre-filled.
Exposes `register_invites(api, db, get_current_user, deps)` where `deps`
carries the auth/user helpers the acceptance flow needs to reuse (hashing,
cookie setup, default document-type seeding, ObjectId coercion, and the
`_send_email` transport).
"""
from __future__ import annotations

import html as _html
import os
import secrets as _secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from bson import ObjectId
from fastapi import Depends, HTTPException, Response
from pydantic import BaseModel, EmailStr


class InviteCreateIn(BaseModel):
    email: EmailStr
    firm_name: Optional[str] = None
    state: Optional[str] = None
    message: Optional[str] = None


class InviteAcceptIn(BaseModel):
    password: str
    name: str


def _render_invite_email(inviter_name: str, firm_name: str, invite_url: str,
                         personal_note: str, app_name: str) -> tuple[str, str]:
    subject = f"You're invited to Notarium — join {firm_name or 'the team'}"
    note_html = ""
    if personal_note:
        note_html = (f'<tr><td style="padding:0 32px 16px 32px;">'
                     f'<div style="border-left:3px solid #DF0D1B;padding:8px 14px;color:#4B5563;'
                     f'background:#F4F7F9;font-style:italic;">{_html.escape(personal_note)}</div></td></tr>')
    firm_line = f"<b>{_html.escape(firm_name)}</b> is inviting you to " if firm_name else "You're invited to "
    html_body = f"""<!doctype html><html><body style="margin:0;padding:32px;background:#F4F7F9;font-family:Georgia,'Times New Roman',serif;">
<table role="presentation" width="580" cellpadding="0" cellspacing="0" align="center"
       style="background:#ffffff;border:1px solid #E2E8F0;border-radius:2px;">
  <tr><td style="background:#00317A;color:#ffffff;padding:20px 32px;">
      <div style="font-size:11px;letter-spacing:0.25em;color:#F0B429;text-transform:uppercase;">Notarium</div>
      <div style="font-size:22px;margin-top:4px;">You're invited</div>
  </td></tr>
  <tr><td style="padding:28px 32px 8px 32px;color:#111827;font-size:15px;line-height:1.6;">
      Hi there,<br/><br/>
      {firm_line}join Notarium — the notary journal, signer intake &amp; certificate platform from
      Domini Business Services. <b>{_html.escape(inviter_name)}</b> set this up for you.
  </td></tr>
  {note_html}
  <tr><td style="padding:8px 32px 24px 32px;">
      <a href="{invite_url}" style="display:inline-block;background:#DF0D1B;color:#ffffff;
             padding:12px 20px;text-decoration:none;font-family:Helvetica,Arial,sans-serif;
             font-size:14px;letter-spacing:0.05em;text-transform:uppercase;border-radius:2px;">
         Accept invitation
      </a>
      <div style="font-family:'SFMono-Regular',Menlo,Courier,monospace;font-size:11px;color:#4B5563;margin-top:14px;word-break:break-all;">
         Or paste this link in a browser:<br/>{_html.escape(invite_url)}
      </div>
  </td></tr>
  <tr><td style="padding:12px 32px 24px 32px;color:#4B5563;font-size:12px;border-top:1px solid #E2E8F0;">
      This invitation expires in 7 days. If you weren't expecting it, you can safely ignore this email.<br/>
      — Powered by Notarium · {_html.escape(app_name)}
  </td></tr>
</table></body></html>"""
    return subject, html_body


def register_invites(api, db, get_current_user, deps):
    """Attach invite routes. `deps` provides the shared helpers we can't
    import directly without pulling half of server.py along with them."""
    send_email = deps["send_email"]
    hash_password = deps["hash_password"]
    create_access_token = deps["create_access_token"]
    create_refresh_token = deps["create_refresh_token"]
    set_auth_cookies = deps["set_auth_cookies"]
    to_oid = deps["to_oid"]
    default_doc_types = deps["default_doc_types"]

    @api.post("/invites")
    async def create_invite(payload: InviteCreateIn, user=Depends(get_current_user)):
        email = payload.email.lower().strip()
        if await db.users.find_one({"email": email}):
            raise HTTPException(status_code=400, detail="That email already has a Notarium account.")

        firm = (payload.firm_name or user.get("firm_name") or "").strip() or None
        state = (payload.state or user.get("state") or "").strip() or None
        token = _secrets.token_urlsafe(24)
        now = datetime.now(timezone.utc)
        expires = now + timedelta(days=7)
        await db.invites.insert_one({
            "token": token,
            "email": email,
            "firm_name": firm,
            "state": state,
            "invited_by_user_id": user["_id"],
            "invited_by_name": user.get("name"),
            "message": payload.message,
            "created_at": now.isoformat(),
            "expires_at": expires.isoformat(),
            "used_at": None,
            "email_status": "pending",
        })
        app_url = os.environ.get("ALERT_APP_URL", "").rstrip("/")
        invite_url = f"{app_url}/invite/{token}"
        subject, html_body = _render_invite_email(
            user.get("name", "A colleague"), firm or "", invite_url,
            payload.message or "",
            os.environ.get("EMAIL_FROM_NAME", "Domini Business Services"),
        )
        email_id, error = await send_email(email, subject, html_body, reply_to=user.get("email"))
        await db.invites.update_one(
            {"token": token},
            {"$set": {"email_status": "sent" if email_id else "failed",
                      "email_error": error,
                      "email_id": email_id,
                      "email_sent_at": datetime.now(timezone.utc).isoformat()}},
        )
        return {
            "token": token,
            "invite_url": invite_url,
            "email": email,
            "firm_name": firm,
            "expires_at": expires.isoformat(),
            "email_sent": bool(email_id),
            "email_error": error,
        }

    @api.get("/invites")
    async def list_invites(user=Depends(get_current_user)):
        docs = await db.invites.find({"invited_by_user_id": user["_id"]}) \
            .sort("created_at", -1).to_list(200)
        return {"invites": [{
            "id": str(d["_id"]),
            "token": d["token"],
            "email": d["email"],
            "firm_name": d.get("firm_name"),
            "created_at": d.get("created_at"),
            "expires_at": d.get("expires_at"),
            "used_at": d.get("used_at"),
            "email_status": d.get("email_status"),
            "email_error": d.get("email_error"),
        } for d in docs]}

    @api.get("/invites/{invite_id}")
    async def get_invite(invite_id: str, user=Depends(get_current_user)):
        try:
            oid = to_oid(invite_id)
        except HTTPException:
            raise HTTPException(status_code=404, detail="Invite not found")
        d = await db.invites.find_one({"_id": oid, "invited_by_user_id": user["_id"]})
        if not d:
            raise HTTPException(status_code=404, detail="Invite not found")
        return {
            "id": str(d["_id"]),
            "token": d["token"],
            "email": d["email"],
            "firm_name": d.get("firm_name"),
            "state": d.get("state"),
            "created_at": d.get("created_at"),
            "expires_at": d.get("expires_at"),
            "used_at": d.get("used_at"),
            "email_status": d.get("email_status"),
            "email_error": d.get("email_error"),
            "invite_url": f"{os.environ.get('ALERT_APP_URL','').rstrip('/')}/invite/{d['token']}",
        }

    @api.delete("/invites/{invite_id}")
    async def revoke_invite(invite_id: str, user=Depends(get_current_user)):
        result = await db.invites.delete_one(
            {"_id": to_oid(invite_id), "invited_by_user_id": user["_id"]})
        if result.deleted_count == 0:
            raise HTTPException(status_code=404, detail="Invite not found")
        return {"ok": True}

    @api.get("/public/invite/{token}")
    async def public_invite_info(token: str):
        inv = await db.invites.find_one({"token": token})
        if not inv:
            raise HTTPException(status_code=404, detail="Invitation not found")
        if inv.get("used_at"):
            raise HTTPException(status_code=410, detail="This invitation has already been accepted.")
        try:
            if datetime.fromisoformat(inv["expires_at"]) < datetime.now(timezone.utc):
                raise HTTPException(status_code=410, detail="This invitation has expired.")
        except (ValueError, TypeError):
            pass
        return {
            "email": inv.get("email"),
            "firm_name": inv.get("firm_name"),
            "state": inv.get("state"),
            "invited_by_name": inv.get("invited_by_name"),
        }

    @api.post("/public/invite/{token}/accept")
    async def public_invite_accept(token: str, payload: InviteAcceptIn, response: Response):
        inv = await db.invites.find_one({"token": token})
        if not inv:
            raise HTTPException(status_code=404, detail="Invitation not found")
        if inv.get("used_at"):
            raise HTTPException(status_code=410, detail="This invitation has already been accepted.")
        try:
            if datetime.fromisoformat(inv["expires_at"]) < datetime.now(timezone.utc):
                raise HTTPException(status_code=410, detail="This invitation has expired.")
        except (ValueError, TypeError):
            pass
        if len(payload.password) < 8:
            raise HTTPException(status_code=400, detail="Password must be at least 8 characters.")
        email = inv["email"]

        # Atomically claim the invite BEFORE creating the user — concurrent
        # losers get 410 instead of a duplicate-user error.
        claim_time = datetime.now(timezone.utc).isoformat()
        claimed = await db.invites.find_one_and_update(
            {"token": token, "used_at": None},
            {"$set": {"used_at": claim_time}},
        )
        if not claimed:
            raise HTTPException(status_code=410, detail="This invitation has already been accepted.")

        user_doc = {
            "email": email,
            "password_hash": hash_password(payload.password),
            "name": payload.name,
            "role": "notary",
            "commission_number": None,
            "commission_expires": None,
            "state": inv.get("state"),
            "firm_name": inv.get("firm_name"),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "invited_by_user_id": inv.get("invited_by_user_id"),
        }
        try:
            result = await db.users.insert_one(user_doc)
        except Exception as e:
            await db.invites.update_one(
                {"token": token, "used_at": claim_time},
                {"$set": {"used_at": None}},
            )
            if "duplicate" in str(e).lower() or "E11000" in str(e):
                raise HTTPException(status_code=410, detail="This invitation has already been accepted.")
            raise
        uid = str(result.inserted_id)
        for t in default_doc_types:
            await db.document_types.insert_one({
                **t, "user_id": uid,
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
        await db.invites.update_one(
            {"token": token},
            {"$set": {"accepted_user_id": uid}},
        )
        access = create_access_token(uid, email)
        refresh = create_refresh_token(uid)
        set_auth_cookies(response, access, refresh)
        return {"id": uid, "email": email, "name": payload.name, "role": "notary",
                "firm_name": inv.get("firm_name")}

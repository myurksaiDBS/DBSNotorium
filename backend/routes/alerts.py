"""Commission expiration + certificate bounce alert routes.

Extracted from server.py. Attach via `register_alerts(api, db, get_current_user)`
after `_send_email` and other shared helpers have been wired in `deps.py`.
"""
from __future__ import annotations

import hmac
import html as _html
import os
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from bson import ObjectId
from fastapi import BackgroundTasks, Depends, Header, HTTPException
from pymongo.errors import DuplicateKeyError

from deps import send_email as _send_email


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
      <tr><td style="background:#00317A;padding:20px 28px;">
        <div style="color:#ffffff;font-family:Georgia,'Times New Roman',serif;font-size:20px;font-weight:700;">Domini Business Services</div>
        <div style="color:rgba(255,255,255,0.7);font-size:10px;letter-spacing:2.5px;text-transform:uppercase;margin-top:2px;">Notarium · Commission Alerts</div>
      </td></tr>
      <tr><td style="background:#DF0D1B;height:3px;line-height:3px;font-size:0;">&nbsp;</td></tr>
      <tr><td style="padding:32px 28px 8px;">
        <div style="font-size:10px;letter-spacing:2.5px;text-transform:uppercase;color:{cta_color};margin-bottom:8px;">Commission alert</div>
        <h1 style="font-family:Georgia,'Times New Roman',serif;font-size:26px;color:#00317A;margin:0 0 12px;">{headline}</h1>
        <p style="font-size:14px;line-height:1.6;color:#4B5563;margin:0 0 18px;">Hi {notary_name or 'there'},</p>
        <p style="font-size:14px;line-height:1.6;color:#111827;margin:0 0 22px;">{body_text}</p>
        <table role="presentation" cellpadding="0" cellspacing="0"><tr><td style="background:{cta_color};border-radius:3px;">
          <a href="{app_url}/settings" style="display:inline-block;padding:12px 22px;color:#ffffff;text-decoration:none;font-size:13px;font-weight:600;letter-spacing:0.5px;">Update commission details</a>
        </td></tr></table>
      </td></tr>
      <tr><td style="padding:22px 28px;border-top:1px solid #E2E8F0;background:#F8FAFC;">
        <div style="font-family:'Courier New',monospace;font-size:11px;color:#4B5563;">Commission expires: <span style="color:#111827;">{expires_on.strftime('%B %d, %Y')}</span></div>
        <div style="font-family:'Courier New',monospace;font-size:11px;color:#4B5563;margin-top:4px;">Days remaining: <span style="color:#111827;">{days_left}</span></div>
      </td></tr>
      <tr><td style="padding:16px 28px;background:#00317A;color:rgba(255,255,255,0.55);font-size:10px;letter-spacing:2px;text-transform:uppercase;text-align:center;font-family:'Courier New',monospace;">A Division of YFI Enterprises, LLC</td></tr>
    </table>
  </td></tr>
</table></body></html>"""
    return subject, html


def _render_bounce_digest_email(notary_name: str, rows: list[dict], app_url: str) -> tuple[str, str]:
    subject = f"[Notarium] {len(rows)} certificate email{'s' if len(rows) != 1 else ''} bounced overnight"
    row_html = ""
    for r in rows:
        entry_link = f"{app_url}/journal?entry={r.get('entry_id','')}"
        recipient = r.get("recipient") or r.get("to") or ""
        error = r.get("error_msg") or r.get("error") or "delivery failed"
        row_html += (
            f'<tr><td style="padding:8px 12px;border-bottom:1px solid #E2E8F0;font-family:Georgia,serif;font-size:13px;">'
            f'{_html.escape(r.get("signer_name") or "—")}'
            f'<div style="font-family:SFMono-Regular,Menlo,Courier,monospace;font-size:11px;color:#4B5563;">'
            f'{_html.escape(recipient)}</div></td>'
            f'<td style="padding:8px 12px;border-bottom:1px solid #E2E8F0;font-size:12px;color:#991B1B;">'
            f'{_html.escape(error[:80])}</td>'
            f'<td style="padding:8px 12px;border-bottom:1px solid #E2E8F0;text-align:right;">'
            f'<a href="{entry_link}" style="color:#DF0D1B;text-decoration:none;font-size:12px;letter-spacing:0.05em;text-transform:uppercase;">Open &amp; resend →</a></td></tr>'
        )
    html = f"""<!doctype html><html><body style="margin:0;padding:32px;background:#F4F7F9;font-family:Georgia,'Times New Roman',serif;">
<table role="presentation" width="620" cellpadding="0" cellspacing="0" align="center" style="background:#ffffff;border:1px solid #E2E8F0;">
  <tr><td style="background:#00317A;color:#ffffff;padding:20px 32px;">
    <div style="font-size:11px;letter-spacing:0.25em;color:#F0B429;text-transform:uppercase;">Delivery Digest</div>
    <div style="font-size:20px;margin-top:4px;">Certificate emails that bounced</div>
  </td></tr>
  <tr><td style="padding:20px 32px 8px 32px;color:#111827;font-size:14px;line-height:1.5;">
    Good morning {_html.escape(notary_name or "there")},<br/><br/>
    {len(rows)} certificate email{'s' if len(rows) != 1 else ''} failed to reach recipients overnight. Open the entry and click <b>Resend</b> to try again.
  </td></tr>
  <tr><td style="padding:0 24px 20px 24px;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #E2E8F0;">
      <thead><tr style="background:#F4F7F9;">
        <th align="left" style="padding:8px 12px;font-size:10px;letter-spacing:0.2em;color:#4B5563;text-transform:uppercase;">Signer</th>
        <th align="left" style="padding:8px 12px;font-size:10px;letter-spacing:0.2em;color:#4B5563;text-transform:uppercase;">Reason</th>
        <th align="right" style="padding:8px 12px;font-size:10px;letter-spacing:0.2em;color:#4B5563;text-transform:uppercase;">Action</th>
      </tr></thead><tbody>{row_html}</tbody>
    </table>
  </td></tr>
  <tr><td style="padding:12px 32px 20px 32px;color:#4B5563;font-size:12px;border-top:1px solid #E2E8F0;">— Notarium · Domini Business Services</td></tr>
</table></body></html>"""
    return subject, html


def register_alerts(api, db, get_current_user):
    """Attach commission-alerts + bounce-digest routes to the app's API router."""

    async def _scan_and_send_alerts() -> dict:
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
                skipped += 1; continue
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

    async def _scan_and_send_bounce_digests():
        now = datetime.now(timezone.utc)
        since = (now - timedelta(hours=24)).isoformat()
        app_url = os.environ.get("ALERT_APP_URL", "").rstrip("/")
        failed_docs = await db.certificate_email_log.find({
            "status": "failed",
            "sent_at": {"$gte": since},
            "bounce_digest_sent": {"$ne": True},
        }).to_list(2000)
        if not failed_docs:
            return {"sent": 0, "notaries": 0, "total_bounces": 0}
        groups: dict[str, list[dict]] = {}
        for d in failed_docs:
            groups.setdefault(d["user_id"], []).append(d)
        sent = 0
        for uid, rows in groups.items():
            try:
                notary = await db.users.find_one({"_id": ObjectId(uid)})
            except Exception:
                continue
            if not notary:
                continue
            subject, html = _render_bounce_digest_email(notary.get("name", ""), rows, app_url)
            email_id, _err = await _send_email(notary["email"], subject, html)
            if email_id:
                sent += 1
                ids = [r["_id"] for r in rows]
                await db.certificate_email_log.update_many(
                    {"_id": {"$in": ids}},
                    {"$set": {"bounce_digest_sent": True,
                              "bounce_digest_sent_at": now.isoformat()}},
                )
        return {"sent": sent, "notaries": len(groups), "total_bounces": len(failed_docs)}

    def _cron_auth(authorization: Optional[str]) -> None:
        expected = os.environ.get("WEBHOOK_CRON_SECRET", "")
        provided = ""
        if authorization and authorization.startswith("Bearer "):
            provided = authorization[7:]
        if not expected or not hmac.compare_digest(expected, provided):
            raise HTTPException(status_code=401, detail="Unauthorized")

    async def _record_cron_run(cron: str, x_webhook_id: Optional[str]) -> bool:
        """Return True to proceed, False if this delivery was a duplicate."""
        if not x_webhook_id:
            return True
        try:
            await db.cron_runs.insert_one({
                "run_id": x_webhook_id,
                "cron": cron,
                "received_at": datetime.now(timezone.utc).isoformat(),
            })
            return True
        except DuplicateKeyError:
            return False

    @api.post("/cron/commission-alerts")
    async def cron_commission_alerts(
        background: BackgroundTasks,
        authorization: Optional[str] = Header(default=None),
        x_webhook_id: Optional[str] = Header(default=None, alias="X-Webhook-Id"),
    ):
        _cron_auth(authorization)
        proceed = await _record_cron_run("commission-alerts", x_webhook_id)
        if not proceed:
            return {"accepted": True, "duplicate": True, "run_id": x_webhook_id}
        background.add_task(_scan_and_send_alerts)
        return {"accepted": True, "run_id": x_webhook_id}

    @api.post("/cron/delivery-bounces")
    async def cron_delivery_bounces(
        background: BackgroundTasks,
        authorization: Optional[str] = Header(default=None),
        x_webhook_id: Optional[str] = Header(default=None, alias="X-Webhook-Id"),
    ):
        _cron_auth(authorization)
        proceed = await _record_cron_run("delivery-bounces", x_webhook_id)
        if not proceed:
            return {"accepted": True, "duplicate": True, "run_id": x_webhook_id}
        background.add_task(_scan_and_send_bounce_digests)
        return {"accepted": True, "run_id": x_webhook_id}

    @api.get("/alerts/bounces")
    async def bounce_summary(user=Depends(get_current_user)):
        since = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        docs = await db.certificate_email_log.find({
            "user_id": user["_id"],
            "status": "failed",
            "sent_at": {"$gte": since},
        }).sort("sent_at", -1).to_list(100)
        return {
            "total": len(docs),
            "bounces": [{
                "id": str(d["_id"]),
                "entry_id": d.get("entry_id"),
                "recipient": d.get("recipient") or d.get("to"),
                "error_msg": d.get("error_msg") or d.get("error"),
                "sent_at": d.get("sent_at"),
                "digest_notified": bool(d.get("bounce_digest_sent")),
            } for d in docs],
        }

    @api.post("/cron/delivery-bounces/preview")
    async def preview_bounce_digest(user=Depends(get_current_user)):
        since = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        rows = await db.certificate_email_log.find({
            "user_id": user["_id"],
            "status": "failed",
            "sent_at": {"$gte": since},
        }).sort("sent_at", -1).to_list(50)
        if not rows:
            raise HTTPException(status_code=400, detail="No recent bounces to preview.")
        app_url = os.environ.get("ALERT_APP_URL", "").rstrip("/")
        subject, html = _render_bounce_digest_email(user.get("name", ""), rows, app_url)
        subject = "[Preview] " + subject
        email_id, err = await _send_email(user["email"], subject, html)
        if not email_id:
            raise HTTPException(status_code=400, detail=err or "Could not send email.")
        return {"ok": True, "email_id": email_id, "sent_to": user["email"], "count": len(rows)}

    @api.post("/alerts/test")
    async def send_test_alert(user=Depends(get_current_user)):
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

"""Shared runtime helpers used by both server.py and modular route packages.

Promoting these out of server.py makes it safe to extract the alerts,
intake, and journal route modules without duplicating logic or hitting
circular imports.
"""
from __future__ import annotations

import base64
import io
import logging
import os
from pathlib import Path
from typing import Optional

import httpx
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.lib.utils import ImageReader
from reportlab.platypus import Image as RLImage


_ROOT_DIR = Path(__file__).parent
LOGO_PATH = _ROOT_DIR / "assets" / "domini-logo.png"
EMAIL_BASE_URL = "https://integrations.emergentagent.com"


async def send_email(to_email: str, subject: str, html: str,
                      reply_to: Optional[str] = None) -> tuple[Optional[str], Optional[str]]:
    """Send a transactional email via Emergent-managed Resend.
    Returns (email_id, error_message) — id set on success, error set on failure."""
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


def draw_domini_stamp(canvas, page_w: float, page_h: float, doc_page: int) -> None:
    """Draw the Domini watermark shield, footer text, and page rule.
    Works on both portrait and landscape page sizes."""
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


def firm_logo_flowable(notary: dict, height_in: float = 0.55):
    """Return a small firm-logo Image flowable if the user uploaded one, else None."""
    data = notary.get("firm_logo_data") or ""
    if not (data.startswith("data:image/") and "base64," in data):
        return None
    try:
        raw = base64.b64decode(data.split("base64,", 1)[1])
        img = RLImage(io.BytesIO(raw),
                      height=height_in * inch,
                      width=height_in * inch * 2.2,
                      kind='proportional')
        img.hAlign = 'RIGHT'
        return img
    except Exception:
        return None

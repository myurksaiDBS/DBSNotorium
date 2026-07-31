"""Stripe subscription routes for Notarium.

This module owns the Stripe billing surface: plan configuration, trial
computation, checkout/session/portal endpoints, and the webhook handler.
The rest of the app uses `register_billing(api, db, get_current_user)` to
attach these routes and receive the `require_active_subscription` FastAPI
dependency used to gate write actions when a trial expires.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import stripe
from bson import ObjectId
from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel


TRIAL_DAYS = 14
BILLING_PLANS = {
    "notarium_pro_monthly": {"label": "Monthly", "amount": 19.00, "interval": "month"},
    "notarium_pro_yearly":  {"label": "Yearly",  "amount": 190.00, "interval": "year"},
}


class CheckoutIn(BaseModel):
    lookup_key: str
    origin_url: str


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
    States: active | trialing | expired | canceled | past_due
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


def _validate_origin_url(origin_url: str) -> str:
    """Only allow the configured app URL host — prevents open-redirect via Stripe return."""
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


def register_billing(api, db, get_current_user):
    """Attach all billing routes to `api` and return the subscription gate dep."""
    stripe.api_key = os.environ["STRIPE_SECRET_KEY"]
    stripe_webhook_secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
    stripe_publishable = os.environ.get("STRIPE_PUBLISHABLE_KEY", "")

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

    async def _apply_subscription_from_session(session_obj) -> None:
        """Persist subscription details from a completed checkout session."""
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
                sub_status = sub.status
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
        """Sync subscription state to user record (customer.subscription.* webhooks)."""
        sub_id = subscription_obj.get("id") if isinstance(subscription_obj, dict) else subscription_obj.id
        status = subscription_obj.get("status") if isinstance(subscription_obj, dict) else subscription_obj.status
        cancel = subscription_obj.get("cancel_at_period_end") if isinstance(subscription_obj, dict) else subscription_obj.cancel_at_period_end
        cpe = subscription_obj.get("current_period_end") if isinstance(subscription_obj, dict) else subscription_obj.current_period_end
        period_end = datetime.fromtimestamp(cpe, tz=timezone.utc).isoformat() if cpe else None

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

    @api.get("/billing/status")
    async def billing_status(user=Depends(get_current_user)):
        snap = _subscription_snapshot(user)
        return {
            "provider": "stripe",
            "publishable_key": stripe_publishable,
            "plans": [
                {"lookup_key": "notarium_pro_monthly", "label": "Monthly",
                 "amount": 19.00, "currency": "usd", "interval": "month"},
                {"lookup_key": "notarium_pro_yearly", "label": "Yearly",
                 "amount": 190.00, "currency": "usd", "interval": "year",
                 "savings_label": "Save $38 (2 months free)"},
            ],
            **snap,
        }

    @api.post("/billing/checkout")
    async def billing_checkout(payload: CheckoutIn, user=Depends(get_current_user)):
        if payload.lookup_key not in BILLING_PLANS:
            raise HTTPException(status_code=400, detail="Unknown plan")

        prices = stripe.Price.list(lookup_keys=[payload.lookup_key], active=True, limit=1).data
        if not prices:
            raise HTTPException(status_code=500, detail=f"Price not found: {payload.lookup_key}")
        price = prices[0]

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

    @api.post("/stripe/webhook")
    async def stripe_webhook(request: Request):
        payload = await request.body()
        sig = request.headers.get("stripe-signature", "")
        try:
            event = stripe.Webhook.construct_event(payload, sig, stripe_webhook_secret)
        except stripe.error.SignatureVerificationError:
            raise HTTPException(status_code=400, detail="Invalid signature")
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Webhook error: {e}")

        t = event["type"]
        obj = event["data"]["object"]
        if t == "checkout.session.completed":
            await _apply_subscription_from_session(obj)
        elif t in ("customer.subscription.created",
                   "customer.subscription.updated",
                   "customer.subscription.deleted"):
            await _sync_subscription(obj)
        elif t == "invoice.payment_failed":
            sub_id = obj.get("subscription")
            if sub_id:
                await db.users.update_one(
                    {"stripe_subscription_id": sub_id},
                    {"$set": {"subscription_status": "past_due"}},
                )
        return {"status": "ok"}

    return require_active_subscription

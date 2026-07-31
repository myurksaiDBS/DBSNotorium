"""Idempotent Stripe catalog setup for Notarium billing.
Creates a single 'Notarium Pro' product with monthly and yearly prices.
"""
import os
import sys
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).parent / ".env")

import stripe

stripe.api_key = os.environ["STRIPE_SECRET_KEY"]

CATALOG = [
    {
        "emergent_product_id": "notarium_pro",
        "name": "Notarium Pro",
        "tax_code": "txcd_10103001",  # SaaS
        "prices": [
            {"lookup_key": "notarium_pro_monthly", "amount": 1900, "currency": "usd", "interval": "month"},
            {"lookup_key": "notarium_pro_yearly",  "amount": 19000, "currency": "usd", "interval": "year"},
        ],
    },
]


def ensure_tax_settings():
    s = stripe.tax.Settings.retrieve()
    if s.head_office and getattr(s.head_office, "address", None):
        return
    stripe.tax.Settings.modify(
        head_office={"address": {"country": "US", "line1": "1 Notary Way",
                                  "city": "Sacramento", "state": "CA",
                                  "postal_code": "95814"}},
        defaults={"tax_behavior": "exclusive"},
    )


def get_or_create_product(entry):
    for p in stripe.Product.list(active=True).auto_paging_iter():
        if p.to_dict().get("metadata", {}).get("emergent_product_id") == entry["emergent_product_id"]:
            return p
    return stripe.Product.create(
        name=entry["name"], tax_code=entry.get("tax_code"),
        metadata={"managed_by": "emergent", "emergent_product_id": entry["emergent_product_id"]},
    )


def ensure_price(product, price_def):
    existing = stripe.Price.list(lookup_keys=[price_def["lookup_key"]], active=True, limit=1).data
    if existing and (existing[0].unit_amount != price_def["amount"] or existing[0].currency != price_def["currency"]):
        stripe.Price.modify(existing[0].id, active=False)
        existing = []
    if not existing:
        kwargs = dict(
            product=product.id,
            unit_amount=price_def["amount"],
            currency=price_def["currency"],
            lookup_key=price_def["lookup_key"],
            transfer_lookup_key=True,
        )
        if price_def.get("interval"):
            kwargs["recurring"] = {"interval": price_def["interval"]}
        return stripe.Price.create(**kwargs)
    return existing[0]


def main():
    try:
        ensure_tax_settings()
    except Exception as e:
        print(f"tax settings note: {e}", file=sys.stderr)

    for entry in CATALOG:
        product = get_or_create_product(entry)
        print(f"product: {product.id} {entry['name']}")
        for pd in entry["prices"]:
            price = ensure_price(product, pd)
            print(f"  price: {price.id} {pd['lookup_key']} ${pd['amount']/100:.2f}/{pd.get('interval','once')}")
    print("done")


if __name__ == "__main__":
    main()

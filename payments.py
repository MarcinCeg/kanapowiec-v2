import stripe
import os
from flask import Blueprint, request, jsonify, redirect, url_for, current_app
from flask_login import login_required, current_user
from models import db

payments_bp = Blueprint("payments", __name__)

# ── Stripe config ──────────────────────────────────────────────────────────
stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")

STRIPE_PUBLISHABLE_KEY  = os.environ.get("STRIPE_PUBLISHABLE_KEY")
STRIPE_WEBHOOK_SECRET   = os.environ.get("STRIPE_WEBHOOK_SECRET")
STRIPE_PRICE_MONTHLY    = os.environ.get("STRIPE_PRICE_MONTHLY")
STRIPE_PRICE_ONETIME    = os.environ.get("STRIPE_PRICE_ONETIME")
DOMAIN                  = os.environ.get("APP_DOMAIN", "https://seriale.fun")


# ── Checkout: subskrypcja miesięczna ──────────────────────────────────────
@payments_bp.route("/api/payments/subscribe", methods=["POST"])
@login_required
def create_subscription_checkout():
    try:
        # Stwórz lub pobierz customer Stripe
        customer_id = current_user.stripe_customer_id
        if not customer_id:
            customer = stripe.Customer.create(
                email=current_user.email,
                metadata={"user_id": current_user.id, "username": current_user.username}
            )
            current_user.stripe_customer_id = customer.id
            db.session.commit()
            customer_id = customer.id

        session = stripe.checkout.Session.create(
            customer=customer_id,
            payment_method_types=["card", "blik", "p24"],
            line_items=[{
                "price": STRIPE_PRICE_MONTHLY,
                "quantity": 1,
            }],
            mode="subscription",
            success_url=f"{DOMAIN}/settings?upgraded=1",
            cancel_url=f"{DOMAIN}/settings?cancelled=1",
            locale="pl",
            metadata={"user_id": str(current_user.id), "type": "subscription"},
        )
        return jsonify({"ok": True, "url": session.url})
    except Exception as e:
        current_app.logger.error(f"[stripe] subscribe error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


# ── Checkout: jednorazowy zakup ────────────────────────────────────────────
@payments_bp.route("/api/payments/onetime", methods=["POST"])
@login_required
def create_onetime_checkout():
    try:
        customer_id = current_user.stripe_customer_id
        if not customer_id:
            customer = stripe.Customer.create(
                email=current_user.email,
                metadata={"user_id": current_user.id, "username": current_user.username}
            )
            current_user.stripe_customer_id = customer.id
            db.session.commit()
            customer_id = customer.id

        session = stripe.checkout.Session.create(
            customer=customer_id,
            payment_method_types=["card", "blik", "p24"],
            line_items=[{
                "price": STRIPE_PRICE_ONETIME,
                "quantity": 1,
            }],
            mode="payment",
            success_url=f"{DOMAIN}/settings?upgraded=1",
            cancel_url=f"{DOMAIN}/settings?cancelled=1",
            locale="pl",
            metadata={"user_id": str(current_user.id), "type": "onetime"},
        )
        return jsonify({"ok": True, "url": session.url})
    except Exception as e:
        current_app.logger.error(f"[stripe] onetime error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


# ── Portal klienta (zarządzanie subskrypcją) ───────────────────────────────
@payments_bp.route("/api/payments/portal", methods=["POST"])
@login_required
def customer_portal():
    if not current_user.stripe_customer_id:
        return jsonify({"ok": False, "error": "Brak konta Stripe"}), 400
    try:
        session = stripe.billing_portal.Session.create(
            customer=current_user.stripe_customer_id,
            return_url=f"{DOMAIN}/settings",
        )
        return jsonify({"ok": True, "url": session.url})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── Webhook Stripe ─────────────────────────────────────────────────────────
@payments_bp.route("/webhook/stripe", methods=["POST"])
def stripe_webhook():
    payload = request.get_data()
    sig_header = request.headers.get("Stripe-Signature")

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, STRIPE_WEBHOOK_SECRET
        )
    except stripe.error.SignatureVerificationError as e:
        current_app.logger.error(f"[webhook] bad signature: {e}")
        return jsonify({"error": "Invalid signature"}), 400
    except Exception as e:
        current_app.logger.error(f"[webhook] error: {e}")
        return jsonify({"error": str(e)}), 400

    event_type = event["type"]
    data = event["data"]["object"]

    current_app.logger.info(f"[webhook] event: {event_type}")

    # ── Płatność zakończona (subskrypcja lub jednorazowa) ──
    if event_type == "checkout.session.completed":
        _handle_checkout_completed(data)

    # ── Subskrypcja odnowiona ──
    elif event_type == "invoice.payment_succeeded":
        _handle_invoice_paid(data)

    # ── Subskrypcja anulowana lub wygasła ──
    elif event_type in ("customer.subscription.deleted", "customer.subscription.paused"):
        _handle_subscription_ended(data)

    # ── Płatność nieudana ──
    elif event_type == "invoice.payment_failed":
        _handle_payment_failed(data)

    return jsonify({"ok": True})


def _handle_checkout_completed(session):
    from models import User
    user_id = session.get("metadata", {}).get("user_id")
    if not user_id:
        return
    user = User.query.get(int(user_id))
    if not user:
        return

    # Zapisz subscription_id jeśli subskrypcja
    if session.get("mode") == "subscription" and session.get("subscription"):
        user.stripe_subscription_id = session["subscription"]

    user.is_pro = True
    db.session.commit()
    current_app.logger.info(f"[webhook] user {user_id} → PRO ✓")


def _handle_invoice_paid(invoice):
    from models import User
    customer_id = invoice.get("customer")
    if not customer_id:
        return
    user = User.query.filter_by(stripe_customer_id=customer_id).first()
    if user:
        user.is_pro = True
        db.session.commit()
        current_app.logger.info(f"[webhook] invoice paid → user {user.id} PRO ✓")


def _handle_subscription_ended(subscription):
    from models import User
    customer_id = subscription.get("customer")
    if not customer_id:
        return
    user = User.query.filter_by(stripe_customer_id=customer_id).first()
    if user:
        user.is_pro = False
        user.stripe_subscription_id = None
        db.session.commit()
        current_app.logger.info(f"[webhook] subscription ended → user {user.id} free")


def _handle_payment_failed(invoice):
    from models import User
    customer_id = invoice.get("customer")
    current_app.logger.warning(f"[webhook] payment failed for customer {customer_id}")
    # Opcjonalnie: możesz wysłać email z info o nieudanej płatności

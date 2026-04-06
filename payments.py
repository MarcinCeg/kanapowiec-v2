from flask import Blueprint, request, jsonify, redirect, url_for, current_app
from flask_login import login_required, current_user
from models import db, User
import stripe

payments_bp = Blueprint("payments", __name__, url_prefix="/payments")


def get_stripe():
    stripe.api_key = current_app.config["STRIPE_SECRET_KEY"]
    return stripe


@payments_bp.route("/checkout", methods=["POST"])
@login_required
def checkout():
    s = get_stripe()
    # Utwórz klienta Stripe jeśli nie ma
    if not current_user.stripe_customer_id:
        customer = s.Customer.create(
            email=current_user.email,
            metadata={"user_id": current_user.id, "username": current_user.username}
        )
        current_user.stripe_customer_id = customer.id
        db.session.commit()

    session = s.checkout.Session.create(
        customer=current_user.stripe_customer_id,
        payment_method_types=["card"],
        line_items=[{"price": current_app.config["STRIPE_PRICE_ID"], "quantity": 1}],
        mode="subscription",
        success_url=url_for("payments.success", _external=True) + "?session_id={CHECKOUT_SESSION_ID}",
        cancel_url=url_for("main.settings", _external=True),
        locale="pl",
    )
    return jsonify({"url": session.url})


@payments_bp.route("/success")
@login_required
def success():
    session_id = request.args.get("session_id")
    if session_id:
        s = get_stripe()
        session = s.checkout.Session.retrieve(session_id)
        if session.subscription:
            current_user.is_pro = True
            current_user.stripe_subscription_id = session.subscription
            db.session.commit()
    return redirect(url_for("main.index"))


@payments_bp.route("/portal", methods=["POST"])
@login_required
def portal():
    """Stripe Customer Portal — zarządzanie subskrypcją."""
    if not current_user.stripe_customer_id:
        return redirect(url_for("main.settings"))
    s = get_stripe()
    session = s.billing_portal.Session.create(
        customer=current_user.stripe_customer_id,
        return_url=url_for("main.settings", _external=True),
    )
    return redirect(session.url)


@payments_bp.route("/webhook", methods=["POST"])
def webhook():
    s = get_stripe()
    payload = request.data
    sig = request.headers.get("Stripe-Signature")
    try:
        event = s.Webhook.construct_event(
            payload, sig, current_app.config["STRIPE_WEBHOOK_SECRET"]
        )
    except Exception:
        return jsonify({"error": "bad signature"}), 400

    if event["type"] == "customer.subscription.deleted":
        sub = event["data"]["object"]
        user = User.query.filter_by(stripe_subscription_id=sub["id"]).first()
        if user:
            user.is_pro = False
            user.stripe_subscription_id = None
            db.session.commit()

    elif event["type"] == "customer.subscription.updated":
        sub = event["data"]["object"]
        user = User.query.filter_by(stripe_customer_id=sub["customer"]).first()
        if user:
            user.is_pro = sub["status"] == "active"
            db.session.commit()

    elif event["type"] == "invoice.payment_succeeded":
        inv = event["data"]["object"]
        user = User.query.filter_by(stripe_customer_id=inv["customer"]).first()
        if user and not user.is_pro:
            user.is_pro = True
            db.session.commit()

    return jsonify({"ok": True})

from flask import Blueprint, render_template, redirect, url_for, request
import uuid

landing_bp = Blueprint("landing", __name__)


@landing_bp.route("/")
def index():
    # Loguj wejście na landing (nawet jeśli redirect)
    try:
        from models import db
        from analytics_model import log_event, is_bot
        from flask import session
        from flask_login import current_user

        ua = request.headers.get('User-Agent', '')
        if not is_bot(ua):
            if 'sid' not in session:
                session['sid'] = str(uuid.uuid4())[:16]
            uid = current_user.id if current_user.is_authenticated else None
            log_event(db, uid, 'pageview', '/', request.referrer, request=request)
    except Exception:
        pass

    return redirect("/app")


@landing_bp.route("/health")
def health():
    return "OK", 200

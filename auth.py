from flask import Blueprint, render_template, redirect, url_for, request, flash, current_app
from flask_login import login_user, logout_user, login_required, current_user
from authlib.integrations.flask_client import OAuth
from sqlalchemy.exc import SQLAlchemyError
from models import db, User
import re
import secrets
from datetime import datetime, timedelta

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")
oauth = OAuth()


def init_oauth(app):
    oauth.init_app(app)
    if app.config.get("GOOGLE_CLIENT_ID") and app.config.get("GOOGLE_CLIENT_SECRET"):
        oauth.register(
            name="google",
            client_id=app.config["GOOGLE_CLIENT_ID"],
            client_secret=app.config["GOOGLE_CLIENT_SECRET"],
            server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
            client_kwargs={"scope": "openid email profile"},
        )


def username_ok(u):
    return bool(re.match(r"^[a-zA-Z0-9_\-]{3,30}$", u))


def random_color():
    colors = ["#534AB7", "#1D9E75", "#D85A30", "#BA7517", "#D4537E", "#185FA5", "#E24B4A"]
    return secrets.choice(colors)


def send_reset_email(email, token):
    """Wysyła email z linkiem do resetu hasła przez Resend."""
    try:
        import requests as req_lib
        api_key = current_app.config.get("RESEND_API_KEY")
        if not api_key:
            current_app.logger.error("RESEND_API_KEY not set")
            return False

        reset_url = url_for("auth.reset_password", token=token, _external=True)

        html = f"""
        <div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:480px;margin:0 auto;padding:32px;background:#fff">
          <div style="text-align:center;margin-bottom:24px">
            <span style="font-size:40px">🛋️</span>
            <h1 style="font-size:22px;font-weight:800;color:#0f0f11;margin:8px 0 4px">seriale.fun</h1>
            <p style="font-size:13px;color:#6b6b78;margin:0">Reset hasła</p>
          </div>
          <div style="background:#f0f0f4;border-radius:12px;padding:24px;margin-bottom:24px">
            <p style="font-size:14px;color:#0f0f11;margin:0 0 16px">Hej! Dostaliśmy prośbę o reset hasła do Twojego konta.</p>
            <p style="font-size:13px;color:#6b6b78;margin:0 0 20px">Kliknij przycisk poniżej żeby ustawić nowe hasło. Link jest ważny przez <strong>1 godzinę</strong>.</p>
            <a href="{reset_url}" style="display:block;text-align:center;background:linear-gradient(135deg,#534AB7,#1D9E75);color:#fff;text-decoration:none;padding:14px 24px;border-radius:10px;font-size:14px;font-weight:700">
              🔑 Ustaw nowe hasło
            </a>
          </div>
          <p style="font-size:11px;color:#a0a0b0;text-align:center;margin:0">
            Jeśli to nie Ty prosiłeś o reset — zignoruj tego emaila.<br>
            Link wygaśnie automatycznie po 1 godzinie.
          </p>
        </div>
        """

        r = req_lib.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "from": "seriale.fun <noreply@seriale.fun>",
                "to": [email],
                "subject": "🔑 Reset hasła — seriale.fun",
                "html": html,
            },
            timeout=10,
        )
        return r.status_code == 200
    except Exception as e:
        current_app.logger.exception(f"Email send error: {e}")
        return False


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect("/app")

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        password2 = request.form.get("password2", "")

        err = None
        if not email or "@" not in email:
            err = "Nieprawidłowy email"
        elif not username_ok(username):
            err = "Login: 3-30 znaków, tylko litery/cyfry/_/-"
        elif len(password) < 8:
            err = "Hasło min. 8 znaków"
        elif password != password2:
            err = "Hasła się różnią"
        elif User.query.filter_by(email=email).first():
            err = "Ten email jest już zajęty"
        elif User.query.filter_by(username=username).first():
            err = "Ten login jest już zajęty"

        if err:
            flash(err, "error")
            return render_template("auth/register.html", email=email, username=username)

        try:
            from models import UserStats, UserTitle
            user = User(email=email, username=username, avatar_color=random_color())
            user.set_password(password)
            db.session.add(user)
            db.session.flush()
            db.session.add(UserStats(user_id=user.id))
            db.session.add(UserTitle(user_id=user.id, title_id="kanapowiec", is_active=True))
            db.session.commit()
            login_user(user, remember=True)
            return redirect("/app")

        except SQLAlchemyError as e:
            db.session.rollback()
            current_app.logger.exception("Register DB error")
            flash(f"Błąd bazy danych: {str(e)}", "error")
            return render_template("auth/register.html", email=email, username=username), 500

        except Exception as e:
            db.session.rollback()
            current_app.logger.exception("Register unexpected error")
            flash(f"Nieoczekiwany błąd: {str(e)}", "error")
            return render_template("auth/register.html", email=email, username=username), 500

    return render_template("auth/register.html")


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect("/app")

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        remember = bool(request.form.get("remember"))

        user = User.query.filter_by(email=email).first()
        if not user or not user.check_password(password):
            flash("Nieprawidłowy email lub hasło", "error")
            return render_template("auth/login.html", email=email)

        try:
            from models import UserStats
            if not user.stats:
                db.session.add(UserStats(user_id=user.id))
                db.session.commit()
        except Exception:
            db.session.rollback()

        login_user(user, remember=remember)
        return redirect("/app")

    return render_template("auth/login.html")


# ── RESET HASŁA ───────────────────────────────────────────────────────────────

@auth_bp.route("/forgot", methods=["GET", "POST"])
def forgot_password():
    if current_user.is_authenticated:
        return redirect("/app")

    sent = False
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        user = User.query.filter_by(email=email).first()

        if user:
            # Generuj token
            token = secrets.token_urlsafe(32)
            user.reset_token = token
            user.reset_token_expires = datetime.utcnow() + timedelta(hours=1)
            db.session.commit()
            send_reset_email(email, token)

        # Zawsze pokazuj success (nie ujawniaj czy email istnieje)
        sent = True

    return render_template("auth/forgot.html", sent=sent)


@auth_bp.route("/reset/<token>", methods=["GET", "POST"])
def reset_password(token):
    if current_user.is_authenticated:
        return redirect("/app")

    user = User.query.filter_by(reset_token=token).first()

    # Sprawdź czy token jest ważny
    if not user or not user.reset_token_expires or user.reset_token_expires < datetime.utcnow():
        return render_template("auth/reset.html", error="Link wygasł lub jest nieprawidłowy. Poproś o nowy.")

    if request.method == "POST":
        password = request.form.get("password", "")
        password2 = request.form.get("password2", "")

        if len(password) < 8:
            return render_template("auth/reset.html", token=token, error="Hasło min. 8 znaków")
        if password != password2:
            return render_template("auth/reset.html", token=token, error="Hasła się różnią")

        user.set_password(password)
        user.reset_token = None
        user.reset_token_expires = None
        db.session.commit()

        login_user(user, remember=True)
        flash("Hasło zostało zmienione!", "success")
        return redirect("/app")

    return render_template("auth/reset.html", token=token)


# ── GOOGLE OAUTH ──────────────────────────────────────────────────────────────

@auth_bp.route("/google")
def google_login():
    if not current_app.config.get("GOOGLE_CLIENT_ID"):
        flash("Google OAuth nie jest skonfigurowane", "error")
        return redirect(url_for("auth.login"))
    redirect_uri = url_for("auth.google_callback", _external=True)
    return oauth.google.authorize_redirect(redirect_uri)


@auth_bp.route("/google/callback")
def google_callback():
    try:
        token = oauth.google.authorize_access_token()
        info = token.get("userinfo") or oauth.google.userinfo()
        google_id = info["sub"]
        email = info.get("email", "").lower()

        from models import UserStats, UserTitle
        user = User.query.filter_by(google_id=google_id).first()
        if not user:
            user = User.query.filter_by(email=email).first()
            if user:
                user.google_id = google_id
            else:
                base = re.sub(r"[^a-zA-Z0-9]", "", email.split("@")[0])[:20] or "user"
                username = base
                i = 1
                while User.query.filter_by(username=username).first():
                    username = f"{base}{i}"
                    i += 1
                user = User(email=email, username=username, google_id=google_id, avatar_color=random_color())
                db.session.add(user)
                db.session.flush()
                db.session.add(UserStats(user_id=user.id))
                db.session.add(UserTitle(user_id=user.id, title_id="kanapowiec", is_active=True))
            db.session.commit()

        login_user(user, remember=True)
        return redirect("/app")

    except Exception as e:
        db.session.rollback()
        current_app.logger.exception("Google OAuth error")
        flash(f"Logowanie Google nie powiodło się: {str(e)}", "error")
        return redirect(url_for("auth.login"))


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("landing.index"))


@auth_bp.route("/settings/username", methods=["POST"])
@login_required
def change_username():
    new_name = request.form.get("username", "").strip()
    if not username_ok(new_name):
        flash("Nieprawidłowy login", "error")
    elif User.query.filter(User.username == new_name, User.id != current_user.id).first():
        flash("Login zajęty", "error")
    else:
        current_user.username = new_name
        db.session.commit()
        flash("Login zmieniony!", "success")
    return redirect(url_for("main.settings"))


@auth_bp.route("/account", methods=["DELETE"])
@login_required
def delete_account():
    user = current_user
    logout_user()
    db.session.delete(user)
    db.session.commit()
    return "", 200

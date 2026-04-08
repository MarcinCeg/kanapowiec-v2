from flask import Blueprint, render_template, redirect, url_for, request, flash, current_app
from flask_login import login_user, logout_user, login_required, current_user
from authlib.integrations.flask_client import OAuth
from sqlalchemy.exc import SQLAlchemyError
from models import db, User
import re
import secrets

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
            flash(f"Błąd bazy danych podczas rejestracji: {str(e)}", "error")
            return render_template("auth/register.html", email=email, username=username), 500

        except Exception as e:
            db.session.rollback()
            current_app.logger.exception("Register unexpected error")
            flash(f"Nieoczekiwany błąd rejestracji: {str(e)}", "error")
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

                user = User(
                    email=email,
                    username=username,
                    google_id=google_id,
                    avatar_color=random_color()
                )
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

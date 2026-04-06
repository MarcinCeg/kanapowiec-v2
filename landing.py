from flask import Blueprint, render_template, redirect, url_for
from flask_login import current_user

landing_bp = Blueprint("landing", __name__)

@landing_bp.route("/")
def index():
    if current_user.is_authenticated:
        return redirect(url_for("main.index"))
    return render_template("landing.html")

@landing_bp.route("/health")
def health():
    return "OK", 200

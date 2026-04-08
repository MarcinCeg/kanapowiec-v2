from flask import Blueprint, render_template, redirect, url_for
from flask_login import current_user

landing_bp = Blueprint("landing", __name__)

@landing_bp.route("/")
def index():
    return redirect("/app")

@landing_bp.route("/health")
def health():
    return "OK", 200

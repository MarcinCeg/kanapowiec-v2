from flask import Flask
from flask_login import LoginManager
from models import db, User
from config import Config

def create_app(config=None):
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config.from_object(config or Config)

    # Fix postgres URL for SQLAlchemy (Railway używa postgres://)
    db_url = app.config.get("SQLALCHEMY_DATABASE_URI", "")
    if db_url.startswith("postgres://"):
        app.config["SQLALCHEMY_DATABASE_URI"] = db_url.replace("postgres://", "postgresql://", 1)

    # DB
    db.init_app(app)

    # Login manager
    login_manager = LoginManager()
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message = "Zaloguj się żeby korzystać z trackera"
    login_manager.login_message_category = "info"

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    # OAuth
    from auth import init_oauth
    init_oauth(app)

    # Blueprinty
    from auth import auth_bp
    from routes import main_bp
    from landing import landing_bp
    app.register_blueprint(landing_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)

    # Stripe webhook (jeśli skonfigurowany)
    try:
        from payments import payments_bp
        app.register_blueprint(payments_bp)
    except ImportError:
        pass

    # Twórz tabele + migracje
    with app.app_context():
        try:
            db.create_all()
        except Exception as e:
            print(f"Warning: db.create_all() failed: {e}")

        # Migracja: dodaj kolumny reset hasła jeśli nie istnieją
        try:
            with db.engine.connect() as conn:
                conn.execute(db.text(
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS reset_token VARCHAR(100)"
                ))
                conn.execute(db.text(
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS reset_token_expires TIMESTAMP"
                ))
                conn.commit()
                print("[migration] reset_token columns OK")
        except Exception as e:
            print(f"[migration] Warning: {e}")

    return app

if __name__ == "__main__":
    app = create_app()
    app.run(debug=True, port=8080, host="0.0.0.0")

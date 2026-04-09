"""Migracja: dodaje kolumny reset_token i reset_token_expires do tabeli users"""
from app import create_app
from models import db

app = create_app()
with app.app_context():
    with db.engine.connect() as conn:
        try:
            conn.execute(db.text("ALTER TABLE users ADD COLUMN reset_token VARCHAR(100)"))
            print("Dodano: reset_token")
        except Exception as e:
            print(f"reset_token już istnieje lub błąd: {e}")
        try:
            conn.execute(db.text("ALTER TABLE users ADD COLUMN reset_token_expires TIMESTAMP"))
            print("Dodano: reset_token_expires")
        except Exception as e:
            print(f"reset_token_expires już istnieje lub błąd: {e}")
        conn.commit()
    print("Migracja zakończona!")

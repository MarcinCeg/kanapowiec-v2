from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
import bcrypt

db = SQLAlchemy()

# ── Stałe ──────────────────────────────────────────────────────────────────
PLATFORMS = ["netflix","hbo","disney","prime","appletv","skyshowtime",
             "canalplus","player","polsat","tvpvod"]
PNAMES = {"netflix":"Netflix","hbo":"HBO Max","disney":"Disney+",
          "prime":"Amazon Prime","appletv":"Apple TV+","skyshowtime":"SkyShowtime",
          "canalplus":"Canal+","player":"Player","polsat":"Polsat Box Go","tvpvod":"TVP VOD"}
PCOLORS = {"netflix":"#E50914","hbo":"#8B5CF6","disney":"#113CCF","prime":"#00A8E1",
           "appletv":"#555","skyshowtime":"#0070C9","canalplus":"#003366",
           "player":"#E4002B","polsat":"#E31E24","tvpvod":"#003F87"}

TMDB_PROVIDER_IDS = {
    "netflix":8,"hbo":384,"disney":337,"prime":119,"appletv":350,
    "skyshowtime":1773,"canalplus":190,"player":None,"polsat":None,"tvpvod":None,
}

TMDB_GENRES = {
    28:"Akcja",12:"Przygodowy",16:"Animacja",35:"Komedia",80:"Kryminał",
    99:"Dokumentalny",18:"Dramat",10751:"Familijny",14:"Fantasy",36:"Historyczny",
    27:"Horror",9648:"Tajemnica",10749:"Romans",878:"Sci-Fi",53:"Thriller",
    10752:"Wojenny",37:"Western",10765:"Sci-Fi i fantasy",10768:"Wojenny i polityczny",
}

COUNTRY_NAMES = {
    "US":"USA","GB":"Wielka Brytania","KR":"Korea","PL":"Polska","JP":"Japonia",
    "ES":"Hiszpania","DE":"Niemcy","FR":"Francja","IT":"Włochy","SE":"Szwecja",
    "DK":"Dania","NO":"Norwegia","AU":"Australia","CA":"Kanada","TR":"Turcja",
}

# Śmieszne tytuły — (id, nazwa, opis, ikona, warunek_typ, warunek_wartość)
TITLES = [
    ("kanapowiec",    "Kanapowiec",        "Zarejestrowany użytkownik",          "🛋️",  "register",   0),
    ("pierwsze10",    "Pierwsze kroki",    "Obejrzał 10 odcinków",               "👣",  "episodes",   10),
    ("popcorn100",    "Popcorn Destroyer", "100 odcinków za sobą",               "🍿",  "episodes",   100),
    ("popcorn500",    "Serial Maniak",     "500 odcinków — to już choroba",      "🎬",  "episodes",   500),
    ("godziny50",     "Nałogowiec",        "50 godzin oglądania",                "⏱️",  "hours",      50),
    ("godziny200",    "Zawodowy Widz",     "200 godzin na kanapie",              "📺",  "hours",      200),
    ("godziny500",    "Mistrz Kanapy",     "500 godzin! Legendarna postać",      "🏆",  "hours",      500),
    ("binge_sezon",   "Binge Sprinter",    "Cały sezon w jeden dzień",           "⚡",  "binge",      1),
    ("seria7",        "Niezłomny",         "7 dni z rzędu bez przerwy",          "🔥",  "streak",     7),
    ("seria30",       "Żyję na kanapie",   "30 dni aktywności z rzędu",          "💪",  "streak",     30),
    ("nocna",         "Nocna Zmiana",      "Odcinek po 3:00 w nocy",             "🌙",  "night",      1),
    ("kraje10",       "Globtroter",        "Seriale z 10 różnych krajów",        "🌍",  "countries",  10),
    ("ukonczyl10",    "Kolekcjoner",       "10 ukończonych seriali",             "🎯",  "finished",   10),
    ("ukonczyl25",    "Encyklopedia",      "25 ukończonych seriali",             "📚",  "finished",   25),
    ("platforma5",    "Multiplatformowiec","Aktywny na 5 platformach",           "📡",  "platforms",  5),
    ("ocena9",        "Wybredny Widz",     "Tylko seriale z oceną 9+",           "⭐",  "quality",    9),
]


# ── Modele ──────────────────────────────────────────────────────────────────
class User(UserMixin, db.Model):
    __tablename__ = "users"
    id            = db.Column(db.Integer, primary_key=True)
    email         = db.Column(db.String(255), unique=True, nullable=False, index=True)
    username      = db.Column(db.String(40), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=True)   # None = OAuth only
    google_id     = db.Column(db.String(255), unique=True, nullable=True)
    avatar_color  = db.Column(db.String(7), default="#534AB7")
    is_pro        = db.Column(db.Boolean, default=False)
    stripe_customer_id    = db.Column(db.String(255), nullable=True)
    stripe_subscription_id= db.Column(db.String(255), nullable=True)
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)
    last_seen     = db.Column(db.DateTime, default=datetime.utcnow)

    # Relacje
    watching    = db.relationship("Watching",   back_populates="user", cascade="all, delete-orphan")
    watched     = db.relationship("Watched",    back_populates="user", cascade="all, delete-orphan")
    candidates  = db.relationship("Candidate",  back_populates="user", cascade="all, delete-orphan")
    platforms   = db.relationship("UserPlatform", back_populates="user", cascade="all, delete-orphan")
    titles      = db.relationship("UserTitle",  back_populates="user", cascade="all, delete-orphan")
    stats       = db.relationship("UserStats",  back_populates="user", uselist=False, cascade="all, delete-orphan")

    def set_password(self, password):
        self.password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

    def check_password(self, password):
        if not self.password_hash:
            return False
        return bcrypt.checkpw(password.encode(), self.password_hash.encode())

    @property
    def initials(self):
        parts = self.username.split()
        if len(parts) >= 2:
            return (parts[0][0] + parts[1][0]).upper()
        return self.username[:2].upper()

    @property
    def active_title(self):
        """Zwraca aktywny tytuł użytkownika."""
        t = UserTitle.query.filter_by(user_id=self.id, is_active=True).first()
        if t:
            for tid, name, desc, ico, *_ in TITLES:
                if tid == t.title_id:
                    return {"id": tid, "name": name, "ico": ico}
        return {"id": "kanapowiec", "name": "Kanapowiec", "ico": "🛋️"}

    def watching_count(self):
        return Watching.query.filter_by(user_id=self.id).count()

    def watched_count(self):
        return Watched.query.filter_by(user_id=self.id).count()

    def candidates_count(self):
        return Candidate.query.filter_by(user_id=self.id).count()

    def can_add_watching(self):
        from flask import current_app
        if self.is_pro:
            return True
        return self.watching_count() < current_app.config["FREE_WATCHING_LIMIT"]

    def can_add_watched(self):
        from flask import current_app
        if self.is_pro:
            return True
        return self.watched_count() < current_app.config["FREE_WATCHED_LIMIT"]

    def can_add_candidate(self):
        from flask import current_app
        if self.is_pro:
            return True
        return self.candidates_count() < current_app.config["FREE_CANDIDATES_LIMIT"]


class Serial(db.Model):
    """Cache danych o serialu z TMDB — współdzielony między użytkownikami."""
    __tablename__ = "serials"
    id              = db.Column(db.Integer, primary_key=True)
    tmdb_id         = db.Column(db.Integer, unique=True, nullable=True, index=True)
    nazwa           = db.Column(db.String(255), nullable=False, index=True)
    cover           = db.Column(db.String(500), default="")
    imdb_url        = db.Column(db.String(500), default="")
    imdb_rating     = db.Column(db.Float, nullable=True)
    imdb_desc       = db.Column(db.Text, default="")
    genres          = db.Column(db.String(255), default="")   # CSV
    countries       = db.Column(db.String(255), default="")   # CSV
    episodes_count  = db.Column(db.Integer, default=0)
    seasons_count   = db.Column(db.Integer, default=0)
    episode_runtime = db.Column(db.Integer, default=45)       # minuty
    status          = db.Column(db.String(50), default="")    # Ended/Returning Series
    first_air_date  = db.Column(db.String(20), default="")
    updated_at      = db.Column(db.DateTime, default=datetime.utcnow)

    @property
    def genres_list(self):
        return [g for g in self.genres.split(",") if g]

    @property
    def countries_list(self):
        return [c for c in self.countries.split(",") if c]

    @property
    def total_hours(self):
        return round(self.episodes_count * self.episode_runtime / 60, 1)


class Watching(db.Model):
    __tablename__ = "watching"
    id          = db.Column(db.Integer, primary_key=True)
    user_id     = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    serial_id   = db.Column(db.Integer, db.ForeignKey("serials.id"), nullable=False)
    platforma   = db.Column(db.String(30), default="")
    last_title  = db.Column(db.String(255), default="")
    last_link   = db.Column(db.String(500), default="#")
    last_date   = db.Column(db.String(30), default="")
    date_label  = db.Column(db.String(50), default="")
    is_new_today= db.Column(db.Boolean, default=False)
    added_at    = db.Column(db.DateTime, default=datetime.utcnow)

    user   = db.relationship("User", back_populates="watching")
    serial = db.relationship("Serial")


class Watched(db.Model):
    __tablename__ = "watched"
    id          = db.Column(db.Integer, primary_key=True)
    user_id     = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    serial_id   = db.Column(db.Integer, db.ForeignKey("serials.id"), nullable=False)
    platforma   = db.Column(db.String(30), default="")
    date_finished = db.Column(db.String(20), default="")
    finished_at = db.Column(db.DateTime, default=datetime.utcnow)

    user   = db.relationship("User", back_populates="watched")
    serial = db.relationship("Serial")


class Candidate(db.Model):
    __tablename__ = "candidates"
    id        = db.Column(db.Integer, primary_key=True)
    user_id   = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    serial_id = db.Column(db.Integer, db.ForeignKey("serials.id"), nullable=False)
    platform  = db.Column(db.String(30), default="")
    added_at  = db.Column(db.DateTime, default=datetime.utcnow)

    user   = db.relationship("User", back_populates="candidates")
    serial = db.relationship("Serial")


class UserPlatform(db.Model):
    __tablename__ = "user_platforms"
    id        = db.Column(db.Integer, primary_key=True)
    user_id   = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    platform  = db.Column(db.String(30), nullable=False)

    user = db.relationship("User", back_populates="platforms")


class UserTitle(db.Model):
    __tablename__ = "user_titles"
    id        = db.Column(db.Integer, primary_key=True)
    user_id   = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    title_id  = db.Column(db.String(50), nullable=False)
    earned_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_active = db.Column(db.Boolean, default=False)

    user = db.relationship("User", back_populates="titles")


class UserStats(db.Model):
    """Zagregowane statystyki użytkownika — aktualizowane asynchronicznie."""
    __tablename__ = "user_stats"
    id              = db.Column(db.Integer, primary_key=True)
    user_id         = db.Column(db.Integer, db.ForeignKey("users.id"), unique=True, nullable=False)
    total_episodes  = db.Column(db.Integer, default=0)
    total_hours     = db.Column(db.Float, default=0.0)
    total_finished  = db.Column(db.Integer, default=0)
    current_streak  = db.Column(db.Integer, default=0)
    longest_streak  = db.Column(db.Integer, default=0)
    last_watch_date = db.Column(db.String(20), default="")
    countries_count = db.Column(db.Integer, default=0)
    updated_at      = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User", back_populates="stats")


class GlobalNowosci(db.Model):
    """Cache nowości z TMDB — współdzielony, odświeżany co godzinę."""
    __tablename__ = "global_nowosci"
    id          = db.Column(db.Integer, primary_key=True)
    platform    = db.Column(db.String(30), nullable=False, index=True)
    serial_id   = db.Column(db.Integer, db.ForeignKey("serials.id"), nullable=False)
    date_added  = db.Column(db.String(20), default="")
    date_label  = db.Column(db.String(50), default="")
    refreshed_at= db.Column(db.DateTime, default=datetime.utcnow)

    serial = db.relationship("Serial")


class RankingCache(db.Model):
    """Cache rankingu — przeliczany raz na godzinę."""
    __tablename__ = "ranking_cache"
    id          = db.Column(db.Integer, primary_key=True)
    period      = db.Column(db.String(20), nullable=False)   # week/month/all
    user_id     = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    position    = db.Column(db.Integer, nullable=False)
    score       = db.Column(db.Float, default=0.0)
    episodes    = db.Column(db.Integer, default=0)
    hours       = db.Column(db.Float, default=0.0)
    updated_at  = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User")

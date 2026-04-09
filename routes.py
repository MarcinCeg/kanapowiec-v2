from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash
from flask_login import login_required, current_user
from models import (db, Serial, Watching, Watched, Candidate, UserPlatform,
                    GlobalNowosci, PLATFORMS, PNAMES, PCOLORS)
from tmdb_service import search_or_create_serial, get_last_episode, fmt_date
from titles_service import recalculate_stats, set_active_title, get_all_titles_for_user
from datetime import datetime
import threading

main_bp = Blueprint("main", __name__)

DEFAULT_PLATFORMS = ['netflix', 'player', 'disney']

def get_user_platforms():
    if not current_user.is_authenticated:
        return []
    return [p.platform for p in current_user.platforms]

def date_sort_key(w):
    label = w.date_label or ""
    if "🔥" in label: return 0
    if "wczoraj" in label: return 1
    import re
    m = re.search(r"(\d+) dni temu", label)
    if m: return int(m.group(1)) + 1
    return 99


@main_bp.route("/app")
def index():
    # Tryb gościa
    if not current_user.is_authenticated:
        nowosci = []
        rows = (GlobalNowosci.query
                .filter(GlobalNowosci.platform.in_(DEFAULT_PLATFORMS))
                .order_by(GlobalNowosci.date_added.desc())
                .all())
        seen = set()
        for row in rows:
            if row.serial_id not in seen:
                seen.add(row.serial_id)
                nowosci.append(row)
        return render_template("index.html",
            ogladam=[], kandydaci=[], obejrzane=[],
            nowosci=nowosci, platforms=PLATFORMS, pnames=PNAMES, pcolors=PCOLORS,
            user_platforms=[], limits={
                "watching":   {"used": 0, "max": 10},
                "watched":    {"used": 0, "max": 30},
                "candidates": {"used": 0, "max": 10},
            },
            is_pro=False,
        )

    # Zalogowany użytkownik
    user_platforms = get_user_platforms()
    ogladam = sorted(current_user.watching, key=date_sort_key)
    kandydaci = sorted(current_user.candidates,
                       key=lambda k: k.serial.imdb_rating or 0, reverse=True)
    obejrzane = sorted(current_user.watched,
                       key=lambda w: w.finished_at, reverse=True)

    watching_ids  = {w.serial_id for w in current_user.watching}
    candidate_ids = {c.serial_id for c in current_user.candidates}
    nowosci = []
    platforms_to_show = user_platforms if user_platforms else DEFAULT_PLATFORMS
    rows = (GlobalNowosci.query
            .filter(GlobalNowosci.platform.in_(platforms_to_show))
            .order_by(GlobalNowosci.date_added.desc())
            .all())
    seen = set()
    for row in rows:
        if row.serial_id not in watching_ids and row.serial_id not in candidate_ids:
            if row.serial_id not in seen:
                seen.add(row.serial_id)
                nowosci.append(row)

    limits = {
        "watching":   {"used": current_user.watching_count(),  "max": 10 if not current_user.is_pro else None},
        "watched":    {"used": current_user.watched_count(),   "max": 30 if not current_user.is_pro else None},
        "candidates": {"used": current_user.candidates_count(),"max": 10 if not current_user.is_pro else None},
    }

    return render_template("index.html",
        ogladam=ogladam, kandydaci=kandydaci, obejrzane=obejrzane,
        nowosci=nowosci, platforms=PLATFORMS, pnames=PNAMES, pcolors=PCOLORS,
        user_platforms=user_platforms, limits=limits,
        is_pro=current_user.is_pro,
    )


@main_bp.route("/api/ogladam", methods=["POST"])
@login_required
def add_ogladam():
    if not current_user.can_add_watching():
        return jsonify({"ok": False, "error": "limit", "msg": "Limit 10 seriali w planie Free"}), 403
    nazwa = request.json.get("nazwa","").strip()
    if not nazwa:
        return jsonify({"ok": False})
    serial = search_or_create_serial(nazwa)
    Candidate.query.filter_by(user_id=current_user.id, serial_id=serial.id).delete()
    if not Watching.query.filter_by(user_id=current_user.id, serial_id=serial.id).first():
        db.session.add(Watching(user_id=current_user.id, serial_id=serial.id))
        db.session.commit()
    return jsonify({"ok": True, "serial": {
        "id": serial.id, "nazwa": serial.nazwa,
        "cover": serial.cover, "imdb_rating": serial.imdb_rating,
        "imdb_url": serial.imdb_url,
    }})


@main_bp.route("/api/ogladam/<int:serial_id>", methods=["DELETE"])
@login_required
def del_ogladam(serial_id):
    w = Watching.query.filter_by(user_id=current_user.id, serial_id=serial_id).first_or_404()
    db.session.delete(w)
    if current_user.can_add_watched():
        if not Watched.query.filter_by(user_id=current_user.id, serial_id=serial_id).first():
            db.session.add(Watched(
                user_id=current_user.id, serial_id=serial_id,
                platforma=w.platforma,
                date_finished=datetime.now().strftime("%d.%m.%Y"),
            ))
    db.session.commit()
    threading.Thread(target=_recalc_bg, args=(current_user.id,), daemon=True).start()
    return jsonify({"ok": True})


@main_bp.route("/api/ogladam/<int:serial_id>/odcinek", methods=["POST"])
@login_required
def mark_odcinek(serial_id):
    w = Watching.query.filter_by(user_id=current_user.id, serial_id=serial_id).first_or_404()
    w.date_label   = None
    w.is_new_today = False
    db.session.commit()
    return jsonify({"ok": True})


@main_bp.route("/api/ogladam/<int:serial_id>/refresh", methods=["POST"])
@login_required
def refresh_odcinek(serial_id):
    w = Watching.query.filter_by(user_id=current_user.id, serial_id=serial_id).first_or_404()
    ep = get_last_episode(w.serial)
    if ep:
        w.last_title  = ep["title"]
        w.last_link   = ep["link"]
        w.last_date   = ep["date_raw"]
        w.date_label  = ep["date_label"]
        w.is_new_today= ep["is_new"]
        db.session.commit()
    return jsonify({"ok": True, "ep": ep})


@main_bp.route("/api/kandydaci", methods=["POST"])
@login_required
def add_kandydat():
    if not current_user.can_add_candidate():
        return jsonify({"ok": False, "error": "limit", "msg": "Limit 10 kandydatów w planie Free"}), 403
    data = request.json
    nazwa = data.get("nazwa","").strip()
    if not nazwa:
        return jsonify({"ok": False})
    serial = search_or_create_serial(nazwa)
    if not Candidate.query.filter_by(user_id=current_user.id, serial_id=serial.id).first():
        db.session.add(Candidate(user_id=current_user.id, serial_id=serial.id,
                                 platform=data.get("platform","")))
        db.session.commit()
    return jsonify({"ok": True, "serial": {
        "id": serial.id, "nazwa": serial.nazwa,
        "cover": serial.cover, "imdb_rating": serial.imdb_rating,
    }})


@main_bp.route("/api/kandydaci/<int:serial_id>", methods=["DELETE"])
@login_required
def del_kandydat(serial_id):
    Candidate.query.filter_by(user_id=current_user.id, serial_id=serial_id).delete()
    db.session.commit()
    return jsonify({"ok": True})


@main_bp.route("/api/kandydaci/<int:serial_id>/promote", methods=["POST"])
@login_required
def promote_kandydat(serial_id):
    if not current_user.can_add_watching():
        return jsonify({"ok": False, "error": "limit"}), 403
    c = Candidate.query.filter_by(user_id=current_user.id, serial_id=serial_id).first_or_404()
    db.session.delete(c)
    if not Watching.query.filter_by(user_id=current_user.id, serial_id=serial_id).first():
        db.session.add(Watching(user_id=current_user.id, serial_id=serial_id, platforma=c.platform))
    db.session.commit()
    return jsonify({"ok": True})


@main_bp.route("/api/obejrzane/<int:serial_id>", methods=["DELETE"])
@login_required
def del_obejrzane(serial_id):
    Watched.query.filter_by(user_id=current_user.id, serial_id=serial_id).delete()
    db.session.commit()
    return jsonify({"ok": True})


@main_bp.route("/api/obejrzane/<int:serial_id>/restore", methods=["POST"])
@login_required
def restore_obejrzane(serial_id):
    if not current_user.can_add_watching():
        return jsonify({"ok": False, "error": "limit"}), 403
    w = Watched.query.filter_by(user_id=current_user.id, serial_id=serial_id).first_or_404()
    db.session.delete(w)
    if not Watching.query.filter_by(user_id=current_user.id, serial_id=serial_id).first():
        db.session.add(Watching(user_id=current_user.id, serial_id=serial_id))
    db.session.commit()
    return jsonify({"ok": True})


@main_bp.route("/api/platformy", methods=["POST"])
@login_required
def set_platformy():
    new_platforms = request.json.get("platformy", [])
    UserPlatform.query.filter_by(user_id=current_user.id).delete()
    for p in new_platforms:
        if p in PLATFORMS:
            db.session.add(UserPlatform(user_id=current_user.id, platform=p))
    db.session.commit()
    return jsonify({"ok": True})


@main_bp.route("/api/refresh/odcinki", methods=["POST"])
@login_required
def refresh_all_odcinki():
    def _task(user_id):
        from app import create_app
        app = create_app()
        with app.app_context():
            from models import User, Watching
            user = User.query.get(user_id)
            if not user: return
            for w in user.watching:
                ep = get_last_episode(w.serial)
                if ep:
                    w.last_title   = ep["title"]
                    w.last_link    = ep["link"]
                    w.last_date    = ep["date_raw"]
                    w.date_label   = ep["date_label"]
                    w.is_new_today = ep["is_new"]
            db.session.commit()
    threading.Thread(target=_task, args=(current_user.id,), daemon=True).start()
    return jsonify({"ok": True})


@main_bp.route("/api/refresh/nowosci", methods=["POST"])
@login_required
def refresh_nowosci():
    user_platforms = get_user_platforms()
    def _task(platforms):
        from app import create_app
        app = create_app()
        with app.app_context():
            from tmdb_service import refresh_nowosci_for_platform
            for p in platforms:
                refresh_nowosci_for_platform(p)
    threading.Thread(target=_task, args=(user_platforms,), daemon=True).start()
    return jsonify({"ok": True})


@main_bp.route("/api/titles/<title_id>/activate", methods=["POST"])
@login_required
def activate_title(title_id):
    ok = set_active_title(current_user, title_id)
    return jsonify({"ok": ok})


@main_bp.route("/settings")
@login_required
def settings():
    all_titles = get_all_titles_for_user(current_user)
    return render_template("settings.html",
        all_titles=all_titles, pnames=PNAMES, pcolors=PCOLORS, platforms=PLATFORMS,
        user_platforms=get_user_platforms())


@main_bp.route("/ranking")
@login_required
def ranking():
    from models import UserStats, User, UserTitle, TITLES
    period = request.args.get("period","all")
    rows = (db.session.query(User, UserStats)
            .join(UserStats, UserStats.user_id == User.id)
            .order_by(UserStats.total_hours.desc())
            .limit(100).all())
    ranking_data = []
    for pos, (user, stats) in enumerate(rows, 1):
        ranking_data.append({
            "pos": pos, "user": user, "stats": stats,
            "title": user.active_title,
            "is_me": user.id == current_user.id,
        })
    my_pos = next((r["pos"] for r in ranking_data if r["is_me"]), None)
    return render_template("ranking.html",
        ranking=ranking_data, period=period, my_pos=my_pos,
        pnames=PNAMES, pcolors=PCOLORS)


@main_bp.route("/stats")
@login_required
def stats():
    from titles_service import get_all_titles_for_user
    all_titles = get_all_titles_for_user(current_user)
    stats = current_user.stats

    all_serials = []
    for w in sorted(current_user.watching + current_user.watched,
                    key=lambda x: x.serial.nazwa.lower() if x.serial else ""):
        if w.serial:
            all_serials.append({
                "serial": w.serial,
                "status": "ogladam" if isinstance(w, Watching) else "obejrzane",
            })

    top = sorted(all_serials, key=lambda x: x["serial"].total_hours, reverse=True)[:10]

    genre_counts = {}
    for item in all_serials:
        for g in item["serial"].genres_list:
            if g: genre_counts[g] = genre_counts.get(g, 0) + 1
    genre_counts = dict(sorted(genre_counts.items(), key=lambda x: -x[1])[:8])

    total_min = (stats.total_hours or 0) * 60
    fun_facts = _fun_facts(total_min, len(all_serials), stats.total_episodes or 0)

    return render_template("stats.html",
        stats=stats, all_serials=all_serials, top_by_time=top,
        genre_counts=genre_counts, fun_facts=fun_facts,
        all_titles=all_titles, is_pro=current_user.is_pro,
        pnames=PNAMES, pcolors=PCOLORS)


def _fun_facts(total_min, serial_count, episode_count):
    km = round(total_min * 5 / 60)
    hours = round(total_min / 60, 1)
    days  = round(total_min / 60 / 24, 1)
    years = round(days / 365, 2)
    return [
        {"ico":"🌍", "text": f"Przeszedłbyś {km:,} km — to {round(km/40075*100,1)}% obwodu Ziemi"},
        {"ico":"🚂", "text": f"Pociągiem Warszawa-Paryż zdążyłbyś pojechać {round(hours/8):.0f} razy"},
        {"ico":"🍕", "text": f"Zjadłbyś {round(episode_count*0.3):,} pizz (po 1 na 3 odcinki)"},
        {"ico":"☕", "text": f"Wypiłbyś {round(episode_count*1.5):,} kaw — po 1,5 na odcinek"},
        {"ico":"💤", "text": f"To {years} roku życia poświęconego na seriale"},
        {"ico":"🌙", "text": f"Przespałbyś {round(days*0.33):.0f} nocy zamiast oglądać"},
        {"ico":"✈️", "text": f"Lotem z Warszawy do NY ({hours:.0f}h) — {round(hours/9):.0f} razy"},
    ]


@main_bp.route("/ai")
@login_required
def ai_page():
    return render_template("ai.html")

@main_bp.route("/api/ai/rekomenduj", methods=["POST"])
@login_required
def ai_rekomenduj():
    try:
        from ai_service import agent_rekomenduj
        nastroj = request.json.get("nastroj","").strip()
        if not nastroj:
            return jsonify({"ok":False,"error":"Brak nastroju"})
        wynik = agent_rekomenduj(current_user, nastroj)
        return jsonify({"ok":True,"wynik":wynik})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@main_bp.route("/api/ai/szybkie", methods=["GET"])
@login_required
def ai_szybkie():
    try:
        from ai_service import szybkie_rekomendacje
        wynik = szybkie_rekomendacje(current_user)
        return jsonify({"ok":True,"rekomendacje":wynik})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@main_bp.route("/api/ai/podsumowanie", methods=["GET"])
@login_required
def ai_podsumowanie():
    try:
        from ai_service import podsumowanie_tygodnia
        tekst = podsumowanie_tygodnia(current_user)
        return jsonify({"ok":True,"tekst":tekst})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@main_bp.route("/api/export/csv")
@login_required
def export_csv():
    if not current_user.is_pro:
        return jsonify({"error":"Pro only"}), 403
    import csv, io
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Status","Tytuł","Platformy","Ocena","Sezony","Odcinki","Godziny"])
    for w in current_user.watching:
        if w.serial:
            s = w.serial
            writer.writerow(["Oglądam",s.nazwa,w.platforma,s.imdb_rating,s.seasons_count,s.episodes_count,s.total_hours])
    for w in current_user.watched:
        if w.serial:
            s = w.serial
            writer.writerow(["Obejrzane",s.nazwa,w.platforma,s.imdb_rating,s.seasons_count,s.episodes_count,s.total_hours])
    from flask import Response
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition":f"attachment;filename=kanapowiec_{current_user.username}.csv"}
    )




# ── Admin Panel ───────────────────────────────────────────────────────────────
ADMIN_EMAIL = "mg.ceglinski@gmail.com"

@main_bp.route("/admin")
@login_required
def admin():
    if current_user.email != ADMIN_EMAIL:
        return redirect("/app")
    from models import User, Serial, Watching, Watched, Candidate, GlobalNowosci, UserStats
    from sqlalchemy import func
    from datetime import date

    users = User.query.order_by(User.created_at.desc()).all()

    # Overview stats
    total_users    = User.query.count()
    pro_users      = User.query.filter_by(is_pro=True).count()
    total_serials  = Serial.query.count()
    total_nowosci  = GlobalNowosci.query.count()
    total_watching = Watching.query.count()
    total_watched  = Watched.query.count()
    total_candidates = Candidate.query.count()

    today = date.today().strftime("%Y-%m-%d")
    active_today = User.query.filter(
        User.last_seen >= today
    ).count()

    # Top seriale w oglądam
    top_watching = (db.session.query(Serial, func.count(Watching.id).label('cnt'))
        .join(Watching, Watching.serial_id == Serial.id)
        .group_by(Serial.id)
        .order_by(func.count(Watching.id).desc())
        .limit(10).all())

    # Top seriale obejrzane
    top_watched = (db.session.query(Serial, func.count(Watched.id).label('cnt'))
        .join(Watched, Watched.serial_id == Serial.id)
        .group_by(Serial.id)
        .order_by(func.count(Watched.id).desc())
        .limit(10).all())

    stats = {
        "total_users": total_users,
        "pro_users": pro_users,
        "active_today": active_today,
        "total_serials": total_serials,
        "total_nowosci": total_nowosci,
        "total_watching": total_watching,
        "total_watched": total_watched,
        "total_candidates": total_candidates,
    }

    from datetime import datetime
    return render_template("admin.html",
        users=users, stats=stats,
        top_watching=top_watching,
        top_watched=top_watched,
        now=datetime.now().strftime("%d.%m.%Y %H:%M"),
    )

def _recalc_bg(user_id):
    from app import create_app
    app = create_app()
    with app.app_context():
        from models import User
        from titles_service import recalculate_stats
        user = User.query.get(user_id)
        if user:
            recalculate_stats(user)

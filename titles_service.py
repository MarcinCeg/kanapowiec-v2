from models import db, UserTitle, UserStats, TITLES
from datetime import datetime


def check_and_grant_titles(user):
    """Sprawdza i przyznaje nowe tytuły użytkownikowi."""
    stats = user.stats
    if not stats:
        return []
    earned = {t.title_id for t in user.titles}
    new_titles = []

    watching_count  = user.watching_count()
    watched_count   = user.watched_count()
    total_episodes  = stats.total_episodes
    total_hours     = stats.total_hours
    total_finished  = stats.total_finished
    streak          = stats.current_streak
    countries_count = stats.countries_count

    for tid, name, desc, ico, cond_type, cond_val in TITLES:
        if tid in earned:
            continue
        granted = False
        if cond_type == "register":
            granted = True
        elif cond_type == "episodes" and total_episodes >= cond_val:
            granted = True
        elif cond_type == "hours" and total_hours >= cond_val:
            granted = True
        elif cond_type == "finished" and total_finished >= cond_val:
            granted = True
        elif cond_type == "streak" and streak >= cond_val:
            granted = True
        elif cond_type == "countries" and countries_count >= cond_val:
            granted = True
        elif cond_type == "platforms":
            from models import UserPlatform
            plat_cnt = UserPlatform.query.filter_by(user_id=user.id).count()
            granted = plat_cnt >= cond_val

        if granted:
            t = UserTitle(user_id=user.id, title_id=tid)
            # Automatycznie ustaw jako aktywny jeśli to pierwszy
            existing = UserTitle.query.filter_by(user_id=user.id).count()
            if existing == 0:
                t.is_active = True
            db.session.add(t)
            new_titles.append({"id": tid, "name": name, "ico": ico, "desc": desc})

    if new_titles:
        db.session.commit()
    return new_titles


def recalculate_stats(user):
    """Przelicza statystyki użytkownika na podstawie jego list."""
    from models import Watched, Watching, UserStats
    stats = user.stats
    if not stats:
        stats = UserStats(user_id=user.id)
        db.session.add(stats)

    # Odcinki i godziny — z obejrzanych i oglądanych
    total_episodes = 0
    total_hours = 0.0
    countries = set()

    all_items = (
        [(w.serial, True)  for w in user.watched] +
        [(w.serial, False) for w in user.watching]
    )

    for serial, is_finished in all_items:
        if not serial: continue
        eps = serial.episodes_count or 0
        runtime = serial.episode_runtime or 45
        total_episodes += eps
        total_hours += round(eps * runtime / 60, 2)
        for c in serial.countries_list:
            if c: countries.add(c)

    stats.total_episodes = total_episodes
    stats.total_hours    = round(total_hours, 1)
    stats.total_finished = len([w for w in user.watched if w.serial])
    stats.countries_count= len(countries)
    stats.updated_at     = datetime.utcnow()
    db.session.commit()

    # Sprawdź nowe tytuły
    new_titles = check_and_grant_titles(user)
    return new_titles


def set_active_title(user, title_id):
    """Ustawia aktywny tytuł użytkownika."""
    from models import UserTitle
    # Sprawdź czy użytkownik ma ten tytuł
    t = UserTitle.query.filter_by(user_id=user.id, title_id=title_id).first()
    if not t:
        return False
    UserTitle.query.filter_by(user_id=user.id).update({"is_active": False})
    t.is_active = True
    db.session.commit()
    return True


def get_all_titles_for_user(user):
    """Zwraca wszystkie tytuły z informacją czy odblokowane."""
    earned = {t.title_id: t for t in user.titles}
    result = []
    for tid, name, desc, ico, cond_type, cond_val in TITLES:
        t = earned.get(tid)
        result.append({
            "id": tid, "name": name, "desc": desc, "ico": ico,
            "earned": t is not None,
            "is_active": t.is_active if t else False,
            "earned_at": t.earned_at.strftime("%d.%m.%Y") if t else None,
            "cond_type": cond_type, "cond_val": cond_val,
        })
    return result

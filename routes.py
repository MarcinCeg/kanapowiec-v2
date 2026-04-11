# ═══════════════════════════════════════════════════════════
# PATCH dla routes.py — dodaj do funkcji stats()
# Nowe zmienne wymagane przez stats.html
# ═══════════════════════════════════════════════════════════
#
# Znajdź w routes.py funkcję def stats() i dodaj przed return render_template:

from datetime import datetime, timedelta
from collections import defaultdict

# ── Aktywność tygodniowa (ostatnie 7 dni) ──
week_activity = [0] * 7  # [Pn, Wt, Śr, Cz, Pt, So, Nd]
try:
    today = datetime.utcnow().date()
    for i in range(7):
        day = today - timedelta(days=6-i)
        # Zlicz odcinki obejrzane tego dnia z tabeli episode_history lub watched
        # Jeśli nie masz tabeli episode_history, użyj mock:
        count = db.session.execute(
            text("SELECT COUNT(*) FROM episode_history WHERE user_id=:uid AND DATE(watched_at)=:d"),
            {'uid': current_user.id, 'd': day}
        ).scalar() or 0
        week_activity[i] = int(count)
except Exception:
    week_activity = [0, 0, 0, 0, 0, 0, 0]

# ── Heatmapa roczna (52 tygodnie × 7 dni = 364 wartości 0-1) ──
heat_data = None  # None = JS użyje losowych danych jako mock
try:
    today = datetime.utcnow().date()
    start = today - timedelta(weeks=52)
    heat_counts = defaultdict(int)
    rows = db.session.execute(
        text("SELECT DATE(watched_at) as d, COUNT(*) as c FROM episode_history WHERE user_id=:uid AND watched_at>=:start GROUP BY d"),
        {'uid': current_user.id, 'start': start}
    ).fetchall()
    for row in rows:
        heat_counts[str(row.d)] = row.c
    max_c = max(heat_counts.values(), default=1)
    heat_data = []
    for w in range(52):
        for d in range(7):
            day = start + timedelta(weeks=w, days=d)
            v = heat_counts.get(str(day), 0)
            heat_data.append(round(v / max_c, 2) if max_c > 0 else 0)
except Exception:
    heat_data = None

# ── Platformy ──
platform_counts = {}
try:
    from collections import Counter
    plat_list = [w.platforma for w in current_user.watching_list if w.platforma]
    plat_list += [w.platforma for w in current_user.watched_list if hasattr(w,'platforma') and w.platforma]
    platform_counts = dict(Counter(plat_list).most_common(8))
except Exception:
    platform_counts = {}

# ── Ranking globalny (tylko PRO) ──
global_ranking = []
if current_user.is_pro:
    try:
        from models import User
        top_users = User.query.filter(User.total_hours > 0).order_by(User.total_hours.desc()).limit(10).all()
        global_ranking = top_users
    except Exception:
        global_ranking = []

# ── Liczba zdobytych odznak ──
earned_count = sum(1 for t in all_titles if t.earned) if all_titles else 0

# ── Dodaj do render_template: ──
# return render_template("stats.html",
#     ...istniejące parametry...,
#     week_activity=week_activity,
#     heat_data=heat_data,
#     platform_counts=platform_counts,
#     global_ranking=global_ranking,
#     earned_count=earned_count,
# )

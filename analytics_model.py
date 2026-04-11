# analytics_model.py — model i funkcje pomocnicze analityki

import hashlib, re, json
from datetime import datetime

# ── Parser User-Agent ─────────────────────────────────────────────────────────

def parse_ua(ua_string):
    """Wyciągnij browser, OS, device z User-Agent string."""
    if not ua_string:
        return 'unknown', 'unknown', 'desktop'

    ua = ua_string.lower()

    # Device type
    if any(x in ua for x in ['iphone','android','mobile','blackberry','windows phone']):
        device = 'mobile'
    elif any(x in ua for x in ['ipad','tablet','kindle']):
        device = 'tablet'
    else:
        device = 'desktop'

    # Browser
    if 'edg/' in ua or 'edge/' in ua:
        browser = 'Edge'
    elif 'opr/' in ua or 'opera' in ua:
        browser = 'Opera'
    elif 'chrome/' in ua and 'chromium' not in ua:
        browser = 'Chrome'
    elif 'firefox/' in ua:
        browser = 'Firefox'
    elif 'safari/' in ua and 'chrome' not in ua:
        browser = 'Safari'
    elif 'msie' in ua or 'trident/' in ua:
        browser = 'IE'
    else:
        browser = 'Other'

    # OS
    if 'windows nt 10' in ua:
        os = 'Windows 10/11'
    elif 'windows nt 6' in ua:
        os = 'Windows 7/8'
    elif 'windows' in ua:
        os = 'Windows'
    elif 'mac os x' in ua or 'macos' in ua:
        os = 'macOS'
    elif 'iphone os' in ua:
        m = re.search(r'iphone os (\d+)', ua)
        os = f'iOS {m.group(1)}' if m else 'iOS'
    elif 'ipad' in ua:
        m = re.search(r'cpu os (\d+)', ua)
        os = f'iPadOS {m.group(1)}' if m else 'iPadOS'
    elif 'android' in ua:
        m = re.search(r'android (\d+)', ua)
        os = f'Android {m.group(1)}' if m else 'Android'
    elif 'linux' in ua:
        os = 'Linux'
    else:
        os = 'Other'

    return browser, os, device


def hash_ip(ip):
    """Zahashuj IP dla prywatności (nie przechowujemy raw IP)."""
    if not ip:
        return None
    return hashlib.sha256(ip.encode()).hexdigest()[:16]


def get_session_id(request):
    """Pobierz lub wygeneruj session ID."""
    try:
        from flask import session
        if 'sid' not in session:
            import uuid
            session['sid'] = str(uuid.uuid4())[:16]
        return session['sid']
    except Exception:
        return None


def log_event(db, user_id, event_type, page=None, referrer=None,
              data=None, request=None, duration_ms=None):
    """Zapisz zdarzenie do bazy danych."""
    try:
        ua_str = request.headers.get('User-Agent', '') if request else ''
        browser, os_name, device = parse_ua(ua_str)
        ip = request.headers.get('X-Forwarded-For', request.remote_addr) if request else None
        if ip and ',' in ip:
            ip = ip.split(',')[0].strip()
        session_id = get_session_id(request) if request else None
        data_json = json.dumps(data, ensure_ascii=False) if data else None

        params = {
            'uid': user_id, 'sid': session_id, 'etype': event_type,
            'page': page[:255] if page else None,
            'ref': referrer[:255] if referrer else None,
            'data': data_json,
            'ip': hash_ip(ip),
            'ua': ua_str[:500] if ua_str else None,
            'device': device, 'browser': browser, 'os': os_name,
            'dur': duration_ms,
        }
        sql = db.text("""
            INSERT INTO user_events
                (user_id, session_id, event_type, page, referrer, data,
                 ip_hash, user_agent, device_type, browser, os, duration_ms, created_at)
            VALUES
                (:uid, :sid, :etype, :page, :ref, CAST(:data AS JSONB),
                 :ip, :ua, :device, :browser, :os, :dur, NOW())
        """)
        with db.engine.connect() as conn:
            conn.execute(sql, params)
            conn.commit()
    except Exception:
        # Nie crashuj aplikacji z powodu analityki
        pass


# ── Zapytania analityczne ─────────────────────────────────────────────────────

def get_analytics_summary(db, days=30):
    """Pobierz podsumowanie analityczne."""
    try:
        result = {}

        # Pageviews per page
        rows = db.engine.execute(f"""
            SELECT page, COUNT(*) as cnt,
                   COUNT(DISTINCT session_id) as sessions,
                   COUNT(DISTINCT user_id) as users
            FROM user_events
            WHERE event_type='pageview'
              AND created_at >= NOW() - INTERVAL '{days} days'
            GROUP BY page ORDER BY cnt DESC LIMIT 20
        """).fetchall()
        result['top_pages'] = [dict(r) for r in rows]

        # Urządzenia
        rows = db.engine.execute(f"""
            SELECT device_type, COUNT(DISTINCT session_id) as sessions
            FROM user_events
            WHERE created_at >= NOW() - INTERVAL '{days} days'
              AND device_type IS NOT NULL
            GROUP BY device_type ORDER BY sessions DESC
        """).fetchall()
        result['devices'] = [dict(r) for r in rows]

        # Przeglądarki
        rows = db.engine.execute(f"""
            SELECT browser, COUNT(DISTINCT session_id) as sessions
            FROM user_events
            WHERE created_at >= NOW() - INTERVAL '{days} days'
              AND browser IS NOT NULL
            GROUP BY browser ORDER BY sessions DESC
        """).fetchall()
        result['browsers'] = [dict(r) for r in rows]

        # Systemy operacyjne
        rows = db.engine.execute(f"""
            SELECT os, COUNT(DISTINCT session_id) as sessions
            FROM user_events
            WHERE created_at >= NOW() - INTERVAL '{days} days'
              AND os IS NOT NULL
            GROUP BY os ORDER BY sessions DESC
        """).fetchall()
        result['os_list'] = [dict(r) for r in rows]

        # Aktywność dziennie (ostatnie N dni)
        rows = db.engine.execute(f"""
            SELECT DATE(created_at) as day,
                   COUNT(*) as events,
                   COUNT(DISTINCT session_id) as sessions,
                   COUNT(DISTINCT user_id) as users
            FROM user_events
            WHERE created_at >= NOW() - INTERVAL '{days} days'
            GROUP BY DATE(created_at) ORDER BY day
        """).fetchall()
        result['daily'] = [dict(r) for r in rows]

        # Akcje użytkowników (co klikają)
        rows = db.engine.execute(f"""
            SELECT event_type, COUNT(*) as cnt
            FROM user_events
            WHERE event_type != 'pageview'
              AND created_at >= NOW() - INTERVAL '{days} days'
            GROUP BY event_type ORDER BY cnt DESC LIMIT 20
        """).fetchall()
        result['actions'] = [dict(r) for r in rows]

        # Ścieżki nawigacji (skąd → dokąd)
        rows = db.engine.execute(f"""
            SELECT referrer, page, COUNT(*) as cnt
            FROM user_events
            WHERE event_type='pageview'
              AND referrer IS NOT NULL
              AND referrer NOT LIKE '%seriale.fun%'
              AND created_at >= NOW() - INTERVAL '{days} days'
            GROUP BY referrer, page ORDER BY cnt DESC LIMIT 15
        """).fetchall()
        result['external_referrers'] = [dict(r) for r in rows]

        # Wewnętrzne ścieżki (skąd → dokąd wewnątrz aplikacji)
        rows = db.engine.execute(f"""
            SELECT
                LAG(page) OVER (PARTITION BY session_id ORDER BY created_at) as from_page,
                page as to_page,
                COUNT(*) as cnt
            FROM user_events
            WHERE event_type='pageview'
              AND created_at >= NOW() - INTERVAL '{days} days'
            GROUP BY from_page, to_page
            HAVING COUNT(*) > 5 AND LAG(page) OVER (PARTITION BY session_id ORDER BY created_at) IS NOT NULL
            ORDER BY cnt DESC LIMIT 20
        """).fetchall()
        result['navigation_paths'] = [dict(r) for r in rows]

        # Sesje — długość i głębokość
        rows = db.engine.execute(f"""
            SELECT
                session_id,
                user_id,
                COUNT(*) as page_views,
                MIN(created_at) as start_time,
                MAX(created_at) as end_time,
                EXTRACT(EPOCH FROM (MAX(created_at) - MIN(created_at)))/60 as duration_min,
                device_type, browser, os
            FROM user_events
            WHERE event_type='pageview'
              AND created_at >= NOW() - INTERVAL '{days} days'
            GROUP BY session_id, user_id, device_type, browser, os
            ORDER BY start_time DESC LIMIT 50
        """).fetchall()
        result['recent_sessions'] = [dict(r) for r in rows]

        # Użytkownicy którzy utknęli (dużo czasu na jednej stronie bez akcji)
        rows = db.engine.execute(f"""
            SELECT page, AVG(duration_ms)/1000 as avg_time_sec, COUNT(*) as cnt
            FROM user_events
            WHERE duration_ms IS NOT NULL AND duration_ms > 0
              AND created_at >= NOW() - INTERVAL '{days} days'
            GROUP BY page ORDER BY avg_time_sec DESC LIMIT 10
        """).fetchall()
        result['slow_pages'] = [dict(r) for r in rows]

        # Bounce rate (sesje z 1 odsłoną)
        rows = db.engine.execute(f"""
            SELECT
                COUNT(CASE WHEN page_views = 1 THEN 1 END)::float / COUNT(*) as bounce_rate,
                COUNT(*) as total_sessions,
                AVG(page_views) as avg_pages_per_session
            FROM (
                SELECT session_id, COUNT(*) as page_views
                FROM user_events WHERE event_type='pageview'
                  AND created_at >= NOW() - INTERVAL '{days} days'
                GROUP BY session_id
            ) sub
        """).fetchone()
        if rows:
            result['session_stats'] = dict(rows)

        # Nowi vs powracający (ostatnie 30 dni)
        rows = db.engine.execute(f"""
            SELECT
                COUNT(DISTINCT CASE WHEN u.created_at >= NOW() - INTERVAL '{days} days' THEN e.user_id END) as new_users,
                COUNT(DISTINCT CASE WHEN u.created_at < NOW() - INTERVAL '{days} days' THEN e.user_id END) as returning_users
            FROM user_events e
            LEFT JOIN users u ON u.id = e.user_id
            WHERE e.created_at >= NOW() - INTERVAL '{days} days'
              AND e.user_id IS NOT NULL
        """).fetchone()
        if rows:
            result['new_vs_returning'] = dict(rows)

        # Goście vs zalogowani
        rows = db.engine.execute(f"""
            SELECT
                COUNT(DISTINCT CASE WHEN user_id IS NULL THEN session_id END) as guest_sessions,
                COUNT(DISTINCT CASE WHEN user_id IS NOT NULL THEN session_id END) as logged_sessions
            FROM user_events
            WHERE created_at >= NOW() - INTERVAL '{days} days'
        """).fetchone()
        if rows:
            result['guest_vs_logged'] = dict(rows)

        # Konwersja: goście którzy się zalogowali w tej sesji
        rows = db.engine.execute(f"""
            SELECT COUNT(DISTINCT session_id) as converted_sessions
            FROM user_events
            WHERE event_type = 'user_register'
              AND created_at >= NOW() - INTERVAL '{days} days'
        """).fetchone()
        if rows:
            result['registrations'] = dict(rows)

        # Godziny aktywności (kiedy użytkownicy są online)
        rows = db.engine.execute(f"""
            SELECT EXTRACT(HOUR FROM created_at) as hour,
                   COUNT(*) as events
            FROM user_events
            WHERE created_at >= NOW() - INTERVAL '{days} days'
            GROUP BY hour ORDER BY hour
        """).fetchall()
        result['hourly'] = [dict(r) for r in rows]

        # Dni tygodnia
        rows = db.engine.execute(f"""
            SELECT EXTRACT(DOW FROM created_at) as dow,
                   COUNT(*) as events
            FROM user_events
            WHERE created_at >= NOW() - INTERVAL '{days} days'
            GROUP BY dow ORDER BY dow
        """).fetchall()
        result['day_of_week'] = [dict(r) for r in rows]

        # Najczęstsze akcje per user (power users)
        rows = db.engine.execute(f"""
            SELECT u.username, u.email, u.is_pro,
                   COUNT(*) as total_events,
                   COUNT(DISTINCT DATE(e.created_at)) as active_days,
                   MAX(e.created_at) as last_seen
            FROM user_events e
            JOIN users u ON u.id = e.user_id
            WHERE e.created_at >= NOW() - INTERVAL '{days} days'
            GROUP BY u.id, u.username, u.email, u.is_pro
            ORDER BY total_events DESC LIMIT 20
        """).fetchall()
        result['power_users'] = [dict(r) for r in rows]

        # Funnel: tracker → rejestracja
        rows = db.engine.execute(f"""
            SELECT
                COUNT(DISTINCT CASE WHEN page='/app' THEN session_id END) as saw_tracker,
                COUNT(DISTINCT CASE WHEN page='/auth/register' THEN session_id END) as went_to_register,
                COUNT(DISTINCT CASE WHEN event_type='user_register' THEN session_id END) as registered
            FROM user_events
            WHERE created_at >= NOW() - INTERVAL '{days} days'
        """).fetchone()
        if rows:
            result['funnel'] = dict(rows)

        return result

    except Exception as e:
        print(f"[analytics] get_analytics_summary error: {e}")
        return {}

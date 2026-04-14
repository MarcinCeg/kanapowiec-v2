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
        pass


# ── Helper ────────────────────────────────────────────────────────────────────

def _q(conn, db, sql_str, params=None):
    """Wykonaj zapytanie i zwróć listę słowników (SQLAlchemy 2.x)."""
    rows = conn.execute(db.text(sql_str), params or {})
    keys = list(rows.keys())
    return [dict(zip(keys, row)) for row in rows]


def _q1(conn, db, sql_str, params=None):
    """Wykonaj zapytanie i zwróć jeden słownik lub None."""
    rows = conn.execute(db.text(sql_str), params or {})
    keys = list(rows.keys())
    row = rows.fetchone()
    return dict(zip(keys, row)) if row else None


# ── Zapytania analityczne ─────────────────────────────────────────────────────

def get_analytics_summary(db, days=30):
    """Pobierz podsumowanie analityczne."""
    try:
        result = {}
        p = {'days': days}  # parametr bezpieczny — używamy :days zamiast f-string

        with db.engine.connect() as conn:

            # Pageviews per page
            result['top_pages'] = _q(conn, db, """
                SELECT page, COUNT(*) as cnt,
                       COUNT(DISTINCT session_id) as sessions,
                       COUNT(DISTINCT user_id) as users
                FROM user_events
                WHERE event_type='pageview'
                  AND created_at >= NOW() - INTERVAL '1 day' * :days
                GROUP BY page ORDER BY cnt DESC LIMIT 20
            """, p)

            # Urządzenia
            result['devices'] = _q(conn, db, """
                SELECT device_type, COUNT(DISTINCT session_id) as sessions
                FROM user_events
                WHERE created_at >= NOW() - INTERVAL '1 day' * :days
                  AND device_type IS NOT NULL
                GROUP BY device_type ORDER BY sessions DESC
            """, p)

            # Przeglądarki
            result['browsers'] = _q(conn, db, """
                SELECT browser, COUNT(DISTINCT session_id) as sessions
                FROM user_events
                WHERE created_at >= NOW() - INTERVAL '1 day' * :days
                  AND browser IS NOT NULL
                GROUP BY browser ORDER BY sessions DESC
            """, p)

            # Systemy operacyjne
            result['os_list'] = _q(conn, db, """
                SELECT os, COUNT(DISTINCT session_id) as sessions
                FROM user_events
                WHERE created_at >= NOW() - INTERVAL '1 day' * :days
                  AND os IS NOT NULL
                GROUP BY os ORDER BY sessions DESC
            """, p)

            # Aktywność dziennie
            result['daily'] = _q(conn, db, """
                SELECT DATE(created_at) as day,
                       COUNT(*) as events,
                       COUNT(DISTINCT session_id) as sessions,
                       COUNT(DISTINCT user_id) as users
                FROM user_events
                WHERE created_at >= NOW() - INTERVAL '1 day' * :days
                GROUP BY DATE(created_at) ORDER BY day
            """, p)

            # Akcje użytkowników
            result['actions'] = _q(conn, db, """
                SELECT event_type, COUNT(*) as cnt
                FROM user_events
                WHERE event_type != 'pageview'
                  AND created_at >= NOW() - INTERVAL '1 day' * :days
                GROUP BY event_type ORDER BY cnt DESC LIMIT 20
            """, p)

            # Zewnętrzne referrery
            result['external_referrers'] = _q(conn, db, """
                SELECT referrer, page, COUNT(*) as cnt
                FROM user_events
                WHERE event_type='pageview'
                  AND referrer IS NOT NULL
                  AND referrer NOT LIKE '%seriale.fun%'
                  AND created_at >= NOW() - INTERVAL '1 day' * :days
                GROUP BY referrer, page ORDER BY cnt DESC LIMIT 15
            """, p)

            # Wewnętrzne ścieżki nawigacji
            result['navigation_paths'] = _q(conn, db, """
                SELECT from_page, to_page, cnt FROM (
                    SELECT
                        LAG(page) OVER (PARTITION BY session_id ORDER BY created_at) as from_page,
                        page as to_page,
                        COUNT(*) OVER (PARTITION BY
                            LAG(page) OVER (PARTITION BY session_id ORDER BY created_at),
                            page
                        ) as cnt
                    FROM user_events
                    WHERE event_type='pageview'
                      AND created_at >= NOW() - INTERVAL '1 day' * :days
                ) sub
                WHERE from_page IS NOT NULL AND cnt > 5
                GROUP BY from_page, to_page, cnt
                ORDER BY cnt DESC LIMIT 20
            """, p)

            # Ostatnie sesje
            result['recent_sessions'] = _q(conn, db, """
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
                  AND created_at >= NOW() - INTERVAL '1 day' * :days
                GROUP BY session_id, user_id, device_type, browser, os
                ORDER BY start_time DESC LIMIT 50
            """, p)

            # Wolne strony
            result['slow_pages'] = _q(conn, db, """
                SELECT page, AVG(duration_ms)/1000 as avg_time_sec, COUNT(*) as cnt
                FROM user_events
                WHERE duration_ms IS NOT NULL AND duration_ms > 0
                  AND created_at >= NOW() - INTERVAL '1 day' * :days
                GROUP BY page ORDER BY avg_time_sec DESC LIMIT 10
            """, p)

            # Bounce rate + statystyki sesji
            row = _q1(conn, db, """
                SELECT
                    COUNT(CASE WHEN page_views = 1 THEN 1 END)::float / NULLIF(COUNT(*), 0) as bounce_rate,
                    COUNT(*) as total_sessions,
                    AVG(page_views) as avg_pages_per_session
                FROM (
                    SELECT session_id, COUNT(*) as page_views
                    FROM user_events WHERE event_type='pageview'
                      AND created_at >= NOW() - INTERVAL '1 day' * :days
                    GROUP BY session_id
                ) sub
            """, p)
            result['session_stats'] = row or {
                'bounce_rate': 0, 'total_sessions': 0, 'avg_pages_per_session': 0
            }

            # Nowi vs powracający
            row = _q1(conn, db, """
                SELECT
                    COUNT(DISTINCT CASE WHEN u.created_at >= NOW() - INTERVAL '1 day' * :days
                          THEN e.user_id END) as new_users,
                    COUNT(DISTINCT CASE WHEN u.created_at < NOW() - INTERVAL '1 day' * :days
                          THEN e.user_id END) as returning_users
                FROM user_events e
                LEFT JOIN users u ON u.id = e.user_id
                WHERE e.created_at >= NOW() - INTERVAL '1 day' * :days
                  AND e.user_id IS NOT NULL
            """, p)
            result['new_vs_returning'] = row or {'new_users': 0, 'returning_users': 0}

            # Goście vs zalogowani
            row = _q1(conn, db, """
                SELECT
                    COUNT(DISTINCT CASE WHEN user_id IS NULL THEN session_id END) as guest_sessions,
                    COUNT(DISTINCT CASE WHEN user_id IS NOT NULL THEN session_id END) as logged_sessions
                FROM user_events
                WHERE created_at >= NOW() - INTERVAL '1 day' * :days
            """, p)
            result['guest_vs_logged'] = row or {'guest_sessions': 0, 'logged_sessions': 0}

            # Rejestracje
            row = _q1(conn, db, """
                SELECT COUNT(DISTINCT session_id) as converted_sessions
                FROM user_events
                WHERE event_type = 'user_register'
                  AND created_at >= NOW() - INTERVAL '1 day' * :days
            """, p)
            result['registrations'] = row or {'converted_sessions': 0}

            # Godziny aktywności
            result['hourly'] = _q(conn, db, """
                SELECT EXTRACT(HOUR FROM created_at) as hour,
                       COUNT(*) as events
                FROM user_events
                WHERE created_at >= NOW() - INTERVAL '1 day' * :days
                GROUP BY hour ORDER BY hour
            """, p)

            # Dni tygodnia
            result['day_of_week'] = _q(conn, db, """
                SELECT EXTRACT(DOW FROM created_at) as dow,
                       COUNT(*) as events
                FROM user_events
                WHERE created_at >= NOW() - INTERVAL '1 day' * :days
                GROUP BY dow ORDER BY dow
            """, p)

            # Power users
            result['power_users'] = _q(conn, db, """
                SELECT u.username, u.email, u.is_pro,
                       COUNT(*) as total_events,
                       COUNT(DISTINCT DATE(e.created_at)) as active_days,
                       MAX(e.created_at) as last_seen
                FROM user_events e
                JOIN users u ON u.id = e.user_id
                WHERE e.created_at >= NOW() - INTERVAL '1 day' * :days
                GROUP BY u.id, u.username, u.email, u.is_pro
                ORDER BY total_events DESC LIMIT 20
            """, p)

            # Funnel
            row = _q1(conn, db, """
                SELECT
                    COUNT(DISTINCT CASE WHEN page='/app' THEN session_id END) as saw_tracker,
                    COUNT(DISTINCT CASE WHEN page='/auth/register' THEN session_id END) as went_to_register,
                    COUNT(DISTINCT CASE WHEN event_type='user_register' THEN session_id END) as registered
                FROM user_events
                WHERE created_at >= NOW() - INTERVAL '1 day' * :days
            """, p)
            result['funnel'] = row or {'saw_tracker': 0, 'went_to_register': 0, 'registered': 0}

        return result

    except Exception as e:
        print(f"[analytics] get_analytics_summary error: {e}")
        # Zwróć pusty dict z wymaganymi kluczami żeby admin.html nie crashował
        return {
            'session_stats': {'bounce_rate': 0, 'total_sessions': 0, 'avg_pages_per_session': 0},
            'top_pages': [], 'devices': [], 'browsers': [], 'os_list': [],
            'daily': [], 'actions': [], 'external_referrers': [], 'navigation_paths': [],
            'recent_sessions': [], 'slow_pages': [], 'new_vs_returning': {},
            'guest_vs_logged': {}, 'registrations': {}, 'hourly': [],
            'day_of_week': [], 'power_users': [], 'funnel': {},
        }

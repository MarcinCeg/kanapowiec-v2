# analytics_model.py — model i funkcje pomocnicze analityki (v2 — pełna wersja)

import hashlib, re, json
from datetime import datetime


# ── Parser User-Agent ─────────────────────────────────────────────────────────

def parse_ua(ua_string):
    if not ua_string:
        return 'unknown', 'unknown', 'desktop'
    ua = ua_string.lower()
    if any(x in ua for x in ['iphone', 'android', 'mobile', 'blackberry', 'windows phone']):
        device = 'mobile'
    elif any(x in ua for x in ['ipad', 'tablet', 'kindle']):
        device = 'tablet'
    else:
        device = 'desktop'
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
    if not ip:
        return None
    return hashlib.sha256(ip.encode()).hexdigest()[:16]


def get_session_id(request):
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
    """Zapisz zdarzenie. Błędy logowane (nie połykane) — łatwiej debugować."""
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
            'data': data_json, 'ip': hash_ip(ip),
            'ua': ua_str[:500] if ua_str else None,
            'device': device, 'browser': browser, 'os': os_name, 'dur': duration_ms,
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
    except Exception as e:
        print(f"[analytics] log_event error ({event_type}): {e}")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _q(conn, db, sql_str, params=None):
    rows = conn.execute(db.text(sql_str), params or {})
    keys = list(rows.keys())
    return [dict(zip(keys, row)) for row in rows]


def _q1(conn, db, sql_str, params=None):
    rows = conn.execute(db.text(sql_str), params or {})
    keys = list(rows.keys())
    row = rows.fetchone()
    return dict(zip(keys, row)) if row else None


# ── Główna funkcja analityczna ────────────────────────────────────────────────

def get_analytics_summary(db, days=30):
    empty = _empty_result()
    try:
        result = {}
        p = {'days': days}

        with db.engine.connect() as conn:

            # 1. TOP STRONY
            result['top_pages'] = _q(conn, db, """
                SELECT page, COUNT(*) as cnt,
                       COUNT(DISTINCT session_id) as sessions,
                       COUNT(DISTINCT user_id) as users
                FROM user_events
                WHERE event_type='pageview'
                  AND created_at >= NOW() - INTERVAL '1 day' * :days
                GROUP BY page ORDER BY cnt DESC LIMIT 20
            """, p)

            # 2. URZĄDZENIA
            result['devices'] = _q(conn, db, """
                SELECT device_type, COUNT(DISTINCT session_id) as sessions
                FROM user_events
                WHERE created_at >= NOW() - INTERVAL '1 day' * :days
                  AND device_type IS NOT NULL
                GROUP BY device_type ORDER BY sessions DESC
            """, p)

            # 3. PRZEGLĄDARKI
            result['browsers'] = _q(conn, db, """
                SELECT browser, COUNT(DISTINCT session_id) as sessions
                FROM user_events
                WHERE created_at >= NOW() - INTERVAL '1 day' * :days
                  AND browser IS NOT NULL
                GROUP BY browser ORDER BY sessions DESC
            """, p)

            # 4. SYSTEMY OPERACYJNE
            result['os_list'] = _q(conn, db, """
                SELECT os, COUNT(DISTINCT session_id) as sessions
                FROM user_events
                WHERE created_at >= NOW() - INTERVAL '1 day' * :days
                  AND os IS NOT NULL
                GROUP BY os ORDER BY sessions DESC
            """, p)

            # 5. AKTYWNOŚĆ DZIENNA
            result['daily'] = _q(conn, db, """
                SELECT DATE(created_at) as day,
                       COUNT(*) as events,
                       COUNT(DISTINCT session_id) as sessions,
                       COUNT(DISTINCT user_id) as users
                FROM user_events
                WHERE created_at >= NOW() - INTERVAL '1 day' * :days
                GROUP BY DATE(created_at) ORDER BY day
            """, p)

            # 6. AKCJE UŻYTKOWNIKÓW
            result['actions'] = _q(conn, db, """
                SELECT event_type, COUNT(*) as cnt,
                       COUNT(DISTINCT user_id) as unique_users
                FROM user_events
                WHERE event_type != 'pageview'
                  AND created_at >= NOW() - INTERVAL '1 day' * :days
                GROUP BY event_type ORDER BY cnt DESC LIMIT 30
            """, p)

            # 7. ZEWNĘTRZNE REFERRERY
            result['external_referrers'] = _q(conn, db, """
                SELECT referrer, COUNT(*) as cnt,
                       COUNT(DISTINCT session_id) as sessions
                FROM user_events
                WHERE event_type='pageview'
                  AND referrer IS NOT NULL
                  AND referrer NOT LIKE '%seriale.fun%'
                  AND referrer NOT LIKE '%localhost%'
                  AND created_at >= NOW() - INTERVAL '1 day' * :days
                GROUP BY referrer ORDER BY cnt DESC LIMIT 20
            """, p)

            # 8. STATYSTYKI SESJI + BOUNCE RATE (rozszerzone)
            row = _q1(conn, db, """
                SELECT
                    COALESCE(COUNT(CASE WHEN page_views=1 THEN 1 END)::float
                        / NULLIF(COUNT(*),0), 0) as bounce_rate,
                    COUNT(*) as total_sessions,
                    COALESCE(AVG(page_views), 0) as avg_pages_per_session,
                    COALESCE(AVG(duration_min), 0) as avg_session_duration_min,
                    COALESCE(MAX(duration_min), 0) as max_session_duration_min,
                    COUNT(CASE WHEN duration_min > 5  THEN 1 END) as engaged_sessions,
                    COUNT(CASE WHEN page_views >= 3   THEN 1 END) as deep_sessions
                FROM (
                    SELECT session_id, COUNT(*) as page_views,
                           EXTRACT(EPOCH FROM (MAX(created_at)-MIN(created_at)))/60 as duration_min
                    FROM user_events
                    WHERE event_type='pageview'
                      AND created_at >= NOW() - INTERVAL '1 day' * :days
                    GROUP BY session_id
                ) sub
            """, p)
            result['session_stats'] = row or {
                'bounce_rate': 0, 'total_sessions': 0, 'avg_pages_per_session': 0,
                'avg_session_duration_min': 0, 'max_session_duration_min': 0,
                'engaged_sessions': 0, 'deep_sessions': 0
            }

            # 9. NOWI VS POWRACAJĄCY
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

            # 10. GOŚCIE VS ZALOGOWANI
            row = _q1(conn, db, """
                SELECT
                    COUNT(DISTINCT CASE WHEN user_id IS NULL     THEN session_id END) as guest_sessions,
                    COUNT(DISTINCT CASE WHEN user_id IS NOT NULL THEN session_id END) as logged_sessions,
                    COALESCE(COUNT(DISTINCT CASE WHEN user_id IS NULL THEN session_id END)::float
                        / NULLIF(COUNT(DISTINCT session_id),0), 0) as guest_ratio
                FROM user_events
                WHERE created_at >= NOW() - INTERVAL '1 day' * :days
            """, p)
            result['guest_vs_logged'] = row or {'guest_sessions': 0, 'logged_sessions': 0, 'guest_ratio': 0}

            # 11. REJESTRACJE
            row = _q1(conn, db, """
                SELECT COUNT(DISTINCT session_id) as converted_sessions
                FROM user_events
                WHERE event_type='user_register'
                  AND created_at >= NOW() - INTERVAL '1 day' * :days
            """, p)
            result['registrations'] = row or {'converted_sessions': 0}

            # 12. GODZINY AKTYWNOŚCI
            result['hourly'] = _q(conn, db, """
                SELECT EXTRACT(HOUR FROM created_at) as hour,
                       COUNT(*) as events,
                       COUNT(DISTINCT session_id) as sessions,
                       COUNT(DISTINCT user_id) as users
                FROM user_events
                WHERE created_at >= NOW() - INTERVAL '1 day' * :days
                GROUP BY hour ORDER BY hour
            """, p)

            # 13. DNI TYGODNIA
            result['day_of_week'] = _q(conn, db, """
                SELECT EXTRACT(DOW FROM created_at) as dow,
                       COUNT(*) as events,
                       COUNT(DISTINCT session_id) as sessions
                FROM user_events
                WHERE created_at >= NOW() - INTERVAL '1 day' * :days
                GROUP BY dow ORDER BY dow
            """, p)

            # 14. POWER USERS (rozszerzone)
            result['power_users'] = _q(conn, db, """
                SELECT u.username, u.email, u.is_pro,
                       COUNT(*) as total_events,
                       COUNT(DISTINCT DATE(e.created_at)) as active_days,
                       COUNT(DISTINCT e.session_id) as total_sessions,
                       MAX(e.created_at) as last_seen,
                       MIN(e.created_at) as first_seen,
                       COUNT(CASE WHEN e.event_type='pageview'     THEN 1 END) as pageviews,
                       COUNT(CASE WHEN e.event_type!='pageview'    THEN 1 END) as actions,
                       COUNT(CASE WHEN e.event_type='add_serial'   THEN 1 END) as added_serials,
                       COUNT(CASE WHEN e.event_type='mark_episode' THEN 1 END) as marked_episodes
                FROM user_events e
                JOIN users u ON u.id = e.user_id
                WHERE e.created_at >= NOW() - INTERVAL '1 day' * :days
                GROUP BY u.id, u.username, u.email, u.is_pro
                ORDER BY total_events DESC LIMIT 20
            """, p)

            # 15. FUNNEL KONWERSJI
            row = _q1(conn, db, """
                SELECT
                    COUNT(DISTINCT CASE WHEN page='/app'           THEN session_id END) as saw_tracker,
                    COUNT(DISTINCT CASE WHEN page LIKE '/auth%'    THEN session_id END) as went_to_auth,
                    COUNT(DISTINCT CASE WHEN page='/auth/register' THEN session_id END) as went_to_register,
                    COUNT(DISTINCT CASE WHEN event_type='user_register' THEN session_id END) as registered
                FROM user_events
                WHERE created_at >= NOW() - INTERVAL '1 day' * :days
            """, p)
            result['funnel'] = row or {
                'saw_tracker': 0, 'went_to_auth': 0, 'went_to_register': 0, 'registered': 0
            }

            # 16. RETENCJA KOHORTOWA (tygodniowa)
            result['retention'] = _q(conn, db, """
                WITH first_seen AS (
                    SELECT user_id, MIN(DATE(created_at)) as cohort_day
                    FROM user_events WHERE user_id IS NOT NULL
                    GROUP BY user_id
                )
                SELECT
                    TO_CHAR(fs.cohort_day, 'YYYY-WW') as cohort_week,
                    COUNT(DISTINCT fs.user_id) as cohort_size,
                    COUNT(DISTINCT CASE WHEN DATE(e.created_at) BETWEEN fs.cohort_day+1
                          AND fs.cohort_day+7  THEN e.user_id END) as ret_d7,
                    COUNT(DISTINCT CASE WHEN DATE(e.created_at) BETWEEN fs.cohort_day+8
                          AND fs.cohort_day+14 THEN e.user_id END) as ret_d14,
                    COUNT(DISTINCT CASE WHEN DATE(e.created_at) BETWEEN fs.cohort_day+15
                          AND fs.cohort_day+30 THEN e.user_id END) as ret_d30,
                    ROUND(COUNT(DISTINCT CASE WHEN DATE(e.created_at) BETWEEN fs.cohort_day+1
                          AND fs.cohort_day+7  THEN e.user_id END)::numeric
                        / NULLIF(COUNT(DISTINCT fs.user_id),0)*100, 1) as pct_d7,
                    ROUND(COUNT(DISTINCT CASE WHEN DATE(e.created_at) BETWEEN fs.cohort_day+8
                          AND fs.cohort_day+14 THEN e.user_id END)::numeric
                        / NULLIF(COUNT(DISTINCT fs.user_id),0)*100, 1) as pct_d14,
                    ROUND(COUNT(DISTINCT CASE WHEN DATE(e.created_at) BETWEEN fs.cohort_day+15
                          AND fs.cohort_day+30 THEN e.user_id END)::numeric
                        / NULLIF(COUNT(DISTINCT fs.user_id),0)*100, 1) as pct_d30
                FROM first_seen fs
                JOIN user_events e ON e.user_id = fs.user_id
                WHERE fs.cohort_day >= NOW() - INTERVAL '90 days'
                GROUP BY cohort_week ORDER BY cohort_week DESC LIMIT 12
            """, p)

            # 17. OSTATNIE SESJE (szczegółowe)
            result['recent_sessions'] = _q(conn, db, """
                SELECT e.session_id, e.user_id, u.username,
                       COUNT(*) as page_views,
                       MIN(e.created_at) as start_time,
                       MAX(e.created_at) as end_time,
                       ROUND(EXTRACT(EPOCH FROM (MAX(e.created_at)-MIN(e.created_at)))/60, 1) as duration_min,
                       e.device_type, e.browser, e.os,
                       MIN(e.page) as entry_page,
                       MAX(e.page) as last_page
                FROM user_events e
                LEFT JOIN users u ON u.id = e.user_id
                WHERE e.event_type='pageview'
                  AND e.created_at >= NOW() - INTERVAL '1 day' * :days
                GROUP BY e.session_id, e.user_id, u.username, e.device_type, e.browser, e.os
                ORDER BY start_time DESC LIMIT 50
            """, p)

            # 18. WOLNE STRONY
            result['slow_pages'] = _q(conn, db, """
                SELECT page,
                       ROUND(AVG(duration_ms)::numeric/1000, 1) as avg_time_sec,
                       COUNT(*) as cnt,
                       ROUND(MAX(duration_ms)::numeric/1000, 1) as max_time_sec
                FROM user_events
                WHERE duration_ms IS NOT NULL AND duration_ms > 0
                  AND created_at >= NOW() - INTERVAL '1 day' * :days
                GROUP BY page ORDER BY avg_time_sec DESC LIMIT 10
            """, p)

            # 19. STRONY WEJŚCIA
            result['entry_pages'] = _q(conn, db, """
                SELECT page as entry_page, COUNT(*) as sessions
                FROM (
                    SELECT DISTINCT ON (session_id) session_id, page
                    FROM user_events
                    WHERE event_type='pageview'
                      AND created_at >= NOW() - INTERVAL '1 day' * :days
                    ORDER BY session_id, created_at ASC
                ) sub
                GROUP BY page ORDER BY sessions DESC LIMIT 10
            """, p)

            # 20. STRONY WYJŚCIA
            result['exit_pages'] = _q(conn, db, """
                SELECT page as exit_page, COUNT(*) as sessions
                FROM (
                    SELECT DISTINCT ON (session_id) session_id, page
                    FROM user_events
                    WHERE event_type='pageview'
                      AND created_at >= NOW() - INTERVAL '1 day' * :days
                    ORDER BY session_id, created_at DESC
                ) sub
                GROUP BY page ORDER BY sessions DESC LIMIT 10
            """, p)

            # 21. WZROST UŻYTKOWNIKÓW (dzienny)
            result['user_growth'] = _q(conn, db, """
                SELECT DATE(created_at) as day,
                       COUNT(*) as new_registrations,
                       SUM(COUNT(*)) OVER (ORDER BY DATE(created_at)) as cumulative_users
                FROM users
                WHERE created_at >= NOW() - INTERVAL '1 day' * :days
                GROUP BY DATE(created_at) ORDER BY day
            """, p)

            # 22. PRO KONWERSJE
            result['pro_conversions'] = _q(conn, db, """
                SELECT DATE(created_at) as day, COUNT(*) as new_pro_users
                FROM users
                WHERE is_pro = TRUE
                  AND created_at >= NOW() - INTERVAL '1 day' * :days
                GROUP BY DATE(created_at) ORDER BY day
            """, p)

            # 23. AKTYWNOŚĆ PER USER (tabela z pełnym profilem)
            result['user_activity'] = _q(conn, db, """
                SELECT u.id, u.username, u.email, u.is_pro,
                       u.created_at as registered_at,
                       COUNT(DISTINCT e.session_id)  as sessions_Nd,
                       COUNT(CASE WHEN e.event_type='pageview'         THEN 1 END) as pageviews_Nd,
                       COUNT(CASE WHEN e.event_type='add_serial'       THEN 1 END) as added_serials,
                       COUNT(CASE WHEN e.event_type='mark_episode'     THEN 1 END) as marked_episodes,
                       COUNT(CASE WHEN e.event_type='promote_candidate'THEN 1 END) as promotions,
                       MAX(e.created_at) as last_event,
                       COUNT(DISTINCT DATE(e.created_at)) as active_days_Nd
                FROM users u
                LEFT JOIN user_events e ON e.user_id = u.id
                    AND e.created_at >= NOW() - INTERVAL '1 day' * :days
                GROUP BY u.id, u.username, u.email, u.is_pro, u.created_at
                ORDER BY sessions_Nd DESC NULLS LAST
            """, p)

            # 24. ENGAGEMENT: akcje per sesja
            row = _q1(conn, db, """
                SELECT
                    ROUND(AVG(action_count)::numeric, 2) as avg_actions_per_session,
                    MAX(action_count) as max_actions_per_session,
                    COUNT(CASE WHEN action_count=0 THEN 1 END) as passive_sessions,
                    COUNT(CASE WHEN action_count>0 THEN 1 END) as active_sessions
                FROM (
                    SELECT session_id,
                           COUNT(CASE WHEN event_type!='pageview' THEN 1 END) as action_count
                    FROM user_events
                    WHERE created_at >= NOW() - INTERVAL '1 day' * :days
                    GROUP BY session_id
                ) sub
            """, p)
            result['engagement'] = row or {
                'avg_actions_per_session': 0, 'max_actions_per_session': 0,
                'passive_sessions': 0, 'active_sessions': 0
            }

            # 25. ŚCIEŻKI NAWIGACJI (CTE zamiast nested window functions)
            result['navigation_paths'] = _q(conn, db, """
                WITH pairs AS (
                    SELECT
                        LAG(page) OVER (PARTITION BY session_id ORDER BY created_at) as from_page,
                        page as to_page
                    FROM user_events
                    WHERE event_type='pageview'
                      AND created_at >= NOW() - INTERVAL '1 day' * :days
                )
                SELECT from_page, to_page, COUNT(*) as cnt
                FROM pairs
                WHERE from_page IS NOT NULL
                GROUP BY from_page, to_page
                HAVING COUNT(*) > 1
                ORDER BY cnt DESC LIMIT 20
            """, p)

            # 26. TRENDY: ostatnie 7d vs poprzednie 7d
            row = _q1(conn, db, """
                SELECT
                    COUNT(DISTINCT CASE WHEN created_at >= NOW()-INTERVAL '7 days'
                          THEN session_id END) as sessions_last7,
                    COUNT(DISTINCT CASE WHEN created_at >= NOW()-INTERVAL '14 days'
                          AND created_at < NOW()-INTERVAL '7 days'
                          THEN session_id END) as sessions_prev7,
                    COUNT(CASE WHEN created_at >= NOW()-INTERVAL '7 days'
                          AND event_type='pageview' THEN 1 END) as pv_last7,
                    COUNT(CASE WHEN created_at >= NOW()-INTERVAL '14 days'
                          AND created_at < NOW()-INTERVAL '7 days'
                          AND event_type='pageview' THEN 1 END) as pv_prev7,
                    COUNT(DISTINCT CASE WHEN created_at >= NOW()-INTERVAL '7 days'
                          AND user_id IS NOT NULL THEN user_id END) as users_last7,
                    COUNT(DISTINCT CASE WHEN created_at >= NOW()-INTERVAL '14 days'
                          AND created_at < NOW()-INTERVAL '7 days'
                          AND user_id IS NOT NULL THEN user_id END) as users_prev7
                FROM user_events
            """, p)
            result['trends'] = row or {
                'sessions_last7': 0, 'sessions_prev7': 0,
                'pv_last7': 0, 'pv_prev7': 0,
                'users_last7': 0, 'users_prev7': 0
            }

            # 27. BŁĘDY 404
            result['errors'] = _q(conn, db, """
                SELECT page, COUNT(*) as cnt, MAX(created_at) as last_seen
                FROM user_events
                WHERE event_type='404'
                  AND created_at >= NOW() - INTERVAL '1 day' * :days
                GROUP BY page ORDER BY cnt DESC LIMIT 10
            """, p)

            # 28. CZAS DO PIERWSZEJ AKCJI (onboarding speed)
            result['time_to_first_action'] = _q(conn, db, """
                SELECT u.username, u.created_at as registered_at,
                       MIN(e.created_at) as first_action_at,
                       ROUND(EXTRACT(EPOCH FROM (MIN(e.created_at)-u.created_at))/60, 1) as minutes_to_first_action,
                       MIN(e.event_type) as first_action_type
                FROM users u
                JOIN user_events e ON e.user_id=u.id AND e.event_type!='pageview'
                WHERE u.created_at >= NOW() - INTERVAL '1 day' * :days
                GROUP BY u.id, u.username, u.created_at
                ORDER BY registered_at DESC LIMIT 20
            """, p)

            # 29. HEATMAPA godzina×dzień tygodnia
            result['hour_dow_heatmap'] = _q(conn, db, """
                SELECT EXTRACT(DOW  FROM created_at) as dow,
                       EXTRACT(HOUR FROM created_at) as hour,
                       COUNT(*) as events
                FROM user_events
                WHERE created_at >= NOW() - INTERVAL '1 day' * :days
                GROUP BY dow, hour ORDER BY dow, hour
            """, p)

            # 30. KPI SUMMARY (dla nagłówka panelu)
            row = _q1(conn, db, """
                SELECT
                    COUNT(DISTINCT CASE WHEN created_at >= NOW()-INTERVAL '1 day'
                          THEN session_id END) as sessions_today,
                    COUNT(DISTINCT CASE WHEN created_at >= NOW()-INTERVAL '7 days'
                          THEN session_id END) as sessions_7d,
                    COUNT(DISTINCT CASE WHEN created_at >= NOW() - INTERVAL '1 day' * :days
                          THEN session_id END) as sessions_total,
                    COUNT(DISTINCT CASE WHEN created_at >= NOW()-INTERVAL '1 day'
                          AND user_id IS NOT NULL THEN user_id END) as active_users_today,
                    COUNT(CASE WHEN created_at >= NOW() - INTERVAL '1 day' * :days
                          AND event_type='pageview'   THEN 1 END) as total_pageviews,
                    COUNT(CASE WHEN created_at >= NOW() - INTERVAL '1 day' * :days
                          AND event_type='add_serial' THEN 1 END) as total_adds,
                    COUNT(CASE WHEN created_at >= NOW() - INTERVAL '1 day' * :days
                          AND event_type='mark_episode' THEN 1 END) as total_marks
                FROM user_events
            """, p)
            result['kpi'] = row or {
                'sessions_today': 0, 'sessions_7d': 0, 'sessions_total': 0,
                'active_users_today': 0, 'total_pageviews': 0,
                'total_adds': 0, 'total_marks': 0
            }

        return result

    except Exception as e:
        print(f"[analytics] get_analytics_summary error: {e}")
        return empty


def _empty_result():
    """Pusty wynik z wszystkimi kluczami — admin.html nigdy nie crashuje."""
    return {
        'top_pages': [], 'devices': [], 'browsers': [], 'os_list': [],
        'daily': [], 'actions': [], 'external_referrers': [], 'navigation_paths': [],
        'recent_sessions': [], 'slow_pages': [], 'entry_pages': [], 'exit_pages': [],
        'user_growth': [], 'pro_conversions': [], 'user_activity': [],
        'retention': [], 'power_users': [], 'errors': [],
        'time_to_first_action': [], 'hour_dow_heatmap': [],
        'session_stats': {
            'bounce_rate': 0, 'total_sessions': 0, 'avg_pages_per_session': 0,
            'avg_session_duration_min': 0, 'max_session_duration_min': 0,
            'engaged_sessions': 0, 'deep_sessions': 0,
        },
        'new_vs_returning': {'new_users': 0, 'returning_users': 0},
        'guest_vs_logged': {'guest_sessions': 0, 'logged_sessions': 0, 'guest_ratio': 0},
        'registrations': {'converted_sessions': 0},
        'engagement': {
            'avg_actions_per_session': 0, 'max_actions_per_session': 0,
            'passive_sessions': 0, 'active_sessions': 0,
        },
        'funnel': {'saw_tracker': 0, 'went_to_auth': 0, 'went_to_register': 0, 'registered': 0},
        'trends': {
            'sessions_last7': 0, 'sessions_prev7': 0,
            'pv_last7': 0, 'pv_prev7': 0,
            'users_last7': 0, 'users_prev7': 0,
        },
        'kpi': {
            'sessions_today': 0, 'sessions_7d': 0, 'sessions_total': 0,
            'active_users_today': 0, 'total_pageviews': 0,
            'total_adds': 0, 'total_marks': 0,
        },
        'hourly': [], 'day_of_week': [],
    }

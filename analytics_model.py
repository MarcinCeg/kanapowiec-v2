# analytics_model.py — v3 — boty, geolokalizacja, rozszerzone metryki

import hashlib, re, json
from datetime import datetime


# ── Lista znanych botów / crawlerów ──────────────────────────────────────────
BOT_SIGNATURES = [
    'googlebot', 'bingbot', 'slurp', 'duckduckbot', 'baiduspider',
    'yandexbot', 'sogou', 'exabot', 'facebot', 'facebookexternalhit',
    'ia_archiver', 'semrushbot', 'ahrefsbot', 'mj12bot', 'dotbot',
    'rogerbot', 'linkedinbot', 'twitterbot', 'whatsapp', 'telegrambot',
    'applebot', 'petalbot', 'bytespider', 'gptbot', 'chatgpt-user',
    'claudebot', 'anthropic-ai', 'cohere-ai', 'perplexitybot',
    'ccbot', 'dataforseobot', 'serpstatbot', 'turnitinbot',
    'headlesschrome', 'phantomjs', 'selenium', 'scrapy', 'wget', 'curl',
    'python-requests', 'python-urllib', 'go-http-client', 'okhttp',
    'jakarta', 'java/', 'libwww', 'lwp-', 'axios/', 'node-fetch',
    'aisearchindex',  # z Twoich logów
]

def is_bot(ua_string):
    """Zwróć True jeśli User-Agent wygląda jak bot/crawler."""
    if not ua_string:
        return True  # brak UA = bot
    ua = ua_string.lower()
    return any(sig in ua for sig in BOT_SIGNATURES)


# ── Geolokalizacja przez ip-api.com (bezpłatna, 45 req/min) ─────────────────

def geolocate_ip(ip):
    """
    Geolokalizuj IP → (country, country_code, region, city).
    Używa ip-api.com (bezpłatny plan: 45 req/min, bez HTTPS).
    Zwraca None przy błędzie lub IP prywatnym.
    """
    if not ip:
        return None
    # Prywatne zakresy — nie lokalizuj
    private = ('10.', '172.16.', '172.17.', '172.18.', '172.19.',
               '172.20.', '172.21.', '172.22.', '172.23.', '172.24.',
               '172.25.', '172.26.', '172.27.', '172.28.', '172.29.',
               '172.30.', '172.31.', '192.168.', '127.', '::1', 'localhost')
    if any(ip.startswith(p) for p in private):
        return None
    try:
        import urllib.request
        url = f'http://ip-api.com/json/{ip}?fields=status,country,countryCode,regionName,city'
        with urllib.request.urlopen(url, timeout=2) as resp:
            data = json.loads(resp.read().decode())
            if data.get('status') == 'success':
                return {
                    'country':      data.get('country', ''),
                    'country_code': data.get('countryCode', ''),
                    'region':       data.get('regionName', ''),
                    'city':         data.get('city', ''),
                }
    except Exception:
        pass
    return None


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


def _ensure_geo_columns(db):
    """Dodaj kolumny geo do user_events jeśli nie istnieją (migracja)."""
    try:
        with db.engine.connect() as conn:
            for col, typ in [
                ('country',      'VARCHAR(100)'),
                ('country_code', 'VARCHAR(10)'),
                ('region',       'VARCHAR(150)'),
                ('city',         'VARCHAR(150)'),
                ('is_bot',       'BOOLEAN DEFAULT FALSE'),
            ]:
                conn.execute(db.text(
                    f"ALTER TABLE user_events ADD COLUMN IF NOT EXISTS {col} {typ}"
                ))
            conn.commit()
    except Exception as e:
        print(f"[analytics] geo migration: {e}")


# ── Logowanie zdarzeń ─────────────────────────────────────────────────────────

def log_event(db, user_id, event_type, page=None, referrer=None,
              data=None, request=None, duration_ms=None):
    """Zapisz zdarzenie. Boty są zapisywane z flagą is_bot=True (nie filtrujemy — tylko oznaczamy)."""
    try:
        ua_str = request.headers.get('User-Agent', '') if request else ''
        browser, os_name, device = parse_ua(ua_str)
        bot = is_bot(ua_str)

        ip = request.headers.get('X-Forwarded-For', request.remote_addr) if request else None
        if ip and ',' in ip:
            ip = ip.split(',')[0].strip()

        session_id = get_session_id(request) if request else None
        data_json = json.dumps(data, ensure_ascii=False) if data else None

        # Geolokalizacja — tylko dla prawdziwych użytkowników, nie botów
        country = country_code = region = city = None
        if not bot and ip:
            geo = geolocate_ip(ip)
            if geo:
                country      = geo['country']
                country_code = geo['country_code']
                region       = geo['region']
                city         = geo['city']

        params = {
            'uid': user_id, 'sid': session_id, 'etype': event_type,
            'page': page[:255] if page else None,
            'ref': referrer[:255] if referrer else None,
            'data': data_json, 'ip': hash_ip(ip),
            'ua': ua_str[:500] if ua_str else None,
            'device': device, 'browser': browser, 'os': os_name,
            'dur': duration_ms, 'bot': bot,
            'country': country, 'country_code': country_code,
            'region': region, 'city': city,
        }

        sql = db.text("""
            INSERT INTO user_events
                (user_id, session_id, event_type, page, referrer, data,
                 ip_hash, user_agent, device_type, browser, os, duration_ms,
                 is_bot, country, country_code, region, city, created_at)
            VALUES
                (:uid, :sid, :etype, :page, :ref, CAST(:data AS JSONB),
                 :ip, :ua, :device, :browser, :os, :dur,
                 :bot, :country, :country_code, :region, :city, NOW())
        """)
        with db.engine.connect() as conn:
            conn.execute(sql, params)
            conn.commit()
    except Exception as e:
        # Fallback bez kolumn geo (stara tabela)
        try:
            params_basic = {k: v for k, v in params.items()
                            if k in ('uid','sid','etype','page','ref','data','ip','ua','device','browser','os','dur')}
            sql_basic = db.text("""
                INSERT INTO user_events
                    (user_id, session_id, event_type, page, referrer, data,
                     ip_hash, user_agent, device_type, browser, os, duration_ms, created_at)
                VALUES
                    (:uid, :sid, :etype, :page, :ref, CAST(:data AS JSONB),
                     :ip, :ua, :device, :browser, :os, :dur, NOW())
            """)
            with db.engine.connect() as conn:
                conn.execute(sql_basic, params_basic)
                conn.commit()
            # Spróbuj dodać kolumny i następnym razem zadziała
            _ensure_geo_columns(db)
        except Exception as e2:
            print(f"[analytics] log_event error ({event_type}): {e2}")


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


# ── Filtr botów w SQL ─────────────────────────────────────────────────────────
# Używamy: is_bot IS NOT TRUE  (działa też gdy kolumna nie istnieje → ignoruje)
# Dla starych rekordów bez kolumny is_bot — filtrujemy po UA

BOT_UA_SQL = """
    AND COALESCE(is_bot, FALSE) = FALSE
    AND (user_agent IS NULL OR NOT (
        user_agent ILIKE '%bot%' OR user_agent ILIKE '%crawler%' OR
        user_agent ILIKE '%spider%' OR user_agent ILIKE '%gptbot%' OR
        user_agent ILIKE '%claudebot%' OR user_agent ILIKE '%headless%' OR
        user_agent ILIKE '%python%' OR user_agent ILIKE '%curl%' OR
        user_agent ILIKE '%wget%' OR user_agent ILIKE '%aisearchindex%' OR
        user_agent ILIKE '%scrapy%' OR user_agent ILIKE '%semrush%' OR
        user_agent ILIKE '%ahrefs%' OR user_agent ILIKE '%dataroo%'
    ))
"""


# ── Główna funkcja analityczna ────────────────────────────────────────────────

def get_analytics_summary(db, days=30):
    empty = _empty_result()
    try:
        result = {}
        p = {'days': days}
        # Skrót dla WHERE bez botów
        NO_BOT = BOT_UA_SQL

        with db.engine.connect() as conn:

            # 1. TOP STRONY (bez botów)
            result['top_pages'] = _q(conn, db, f"""
                SELECT page, COUNT(*) as cnt,
                       COUNT(DISTINCT session_id) as sessions,
                       COUNT(DISTINCT user_id) as users
                FROM user_events
                WHERE event_type='pageview'
                  AND created_at >= NOW() - INTERVAL '1 day' * :days
                  {NO_BOT}
                GROUP BY page ORDER BY cnt DESC LIMIT 20
            """, p)

            # 2. URZĄDZENIA
            result['devices'] = _q(conn, db, f"""
                SELECT device_type, COUNT(DISTINCT session_id) as sessions
                FROM user_events
                WHERE created_at >= NOW() - INTERVAL '1 day' * :days
                  AND device_type IS NOT NULL {NO_BOT}
                GROUP BY device_type ORDER BY sessions DESC
            """, p)

            # 3. PRZEGLĄDARKI
            result['browsers'] = _q(conn, db, f"""
                SELECT browser, COUNT(DISTINCT session_id) as sessions
                FROM user_events
                WHERE created_at >= NOW() - INTERVAL '1 day' * :days
                  AND browser IS NOT NULL {NO_BOT}
                GROUP BY browser ORDER BY sessions DESC
            """, p)

            # 4. SYSTEMY OPERACYJNE
            result['os_list'] = _q(conn, db, f"""
                SELECT os, COUNT(DISTINCT session_id) as sessions
                FROM user_events
                WHERE created_at >= NOW() - INTERVAL '1 day' * :days
                  AND os IS NOT NULL {NO_BOT}
                GROUP BY os ORDER BY sessions DESC
            """, p)

            # 5. AKTYWNOŚĆ DZIENNA
            result['daily'] = _q(conn, db, f"""
                SELECT DATE(created_at) as day,
                       COUNT(*) as events,
                       COUNT(DISTINCT session_id) as sessions,
                       COUNT(DISTINCT user_id) as users
                FROM user_events
                WHERE created_at >= NOW() - INTERVAL '1 day' * :days
                  {NO_BOT}
                GROUP BY DATE(created_at) ORDER BY day
            """, p)

            # 6. AKCJE UŻYTKOWNIKÓW
            result['actions'] = _q(conn, db, f"""
                SELECT event_type, COUNT(*) as cnt,
                       COUNT(DISTINCT user_id) as unique_users
                FROM user_events
                WHERE event_type != 'pageview'
                  AND created_at >= NOW() - INTERVAL '1 day' * :days
                  {NO_BOT}
                GROUP BY event_type ORDER BY cnt DESC LIMIT 30
            """, p)

            # 7. ZEWNĘTRZNE REFERRERY
            result['external_referrers'] = _q(conn, db, f"""
                SELECT referrer, COUNT(*) as cnt,
                       COUNT(DISTINCT session_id) as sessions
                FROM user_events
                WHERE event_type='pageview'
                  AND referrer IS NOT NULL
                  AND referrer NOT LIKE '%seriale.fun%'
                  AND referrer NOT LIKE '%localhost%'
                  AND created_at >= NOW() - INTERVAL '1 day' * :days
                  {NO_BOT}
                GROUP BY referrer ORDER BY cnt DESC LIMIT 20
            """, p)

            # 8. STATYSTYKI SESJI
            row = _q1(conn, db, f"""
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
                      {NO_BOT}
                    GROUP BY session_id
                ) sub
            """, p)
            result['session_stats'] = row or {
                'bounce_rate': 0, 'total_sessions': 0, 'avg_pages_per_session': 0,
                'avg_session_duration_min': 0, 'max_session_duration_min': 0,
                'engaged_sessions': 0, 'deep_sessions': 0
            }

            # 9. NOWI VS POWRACAJĄCY
            row = _q1(conn, db, f"""
                SELECT
                    COUNT(DISTINCT CASE WHEN u.created_at >= NOW() - INTERVAL '1 day' * :days
                          THEN e.user_id END) as new_users,
                    COUNT(DISTINCT CASE WHEN u.created_at < NOW() - INTERVAL '1 day' * :days
                          THEN e.user_id END) as returning_users
                FROM user_events e
                LEFT JOIN users u ON u.id = e.user_id
                WHERE e.created_at >= NOW() - INTERVAL '1 day' * :days
                  AND e.user_id IS NOT NULL {NO_BOT}
            """, p)
            result['new_vs_returning'] = row or {'new_users': 0, 'returning_users': 0}

            # 10. GOŚCIE VS ZALOGOWANI
            row = _q1(conn, db, f"""
                SELECT
                    COUNT(DISTINCT CASE WHEN user_id IS NULL     THEN session_id END) as guest_sessions,
                    COUNT(DISTINCT CASE WHEN user_id IS NOT NULL THEN session_id END) as logged_sessions,
                    COALESCE(COUNT(DISTINCT CASE WHEN user_id IS NULL THEN session_id END)::float
                        / NULLIF(COUNT(DISTINCT session_id),0), 0) as guest_ratio
                FROM user_events
                WHERE created_at >= NOW() - INTERVAL '1 day' * :days {NO_BOT}
            """, p)
            result['guest_vs_logged'] = row or {'guest_sessions': 0, 'logged_sessions': 0, 'guest_ratio': 0}

            # 11. REJESTRACJE
            row = _q1(conn, db, f"""
                SELECT COUNT(DISTINCT session_id) as converted_sessions
                FROM user_events
                WHERE event_type='user_register'
                  AND created_at >= NOW() - INTERVAL '1 day' * :days {NO_BOT}
            """, p)
            result['registrations'] = row or {'converted_sessions': 0}

            # 12. GODZINY AKTYWNOŚCI
            result['hourly'] = _q(conn, db, f"""
                SELECT EXTRACT(HOUR FROM created_at) as hour,
                       COUNT(*) as events,
                       COUNT(DISTINCT session_id) as sessions,
                       COUNT(DISTINCT user_id) as users
                FROM user_events
                WHERE created_at >= NOW() - INTERVAL '1 day' * :days {NO_BOT}
                GROUP BY hour ORDER BY hour
            """, p)

            # 13. DNI TYGODNIA
            result['day_of_week'] = _q(conn, db, f"""
                SELECT EXTRACT(DOW FROM created_at) as dow,
                       COUNT(*) as events,
                       COUNT(DISTINCT session_id) as sessions
                FROM user_events
                WHERE created_at >= NOW() - INTERVAL '1 day' * :days {NO_BOT}
                GROUP BY dow ORDER BY dow
            """, p)

            # 14. POWER USERS
            result['power_users'] = _q(conn, db, f"""
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
            row = _q1(conn, db, f"""
                SELECT
                    COUNT(DISTINCT CASE WHEN page='/app'           THEN session_id END) as saw_tracker,
                    COUNT(DISTINCT CASE WHEN page LIKE '/auth%'    THEN session_id END) as went_to_auth,
                    COUNT(DISTINCT CASE WHEN page='/auth/register' THEN session_id END) as went_to_register,
                    COUNT(DISTINCT CASE WHEN event_type='user_register' THEN session_id END) as registered
                FROM user_events
                WHERE created_at >= NOW() - INTERVAL '1 day' * :days {NO_BOT}
            """, p)
            result['funnel'] = row or {
                'saw_tracker': 0, 'went_to_auth': 0, 'went_to_register': 0, 'registered': 0
            }

            # 16. RETENCJA KOHORTOWA
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

            # 17. OSTATNIE SESJE
            result['recent_sessions'] = _q(conn, db, f"""
                SELECT e.session_id, e.user_id, u.username,
                       COUNT(*) as page_views,
                       MIN(e.created_at) as start_time,
                       MAX(e.created_at) as end_time,
                       ROUND(EXTRACT(EPOCH FROM (MAX(e.created_at)-MIN(e.created_at)))/60, 1) as duration_min,
                       e.device_type, e.browser, e.os,
                       MIN(e.page) as entry_page,
                       MAX(e.page) as last_page,
                       MAX(e.country) as country,
                       MAX(e.city) as city
                FROM user_events e
                LEFT JOIN users u ON u.id = e.user_id
                WHERE e.event_type='pageview'
                  AND e.created_at >= NOW() - INTERVAL '1 day' * :days
                  {NO_BOT}
                GROUP BY e.session_id, e.user_id, u.username, e.device_type, e.browser, e.os
                ORDER BY start_time DESC LIMIT 50
            """, p)

            # 18. WOLNE STRONY
            result['slow_pages'] = _q(conn, db, f"""
                SELECT page,
                       ROUND(AVG(duration_ms)::numeric/1000, 1) as avg_time_sec,
                       COUNT(*) as cnt,
                       ROUND(MAX(duration_ms)::numeric/1000, 1) as max_time_sec
                FROM user_events
                WHERE duration_ms IS NOT NULL AND duration_ms > 0
                  AND created_at >= NOW() - INTERVAL '1 day' * :days {NO_BOT}
                GROUP BY page ORDER BY avg_time_sec DESC LIMIT 10
            """, p)

            # 19. STRONY WEJŚCIA
            result['entry_pages'] = _q(conn, db, f"""
                SELECT page as entry_page, COUNT(*) as sessions
                FROM (
                    SELECT DISTINCT ON (session_id) session_id, page
                    FROM user_events
                    WHERE event_type='pageview'
                      AND created_at >= NOW() - INTERVAL '1 day' * :days
                      {NO_BOT}
                    ORDER BY session_id, created_at ASC
                ) sub
                GROUP BY page ORDER BY sessions DESC LIMIT 10
            """, p)

            # 20. STRONY WYJŚCIA
            result['exit_pages'] = _q(conn, db, f"""
                SELECT page as exit_page, COUNT(*) as sessions
                FROM (
                    SELECT DISTINCT ON (session_id) session_id, page
                    FROM user_events
                    WHERE event_type='pageview'
                      AND created_at >= NOW() - INTERVAL '1 day' * :days
                      {NO_BOT}
                    ORDER BY session_id, created_at DESC
                ) sub
                GROUP BY page ORDER BY sessions DESC LIMIT 10
            """, p)

            # 21. WZROST UŻYTKOWNIKÓW
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

            # 23. AKTYWNOŚĆ PER USER
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

            # 24. ENGAGEMENT
            row = _q1(conn, db, f"""
                SELECT
                    ROUND(AVG(action_count)::numeric, 2) as avg_actions_per_session,
                    MAX(action_count) as max_actions_per_session,
                    COUNT(CASE WHEN action_count=0 THEN 1 END) as passive_sessions,
                    COUNT(CASE WHEN action_count>0 THEN 1 END) as active_sessions
                FROM (
                    SELECT session_id,
                           COUNT(CASE WHEN event_type!='pageview' THEN 1 END) as action_count
                    FROM user_events
                    WHERE created_at >= NOW() - INTERVAL '1 day' * :days {NO_BOT}
                    GROUP BY session_id
                ) sub
            """, p)
            result['engagement'] = row or {
                'avg_actions_per_session': 0, 'max_actions_per_session': 0,
                'passive_sessions': 0, 'active_sessions': 0
            }

            # 25. ŚCIEŻKI NAWIGACJI
            result['navigation_paths'] = _q(conn, db, f"""
                WITH pairs AS (
                    SELECT
                        LAG(page) OVER (PARTITION BY session_id ORDER BY created_at) as from_page,
                        page as to_page
                    FROM user_events
                    WHERE event_type='pageview'
                      AND created_at >= NOW() - INTERVAL '1 day' * :days
                      {NO_BOT}
                )
                SELECT from_page, to_page, COUNT(*) as cnt
                FROM pairs
                WHERE from_page IS NOT NULL
                GROUP BY from_page, to_page
                HAVING COUNT(*) > 1
                ORDER BY cnt DESC LIMIT 20
            """, p)

            # 26. TRENDY 7d vs 7d
            row = _q1(conn, db, f"""
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
                WHERE TRUE {NO_BOT}
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

            # 28. CZAS DO PIERWSZEJ AKCJI
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

            # 29. HEATMAPA
            result['hour_dow_heatmap'] = _q(conn, db, f"""
                SELECT EXTRACT(DOW  FROM created_at) as dow,
                       EXTRACT(HOUR FROM created_at) as hour,
                       COUNT(*) as events
                FROM user_events
                WHERE created_at >= NOW() - INTERVAL '1 day' * :days {NO_BOT}
                GROUP BY dow, hour ORDER BY dow, hour
            """, p)

            # 30. KPI SUMMARY
            row = _q1(conn, db, f"""
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
                WHERE TRUE {NO_BOT}
            """, p)
            result['kpi'] = row or {
                'sessions_today': 0, 'sessions_7d': 0, 'sessions_total': 0,
                'active_users_today': 0, 'total_pageviews': 0,
                'total_adds': 0, 'total_marks': 0
            }

            # ── NOWE ───────────────────────────────────────────────────────────

            # 31. KRAJE (top 20)
            result['countries'] = _q(conn, db, f"""
                SELECT
                    COALESCE(country, 'Nieznany') as country,
                    COALESCE(country_code, '??') as country_code,
                    COUNT(DISTINCT session_id) as sessions,
                    COUNT(DISTINCT user_id) as users
                FROM user_events
                WHERE created_at >= NOW() - INTERVAL '1 day' * :days
                  AND country IS NOT NULL {NO_BOT}
                GROUP BY country, country_code
                ORDER BY sessions DESC LIMIT 20
            """, p)

            # 32. REGIONY / WOJEWÓDZTWA (top 20)
            result['regions'] = _q(conn, db, f"""
                SELECT
                    COALESCE(country, '?') as country,
                    COALESCE(region, 'Nieznany') as region,
                    COALESCE(city, '') as city,
                    COUNT(DISTINCT session_id) as sessions
                FROM user_events
                WHERE created_at >= NOW() - INTERVAL '1 day' * :days
                  AND region IS NOT NULL {NO_BOT}
                GROUP BY country, region, city
                ORDER BY sessions DESC LIMIT 30
            """, p)

            # 33. BOTY — statystyki (osobno, żeby wiedzieć ile odfiltrowano)
            row = _q1(conn, db, """
                SELECT
                    COUNT(DISTINCT session_id) as bot_sessions,
                    COUNT(*) as bot_events,
                    COUNT(DISTINCT COALESCE(
                        CASE WHEN user_agent ILIKE '%bot%' THEN 'bot'
                             WHEN user_agent ILIKE '%crawler%' THEN 'crawler'
                             WHEN user_agent ILIKE '%python%' THEN 'python'
                             WHEN user_agent ILIKE '%curl%' THEN 'curl'
                             ELSE 'other'
                        END, 'other'
                    )) as bot_types
                FROM user_events
                WHERE created_at >= NOW() - INTERVAL '1 day' * :days
                  AND (
                    COALESCE(is_bot, FALSE) = TRUE
                    OR user_agent ILIKE '%bot%' OR user_agent ILIKE '%crawler%'
                    OR user_agent ILIKE '%python%' OR user_agent ILIKE '%curl%'
                    OR user_agent ILIKE '%aisearchindex%'
                  )
            """, p)
            result['bot_stats'] = row or {'bot_sessions': 0, 'bot_events': 0, 'bot_types': 0}

            # 34. TOP MIASTA
            result['cities'] = _q(conn, db, f"""
                SELECT
                    COALESCE(city, 'Nieznane') as city,
                    COALESCE(country, '') as country,
                    COUNT(DISTINCT session_id) as sessions
                FROM user_events
                WHERE created_at >= NOW() - INTERVAL '1 day' * :days
                  AND city IS NOT NULL AND city != '' {NO_BOT}
                GROUP BY city, country
                ORDER BY sessions DESC LIMIT 20
            """, p)

            # 35. ŚREDNI CZAS NA STRONIE wg kraju
            result['engagement_by_country'] = _q(conn, db, f"""
                SELECT
                    COALESCE(country, 'Nieznany') as country,
                    COUNT(DISTINCT session_id) as sessions,
                    ROUND(AVG(page_views)::numeric, 1) as avg_pages
                FROM (
                    SELECT session_id,
                           MAX(country) as country,
                           COUNT(*) as page_views
                    FROM user_events
                    WHERE event_type='pageview'
                      AND created_at >= NOW() - INTERVAL '1 day' * :days
                      {NO_BOT}
                    GROUP BY session_id
                ) sub
                WHERE country IS NOT NULL
                GROUP BY country
                ORDER BY sessions DESC LIMIT 15
            """, p)

        return result

    except Exception as e:
        print(f"[analytics] get_analytics_summary error: {e}")
        import traceback
        traceback.print_exc()
        return empty


def _empty_result():
    return {
        'top_pages': [], 'devices': [], 'browsers': [], 'os_list': [],
        'daily': [], 'actions': [], 'external_referrers': [], 'navigation_paths': [],
        'recent_sessions': [], 'slow_pages': [], 'entry_pages': [], 'exit_pages': [],
        'user_growth': [], 'pro_conversions': [], 'user_activity': [],
        'retention': [], 'power_users': [], 'errors': [],
        'time_to_first_action': [], 'hour_dow_heatmap': [],
        'countries': [], 'regions': [], 'cities': [], 'engagement_by_country': [],
        'bot_stats': {'bot_sessions': 0, 'bot_events': 0, 'bot_types': 0},
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

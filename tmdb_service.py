import requests, time, re
from datetime import datetime, date
from models import db, Serial, GlobalNowosci, TMDB_GENRES, COUNTRY_NAMES, TMDB_PROVIDER_IDS
from flask import current_app

def tmdb_key():
    return current_app.config["TMDB_KEY"]

HDR = {"User-Agent":"Mozilla/5.0","Accept-Language":"pl-PL,pl;q=0.9"}


def fmt_date(ds):
    if not ds: return ""
    today = date.today()
    for fmt in ["%Y-%m-%d","%d.%m.%Y"]:
        try:
            d = datetime.strptime(ds[:10], fmt).date()
            diff = (today - d).days
            if diff == 0: return f"🔥 dziś"
            if diff == 1: return "wczoraj"
            if diff <= 7: return f"{diff} dni temu"
            return d.strftime("%d.%m.%Y")
        except: continue
    return ds[:10]


def search_or_create_serial(nazwa):
    """Szuka serialu w cache lub pobiera z TMDB i tworzy rekord."""
    # Sprawdź cache
    s = Serial.query.filter(Serial.nazwa.ilike(nazwa)).first()
    if s:
        return s
    # Szukaj w TMDB
    try:
        for lang in ["pl-PL","en-US"]:
            r = requests.get("https://api.themoviedb.org/3/search/tv",
                params={"api_key": tmdb_key(), "query": nazwa, "language": lang}, timeout=8)
            items = r.json().get("results", [])
            if items: break
        if not items:
            s = Serial(nazwa=nazwa)
            db.session.add(s); db.session.commit()
            return s
        item = items[0]
        return _upsert_serial_from_tmdb(item, nazwa)
    except Exception as e:
        print(f"TMDB search err {nazwa}: {e}")
        s = Serial(nazwa=nazwa)
        db.session.add(s); db.session.commit()
        return s


def _upsert_serial_from_tmdb(item, nazwa=None):
    """Tworzy lub aktualizuje Serial z danych TMDB discover/search."""
    tmdb_id = item["id"]
    s = Serial.query.filter_by(tmdb_id=tmdb_id).first()
    if not s:
        s = Serial(tmdb_id=tmdb_id)
        db.session.add(s)

    s.nazwa = item.get("name") or item.get("original_name") or nazwa or ""
    s.cover = f"https://image.tmdb.org/t/p/w342{item['poster_path']}" if item.get("poster_path") else ""
    s.imdb_url = f"https://www.themoviedb.org/tv/{tmdb_id}"
    rating = item.get("vote_average", 0)
    s.imdb_rating = round(rating, 1) if rating else None
    s.imdb_desc = item.get("overview", "") or ""
    genres = [TMDB_GENRES.get(gid,"") for gid in item.get("genre_ids",[])]
    s.genres = ",".join(g for g in genres if g)[:255]
    raw_c = item.get("origin_country", [])
    s.countries = ",".join(COUNTRY_NAMES.get(c,c) for c in raw_c)[:255]
    s.first_air_date = item.get("first_air_date","")

    # Pobierz szczegóły (odcinki, sezony, runtime) jeśli brak
    if not s.episodes_count:
        try:
            r2 = requests.get(f"https://api.themoviedb.org/3/tv/{tmdb_id}",
                params={"api_key": tmdb_key(), "language": "pl-PL"}, timeout=8)
            det = r2.json()
            s.episodes_count  = det.get("number_of_episodes", 0)
            s.seasons_count   = det.get("number_of_seasons", 0)
            rts = det.get("episode_run_time", [])
            s.episode_runtime = rts[0] if rts else 45
            s.status = det.get("status","")
        except: pass

    s.updated_at = datetime.utcnow()
    db.session.commit()
    return s


def get_last_episode(serial):
    """Pobiera info o ostatnim odcinku z TMDB."""
    if not serial.tmdb_id:
        return None
    try:
        r = requests.get(f"https://api.themoviedb.org/3/tv/{serial.tmdb_id}",
            params={"api_key": tmdb_key(), "language": "pl-PL"}, timeout=8)
        det = r.json()
        ep = det.get("last_episode_to_air") or {}
        if not ep:
            return None
        s_num = ep.get("season_number","")
        e_num = ep.get("episode_number","")
        ep_name = ep.get("name","") or ""
        air_date = ep.get("air_date","")
        label = fmt_date(air_date)
        title = f"S{s_num}E{e_num}"
        if ep_name: title += f" · {ep_name}"
        return {
            "title": title,
            "link": f"https://www.themoviedb.org/tv/{serial.tmdb_id}/season/{s_num}/episode/{e_num}",
            "date_raw": air_date,
            "date_label": label,
            "is_new": "🔥" in label or "wczoraj" in label,
        }
    except Exception as e:
        print(f"last_ep err {serial.nazwa}: {e}")
        return None


def refresh_nowosci_for_platform(platform_key):
    """Pobiera nowości z TMDB dla platformy i zapisuje do GlobalNowosci."""
    provider_id = TMDB_PROVIDER_IDS.get(platform_key)
    results = []
    try:
        if provider_id:
            params = {
                "api_key": tmdb_key(), "language": "pl-PL",
                "sort_by": "first_air_date.desc", "watch_region": "PL",
                "with_watch_providers": str(provider_id),
                "first_air_date.gte": "2023-01-01", "page": 1,
            }
            r = requests.get("https://api.themoviedb.org/3/discover/tv",
                             params=params, timeout=10)
            items = r.json().get("results", [])
        else:
            # Platformy bez TMDB coverage — trending PL
            r = requests.get("https://api.themoviedb.org/3/trending/tv/week",
                params={"api_key": tmdb_key(), "language": "pl-PL"}, timeout=10)
            items = r.json().get("results", [])
            if platform_key in ("tvpvod","polsat"):
                items = [i for i in items if "PL" in i.get("origin_country",[])]

        for item in items[:20]:
            serial = _upsert_serial_from_tmdb(item)
            results.append((serial, item.get("first_air_date","")))
            time.sleep(0.05)

        # Zapisz do GlobalNowosci
        GlobalNowosci.query.filter_by(platform=platform_key).delete()
        for serial, date_raw in results:
            db.session.add(GlobalNowosci(
                platform=platform_key,
                serial_id=serial.id,
                date_added=date_raw,
                date_label=fmt_date(date_raw),
            ))
        db.session.commit()
        print(f"  [{platform_key}] nowosci: {len(results)}")
    except Exception as e:
        print(f"  [{platform_key}] err: {e}")

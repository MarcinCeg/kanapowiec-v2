"""
AI Service — agent rekomendacji seriali dla Kanapowiec v2
Używa Claude claude-sonnet-4-6 z narzędziami (tool use)
"""
import json
import requests
from anthropic import Anthropic
from flask import current_app
from models import db, Serial, Watching, Watched, Candidate, GlobalNowosci

def _get_client():
    """Tworzy klienta Anthropic z kluczem z konfiguracji Flask."""
    api_key = current_app.config.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("Brak ANTHROPIC_API_KEY w konfiguracji aplikacji")
    return Anthropic(api_key=api_key)


# ── Narzędzia agenta ──────────────────────────────────────────────────────────
TOOLS = [
    {
        "name": "szukaj_seriale_tmdb",
        "description": "Szuka seriali w TMDB po gatunku, nastroju lub słowach kluczowych. Zwraca listę seriali z ocenami.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Słowa kluczowe do wyszukania np. 'thriller psychologiczny' lub tytuł serialu"
                },
                "min_rating": {
                    "type": "number",
                    "description": "Minimalna ocena TMDB (0-10), domyślnie 7.0"
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "pobierz_liste_uzytkownika",
        "description": "Pobiera listy seriali użytkownika: oglądam, obejrzane lub kandydaci",
        "input_schema": {
            "type": "object",
            "properties": {
                "lista": {
                    "type": "string",
                    "enum": ["ogladam", "obejrzane", "kandydaci"],
                    "description": "Która lista ma być pobrana"
                }
            },
            "required": ["lista"]
        }
    },
    {
        "name": "pobierz_nowosci_platformy",
        "description": "Pobiera nowości z konkretnej platformy streamingowej",
        "input_schema": {
            "type": "object",
            "properties": {
                "platforma": {
                    "type": "string",
                    "enum": ["netflix","hbo","disney","prime","appletv","skyshowtime","canalplus","player","polsat","tvpvod"],
                    "description": "Klucz platformy"
                }
            },
            "required": ["platforma"]
        }
    }
]


# ── Implementacje narzędzi ────────────────────────────────────────────────────
def _exec_tool(tool_name: str, tool_input: dict, user_id: int) -> str:
    """Wykonuje narzędzie i zwraca wynik jako string."""

    if tool_name == "szukaj_seriale_tmdb":
        tmdb_key = current_app.config["TMDB_KEY"]
        query = tool_input.get("query", "")
        min_rating = tool_input.get("min_rating", 7.0)
        try:
            r = requests.get(
                "https://api.themoviedb.org/3/search/tv",
                params={"api_key": tmdb_key, "query": query, "language": "pl-PL"},
                timeout=8
            )
            results = r.json().get("results", [])[:8]
            filtered = [
                {
                    "nazwa": item.get("name",""),
                    "ocena": round(item.get("vote_average",0),1),
                    "opis": (item.get("overview","") or "")[:120],
                    "rok": (item.get("first_air_date","") or "")[:4],
                    "kraje": item.get("origin_country",[]),
                }
                for item in results
                if item.get("vote_average",0) >= min_rating
            ]
            return json.dumps(filtered, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"error": str(e)})

    elif tool_name == "pobierz_liste_uzytkownika":
        lista = tool_input.get("lista")
        try:
            if lista == "ogladam":
                items = Watching.query.filter_by(user_id=user_id).all()
                return json.dumps([
                    {"nazwa": w.serial.nazwa, "ocena": w.serial.imdb_rating,
                     "gatunki": w.serial.genres_list, "platforma": w.platforma}
                    for w in items if w.serial
                ], ensure_ascii=False)
            elif lista == "obejrzane":
                items = Watched.query.filter_by(user_id=user_id).all()
                return json.dumps([
                    {"nazwa": w.serial.nazwa, "ocena": w.serial.imdb_rating,
                     "gatunki": w.serial.genres_list}
                    for w in items if w.serial
                ], ensure_ascii=False)
            elif lista == "kandydaci":
                items = Candidate.query.filter_by(user_id=user_id).all()
                return json.dumps([
                    {"nazwa": c.serial.nazwa, "ocena": c.serial.imdb_rating,
                     "platforma": c.platform}
                    for c in items if c.serial
                ], ensure_ascii=False)
        except Exception as e:
            return json.dumps({"error": str(e)})

    elif tool_name == "pobierz_nowosci_platformy":
        platforma = tool_input.get("platforma")
        try:
            rows = GlobalNowosci.query.filter_by(platform=platforma).limit(10).all()
            return json.dumps([
                {"nazwa": r.serial.nazwa, "ocena": r.serial.imdb_rating,
                 "opis": (r.serial.imdb_desc or "")[:100],
                 "data": r.date_label}
                for r in rows if r.serial
            ], ensure_ascii=False)
        except Exception as e:
            return json.dumps({"error": str(e)})

    return json.dumps({"error": f"Nieznane narzędzie: {tool_name}"})


# ── Główna funkcja agenta ─────────────────────────────────────────────────────
def agent_rekomenduj(user, nastroj: str, max_turns: int = 5) -> str:
    """
    Agent rekomendacji seriali.
    Analizuje historię oglądania użytkownika i jego nastrój,
    używa narzędzi TMDB żeby znaleźć najlepsze dopasowanie.
    """
    client = _get_client()

    system = """Jesteś asystentem rekomendacji seriali dla polskiego użytkownika.
Masz dostęp do jego list seriali oraz TMDB API.

Zasady:
1. Zawsze najpierw sprawdź co użytkownik już oglądał (pobierz_liste_uzytkownika)
2. Dopasuj rekomendacje do jego gustu i podanego nastroju
3. Nie polecaj seriali które już ogląda lub obejrzał
4. Jeśli masz kandydatów — sprawdź czy pasują do nastroju
5. Odpowiadaj po polsku, krótko i konkretnie
6. Podaj 3-5 propozycji z krótkim uzasadnieniem dlaczego pasują do tego użytkownika
7. Format: emoji + tytuł + 1 zdanie dlaczego"""

    messages = [
        {
            "role": "user",
            "content": f"Użytkownik {user.username} mówi: \"{nastroj}\"\n\nPomóż mu znaleźć idealny serial na dziś."
        }
    ]

    for turn in range(max_turns):
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1500,
            system=system,
            tools=TOOLS,
            messages=messages
        )

        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            return _extract_text(response.content)

        if response.stop_reason == "tool_use":
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    result = _exec_tool(block.name, block.input, user.id)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result
                    })
            if tool_results:
                messages.append({"role": "user", "content": tool_results})
            else:
                break
        else:
            break

    return _extract_text(response.content) if response else "Przepraszam, nie mogłem znaleźć rekomendacji."


def _extract_text(content) -> str:
    """Wyciąga tekst z odpowiedzi Claude."""
    parts = []
    for block in content:
        if hasattr(block, "text"):
            parts.append(block.text)
    return "\n".join(parts) or "Brak rekomendacji."


# ── Szybkie rekomendacje ──────────────────────────────────────────────────────
def szybkie_rekomendacje(user, limit: int = 5) -> list:
    """
    Szybkie rekomendacje bez pełnego agenta.
    Zwraca listę {"nazwa": str, "powod": str}
    """
    client = _get_client()

    obejrzane = [w.serial.nazwa for w in user.watched if w.serial][:20]
    ogladam   = [w.serial.nazwa for w in user.watching if w.serial][:10]
    gatunki = {}
    for w in list(user.watched) + list(user.watching):
        if w.serial:
            for g in w.serial.genres_list:
                gatunki[g] = gatunki.get(g, 0) + 1
    top_gatunki = sorted(gatunki, key=lambda x: -gatunki[x])[:5]

    prompt = f"""Na podstawie gustów użytkownika zaproponuj {limit} seriali do obejrzenia.

Obejrzał: {', '.join(obejrzane[:10]) or 'brak danych'}
Ogląda: {', '.join(ogladam) or 'brak danych'}
Ulubione gatunki: {', '.join(top_gatunki) or 'brak danych'}

Odpowiedz TYLKO w formacie JSON (bez żadnego tekstu przed ani po):
[{{"nazwa": "Tytuł serialu", "powod": "Krótkie uzasadnienie 1 zdanie"}}]

Zasady:
- Nie powtarzaj seriali które już obejrzał lub ogląda
- Uzasadnienie odwołuj się do jego gustu ("skoro lubisz X, spodoba Ci się Y bo...")
- Pisz po polsku"""

    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}]
        )
        text = response.content[0].text.strip()
        text = text.replace("```json","").replace("```","").strip()
        return json.loads(text)
    except Exception as e:
        print(f"AI rekomendacje err: {e}")
        return []


# ── Podsumowanie tygodnia ─────────────────────────────────────────────────────
def podsumowanie_tygodnia(user) -> str:
    """Generuje tygodniowe podsumowanie w formie śmiesznego tekstu."""
    client = _get_client()

    stats = user.stats
    if not stats:
        return ""

    ogladam_nazwy = [w.serial.nazwa for w in user.watching if w.serial][:5]

    prompt = f"""Napisz śmieszne, ciepłe podsumowanie tygodnia oglądania seriali dla użytkownika "{user.username}".

Dane:
- Godziny łącznie: {stats.total_hours}h
- Odcinki: {stats.total_episodes}
- Aktualnie ogląda: {', '.join(ogladam_nazwy) or 'nic'}
- Tytuł użytkownika: {user.active_title['ico']} {user.active_title['name']}

Styl: krótki (3-4 zdania), po polsku, z humorem, jakbyś był kumplem który też uwielbia seriale.
Zacznij od emoji."""

    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}]
        )
        return response.content[0].text.strip()
    except Exception as e:
        print(f"AI podsumowanie err: {e}")
        return ""

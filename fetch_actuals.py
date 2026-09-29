"""Macro Score : récupère les derniers chiffres officiels (Actual) de l'USD sur FRED.

Exécuté par GitHub Actions. La clé FRED est lue dans la variable FRED_API_KEY
(secret du dépôt). Écrit un fichier actuals.json lu par l'application.
Usage : python3 fetch_actuals.py _site/actuals.json
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

API = "https://api.stlouisfed.org/fred"

# clé de l'app -> {type de valeur: (série FRED, calcul)}
#   yy = variation sur 12 mois (%), mm = variation sur 1 mois (%),
#   chg = variation absolue sur 1 mois, level = dernière valeur telle quelle
SERIES = {
    "cpi":    {"yy": ("CPIAUCNS", "yy"), "mm": ("CPIAUCSL", "mm")},
    "core":   {"yy": ("CPILFENS", "yy"), "mm": ("CPILFESL", "mm")},
    "ppi":    {"yy": ("PPIFIS", "yy"), "mm": ("PPIFIS", "mm")},
    "nfp":    {"chg": ("PAYEMS", "chg")},
    "unrate": {"level": ("UNRATE", "level")},
    "ahe":    {"yy": ("CES0500000003", "yy"), "mm": ("CES0500000003", "mm")},
    "gdp":    {"level": ("A191RL1Q225SBEA", "level")},
    "retail": {"yy": ("RSAFS", "yy"), "mm": ("RSAFS", "mm")},
    "rate":   {"level": ("DFEDTARU", "level")},
}


def get(path, key, **params):
    params.update(api_key=key, file_type="json")
    url = f"{API}/{path}?{urllib.parse.urlencode(params)}"
    last = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "macro-score/1.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except Exception as e:  # réseau, quota : on réessaie
            last = e
            time.sleep(2 + attempt * 3)
    raise last


def parse_updated(s):
    """'2026-09-11 07:44:02-05' -> millisecondes depuis 1970 (UTC)."""
    s = s.strip()
    if len(s) >= 3 and s[-3] in "+-" and s[-2:].isdigit():
        s = s + ":00"
    return int(datetime.fromisoformat(s.replace(" ", "T")).timestamp() * 1000)


def compute(obs, how):
    """obs : observations triées de la plus récente à la plus ancienne."""
    vals = [(o["date"], float(o["value"])) for o in obs if o.get("value") not in (None, "", ".")]
    if not vals:
        return None
    d0, v0 = vals[0]
    if how == "level":
        return d0, round(v0, 2)
    if how == "chg":
        if len(vals) < 2:
            return None
        return d0, round(v0 - vals[1][1], 1)
    if how == "mm":
        if len(vals) < 2 or vals[1][1] == 0:
            return None
        return d0, round((v0 / vals[1][1] - 1) * 100, 1)
    if how == "yy":
        if len(vals) < 13 or vals[12][1] == 0:
            return None
        return d0, round((v0 / vals[12][1] - 1) * 100, 1)
    return None


def main(out_path, key=None, fetch=get):
    key = key or os.environ.get("FRED_API_KEY", "").strip()
    if not key:
        print("FRED_API_KEY absente : aucun Actual automatique cette fois.")
        return 0
    cache, result = {}, {}
    for name, kinds in SERIES.items():
        for kind, (sid, how) in kinds.items():
            try:
                if sid not in cache:
                    info = fetch("series", key, series_id=sid)
                    upd = parse_updated(info["seriess"][0]["last_updated"])
                    limit = 40 if sid == "DFEDTARU" else 20
                    obs = fetch("series/observations", key, series_id=sid,
                                sort_order="desc", limit=limit)["observations"]
                    cache[sid] = (upd, obs)
                upd, obs = cache[sid]
                r = compute(obs, how)
                if r:
                    result.setdefault(name, {})[kind] = {"v": r[1], "obs": r[0], "upd": upd, "series": sid}
            except Exception as e:
                print(f"{sid} ({name}/{kind}) indisponible : {e}")
    if not result:
        print("Aucune série récupérée.")
        return 0
    data = {"generatedAt": int(datetime.now(timezone.utc).timestamp() * 1000),
            "source": "FRED, Federal Reserve Bank of St. Louis", "USD": result}
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print(f"{sum(len(v) for v in result.values())} valeurs USD écrites dans {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "actuals.json"))

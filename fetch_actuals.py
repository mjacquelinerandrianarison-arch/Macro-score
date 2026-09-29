"""Macro Score : récupère les derniers chiffres officiels (Actual).

- USD : FRED (clé lue dans la variable FRED_API_KEY, secret du dépôt)
- EUR : Eurostat et BCE (sans clé)
Exécuté par GitHub Actions. Écrit un fichier actuals.json lu par l'application.
Usage : python3 fetch_actuals.py _site/actuals.json
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.error
import urllib.request
import csv
import io
from datetime import datetime, timezone, timedelta

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


def fetch_usd(key, fetch=get):
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
                print(f"USD {sid} ({name}/{kind}) indisponible : {e}")
    return result


# ---------------------------------------------------------------- EUR
EUROSTAT = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data"
ECB = "https://data-api.ecb.europa.eu/service/data"
GEOS = ["EA", "EA21", "EA20"]  # zone euro (composition évolutive), puis à 21 et 20 pays

# clé de l'app -> type -> (jeu Eurostat, filtres, fréquence, délai maximal en mois entre la période et l'annonce)
EUR_SERIES = {
    "cpi":    {"yy": ("prc_hicp_manr", {"unit": "RCH_A", "coicop": "CP00"}, "M", 1),
               "mm": ("prc_hicp_mmor", {"unit": "RCH_M", "coicop": "CP00"}, "M", 1)},
    "core":   {"yy": ("prc_hicp_manr", {"unit": "RCH_A", "coicop": "TOT_X_NRG_FOOD"}, "M", 1),
               "mm": ("prc_hicp_mmor", {"unit": "RCH_M", "coicop": "TOT_X_NRG_FOOD"}, "M", 1)},
    "ppi":    {"mm": ("sts_inppd_m", {"indic_bt": "PRC_PRR_DOM", "nace_r2": "B-E36", "s_adj": "NSA", "unit": "PCH_PRE"}, "M", 2),
               "yy": ("sts_inppd_m", {"indic_bt": "PRC_PRR_DOM", "nace_r2": "B-E36", "s_adj": "NSA", "unit": "PCH_SM"}, "M", 2)},
    "unrate": {"level": ("une_rt_m", {"age": "TOTAL", "sex": "T", "s_adj": "SA", "unit": "PC_ACT"}, "M", 2)},
    "gdp":    {"level": ("namq_10_gdp", {"na_item": "B1GQ", "unit": "CLV_PCH_PRE", "s_adj": "SCA"}, "Q", 3)},
    "retail": {"mm": ("sts_trtu_m", {"indic_bt": "VOL_SLS", "nace_r2": "G47", "s_adj": "SCA", "unit": "PCH_PRE"}, "M", 2),
               "yy": ("sts_trtu_m", {"indic_bt": "VOL_SLS", "nace_r2": "G47", "s_adj": "CA", "unit": "PCH_SM"}, "M", 2)},
    # variation de l'emploi (q/q, %) : rangée sous la clé "nfp" de l'app, type "chg"
    "nfp":    {"chg": ("namq_10_pe", {"na_item": "EMP_DC", "unit": "PCH_PRE_PER", "s_adj": "SCA"}, "Q", 3)},
}


def http_get(url, fmt="json"):
    last = None
    for attempt in range(2):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "macro-score/1.0", "Accept": "*/*"})
            with urllib.request.urlopen(req, timeout=40) as r:
                raw = r.read().decode("utf-8")
                return json.loads(raw) if fmt == "json" else raw
        except urllib.error.HTTPError as e:
            if e.code in (400, 404):  # filtre ou code pays inconnu : inutile de réessayer
                raise
            last = e
        except Exception as e:
            last = e
        time.sleep(2)
    raise last


def iso_to_ms(s):
    s = s.strip()
    if len(s) >= 5 and s[-5] in "+-" and s[-4:].isdigit():  # +0200 -> +02:00
        s = s[:-2] + ":" + s[-2:]
    return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() * 1000)


def period_end_month(p):
    """'2026-08' -> '2026-08' ; '2026-Q3' -> '2026-09'."""
    if "-Q" in p:
        y, q = p.split("-Q")
        return f"{y}-{int(q) * 3:02d}"
    return p[:7]


def jsonstat_last(data):
    """Dernière observation d'une réponse JSON-stat où tous les filtres sauf le temps sont fixés."""
    ids, sizes = data["id"], data["size"]
    if any(sz != 1 for d, sz in zip(ids, sizes) if d != "time"):
        raise ValueError("filtre incomplet : plusieurs séries renvoyées")
    tpos = ids.index("time")
    stride = 1
    for sz in sizes[tpos + 1:]:
        stride *= sz
    tidx = data["dimension"]["time"]["category"]["index"]
    values = data.get("value", {})
    get_v = (lambda k: values.get(str(k))) if isinstance(values, dict) else (lambda k: values[k] if k < len(values) else None)
    best = None
    for label, i in tidx.items():
        v = get_v(i * stride)
        if v is not None and (best is None or label > best[0]):
            best = (label, v)
    return best


def since(freq):
    d = datetime.now(timezone.utc) - timedelta(days=730)
    return f"{d.year}-Q{(d.month - 1) // 3 + 1}" if freq == "Q" else f"{d.year}-{d.month:02d}"


def fetch_eur(get_json=http_get):
    result, now_ms = {}, int(datetime.now(timezone.utc).timestamp() * 1000)
    for name, kinds in EUR_SERIES.items():
        for kind, (ds, flt, freq, lag) in kinds.items():
            found = None
            for geo in GEOS:
                q = dict(flt, geo=geo, freq=freq, format="JSON", lang="EN", sinceTimePeriod=since(freq))
                try:
                    data = get_json(f"{EUROSTAT}/{ds}?{urllib.parse.urlencode(q)}")
                    last = jsonstat_last(data)
                    if not last:
                        continue
                    upd = iso_to_ms(data["updated"]) if data.get("updated") else now_ms
                    if found is None or last[0] > found[0]:
                        found = (last[0], last[1], upd, geo)
                except Exception as e:
                    print(f"EUR {ds} {geo} ({name}/{kind}) : {e}")
            if found:
                p, v, upd, geo = found
                result.setdefault(name, {})[kind] = {"v": round(float(v), 2 if kind == "level" and name == "unrate" else 1),
                    "obs": period_end_month(p) + "-01", "period": p, "upd": upd,
                    "series": f"Eurostat {ds} {geo}", "maxLag": lag}
    # taux de la BCE : taux des opérations principales de refinancement
    try:
        raw = get_json(f"{ECB}/FM/B.U2.EUR.4F.KR.MRR_FR.LEV?format=csvdata&lastNObservations=5", fmt="csv")
        rows = [r for r in csv.DictReader(io.StringIO(raw)) if r.get("OBS_VALUE")]
        rows.sort(key=lambda r: r["TIME_PERIOD"])
        if rows:
            last = rows[-1]
            result["rate"] = {"level": {"v": round(float(last["OBS_VALUE"]), 2), "obs": last["TIME_PERIOD"],
                                        "upd": now_ms, "series": "BCE FM MRR_FR"}}
    except Exception as e:
        print(f"EUR taux BCE indisponible : {e}")
    return result


def main(out_path, key=None, fetch=get, get_json=http_get):
    key = key if key is not None else os.environ.get("FRED_API_KEY", "").strip()
    data = {"generatedAt": int(datetime.now(timezone.utc).timestamp() * 1000),
            "source": "FRED (USD), Eurostat et BCE (EUR)"}
    if key:
        usd = fetch_usd(key, fetch)
        if usd:
            data["USD"] = usd
    else:
        print("FRED_API_KEY absente : pas d'Actual USD cette fois.")
    eur = fetch_eur(get_json)
    if eur:
        data["EUR"] = eur
    for c in ("USD", "EUR"):
        if c in data:
            print(f"{c} : {sum(len(v) for v in data[c].values())} valeurs")
    if "USD" not in data and "EUR" not in data:
        print("Aucune série récupérée.")
        return 0
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print(f"Écrit dans {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "actuals.json"))

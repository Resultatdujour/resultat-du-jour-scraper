#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Scraper des resultats de loterie — Cote d'Ivoire (Loto Bonheur / LONACI).

Principe :
  1) Se connecte a Supabase avec le compte dedie du scraper.
  2) Lit, dans la base, les tirages suivis pour la Cote d'Ivoire (avec leur heure).
  3) Interroge l'API officielle du Loto Bonheur (donnees JSON propres).
  4) Fait correspondre chaque resultat a un tirage PAR SON HEURE.
  5) Ecrit les resultats dans la base (idempotent : re-ecrire n'abime rien).

Aucune cle secrete dans ce fichier : seuls l'email et le mot de passe du
scraper sont fournis par les "secrets" de GitHub (variables d'environnement).
"""

import os
import re
import sys
from datetime import date, timedelta

import requests
from supabase import create_client

# --- Connexion Supabase : URL + cle PUBLIQUE (sans danger, deja publiques sur le site) ---
SUPABASE_URL = "https://iviyzfaqinkoiiaylvdn.supabase.co"
SUPABASE_ANON_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Iml2aXl6ZmFxaW5rb2lpYXlsdmRuIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODUzNTA0NjUsImV4cCI6MjEwMDkyNjQ2NX0."
    "QjUUBPDwu0HGYl33jYVlktYXLQvLBqC0U58Sgp_QLxc"
)

# --- Identifiants du scraper : fournis par les secrets GitHub (jamais ecrits ici) ---
EMAIL = os.environ.get("SCRAPER_EMAIL", "")
PASSWORD = os.environ.get("SCRAPER_PASSWORD", "")

COUNTRY_CODE = "CI"
DAYS_BACK = 3  # on traite les resultats des 3 derniers jours
MONTHS_FR = [
    "janvier", "fevrier", "mars", "avril", "mai", "juin",
    "juillet", "aout", "septembre", "octobre", "novembre", "decembre",
]
# L'API attend le mois en francais avec accents ; on garde une version accentuee.
MONTHS_FR_ACCENTS = [
    "janvier", "f\u00e9vrier", "mars", "avril", "mai", "juin",
    "juillet", "ao\u00fbt", "septembre", "octobre", "novembre", "d\u00e9cembre",
]

API_URL = "https://lotobonheur.ci/api/results"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}


def log(*a):
    print(*a, flush=True)


def parse_numbers(s):
    """'12 - 45 - 07 - 88 - 23' -> [12, 45, 7, 88, 23] ; ignore les points et vides."""
    out = []
    for part in str(s).replace("\u2013", "-").split("-"):
        part = part.strip()
        if part.isdigit():
            out.append(int(part))
    return out


def hour_from_name(name):
    """Extrait l'heure d'un nom de tirage : 'Digital Reveil 8h' -> 8."""
    m = re.search(r"(\d{1,2})\s*[hH]", str(name))
    return int(m.group(1)) if m else None


def main():
    if not EMAIL or not PASSWORD:
        log("ERREUR : identifiants manquants (secrets SCRAPER_EMAIL / SCRAPER_PASSWORD).")
        sys.exit(1)

    log("=== Scraper Cote d'Ivoire ===")
    sb = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)

    # 1) Connexion du scraper
    try:
        auth = sb.auth.sign_in_with_password({"email": EMAIL, "password": PASSWORD})
        if not getattr(auth, "session", None):
            log("ERREUR : connexion refusee (verifie l'email / mot de passe du scraper).")
            sys.exit(1)
        try:
            sb.postgrest.auth(auth.session.access_token)
        except Exception:
            pass  # selon la version, le jeton est deja pris en compte automatiquement
        log("Connexion Supabase : OK")
    except Exception as e:
        log("ERREUR de connexion Supabase :", repr(e))
        sys.exit(1)

    # 2) Pays + decalage horaire
    countries = (
        sb.table("countries").select("id,code,tz_offset").eq("code", COUNTRY_CODE).execute().data
    )
    if not countries:
        log(f"ERREUR : pays {COUNTRY_CODE} introuvable dans la base.")
        sys.exit(1)
    country_id = countries[0]["id"]
    tz_offset = countries[0].get("tz_offset") or 0
    log(f"Pays {COUNTRY_CODE} : id={country_id}, decalage horaire={tz_offset}h")

    # 3) Nos tirages suivis (heure -> tirage)
    draws = (
        sb.table("draws").select("id,name,hour")
        .eq("country_id", country_id).eq("active", True).execute().data
    )
    hour_to_draw = {}
    for d in draws:
        try:
            hour_to_draw[int(round(float(d["hour"])))] = d
        except Exception:
            pass
    log(f"Tirages suivis ({len(hour_to_draw)}) : "
        + ", ".join(f"{h}h={d['name']}" for h, d in sorted(hour_to_draw.items())))
    if not hour_to_draw:
        log("Aucun tirage suivi pour ce pays. Rien a faire.")
        return

    # 4) Appel de l'API Loto Bonheur (mois courant)
    today = date.today()
    month_year = f"{MONTHS_FR_ACCENTS[today.month - 1]} {today.year}"
    params = {"monthYear": month_year, "drawType": "Tous les tirages"}
    log(f"Appel API : {API_URL} (mois : {month_year})")
    try:
        r = requests.get(API_URL, params=params, headers=HEADERS, timeout=25)
        log("Statut HTTP :", r.status_code)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        log("ERREUR lors de l'appel API :", repr(e))
        sys.exit(1)

    weeks = data.get("drawsResultsWeekly", []) if isinstance(data, dict) else []
    log(f"Semaines de resultats recues : {len(weeks)}")

    recent = {today - timedelta(days=i) for i in range(DAYS_BACK)}
    seen = 0
    written = 0

    for week in weeks:
        for day in week.get("drawResultsDaily", []):
            date_str = day.get("date", "")  # ex. "samedi 09/05"
            m = re.search(r"(\d{1,2})/(\d{1,2})", date_str)
            if not m:
                continue
            dd, mm = int(m.group(1)), int(m.group(2))
            try:
                d_obj = date(today.year, mm, dd)
            except ValueError:
                continue
            if d_obj not in recent:
                continue

            dr = day.get("drawResults", {}) or {}
            entries = (dr.get("standardDraws", []) or []) + (dr.get("nightDraws", []) or [])
            for draw in entries:
                name = draw.get("drawName", "")
                win_raw = draw.get("winningNumbers", "")
                if not name or name == "-" or not win_raw or win_raw.strip().startswith("."):
                    continue
                seen += 1

                win = parse_numbers(win_raw)
                if len(win) != 5:
                    continue
                mac = parse_numbers(draw.get("machineNumbers", ""))

                src_hour = hour_from_name(name)
                if src_hour is None:
                    continue
                togo_hour = src_hour - tz_offset  # Cote d'Ivoire : decalage = 0
                match = hour_to_draw.get(togo_hour)
                if not match:
                    continue  # tirage non suivi pour ce pays

                row = {
                    "country_id": country_id,
                    "draw_id": match["id"],
                    "draw_name": match["name"],
                    "draw_date": d_obj.isoformat(),
                    "winning_numbers": win,
                    "machine_numbers": mac if len(mac) == 5 else None,
                    "status": "published",
                    "source": "scraper-lotobonheur",
                }
                try:
                    sb.table("results").upsert(row, on_conflict="draw_id,draw_date").execute()
                    written += 1
                    log(f"  ecrit : {d_obj.isoformat()} | {match['name']} ({src_hour}h) | {win}"
                        + (f" | machine {mac}" if len(mac) == 5 else ""))
                except Exception as e:
                    log(f"  ECHEC ecriture {match['name']} {d_obj.isoformat()} :", repr(e))

    log(f"=== Termine : {seen} tirages lus (fenetre {DAYS_BACK} jours), "
        f"{written} resultats ecrits/mis a jour. ===")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Scraper des resultats de loterie — plateforme Ubiq.
Couvre la Cote d'Ivoire (Loto Bonheur / LONACI) ET le Benin (LNB),
qui partagent la meme API (memes donnees JSON).

Principe pour CHAQUE pays :
  1) Lit dans la base les tirages suivis (avec leur heure, en heure du Togo).
  2) Interroge l'API du pays.
  3) Fait correspondre chaque resultat PAR SON HEURE.
     - Le Benin est en GMT+1 : on retire 1h pour passer en heure du Togo.
     - Cas special : un tirage de minuit (00H Benin) devient 23h la veille (Togo).
  4) Ecrit les resultats dans la base (idempotent).
"""

import os
import re
import sys
from datetime import date, timedelta

import requests
from supabase import create_client

SUPABASE_URL = "https://iviyzfaqinkoiiaylvdn.supabase.co"
SUPABASE_ANON_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Iml2aXl6ZmFxaW5rb2lpYXlsdmRuIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODUzNTA0NjUsImV4cCI6MjEwMDkyNjQ2NX0."
    "QjUUBPDwu0HGYl33jYVlktYXLQvLBqC0U58Sgp_QLxc"
)

EMAIL = os.environ.get("SCRAPER_EMAIL", "")
PASSWORD = os.environ.get("SCRAPER_PASSWORD", "")

DAYS_BACK = 3
MONTHS_FR_ACCENTS = [
    "janvier", "f\u00e9vrier", "mars", "avril", "mai", "juin",
    "juillet", "ao\u00fbt", "septembre", "octobre", "novembre", "d\u00e9cembre",
]

COUNTRIES = [
    {"code": "CI", "api": "https://lotobonheur.ci/api/results"},
    {"code": "BJ", "api": "https://www.lnbloto.bj/api/results"},
]

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
    out = []
    for part in str(s).replace("\u2013", "-").split("-"):
        part = part.strip()
        if part.isdigit():
            out.append(int(part))
    return out


def hour_from_name(name):
    m = re.search(r"(\d{1,2})\s*[hH]", str(name))
    return int(m.group(1)) if m else None


def process_country(sb, cfg, today):
    code = cfg["code"]
    log("\n----- Pays : %s -----" % code)

    countries = (
        sb.table("countries").select("id,code,tz_offset").eq("code", code).execute().data
    )
    if not countries:
        log("  Pays %s introuvable dans la base, on passe." % code)
        return
    country_id = countries[0]["id"]
    tz_offset = countries[0].get("tz_offset") or 0
    log("  id=%s | decalage horaire=%sh" % (country_id, tz_offset))

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
    log("  Tirages suivis : " + ", ".join(
        "%sh=%s" % (h, d["name"]) for h, d in sorted(hour_to_draw.items())))
    if not hour_to_draw:
        log("  Aucun tirage suivi pour ce pays, on passe.")
        return

    month_year = "%s %s" % (MONTHS_FR_ACCENTS[today.month - 1], today.year)
    try:
        r = requests.get(
            cfg["api"],
            params={"monthYear": month_year, "drawType": "Tous les tirages"},
            headers=HEADERS, timeout=25,
        )
        log("  API %s -> HTTP %s (mois : %s)" % (cfg["api"], r.status_code, month_year))
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        log("  ERREUR API %s : %r (on passe au pays suivant)" % (code, e))
        return

    weeks = data.get("drawsResultsWeekly", []) if isinstance(data, dict) else []
    log("  Semaines de resultats recues : %s" % len(weeks))

    recent = {today - timedelta(days=i) for i in range(DAYS_BACK)}
    written = 0
    unmatched = {}

    for week in weeks:
        for day in week.get("drawResultsDaily", []):
            m = re.search(r"(\d{1,2})/(\d{1,2})", day.get("date", ""))
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
                win = parse_numbers(win_raw)
                if len(win) != 5:
                    continue
                mac = parse_numbers(draw.get("machineNumbers", ""))

                src_hour = hour_from_name(name)
                d_result = d_obj
                togo_hour = None
                if src_hour is not None:
                    togo_hour = src_hour - tz_offset
                    if togo_hour < 0:
                        togo_hour += 24
                        d_result = d_obj - timedelta(days=1)

                match = hour_to_draw.get(togo_hour) if togo_hour is not None else None
                if not match:
                    unmatched[name] = src_hour
                    continue

                row = {
                    "country_id": country_id,
                    "draw_id": match["id"],
                    "draw_name": match["name"],
                    "draw_date": d_result.isoformat(),
                    "winning_numbers": win,
                    "machine_numbers": mac if len(mac) == 5 else None,
                    "status": "published",
                    "source": "scraper-%s" % code,
                }
                try:
                    sb.table("results").upsert(row, on_conflict="draw_id,draw_date").execute()
                    written += 1
                    log("  ecrit : %s | %s | %s%s" % (
                        d_result.isoformat(), match["name"], win,
                        (" | machine %s" % mac) if len(mac) == 5 else ""))
                except Exception as e:
                    log("  ECHEC ecriture %s %s : %r" % (match["name"], d_result.isoformat(), e))

    log("  => %s resultats ecrits/mis a jour pour %s." % (written, code))
    if unmatched:
        parts = "; ".join("'%s' (heure=%s)" % (n, h) for n, h in unmatched.items())
        log("  (Tirages lus mais non suivis, pour info : %s)" % parts)


def main():
    if not EMAIL or not PASSWORD:
        log("ERREUR : identifiants manquants (secrets SCRAPER_EMAIL / SCRAPER_PASSWORD).")
        sys.exit(1)

    log("=== Scraper multi-pays (Cote d'Ivoire + Benin) ===")
    sb = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)

    try:
        auth = sb.auth.sign_in_with_password({"email": EMAIL, "password": PASSWORD})
        if not getattr(auth, "session", None):
            log("ERREUR : connexion refusee (verifie l'email / mot de passe du scraper).")
            sys.exit(1)
        try:
            sb.postgrest.auth(auth.session.access_token)
        except Exception:
            pass
        log("Connexion Supabase : OK")
    except Exception as e:
        log("ERREUR de connexion Supabase : %r" % e)
        sys.exit(1)

    today = date.today()
    for cfg in COUNTRIES:
        try:
            process_country(sb, cfg, today)
        except Exception as e:
            log("ERREUR inattendue pour %s : %r" % (cfg["code"], e))

    log("\n=== Termine. ===")


if __name__ == "__main__":
    main()

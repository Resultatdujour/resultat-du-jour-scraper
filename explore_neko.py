#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Explorateur du backend Firebase de l'appli Neko Dev (avec son autorisation).
But : decouvrir OU sont rangees les donnees et sous quelle forme.
N'ecrit rien nulle part : il lit et affiche seulement, pour que je comprenne
la structure, puis j'ecrirai le vrai scraper.
"""
import json
import requests

HOSTING = "https://neko-dev-fireb.web.app"
UA = {"User-Agent": "Mozilla/5.0 (compatible; explorer/1.0)"}


def log(*a):
    print(*a, flush=True)


# 1) Recuperer la configuration Firebase (fournie par Firebase Hosting)
log("=== 1) Configuration Firebase ===")
cfg = {}
try:
    r = requests.get(HOSTING + "/__/firebase/init.json", headers=UA, timeout=20)
    log(HOSTING + "/__/firebase/init.json  ->  HTTP", r.status_code)
    if r.status_code == 200:
        cfg = r.json()
        log(json.dumps(cfg, indent=2))
    else:
        log("Contenu :", r.text[:500])
except Exception as e:
    log("Erreur config :", repr(e))

project_id = cfg.get("projectId")
db_url = cfg.get("databaseURL")
api_key = cfg.get("apiKey")
log("\nprojectId =", project_id, "|  databaseURL =", db_url)

# 2) Realtime Database : structure (sans tout telecharger)
log("\n=== 2) Realtime Database (structure) ===")
if db_url:
    try:
        top = requests.get(db_url + "/.json", params={"shallow": "true"}, headers=UA, timeout=20)
        log("Racine (cles de 1er niveau) -> HTTP", top.status_code)
        log(top.text[:1500])
        keys = []
        try:
            j = top.json()
            if isinstance(j, dict):
                keys = list(j.keys())
        except Exception:
            pass
        for k in keys[:12]:
            sub = requests.get(db_url + "/" + k + ".json", params={"shallow": "true"}, headers=UA, timeout=20)
            log("  /" + str(k) + "  -> HTTP", sub.status_code, ":", sub.text[:700])
    except Exception as e:
        log("Erreur RTDB :", repr(e))
else:
    log("(pas de databaseURL dans la config -> probablement Firestore)")

# 3) Firestore : liste des collections racine
log("\n=== 3) Firestore (collections) ===")
if project_id:
    try:
        url = ("https://firestore.googleapis.com/v1/projects/" + project_id
               + "/databases/(default)/documents:listCollectionIds")
        params = {"key": api_key} if api_key else {}
        r = requests.post(url, params=params, json={}, headers=UA, timeout=20)
        log("listCollectionIds -> HTTP", r.status_code)
        log(r.text[:1500])
    except Exception as e:
        log("Erreur Firestore :", repr(e))
else:
    log("(pas de projectId -> impossible de tester Firestore)")

log("\n=== Fin de l'exploration ===")

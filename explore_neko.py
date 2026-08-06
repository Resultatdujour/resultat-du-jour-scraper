#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Explorateur v2 du backend Neko Dev (avec autorisation).
Se connecte comme le fait l'appli : en UTILISATEUR ANONYME (Firebase Auth),
puis lit la Realtime Database avec le jeton obtenu.
Ne modifie rien : lecture seule, affichage de la structure.
"""
import json
import requests

API_KEY = "AIzaSyDwe96aJva0CeNYTG8C9OyIRXYgqVTN7YY"
DB_URL = "https://neko-dev-fireb.firebaseio.com"
UA = {"User-Agent": "Mozilla/5.0"}


def log(*a):
    print(*a, flush=True)


# 1) Se connecter en anonyme (comme l'appli)
log("=== 1) Connexion anonyme (comme l'appli) ===")
token = None
try:
    url = "https://identitytoolkit.googleapis.com/v1/accounts:signUp"
    r = requests.post(url, params={"key": API_KEY},
                      json={"returnSecureToken": True}, headers=UA, timeout=20)
    log("Connexion anonyme -> HTTP", r.status_code)
    if r.status_code == 200:
        j = r.json()
        token = j.get("idToken")
        log("Jeton obtenu : OUI (utilisateur anonyme cree)")
    else:
        log("Reponse :", r.text[:600])
        log(">>> Si erreur 'ADMIN_ONLY_OPERATION' ou 'anonymous...disabled' :")
        log(">>> la connexion anonyme est DESACTIVEE chez lui. Il faudra l'Option 1.")
except Exception as e:
    log("Erreur connexion :", repr(e))


def read(path, shallow=False):
    params = {}
    if token:
        params["auth"] = token
    if shallow:
        params["shallow"] = "true"
    return requests.get(DB_URL + path + ".json", params=params, headers=UA, timeout=20)


# 2) Lire la racine (structure de 1er niveau)
log("\n=== 2) Structure de la base (avec le jeton) ===")
try:
    top = read("/", shallow=True)
    log("Racine -> HTTP", top.status_code)
    log(top.text[:2000])
    keys = []
    try:
        j = top.json()
        if isinstance(j, dict):
            keys = list(j.keys())
    except Exception:
        pass

    # 3) Explorer chaque branche de 1er niveau (juste les cles)
    for k in keys[:15]:
        sub = read("/" + k, shallow=True)
        log("\n  /" + str(k) + "  -> HTTP", sub.status_code)
        log("   cles :", sub.text[:900])
except Exception as e:
    log("Erreur lecture :", repr(e))

log("\n=== Fin ===")

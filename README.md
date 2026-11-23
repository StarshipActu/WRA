# StarshipActu Bot

Poste sur Discord, avec **une nouvelle carte à chaque nouvel avis** :
- **TFR** (FAA, tfr.faa.gov) : cercles/polygones d'exclusion + dates + altitudes
- **NOTMAR / NAVAREA IV** (NGA) : zones maritimes extraites du texte de l'avertissement
- **NOTAM** classiques (API FAA, optionnel, nécessite des clés)

Fond de carte : OpenFreeMap © OpenMapTiles, données OpenStreetMap (attribution visible sur l'image).

## Installation
```
pip install -r requirements.txt
playwright install chromium        # sous Linux : playwright install --with-deps chromium
cp .env.example .env               # puis remplir DISCORD_TOKEN et CHANNEL_ID
python bot.py
```
Permissions Discord à donner au bot dans le salon : Voir le salon, Envoyer des messages, Intégrer des liens, Joindre des fichiers.

Au premier lancement, les avis déjà actifs sont mémorisés sans être postés (POST_EXISTING_ON_FIRST_RUN=1 pour les poster).
Un avis modifié par la FAA est reposté avec une nouvelle carte (« Mise à jour »).

## Hébergement gratuit avec GitHub Actions
Le workflow `.github/workflows/poll.yml` lance `python bot.py --once` toutes les ~10 minutes.
- Secrets (Settings > Secrets and variables > Actions > Secrets) : `DISCORD_TOKEN` (+ `FAA_CLIENT_ID`, `FAA_CLIENT_SECRET` si NOTAM).
- Variables (onglet Variables) : `CHANNEL_ID` (ou `CHANNEL_TFR`, `CHANNEL_NOTAM`, `CHANNEL_NAVAREA`), et au choix `MAP_STYLE`, `TFR_PLACES`, etc.
- Un filtre à `*` signifie « tout garder » ; une variable vide utilise la valeur par défaut.
- Dépôt public recommandé (minutes Actions illimitées). Ne commitez jamais `.env`.

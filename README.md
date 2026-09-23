# Collection Manager

A self-hosted web app for discovering, improving and rotating Plex collections.
It runs in Docker alongside Radarr, Sonarr and Tautulli; Plex can be on a different
machine. Your instance starts empty and uses your own service credentials.

## Install

Follow [the VM installation guide](docs/SHARING_INSTALL.md). It covers Docker
Compose, service connections, login, upgrades, backups and rollback.

```bash
git clone https://github.com/jayballz69/Jamies-Media-Command.git collection-manager
cd collection-manager
cp .env.friend.example .env
sudo install -d -m 0700 -o 1000 -g 1000 web-data
# Edit .env: set VM_LAN_IP to your VM's LAN address.
docker compose -f compose.friend.yaml up -d --build
```

Open `http://<VM_LAN_IP>:8780` and choose your username and password.
Enter your own Plex and model connection in Settings.
Optional Arr connections provide catalog lookups and missing-title requests.
Use the named root-folder and quality-profile choices before requesting media.

## What it does

- Browse Plex collections in a dark, responsive interface.
- Paste lists with a collection name on the first line and `Title (Year)` below.
- Create a collection from a prompt or a surprise idea grounded in your library.
- Improve existing collections with library-only or external picks and fit reasons.
- Review owned additions and missing suggestions inside each collection.
- Request missing films or shows through Arr. Requests can start downloads.
- Track requested arrivals in Plex every ten minutes by default and add them to
  managed published collections. Drafts must be applied or published first.
- Run **Drift**: temporary collections with independently reviewed themes, titles
  and names. Useful partial runs publish their reviewed shelves and retain older shelves in remaining spaces. Extra reviewed ideas stay queued. Drift arrivals require a
  fresh item and batch review before joining an existing shelf.
- Keep a discovery permanently, improve and keep it, or revisit Drift history.
- Rotate separate permanent movie/TV shelves alongside temporary Drift shelves.

The default layout is two permanent movie shelves, two permanent TV shelves, plus
two movie and two TV Drift shelves. Drift generates a larger pool once a week
(default target: 12 collections), then switches the four displayed shelves every
48 hours without regenerating. Previous pools remain in Drift history for browsing,
improvement and promotion. Permanent rotation defaults to 24 hours. Change pool
size, generation interval and shelf timing separately in Rotation.
Automation starts disabled on a fresh install; enable the desired switches in
Settings → Advanced after connecting and syncing your library.

Actor, director, studio and franchise collections use metadata evidence.
Household viewing is optional, aggregate inspiration; there are no personal-user
Drift shelves. AI judgments remain reviewable and can be wrong.

## Data and boundaries

Credentials and state live in your private data directory, never in this repository.
Retiring a shelf removes its managed Home placement and preserves Plex collections
and media. Exact matches and live ownership checks guard collection changes.

A compatible model endpoint is required for AI features. Hosted model calls send
collection/library facts to that provider and can incur charges. Optional external
metadata comes from read-only Arr catalog lookups. No hosted accounts or keys are
included. Browsing and existing collection rotation do not require model calls.

This release includes manual Trakt import. It does not include continuous external
feed subscriptions, poster overlays, episode-level download management, global
recommendation snooze/ignore memory, or per-collection scheduled AI improvements.

## Development

Python 3.13 is the container runtime.

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements-web.txt pytest
python -m pytest tests -q
python -m collection_web
```

Local execution binds to `127.0.0.1:8780` by default. Set `CM_DATA_DIR` to a private
directory outside the checkout. Your account password hash and application state are stored there.

The isolated browser checks intercept all requests and use synthetic data:

```bash
pip install playwright
playwright install chromium
python tools/web_ui_workflows.py --browser-channel chromium
```

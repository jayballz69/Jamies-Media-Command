# Install Collection Manager on your own VM

This guide installs an independent instance from a clean Git checkout. It uses
`compose.friend.yaml`, which creates its own Docker network and needs no existing
stack-specific paths or configuration. It does not mount your media folders.

## Requirements

- A Linux VM with Docker Engine, the Docker Compose plugin and Git.
- A reachable Plex server and its token.
- An OpenAI-compatible model endpoint and model name for AI curation. A local
  endpoint can work; a hosted endpoint may charge for requests.
- Optional Radarr/Sonarr for acquisition and Tautulli for aggregate viewing input.

Check `docker version`, `docker compose version` and `git --version`. Docker's
[Compose installation guide](https://docs.docker.com/compose/install/) covers the
plugin if it is missing. The app container runs as UID/GID `1000:1000`.

## First install

Clone the repository:

```bash
git clone https://github.com/jayballz69/Jamies-Media-Command.git collection-manager
cd collection-manager
cp .env.friend.example .env
sudo install -d -m 0700 -o 1000 -g 1000 web-data
```

Edit `.env`:

- Set `VM_LAN_IP` to the VM's specific LAN address to open the app from another
  computer. Its default, `127.0.0.1`, only accepts connections on the VM itself.
- Leave `CM_PORT=8780`, or choose an unused host port.
- Set `TZ` to your timezone if desired.
- Keep `CM_DATA_PATH=./web-data`, or use your own absolute data path. Create that
  directory with owner `1000:1000` and mode `0700` before starting the container.

Compose reads local values from `.env`; they are not included in the Git-tracked
example. See Docker's [environment-variable documentation](https://docs.docker.com/compose/how-tos/environment-variables/variable-interpolation/).

```bash
docker compose -f compose.friend.yaml config --quiet
docker compose -f compose.friend.yaml up -d --build
docker compose -f compose.friend.yaml ps
```

Open `http://<VM_LAN_IP>:8780` in a browser, adjusting the port if changed.
On first open, choose your own username and password (at least 12 characters),
confirm the password and select **Create account**. Later visits require both.
There is no predetermined password. The account is stored as a password hash in
the private data directory and survives container rebuilds.
With the loopback default, use a browser on the VM or an SSH tunnel.
The image's health check checks `/healthz`; `ps` should eventually show healthy.

## Connect your services

Open **Settings**, enter your own connection details and test them. Configure a
Plex URL such as `http://<PLEX_HOST>:32400`, then sync the movie/TV libraries. Set
the curator endpoint to its compatible `/v1` address and choose its model.

The simplest Arr setup uses services' published LAN ports, for example
`http://<ARR_HOST>:7878` and `http://<ARR_HOST>:8989`. These addresses must be
reachable from the container. `localhost` inside the app container refers to the
app itself. Select each service's root folder and quality profile from its
connection settings. API keys belong in your instance's Settings, not Git.

Review **Rotation** to set permanent movie/TV counts, displayed Drift slots, weekly
pool size, generation interval and shelf-switch interval. Defaults are 12 choices
per weekly generation, with two movie and two TV shelves switching every 48 hours.
Use **Advanced settings** for behavior toggles. Optional automatic requests can
start downloads through your own Arr services when you enable that action.

### Optional: join an existing Docker network

If Arr exposes no host ports, attach the app to the same existing Docker network.
Find its name with `docker network ls`. Create
`../collection-manager-network.yaml` outside the Git checkout:

```yaml
services:
  collection-manager:
    networks:
      - default
      - arr
networks:
  arr:
    external: true
    name: YOUR_EXISTING_NETWORK_NAME
```

Replace the placeholder with the actual network name. Include the extra file in
**every** Compose command for this installation, including upgrades and backups:

```bash
docker compose -f compose.friend.yaml -f ../collection-manager-network.yaml up -d --build
```

You can then use service names such as `http://radarr:7878` when those names exist
on the shared network. Docker documents this in [networking with Compose](https://docs.docker.com/compose/how-tos/networking/).

### Optional: HTTPS reverse proxy

Use `CM_SECURE_COOKIE=1` only when the browser accesses the app through HTTPS.
Set `CM_PUBLIC_ORIGIN` to that exact origin, such as `https://collections.example.com`,
including a nonstandard port if used. Configure the proxy to preserve the request
host. A proxy on the VM can use the default loopback binding; a proxy in another
container needs a shared network and the app's internal port `8780`.

## Back up and upgrade

The data directory contains your SQLite state, login material, stored integration
credentials and automatic backups. Keep it private and back it up separately
from source code. The commands below assume the default `./web-data`; substitute
your actual `CM_DATA_PATH` if changed.

First record the current commit and branch so you can return to them:

```bash
git rev-parse HEAD
git branch --show-current
git status --short
```

Stop the app briefly for a consistent full-directory backup. Keep the resulting
archive outside the checkout and note its filename:

```bash
docker compose -f compose.friend.yaml stop
backup_file="../collection-manager-data-$(date +%Y%m%d-%H%M%S).tar.gz"
sudo tar -czf "$backup_file" -C web-data .
sudo chmod 600 "$backup_file"
git pull --ff-only
docker compose -f compose.friend.yaml up -d --build
docker compose -f compose.friend.yaml ps
```

Only continue past the archive command if it succeeds. `git pull --ff-only`
refuses to invent a merge when local changes or history differ; resolve that
before upgrading. If the upgrade cannot proceed, restart the existing image with
`docker compose -f compose.friend.yaml start`.

After rebuilding, sign in and check **Activity**, connection status and a few
collections. A healthy container verifies startup, not every external service.

## Roll back

Stop the new container, then switch to the recorded previous commit:

```bash
docker compose -f compose.friend.yaml stop
git switch --detach <PREVIOUS_COMMIT>
```

If the new version changed stored data, restore the matching pre-upgrade archive.
Keep the failed version's state intact for diagnosis rather than overwriting it:

```bash
sudo mv web-data "../collection-manager-data-failed-$(date +%Y%m%d-%H%M%S)"
sudo install -d -m 0700 -o 1000 -g 1000 web-data
sudo tar -xzf <BACKUP_ARCHIVE> -C web-data
sudo chown -R 1000:1000 web-data
sudo chmod 700 web-data
docker compose -f compose.friend.yaml up -d --build
```

Use your actual data path in every filesystem command if customized. The
restore returns stored connections, password and schedules to their backup state.
For a later upgrade, switch back to your recorded branch before pulling again.
Bind mounts retain host filesystem permissions; Docker's [bind-mount guide](https://docs.docker.com/engine/storage/bind-mounts/)
explains that relationship.

## Share source, not your instance

Share the clean repository URL and this guide. Never include `.env`, the data
directory, password files, integration keys, database copies, migration input,
backups or browser/deployment artifacts. `.env` and the default `web-data/`
directory are ignored by the repository. A custom data directory should live
outside the checkout. The friend starts with their own password, services and
library; your running instance and collections are not copied to their VM.

## Android: nzb360 and Tailscale

Add the app through nzb360 **Settings > Services > Add service > Web Interface**
(menu wording may vary by version). Name it Collection Manager, enter the app's
full URL and choose **Fetch Favicon** to use its included icon. Leave **Use external
browser** off to open it inside nzb360. Sign in with the account created on your
instance; no API key or HTTP Basic Auth is needed. Web Interfaces may require PRO.
If the embedded view has a login problem, enable Use external browser.

For remote access, connect the phone and VM to your Tailscale network, then use
`http://<VM_TAILSCALE_IP>:8780`. Keep Tailscale connected when using that address.
The app must listen on the VM's Tailscale address as well as any desired LAN address;
the default friend Compose file binds only the address specified by `VM_LAN_IP`.
To expose only through Tailscale, set that variable to the VM's Tailscale IPv4 address
and recreate the container. Tailnet access rules must permit the connection.
The app contacts Plex and Arr from the VM, so its configured service URLs need not
change for mobile use. Do not add router port forwards for this setup.

The icon is also available at `/static/icon.png` and `/favicon.ico`, without login.
See the [developer's favicon announcement](https://www.reddit.com/r/nzb360/comments/qkv3iz/)
and [remote-access guide](https://github.com/Kev1000000/nzb360Guides/blob/main/remoteaccessguide.html).

## Seasonal Drift, requests and collection review

**Drift > Seasonal awareness** switches seasonal curation on or off immediately.
Individual occasions live under **Settings > Seasonal awareness**. The feature is
opt-in on a fresh installation and uses Australia/Brisbane dates:

- Halloween: 17-31 October; Christmas: 11-25 December.
- Easter: seven days before Easter Sunday through Easter Monday.
- St Patrick's Day: 14-17 March.
- Queensland school holidays: published state-school dates, with more viewing
  aimed at ages 10-16. Coverage runs through the October 2029 break; later dates
  are not guessed. Private-school dates can differ.
- Optional New Year, Valentine's Day and May the Fourth windows.

Active occasions guide generation and independent editorial review. Only shelves
with a reviewed seasonal connection get Home priority; those shelves remain in the
pool across weekly refreshes while the occasion lasts. Normal movie/TV slot counts
still apply. An occasion change can trigger a new pool when Drift scheduling is on;
otherwise use Generate weekly pool. Auto-publish remains a separate control.
This is curation guidance, not a parental-control or age-classification guarantee.

Requested suggestions show available Radarr/Sonarr queue progress, refreshed about
once a minute while the app is running. Plex arrivals follow the library-sync
interval. A series can appear in Plex before all episodes are downloaded. Queue
warnings are marked for attention in Arr. Catalog outages are distinguished from
ambiguous titles, and a failed bulk request preserves successful additions.

**Collections > Review permanent collections** makes one bounded AI review of the
whole permanent set. It saves a report of collection flavour, useful overlap and
owned additions. No titles are moved, removed, or requested. Individual Improve
also considers neighbouring collections. Large sets that exceed the review context
limit fail with a clear message instead of silently reviewing only a subset.

## New-arrival collection suggestions

Enable **Collections > New arrivals, familiar shelves > Suggest homes for new
movies and shows** (also in Advanced settings). Plex sync notices new movies and
series. Once a day the curator compares up to 40 pending titles with all published
permanent collections, using their themes, existing members and the new titles'
metadata. Further titles wait for the next daily batch, or **Review new arrivals**.
The first sync establishes a baseline; existing episodes and file upgrades do not
trigger repeat recommendations. Arrivals discovered while this option is off are
not queued. Existing pending reviews and suggestions are retained when paused.

Suggestions appear here and in each collection's **Could complete the picture**.
Each explains its fit. Choose **Add to collection** or **Dismiss**; detection and
AI review never change Plex membership. Collections imported from Plex must first
be managed here before adding. Dismissals are remembered, provider failures keep
pending titles for retry, and automatic retries run at most once a day. Changed
collection themes require a new review. Ordinary scans with no arrivals cost no
AI calls. Temporary Drift shelves are outside this feature; kept Drift shelves
retain their existing additional fit review when adding a title.

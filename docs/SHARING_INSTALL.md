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

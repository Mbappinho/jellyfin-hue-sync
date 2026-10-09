# Linux installation

This service runs on the host, uses Java for Hue DTLS, and launches Docker containers to analyze library files. It is not a Jellyfin plugin and does not need an application installed on your TV. Read [security](../SECURITY.md) before granting Docker access.

## 1. Check prerequisites

Use a Linux systemd host with Python 3.11+, JDK 21+ for building, a Java 21+ runtime, Docker, readable local library files, and connectivity to Jellyfin and a Hue Bridge v2. Node is required only to run the JavaScript development tests. Install these tools using your distribution's packages, then verify:

```bash
python3 --version
java -version
javac -version
docker version
systemctl --version
```

The installation account needs permission to traverse and read the library directory. The daemon receives Docker-group access: **this is root-equivalent access to the host**, not a rootless sandbox. Do not run it on a host whose administrators do not accept that boundary.

## 2. Build from source

```bash
git clone https://github.com/Mbappinho/jellyfin-hue-sync.git
cd jellyfin-hue-sync
python3 transport/build.py --test
python3 transport/verify_dist.py
python3 -m unittest discover -s tests -v
```

Set `JAVA_HOME` to a JDK 21+ installation if needed. Dependencies are SHA256-locked. Generated transport files must exist before staging an installation. A full UI test run requires Node; check for skipped tests rather than assuming an incomplete run passed every gate.

## 3. Prepare a dedicated Jellyfin API key

Create an API key for this integration in Jellyfin's administration dashboard. Save only that key, optionally followed by a newline, in a private regular file such as `$HOME/.config/jellyfin-hue-sync/api-key`. Use a local editor and restrict its parent directory and file:

```bash
mkdir -p "$HOME/.config/jellyfin-hue-sync"
chmod 700 "$HOME/.config/jellyfin-hue-sync"
# Create/edit api-key locally; do not put its value in a shell command.
chmod 600 "$HOME/.config/jellyfin-hue-sync/api-key"
```

Do not put a key in CLI arguments, a URL, screenshots, Git or an issue. This application's Jellyfin requests are read-only, but that does not restrict the permissions of the API key itself. The legacy `--settings-file` option reads a Jellyseerr settings file; a separate `--api-key-file` is preferable for a fresh install.

## 4. Identify the media mapping and immutable image

`--library-root` is the **host** directory containing the files Jellyfin sees under `--media-prefix` (default `/media`). For example, a Jellyfin path `/media/movies/example.mkv` with a host library at `/srv/media/library` requires `--library-root /srv/media/library`. If Jellyfin uses `/library` instead, pass `--media-prefix /library`. Confirm your mounts; do not edit application source to change this mapping.

Extraction expects `/usr/lib/jellyfin-ffmpeg/ffmpeg` inside the chosen image. A running compatible Jellyfin container can supply a preloaded, immutable image ID:

```bash
# Replace jellyfin with the actual container name.
IMAGE_ID="$(docker inspect --format '{{.Image}}' jellyfin)"
docker image inspect "$IMAGE_ID" --format '{{.Id}}'
```

Use this `sha256:...` ID or a preloaded `repository@sha256:...` digest, **not a mutable tag**. The extractor uses `--pull=never`; it will not pull or upgrade Jellyfin. CPU decoding is the default. `--decoder vaapi` is optional and needs a working `/dev/dri/renderD128` and compatible media/driver. A passing CPU fixture does not establish GPU or HDR compatibility.

## 5. Stage and inspect without host changes

Replace the example host paths with your own. The image variable comes from the preceding step; it is not a credential.

```bash
python3 scripts/install.py \
  --stage-dir build/install-root \
  --api-key-file "$HOME/.config/jellyfin-hue-sync/api-key" \
  --library-root /srv/media/library \
  --image "$IMAGE_ID" \
  --ack-docker-root
```

The stage directory must be empty or absent. This copies application files, private credentials, transport and a rendered systemd unit under that directory; it does not create a host user, reload systemd, or start a service. Treat the staged tree as private because it contains an API key. Review `build/install-root/etc/systemd/system/jellyfin-hue-sync.service` and the CLI help before applying.

Defaults are loopback binding on port 3187, CPU decoding, code at `/opt/jellyfin-hue-sync`, private state at `/var/lib/jellyfin-hue-sync`, and the key at `/etc/jellyfin-hue-sync/api-key`. State and credential directories are mode 0700; the credential file is 0600.

## 6. Choose loopback or trusted-LAN access

Loopback is the default. For a local browser use `http://127.0.0.1:3187`. To access a remote loopback installation, use SSH forwarding:

```bash
ssh -L 3187:127.0.0.1:3187 user@analysis-host
```

For direct trusted-LAN access, pass `--bind YOUR_PRIVATE_HOST_IP`. It automatically permits that IP in HTTP Host checks. Add `--allowed-host HOSTNAME` if using a hostname instead. Wildcard `--bind 0.0.0.0` requires explicit, repeated `--allowed-host` values. These must be bare hostnames or addresses, without a scheme or port.

**Host checks and CSRF are not authentication.** Restrict the port to trusted clients with the host firewall. Do not expose it through a tunnel, public reverse proxy or port forwarding. Never publish your key or the private state to make remote setup easier.

## 7. Apply explicitly, then start explicitly

Review the staging output first. Applying writes host files, creates/configures a dedicated service account, grants Docker access and reloads systemd. It does **not** start, enable or restart any service.

```bash
sudo python3 scripts/install.py \
  --apply \
  --api-key-file "$HOME/.config/jellyfin-hue-sync/api-key" \
  --library-root /srv/media/library \
  --image "$IMAGE_ID" \
  --ack-docker-root
```

Repeat the same non-default options used during staging, including bind, decoder, library prefix and install directories. Use repeated `--media-group GROUP` for existing groups needed to read your library. On filesystems with mount-wide ownership, check the actual mount permissions rather than changing the whole media tree.

After reviewing the installed unit:

```bash
sudo systemctl enable --now jellyfin-hue-sync.service
systemctl status jellyfin-hue-sync.service
journalctl -u jellyfin-hue-sync.service --since '10 minutes ago'
```

None of these commands restarts Jellyfin. The daemon has independent lifecycle and configuration. Library files are mounted read-only in extraction containers. CPU, memory, PIDs and network are constrained, but the service's Docker access remains privileged.

## 8. Pair and validate

Open the configured local control page. Stop other Hue Entertainment applications, press the bridge's physical button, pair, select the Entertainment area and choose the exact Jellyfin client. Device labels show Active/Idle plus a short ID suffix because display names can repeat. Start playback while choosing the device; do not select solely by name.

Run the four-second light test and check restoration. Enable sync once for automatic playback following. Verify [the beta acceptance checklist](validation.md) on your hardware. Avoid concurrent Hue streamers on the same area; REST ownership protection is best-effort, not an atomic handover guarantee.

## Updating and troubleshooting

Stop the sync service before replacing code or rerunning the installer; do not update a live instance in place. Back up private state securely, rebuild the transport, re-stage, review changes, then apply and start. Reapplying replaces the installed API key with the supplied file. The installer is not a transactional upgrade/rollback manager.

If colors do not start, check the exact active client ID, accessible source path, preloaded image and selected area. Uncached seeks can temporarily release the lights while preparing colors. Check CPU/RAM and disk use during long sessions. Never fix a playback problem by disabling a VPN or restarting the media stack without diagnosing it separately.

For logs/issues, remove credentials, names and private paths. Never upload `config.json`, `restore.json`, API-key files or complete session responses. See [validation](validation.md) for the tested evidence and outstanding compatibility limits.

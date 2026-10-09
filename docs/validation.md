# Validation and compatibility

## Evidence boundaries

This is an experimental community project. A passing unit test, a local DTLS loopback test and a physical lamp test are different evidence. Do not advertise hardware or Jellyfin versions that nobody has exercised.

## Physical baseline

One installation has been exercised: Ubuntu 26.04, Jellyfin 12.1.0 in Docker, Java 21, a Philips Hue Bridge v2 (BSB002), and a Xiaomi MiTV-MOOR2 using Jellyfin Android TV. The owner confirmed actual film-driven lamp colors, fluent TV playback, and restoration when leaving the video. This is user-reported validation on one setup, not a timing/latency benchmark or a multi-device certification.

The original development system also decoded real library SDR/HDR frames and sent HueStream packets to a physical bridge. Those files and credentials are private and are not bundled with this repository. Publication changes must be separately verified; the baseline is not evidence that every later change passed a physical test.

## Automated coverage

The suite covers playback-clock freshness, duplicate reports, pause/resume, seeking, path confinement, RGB frame boundaries, cache reuse, settings validation, lamp snapshot/restoration, ownership changes, secret-safe errors, HTTP Host/Origin/CSRF checks and UI behavior. The Java suite exercises a real local DTLS PSK endpoint, wrong-key rejection, packet validation and bounded handshake handling. Synthetic fixture data is explicitly test data, not a physical demonstration.

Build the transport before running the complete Python suite: dependency policy tests inspect the generated runtime jars. Node is needed for JavaScript runtime tests; a run that skips these is not equivalent to complete UI validation. See the CI workflow and README for the exact verified commands.

## Isolated Linux packaging checks

A staged public-copy installation was exercised separately on Linux with a synthetic local Jellyfin HTTP fixture and no paired Hue bridge. Checks included private key/state modes (0600/0700), the copied daemon's HTTP status and English page, and a clean SIGTERM exit. The rendered unit passed the actual `systemd-analyze verify` parser after a quoting defect was found and corrected. Staging does not apply the installer to the host or execute a boot-enabled systemd service.

A real network-isolated Docker FFmpeg extraction decoded eight 96-by-54 RGB samples from a self-generated two-second red SDR clip. Expected color values and an unchanged source SHA256 were checked. This does not establish real-media HDR, VAAPI, load, long-session or physical lamp compatibility.

Host `--apply`, service-account provisioning on a fresh machine, and a boot/reboot test remain separate checks. No live media-stack services were restarted for packaging validation. The public revision's automated tests and remote CI must still pass before it is treated as the release candidate.

## Beta acceptance checklist

Use the following checklist on your own hardware and report each result separately:

- [ ] Install from a clean checkout without editing application source.
- [ ] Pair the bridge; choose the intended exact Jellyfin client and correct Entertainment area.
- [ ] Check the four-second static light test and restoration.
- [ ] Start a library video without opening the control page; colors begin after preparation.
- [ ] Compare playback with sync off/on on the same video; record any new stutter.
- [ ] Pause and resume; confirm restoration and automatic restart.
- [ ] Seek forward and backward, including across 30-second segment boundaries.
- [ ] Stop the video; compare on/off, brightness and color with the pre-video settings.
- [ ] Switch to another item; old colors must not continue.
- [ ] Test a long session and record CPU/RAM, preparation stalls and color drift.
- [ ] Restart only the sync service while idle; saved settings survive.
- [ ] If you choose to test restart during sync, verify restoration and recovery; do not interrupt other viewers.
- [ ] Confirm a stream already owned by another Hue application is not taken over.

## Not established

Multiple physical bridge models, multiple homes, other operating systems as production hosts, broad GPU/codec/HDR compatibility, simultaneous rooms, non-1x playback and Internet exposure are not certified. Windows test execution alone does not mean Windows is a supported production extraction host. Additional testers and reproducible bug reports are needed.

## Reporting results

Include service commit/tag, server OS/architecture, Jellyfin and TV-client versions, CPU/GPU, decoder mode, bridge model, media codec/HDR type, and the checklist items exercised. Report expected/actual behavior and sanitized errors. Never include credentials, full session DTOs, private state or copyrighted media files.

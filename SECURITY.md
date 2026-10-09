# Security

## Deployment boundary

The control interface is unauthenticated. Host allowlisting, exact-Origin checks and CSRF protection reduce browser-based cross-origin attacks; they do not protect against a malicious user already on your LAN. Bind to loopback or a trusted private interface, restrict access with your firewall, and do not port-forward it or expose it through a public tunnel/reverse proxy.

A device allowed to reach the interface can change setup and control selected lamps. Public status exposes client names and now-playing metadata. Only give trusted users access.

## Credentials and privileges

Jellyfin access uses an API key held in a local file, not a command-line argument. Requests are read-only, but the Jellyfin API key may have broader permissions: do not describe it as a server-enforced read-only credential. Hue association credentials and recovery journals belong in the private state directory. Never paste these files into an issue.

Access to the Docker daemon is effectively root-equivalent on a typical Linux host. The isolated extraction container's read-only mount, lack of network and resource limits do not make Docker socket access rootless or sandbox the parent daemon from an administrator-level attacker.

Hue's local v1 control API is HTTP; continuous Entertainment packets use authenticated DTLS. Keep the bridge and service on a trusted network. Avoid core dumps and process-memory collection containing credentials. Public API errors and transport diagnostics intentionally omit raw input, credentials and exception details.

## Entertainment ownership and restoration

Ownership checks are best-effort. The Hue REST v1 API uses separate read and write requests; another application can acquire an area between an ownership check and the following update. This service cannot provide an atomic compare-and-swap handover guarantee. Do not run concurrent Entertainment controllers on the same area, even with different Hue credentials. Stop other controllers before pairing/testing and before enabling synchronization.

Restoration attempts to return individual lamp attributes, not arbitrary dynamic scenes or third-party effects. Network failures and externally changed ownership can prevent restoration. Keep private recovery state intact; deleting a journal or running a second instance to force recovery is unsafe. Use one service instance per private state directory. The daemon enforces a kernel-backed lifetime lock before reading/recovering that directory. Never unlink its lock file to bypass it.

Observed ownership takeovers fence restoration with a private `restore_blocked` journal marker. This intentionally prevents automatic replay, including after a restart. Stop synchronization and review the affected lights/recovery state before re-enabling it; do not erase the marker to force reconnection or upload the journal publicly. A write issued just before a takeover is detected cannot be undone by a later ownership check. If persisting a fencing marker fails, durable fencing across a restart is not guaranteed.

## Dependency and scope policy

Transport dependencies are version- and checksum-locked. Build from source and verify the distribution. Locked dependencies do not eliminate the need for future security updates. Compatibility and security auditing are limited; this repository has not had a formal independent penetration test.

## Reporting a vulnerability

If GitHub private vulnerability reporting is enabled, use the repository's Security tab. Otherwise contact the maintainer privately through their GitHub profile before disclosing a secret or working exploit publicly. For an accidental leak, revoke the affected credentials first. Do not include real keys in a public issue even when describing a reproduction.

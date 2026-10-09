# Hue Entertainment DTLS transport

`fr.theo.hueserver.StreamMain` is the Java 21 transport subprocess. Python owns bridge association, Entertainment activation, snapshots and restoration; Java owns authenticated UDP/DTLS and packet delivery.

## Build and verify

From the repository root:

```bash
python transport/build.py --test
python transport/verify_dist.py
```

Dependencies come from Maven Central and are checked against committed SHA256 values. Generated jars/classes belong in `transport-dist/`, not Git. JDK 21 or newer must be available through `JAVA_HOME` or PATH; a JRE without `javac` cannot build from source.

## Tests and proof boundary

Tests cover configuration validation, bounded UDP handshake fragments, a real loopback DTLS PSK server, wrong-key rejection, packet preservation and the stdin protocol. Subprocess tests check generic diagnostics and rejection before network access. They do not contact a physical bridge.

Physical bridge synchronization was exercised separately on one development setup. That does not certify all firmware, bridge models or future code revisions. See [validation](../docs/validation.md).

## Parent protocol

Launch without credential arguments:

```bash
java -Xms16m -Xmx64m -cp 'transport-dist/classes:transport-dist/lib/*' fr.theo.hueserver.StreamMain
```

Windows uses `;` instead of `:` as the classpath separator. The daemon uses an argument list, not a shell string.

1. Write exactly `ip`, `username` and `clientkey` in one JSON object to stdin, then flush. Never print this line in logs.
2. Wait for the exact `READY` line. It means the authenticated DTLS handshake succeeded, not merely that a process started.
3. Write one validated HueStream datagram per line as hexadecimal, then flush.
4. Close stdin on shutdown to close DTLS and the UDP socket.

Input lines are bounded. Production accepts canonical RFC1918 IPv4 addresses, not DNS, loopback, public addresses or IPv6, and uses UDP 2100. Local test seams are not production flags.

HueStream v1 has a 16-byte header and 9 bytes per lamp, with 1–10 unique lamp IDs. The 9-byte `HueStream` marker precedes version, sequence and reserved/mode bytes. RGB is unsigned 16-bit network order; the engine expands RGB8 with `channel * 257`.

## Failure behavior

Stdout contains only `READY` after the handshake. Nonzero exits use generic stderr labels, never raw input or exception traces:

| Exit | Labels |
| --- | --- |
| 2 | `invalid-arguments`, `invalid-config` |
| 3 | `handshake-failed` |
| 4 | `invalid-packet` |
| 6 | `stream-failed` |
| 7 | `runtime-unavailable` |

An extra guard bounds plaintext epoch-zero handshake fragments before DTLS reassembly. It supplements pinned dependencies, not upstream fixes. Credentials can remain in JVM strings, library buffers and process memory: zeroing owned byte arrays cannot guarantee complete erasure.

## Licensing

Dependencies retain embedded MIT notices. See [third-party notices](../THIRD_PARTY_NOTICES.md). This repository does not redistribute a JDK, Docker image or media file.

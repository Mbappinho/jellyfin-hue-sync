# Acknowledgements and provenance

The design was informed by the server-side analysis approach of [Jellyfin Ambilight](https://github.com/gabrielprat/jellyfin-ambilight), a separate WLED-focused project. Inspiration does not imply compatibility, endorsement or a shared release. That project's source has its own license; it is not bundled here.

The public tree contains the independent Python synchronizer and Java Hue transport, not the earlier experimental Android screen-capture application. The packet encoder evolved from that earlier prototype in this same project; its reference is not a claim that the Android application is included or required. Bouncy Castle supplies the DTLS implementation; see the exact dependency versions and notices in the transport build metadata and [third-party notices](THIRD_PARTY_NOTICES.md).

Implementation and documentation were developed with AI assistance and must be reviewed as experimental software. Do not assume code provenance from an architecture diagram alone: report any concrete copied-source/license concern with the affected file and upstream location.

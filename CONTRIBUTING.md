# Contributing

This project is experimental and maintained as a small community tool. Bug reports from other setups are especially useful. Read [validation and compatibility](docs/validation.md) before making support claims.

## Local checks

Use Python 3.11 or newer, JDK 21 or newer, and Node for UI tests. Build and verify the transport, then run the Python suite:

```bash
python transport/build.py --test
python transport/verify_dist.py
python -m unittest discover -s tests -v
```

The tests use synthetic fixtures or local test endpoints; they must not contact a real bridge, alter a media library, or need production credentials. Do not commit generated jars, build output, state or tokens. Add a failing regression test before behavior changes and keep patches focused.

## Pull requests

Describe the change, the failure it fixes, and the commands actually run. Identify skipped tests and hardware checks separately. Code, comments, documentation and interface strings are in English. Avoid promising support based only on an API response or passing mocks.

## Beta reports

Use the beta testing issue template, attach sanitized results and specify your exact versions. Please do not attach private state, server tokens, bridge credentials or copyrighted videos. There is no guaranteed response time or commercial support agreement.

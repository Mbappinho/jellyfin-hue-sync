"""Verify packaged runtime assets; write reproducible local integrity hashes."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
def require(condition, message):
    if not condition:
        raise SystemExit(message)

ROOT = Path(__file__).resolve().parent
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--dist', type=Path, default=ROOT.parent / 'transport-dist')
DIST = parser.parse_args().dist.resolve()
manifest = json.loads((DIST / 'dependencies.json').read_text(encoding='utf-8'))
locked = json.loads((ROOT / 'dependencies.lock.json').read_text(encoding='utf-8'))
expected = set(locked)
require(manifest == locked, 'Packaged dependency manifest differs from source lock')
require((DIST / 'licenses/Bouncy-Castle.txt').read_bytes() == (ROOT / 'licenses/Bouncy-Castle.txt').read_bytes(), 'Missing or changed third-party license')
require(set(manifest) == expected, 'Unexpected packaged dependencies')
for name, metadata in manifest.items():
    copied = (DIST / 'lib' / name).read_bytes()
    require(hashlib.sha256(copied).hexdigest() == metadata['sha256'], 'Packaged dependency digest mismatch')
require({file.name for file in (DIST / 'lib').iterdir()} == expected, 'Unexpected library artifact')
classes = sorted((DIST / 'classes').rglob('*.class'))
require(classes, 'Missing compiled classes')
require((DIST / 'classes/fr/theo/hueserver/StreamMain.class').is_file(), 'Missing entry point')
for file in classes:
    data = file.read_bytes()
    require(data[:4] == bytes.fromhex('cafebabe') and int.from_bytes(data[6:8], 'big') == 65, 'Incorrect Java target')
    require('fr/theo/hueserver/' in file.relative_to(DIST).as_posix(), 'Unexpected class package')
    for forbidden in (b'artificial_test_identity', b'00112233445566778899aabbccddeeff', b'fr/theo/huetvsync', b'android/'):
        require(forbidden not in data, 'Test fixture or Android reference leaked into distribution')
files = sorted((file for file in DIST.rglob('*') if file.is_file() and file.name != 'SHA256SUMS'))
lines = [hashlib.sha256(file.read_bytes()).hexdigest() + '  ' + file.relative_to(DIST).as_posix() for file in files]
(DIST / 'SHA256SUMS').write_text('\n'.join(lines) + '\n', encoding='utf-8')
print('DISTRIBUTION VERIFIED: ' + str(len(classes)) + ' Java 21 classes; 3 source-hash-locked jars with license; no artificial test credentials or Android references in classes')
print('SHA256SUMS entries: ' + str(len(lines)))

"""Subprocess protocol tests. No real credentials or network targets are used."""
import argparse
import shutil
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--dist',type=Path,default=ROOT.parent/'transport-dist')
parser.add_argument('--java')
args=parser.parse_args()
DIST=args.dist.resolve()
home=os.environ.get('JAVA_HOME')
java=args.java or (str(Path(home)/'bin'/('java.exe' if os.name=='nt' else 'java')) if home else shutil.which('java'))
if not java or not (Path(java).is_file() or shutil.which(java)) or not (DIST/'classes/fr/theo/hueserver/StreamMain.class').is_file():
    raise SystemExit('Java executable or transport distribution unavailable')
command = [str(java), "-Xmx128m", "-cp", str(DIST / "classes") + os.pathsep + str(DIST / "lib/*"), "fr.theo.hueserver.StreamMain"]
cases = [
    ("missing configuration", b"", [], 2, b"invalid-config\n"),
    ("invalid JSON", b"not JSON\n", [], 2, b"invalid-config\n"),
    ("public IPv4 refused before network", b'{"ip":"8.8.8.8","username":"artificial_fixture","clientkey":"00112233445566778899aabbccddeeff"}\n', [], 2, b"invalid-config\n"),
    ("loopback refused before network", b'{"ip":"127.0.0.1","username":"artificial_fixture","clientkey":"00112233445566778899aabbccddeeff"}\n', [], 2, b"invalid-config\n"),
    ("oversized configuration", b"x" * 5000 + b"\n", [], 2, b"invalid-config\n"),
    ("malformed UTF-8 configuration", b"\xff\n", [], 2, b"invalid-config\n"),
    ("arguments forbidden", b"", ["unexpected"], 2, b"invalid-arguments\n"),
]
passed = 0
for label, data, arguments, code, diagnostic in cases:
    result = subprocess.run(command + arguments, input=data, capture_output=True, timeout=15, check=False)
    # Diagnostics must be exact generic labels: never echo untrusted input or Java stack traces.
    if result.returncode != code or result.stdout != b"" or result.stderr.replace(b"\r\n", b"\n") != diagnostic:
        print("FAIL " + label + ": expected sanitized protocol behavior (raw output withheld)")
        sys.exit(1)
    passed += 1
    print("PASS " + label)
print("CLI SUBPROCESS TESTS PASS: " + str(passed))

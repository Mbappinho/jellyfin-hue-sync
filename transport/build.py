"""Build a Java 21 transport using the independently hash-locked dependencies."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
DIST = ROOT.parent / "transport-dist"
parser = argparse.ArgumentParser()
parser.add_argument("--test", action="store_true")
parser.add_argument("--offline", action="store_true")
parser.add_argument("--dist", type=Path, default=DIST)
parser.add_argument("--vendor", type=Path, default=ROOT / "vendor")
parser.add_argument("--java-home", type=Path)
parser.add_argument("--label", default="build")
args = parser.parse_args()
DIST = args.dist.resolve()
java_home = args.java_home or os.environ.get("JAVA_HOME")
suffix = ".exe" if os.name == "nt" else ""
if java_home:
    javac, java = (Path(java_home) / "bin" / (tool + suffix) for tool in ("javac", "java"))
else:
    javac, java = (shutil.which(tool) for tool in ("javac", "java"))
if not all(tool and Path(tool).is_file() for tool in (javac,java)):
    raise SystemExit("JDK 21+ tools not found; set JAVA_HOME, --java-home or PATH")
(DIST / "lib").mkdir(parents=True, exist_ok=True)
manifest = {}
import urllib.request
vendor = args.vendor.resolve()
vendor.mkdir(parents=True,exist_ok=True)
locked = json.loads((ROOT / "dependencies.lock.json").read_text(encoding="utf-8"))
for name, metadata in locked.items():
    source = vendor / name
    expected = metadata["sha256"]
    if not source.exists():
        if args.offline:
            raise SystemExit("Missing offline dependency: " + name)
        data = urllib.request.urlopen(metadata["url"], timeout=60).read()
        if hashlib.sha256(data).hexdigest() != expected:
            raise SystemExit("Locked dependency digest mismatch: " + name)
        source.write_bytes(data)
    if hashlib.sha256(source.read_bytes()).hexdigest() != expected:
        raise SystemExit("Locked dependency digest mismatch: " + name)
    shutil.copy2(source, DIST / "lib" / name)
    manifest[name] = metadata
for old_jar in (DIST / "lib").glob("*.jar"):
    if old_jar.name not in manifest:
        old_jar.unlink()
(DIST / "dependencies.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
(DIST / "licenses").mkdir(exist_ok=True)
shutil.copyfile(ROOT / "licenses/Bouncy-Castle.txt", DIST / "licenses/Bouncy-Castle.txt")
classes = DIST / "classes"
if classes.exists():shutil.rmtree(classes)
classes.mkdir(exist_ok=True)
cp = str(classes) + os.pathsep + str(DIST / "lib/*")
commands = []
source = sorted((ROOT / "src").rglob("*.java"))
if source:
    commands.append([str(javac), "--release", "21", "-Xlint:all", "-Werror", "-cp", cp, "-d", str(classes), *map(str, source)])
if args.test:
    tests = ROOT / "build/test-classes"
    tests.mkdir(parents=True, exist_ok=True)
    commands.append([str(javac), "--release", "21", "-Xlint:all", "-Werror", "-cp", cp, "-d", str(tests), *map(str, sorted((ROOT / "tests").rglob("*.java")))])
    for suite in ("ValidationTests", "DatagramGuardTests", "DtlsTransportTests", "StreamProtocolTests"):
        commands.append([str(java), "-cp", str(tests) + os.pathsep + cp, "fr.theo.hueserver." + suite])
    commands.append([sys.executable, str(ROOT / "cli_tests.py"), "--dist", str(DIST), "--java", str(java)])
outputs = []
rc = 0
for command in commands:
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    output = result.stdout + result.stderr
    print(output, end="")
    outputs.append(output)
    rc = result.returncode
    if rc:
        break
with (ROOT / "test-results.jsonl").open("a", encoding="utf-8") as log:
    log.write(json.dumps({"label": args.label, "exit": rc, "output": "".join(outputs)}) + "\n")
if not rc:
    print("BUILD PASS (Java 21; vendor-fixed bctls 1.86.2, bcprov/bcutil 1.86)")
sys.exit(rc)

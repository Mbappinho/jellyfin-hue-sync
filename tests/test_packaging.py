"""Public build/install contracts; tests never contact lamps or media services."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]

class BuildTests(unittest.TestCase):
    def test_rebuild_removes_stale_compiled_classes(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist=Path(tmp)/'dist';classes=dist/'classes';classes.mkdir(parents=True)
            stale=classes/'Stale.class';stale.write_bytes(b'NOT A REAL CLASS')
            result=subprocess.run([sys.executable,str(ROOT/'transport/build.py'),'--offline','--dist',str(dist)],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertFalse(stale.exists())

    def test_integrity_verification_cannot_be_disabled_with_python_optimization(self):
        import shutil
        with tempfile.TemporaryDirectory() as tmp:
            dist=Path(tmp)/'dist';shutil.copytree(ROOT/'transport-dist',dist)
            jar=dist/'lib/bcprov-jdk18on-1.86.jar'
            with jar.open('ab') as stream:stream.write(b'poisoned trailing data')
            result=subprocess.run([sys.executable,'-O',str(ROOT/'transport/verify_dist.py'),'--dist',str(dist)],capture_output=True,text=True)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('Packaged dependency digest mismatch',result.stdout+result.stderr)

    def test_cli_protocol_tests_accept_relocated_dist_and_explicit_java(self):
        result=subprocess.run([sys.executable,str(ROOT/'transport/cli_tests.py'),'--dist',str(ROOT/'missing-dist'),'--java','missing-java-binary'],capture_output=True,text=True)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('Java executable or transport distribution unavailable',result.stdout+result.stderr)

    def test_build_exposes_offline_and_relocatable_distribution_options(self):
        result=subprocess.run([sys.executable,str(ROOT/'transport/build.py'),'--help'],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        for flag in ('--offline','--dist','--vendor','--java-home'):
            self.assertIn(flag,result.stdout)

    def test_offline_relocatable_build_includes_third_party_license(self):
        import zipfile
        vendor=ROOT/'transport/vendor'
        if not all((vendor/name).is_file() for name in json.loads((ROOT/'transport/dependencies.lock.json').read_text())):
            self.skipTest('Run transport/build.py to populate the hash-locked cache first')
        with tempfile.TemporaryDirectory() as tmp:
            dist=Path(tmp)/'dist'
            result=subprocess.run([sys.executable,str(ROOT/'transport/build.py'),'--offline','--dist',str(dist)],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertTrue((dist/'classes/fr/theo/hueserver/StreamMain.class').is_file())
            with zipfile.ZipFile(vendor/'bcprov-jdk18on-1.86.jar') as jar:
                self.assertEqual((dist/'licenses/Bouncy-Castle.txt').read_bytes(),jar.read('META-INF/LICENSE.md'))
            self.assertNotIn(str(ROOT), (dist/'dependencies.json').read_text())
            result=subprocess.run([sys.executable,str(ROOT/'transport/verify_dist.py'),'--dist',str(dist)],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertTrue((dist/'SHA256SUMS').is_file())

    def test_explicit_java_home_is_honored_and_invalid_home_fails_clearly(self):
        with tempfile.TemporaryDirectory() as tmp:
            result=subprocess.run([sys.executable,str(ROOT/'transport/build.py'),'--java-home',str(Path(tmp)/'missing')],capture_output=True,text=True)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('JDK 21+ tools not found',result.stdout+result.stderr)

    def test_offline_cache_cannot_override_source_hash_lock_with_a_sidecar(self):
        import hashlib
        with tempfile.TemporaryDirectory() as tmp:
            vendor=Path(tmp)/'vendor';vendor.mkdir()
            name='bcprov-jdk18on-1.86.jar'
            (vendor/name).write_bytes(b'poisoned cache')
            (vendor/(name+'.sha256')).write_text(hashlib.sha256(b'poisoned cache').hexdigest())
            result=subprocess.run([sys.executable,str(ROOT/'transport/build.py'),'--offline','--vendor',str(vendor),'--dist',str(Path(tmp)/'dist')],capture_output=True,text=True)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('Locked dependency digest mismatch',result.stdout+result.stderr)
            self.assertFalse((Path(tmp)/'dist'/'lib'/name).exists())

class InstallTests(unittest.TestCase):
    def test_install_refuses_symlink_destination_components_before_writing(self):
        import importlib.util
        from unittest.mock import patch
        spec=importlib.util.spec_from_file_location('installer_test',ROOT/'scripts/install.py')
        installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);key=root/'key';key.write_text('UNIT_TEST_ONLY');key.chmod(0o600)
            library=root/'library';library.mkdir();stage=root/'stage'
            args=['--stage-dir',str(stage),'--api-key-file',str(key),'--library-root',str(library),'--image','sha256:'+'a'*64,'--ack-docker-root']
            # Windows without Developer Mode cannot create symlinks; simulate the filesystem flag.
            import contextlib,io
            with patch.object(Path,'is_symlink',lambda path:path.name=='jellyfin-hue-sync'),contextlib.redirect_stderr(io.StringIO()):
                self.assertNotEqual(installer.main(args),0)
            self.assertFalse(stage.exists())

    def test_staging_refuses_nonempty_directory_before_writing_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);stage=root/'stage';stage.mkdir();(stage/'existing').write_text('untouched')
            key=root/'key';key.write_text('UNIT_TEST_ONLY');key.chmod(0o600)
            library=root/'library';library.mkdir()
            result=subprocess.run([sys.executable,str(ROOT/'scripts/install.py'),'--stage-dir',str(stage),'--api-key-file',str(key),'--library-root',str(library),'--image','sha256:'+'a'*64,'--ack-docker-root'],capture_output=True,text=True)
            self.assertNotEqual(result.returncode,0)
            self.assertEqual(list(stage.iterdir()),[stage/'existing'])

    def test_staged_install_is_complete_private_and_does_not_manage_services(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);key=root/'source-key';key.write_text('UNIT_TEST_ONLY_NOT_REAL\n');key.chmod(0o600)
            stage=root/'stage';library=root/'library';library.mkdir()
            result=subprocess.run([sys.executable,str(ROOT/'scripts/install.py'),'--stage-dir',str(stage),'--api-key-file',str(key),'--library-root',str(library),'--image','sha256:'+'a'*64,'--ack-docker-root'],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            app=stage/'opt/jellyfin-hue-sync'
            self.assertTrue((app/'ambilight/__main__.py').is_file())
            self.assertTrue((app/'static/index.html').is_file())
            self.assertTrue((app/'transport-dist/classes/fr/theo/hueserver/StreamMain.class').is_file())
            self.assertEqual((stage/'etc/jellyfin-hue-sync/api-key').read_text().strip(),'UNIT_TEST_ONLY_NOT_REAL')
            unit=(stage/'etc/systemd/system/jellyfin-hue-sync.service').read_text()
            self.assertIn('User=jellyfin-hue-sync',unit)
            self.assertIn('--api-key-file',unit)
            self.assertNotIn('UNIT_TEST_ONLY_NOT_REAL',unit)
            self.assertNotIn('jellyfin.service',unit)
            self.assertIn('No services were started or restarted',result.stdout)
            self.assertIn('root-equivalent',result.stdout)
            if sys.platform=='linux':
                import shutil
                if shutil.which('systemd-analyze'):
                    # Validate the actual staged CLI artifact, not a shell-parsed rendering.
                    unitpath=stage/'etc/systemd/system/jellyfin-hue-sync.service'
                    verified=subprocess.run(['systemd-analyze','verify','--man=no','--generators=no',str(unitpath)],
                        capture_output=True,text=True,timeout=30)
                    self.assertEqual(verified.returncode,0,verified.stdout+verified.stderr)
                    self.assertNotIn(str(unitpath)+':',verified.stderr)
            if sys.platform!='win32':
                import stat
                self.assertEqual(stat.S_IMODE((stage/'etc/jellyfin-hue-sync/api-key').stat().st_mode),0o600)
                self.assertEqual(stat.S_IMODE((stage/'var/lib/jellyfin-hue-sync').stat().st_mode),0o700)
            # Run the actual copied package, against a local read-only Jellyfin fixture.
            import os,socket,time,threading,urllib.request
            from http.server import BaseHTTPRequestHandler,HTTPServer
            class JellyfinFixture(BaseHTTPRequestHandler):
                def do_GET(self):
                    self.send_response(200);self.end_headers();self.wfile.write(b'[]')
                def log_message(self,*_):pass
            fixture=HTTPServer(('127.0.0.1',0),JellyfinFixture)
            thread=threading.Thread(target=fixture.serve_forever,daemon=True);thread.start()
            with socket.socket() as listener:
                listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
            env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1')
            process=subprocess.Popen([sys.executable,'-m','ambilight','--library-root',str(library),
                '--api-key-file',str(stage/'etc/jellyfin-hue-sync/api-key'),'--image','sha256:'+'a'*64,
                '--state-dir',str(root/'runtime-state'),'--jellyfin-url','http://127.0.0.1:'+str(fixture.server_port),
                '--port',str(port)],cwd=app,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            try:
                deadline=time.monotonic()+5;status=None
                while time.monotonic()<deadline and process.poll() is None:
                    try:
                        with urllib.request.urlopen('http://127.0.0.1:'+str(port)+'/api/status',timeout=.5) as response:
                            status=json.loads(response.read());break
                    except OSError:time.sleep(.05)
                self.assertIsNotNone(status,'Copied package did not serve status')
                self.assertFalse(status['paired']);self.assertFalse(status['config']['enabled'])
            finally:
                process.terminate()
                try:process.communicate(timeout=5)
                except subprocess.TimeoutExpired:process.kill();process.communicate(timeout=5)
                fixture.shutdown();fixture.server_close();thread.join(2)

if __name__=='__main__':unittest.main()

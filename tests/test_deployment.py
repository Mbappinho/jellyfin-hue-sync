import configparser
import importlib.util
from pathlib import Path
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('installer',ROOT/'scripts/install.py')
installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)

class DeploymentTests(unittest.TestCase):
    def unit(self):
        args=installer.arguments(['--api-key-file','/private/key','--library-root','/srv/library',
             '--image','sha256:'+'b'*64,'--bind','192.168.50.2','--ack-docker-root'])
        parser=configparser.ConfigParser(interpolation=None);parser.optionxform=str
        parser.read_string(installer.render(args));return parser

    def test_application_symlinks_are_rejected_before_permission_changes(self):
        class Entry:
            mode=0o600
            def is_symlink(self):return True
            def is_dir(self):return False
            def chmod(self,mode):self.mode=mode
        linked=Entry()
        class Application(Entry):
            mode=0o700
            def is_symlink(self):return False
            def is_dir(self):return True
            def rglob(self,_):return iter([linked])
        app=Application()
        with self.assertRaises(ValueError):installer.normalize_application(app)
        self.assertEqual(app.mode,0o700);self.assertEqual(linked.mode,0o600)

    def test_application_modes_are_normalized_independent_of_source_umask(self):
        class Entry:
            def is_symlink(self):return False
            def __init__(self,directory,mode):self.directory=directory;self.mode=mode
            def is_dir(self):return self.directory
            def chmod(self,mode):self.mode=mode
        folder=Entry(True,0o700);file=Entry(False,0o600)
        app=Entry(True,0o700);app.rglob=lambda _:iter([folder,file])
        installer.normalize_application(app)
        self.assertEqual(app.mode,0o755);self.assertEqual(folder.mode,0o755);self.assertEqual(file.mode,0o644)

    def test_systemd_path_fields_preserve_literal_dollars_while_exec_escapes_them(self):
        args=installer.arguments(['--api-key-file','/private/key','--library-root','/srv/library',
             '--image','sha256:'+'b'*64,'--ack-docker-root','--prefix','/opt/price$tag%mode'])
        unit=installer.render(args)
        self.assertIn('WorkingDirectory=/opt/price$tag%%mode\n',unit)
        self.assertIn('"/opt/price$$tag%%mode/transport-dist"',unit)

    def test_path_serializers_reject_line_break_and_nul_injection(self):
        for character in ('\n','\r','\x00'):
            for serializer in (installer.working_directory,installer.quote):
                with self.subTest(character=character,serializer=serializer.__name__),self.assertRaises(ValueError):
                    serializer('/opt/path'+character+'ExecStart=/bad')

    def test_installer_checks_runtime_java_before_host_installation(self):
        from unittest.mock import patch
        import subprocess
        with patch.object(subprocess,'run',return_value=subprocess.CompletedProcess(['java'],0,'','openjdk version "17.0.1"')):
            with self.assertRaises(ValueError):installer.check_runtime_java()
        with patch.object(subprocess,'run',return_value=subprocess.CompletedProcess(['java'],0,'','openjdk version "21.0.6"')):
            self.assertEqual(installer.check_runtime_java(),21)

    def test_system_python_may_be_a_symlink_without_allowing_destination_symlinks(self):
        from unittest.mock import patch
        args=installer.arguments(['--api-key-file','/private/key','--library-root','/srv/library',
             '--image','sha256:'+'b'*64,'--ack-docker-root'])
        with patch.object(Path,'is_symlink',lambda path:path.name=='python3'):
            self.assertIn('/usr/bin/python3',installer.render(args))

    def test_installer_keeps_private_state_and_credentials_outside_public_code_tree(self):
        for option in ('--state-dir','--config-dir'):
            args=installer.arguments(['--api-key-file','/private/key','--library-root','/srv/library',
                 '--image','sha256:'+'b'*64,'--ack-docker-root',option,'/opt/jellyfin-hue-sync/private'])
            with self.subTest(option=option),self.assertRaises(ValueError):installer.render(args)

    def test_installer_refuses_system_directories_as_private_state_or_config(self):
        for option in ('--prefix','--state-dir','--config-dir'):
            args=installer.arguments(['--api-key-file','/private/key','--library-root','/srv/library',
                 '--image','sha256:'+'b'*64,'--ack-docker-root',option,'/etc'])
            with self.subTest(option=option),self.assertRaises(ValueError):installer.render(args)

    def test_rendered_unit_uses_user_selected_immutable_image_and_key_file(self):
        parser=self.unit();args=shlex.split(parser['Service']['ExecStart'])
        self.assertEqual(args[args.index('--image')+1],'sha256:'+'b'*64)
        self.assertEqual(args[args.index('--api-key-file')+1],'/etc/jellyfin-hue-sync/api-key')
        self.assertEqual(args[args.index('--decoder')+1],'cpu')
        self.assertNotIn('latest',' '.join(args))

    def test_unit_is_separate_from_media_stack_and_writes_only_private_state(self):
        parser=self.unit();service=parser['Service']
        self.assertEqual(service['User'],'jellyfin-hue-sync')
        self.assertEqual(service['WorkingDirectory'],'/opt/jellyfin-hue-sync')
        self.assertEqual(shlex.split(service['ReadWritePaths']),['/var/lib/jellyfin-hue-sync'])
        self.assertEqual(service['ProtectSystem'],'strict')
        self.assertEqual(service['NoNewPrivileges'],'true')
        self.assertEqual(service['KillMode'],'mixed')
        self.assertEqual(service['UMask'],'0077')
        self.assertEqual(service['MemoryMax'],'512M')
        self.assertEqual(service['SupplementaryGroups'],'docker')
        self.assertNotIn('jellyfin.service',str(dict(parser['Unit'])))
        self.assertNotIn('/home/',str(dict(service)))

@unittest.skipUnless(sys.platform=='linux' and shutil.which('systemd-analyze'),
                     'Requires the real Linux systemd unit parser')
class SystemdParserTests(unittest.TestCase):
    def verify(self, unit):
        # verify uses a temporary manager: no daemon reload, apply, or service start.
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'jhs-parser-regression.service'
            path.write_text(unit,encoding='utf-8')
            result=subprocess.run(['systemd-analyze','verify','--man=no','--generators=no',str(path)],
                env=dict(os.environ,SYSTEMD_LOG_LEVEL='debug',LC_ALL='C'),
                capture_output=True,text=True,timeout=30)
        diagnostics='\n'.join(line for line in result.stderr.splitlines() if path.name in line)
        self.assertEqual(result.returncode,0,diagnostics or result.stderr)
        # Invalid list entries may only warn and be ignored despite a zero exit code.
        self.assertFalse(any(str(path)+':' in line for line in result.stderr.splitlines()),diagnostics)
        return result.stdout

    def field(self, output, name):
        values=[line.lstrip('\t').removeprefix(name+': ') for line in output.splitlines()
                if line.lstrip('\t').startswith(name+': ')]
        self.assertEqual(len(values),1,'Missing or duplicate parsed '+name)
        return values[0]

    def command(self, output):
        # Decode systemd's human-readable dump, NOT the unit source. Its shell
        # formatter emits \$ in double quotes; Python shlex keeps that slash.
        return [value.replace('\\$','$') for value in shlex.split(self.field(output,'Command Line'))]

    def arguments(self, *options):
        return installer.arguments(['--api-key-file','/private/key','--library-root','/srv/library',
             '--image','sha256:'+'b'*64,'--ack-docker-root',*options])

    def test_working_directory_ending_in_backslash_is_not_line_continuation(self):
        prefix='/opt/trailing\\'
        output=self.verify(installer.render(self.arguments('--prefix',prefix)))
        self.assertEqual(self.field(output,'WorkingDirectory'),prefix)

    def test_working_directory_preserves_trailing_space(self):
        prefix='/opt/trailing '
        output=self.verify(installer.render(self.arguments('--prefix',prefix)))
        self.assertEqual(self.field(output,'WorkingDirectory'),prefix)

    def test_exec_executable_with_literal_dollar_is_found_by_systemd(self):
        with tempfile.TemporaryDirectory() as tmp:
            executable=Path(tmp)/'python $price%mode'
            executable.symlink_to(sys.executable)
            output=self.verify(installer.render(self.arguments('--python',str(executable))))
            command=self.command(output)
            self.assertEqual(command[0],str(executable))

    def test_special_paths_round_trip_through_real_systemd_parser(self):
        cases=['space name','percent%n%q','dollar${HOME}$tag',
               'double"single\'quote',r'back\slash\n',
               'space %n ${HOME} "quote" \\slash', 'trailing\\', 'trailing ']
        for suffix in cases:
            with self.subTest(suffix=suffix):
                prefix='/opt/'+suffix;state='/var/lib/'+suffix;config='/etc/'+suffix
                library=Path('/srv')/suffix
                args=self.arguments('--prefix',prefix,'--state-dir',state,
                                    '--config-dir',config,'--library-root',str(library))
                output=self.verify(installer.render(args))
                self.assertEqual(self.field(output,'WorkingDirectory'),prefix)
                self.assertEqual(self.field(output,'ReadWritePaths'),state)
                command=self.command(output)
                self.assertEqual(command[:3],['/usr/bin/python3','-m','ambilight'])
                # verify dumps argv before execution-time $$ -> $ expansion.
                for option,value in [('--state-dir',state),('--transport-dir',prefix+'/transport-dist'),
                                     ('--api-key-file',config+'/api-key'),('--library-root',str(library))]:
                    self.assertEqual(command[command.index(option)+1],value.replace('$','$$'))
                self.assertEqual(command[-2:],['--port','3187'])

    def test_default_rendered_unit_is_accepted_by_systemd(self):
        output=self.verify(installer.render(self.arguments()))
        self.assertIn('WorkingDirectory: /opt/jellyfin-hue-sync',output)


if __name__=='__main__':unittest.main()

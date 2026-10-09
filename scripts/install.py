#!/usr/bin/env python3
"""Stage an offline host/systemd install, or apply it explicitly as Linux root.

Docker socket/group access is root-equivalent, not a rootless deployment.
No services are enabled, started or restarted by this installer.
"""
import argparse
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def quote(value,*,exec_arg=False):
    """Encode one systemd list token, never a scalar path or a shell word."""
    value=str(value)
    if any(c in value for c in '\n\r\x00'):
        raise ValueError('Newlines and NUL are forbidden in installation arguments')
    value=value.replace('\\','\\\\').replace('"','\\"').replace('%','%%')
    if exec_arg:value=value.replace('$','$$')
    return '"'+value+'"'


def working_directory(value):
    # WorkingDirectory is a scalar path: quotes/backslashes are literal, not tokens.
    value=str(value)
    if any(c in value for c in '\n\r\x00'):
        raise ValueError('Newlines and NUL are forbidden in installation arguments')
    # Append /. to preserve the ending across INI trimming/continuation.
    # WorkingDirectory then normalizes this equivalent directory path.
    if value.endswith(('\\',' ','\t')):
        value+='/.'
    return value.replace('%','%%')


def destination(stage,path,*,allow_symlinks=False):
    path=PurePosixPath(path)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('Installation paths must be absolute POSIX paths without traversal')
    target=stage/Path(*path.parts[1:])
    if not allow_symlinks and any(part.is_symlink() for part in (target,*target.parents)):
        raise ValueError('Refusing symlink installation destination or parent')
    return target


def read_key(args):
    source=args.api_key_file or args.settings_file
    fd=os.open(source,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))
    with os.fdopen(fd,'rb') as stream:
        info=os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size>1024*1024:
            raise ValueError('Credential file must be a bounded regular file')
        if os.name=='posix' and info.st_mode&0o077:
            raise ValueError('Credential file must be private (chmod 600)')
        data=stream.read(1024*1024+1).decode('utf-8')
    key=json.loads(data)['jellyfin']['apiKey'] if args.settings_file else data.rstrip('\r\n')
    if not isinstance(key,str) or not key or len(key)>4096 or any(c in key for c in '\r\n"'):
        raise ValueError('Invalid credential file contents')
    return key


def arguments(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    target=p.add_mutually_exclusive_group()
    target.add_argument('--stage-dir',type=Path,help='Write a complete installation tree without privileged host changes (default: build/install-root)')
    target.add_argument('--apply',action='store_true',help='Apply to this Linux host as root; never start/restart services')
    p.add_argument('--prefix',default='/opt/jellyfin-hue-sync')
    p.add_argument('--state-dir',default='/var/lib/jellyfin-hue-sync')
    p.add_argument('--config-dir',default='/etc/jellyfin-hue-sync')
    p.add_argument('--user',default='jellyfin-hue-sync')
    p.add_argument('--media-group',action='append',default=[],help='Existing groups needed to traverse/read the library; repeatable')
    p.add_argument('--python',default='/usr/bin/python3',help='Absolute Linux Python 3.11+ interpreter')
    credential=p.add_mutually_exclusive_group(required=True)
    credential.add_argument('--api-key-file',type=Path)
    credential.add_argument('--settings-file',type=Path,help='Legacy input; only its Jellyfin API key is installed')
    p.add_argument('--library-root',type=Path,required=True)
    p.add_argument('--jellyfin-url',default='http://127.0.0.1:8096')
    p.add_argument('--image',required=True)
    p.add_argument('--decoder',choices=('cpu','vaapi'),default='cpu')
    p.add_argument('--media-prefix',default='/media')
    p.add_argument('--bind',default='127.0.0.1')
    p.add_argument('--allowed-host',action='append',default=[])
    p.add_argument('--port',type=int,default=3187)
    p.add_argument('--ack-docker-root',action='store_true',help='Acknowledge Docker access grants root-equivalent host privilege')
    args=p.parse_args(argv)
    if not args.ack_docker_root:
        p.error('--ack-docker-root is required: Docker group/socket access grants root-equivalent host privilege')
    for name in (args.user,*args.media_group):
        if not re.fullmatch(r'[a-z_][a-z0-9_-]{0,31}',name):
            p.error('Invalid Linux service user/group name')
    return args


def render(args):
    from ambilight.__main__ import parse_args
    for path in (args.prefix,args.state_dir,args.config_dir):
        destination(Path('/'),path)
    destination(Path('/'),args.python,allow_symlinks=True)
    reserved={'/','/etc','/opt','/srv','/var','/var/lib','/var/cache','/usr','/usr/local','/home','/root','/tmp','/run','/var/run'}
    if any(str(PurePosixPath(path)) in reserved for path in (args.prefix,args.state_dir,args.config_dir)):
        raise ValueError('Use dedicated application directories, not shared system directories')
    directories=[PurePosixPath(path) for path in (args.prefix,args.state_dir,args.config_dir)]
    if any(left.is_relative_to(right) for i,left in enumerate(directories) for j,right in enumerate(directories) if i!=j):
        raise ValueError('Application, private state and credential directories must not overlap')
    command=[args.python,'-m','ambilight','--state-dir',args.state_dir,'--transport-dir',args.prefix+'/transport-dist',
             '--library-root',str(args.library_root.resolve()),'--api-key-file',args.config_dir+'/api-key',
             '--jellyfin-url',args.jellyfin_url,'--image',args.image,'--decoder',args.decoder,
             '--media-prefix',args.media_prefix,'--bind',args.bind,'--port',str(args.port)]
    for host in args.allowed_host:command+=['--allowed-host',host]
    parse_args(command[3:])
    template=(ROOT/'deploy/hue-jellyfin-sync.service').read_text(encoding='utf-8')
    values={'USER':args.user,'GROUPS':' '.join(['docker',*args.media_group]),'PREFIX':working_directory(args.prefix),
            # systemd expands dollars in arguments, never in the executable path.
            'COMMAND':' '.join(quote(value,exec_arg=i>0) for i,value in enumerate(command)),
            'STATE':quote(args.state_dir)}
    for name,value in values.items():template=template.replace('@'+name+'@',value)
    return template


def application_paths(app):
    paths=(app,*app.rglob('*'))
    if any(path.is_symlink() for path in paths):
        raise ValueError('Refusing symlinks within an application source or destination')
    return paths


def normalize_application(app):
    # Public code/assets remain readable even when source checkout/build used umask 077.
    for path in application_paths(app):
        path.chmod(0o755 if path.is_dir() else 0o644)


def check_runtime_java(java='java'):
    result=subprocess.run([java,'-version'],capture_output=True,text=True,check=True,timeout=15)
    match=re.search(r'(?:version\s+"|openjdk\s+)([0-9]+)',result.stdout+result.stderr)
    if not match or int(match.group(1))<21:
        raise ValueError('The service requires Java 21 or newer on its runtime PATH')
    return int(match.group(1))


def main(argv=None):
    args=arguments(argv)
    try:
        unit=render(args)
        key=read_key(args)
        if not args.library_root.is_dir():raise ValueError('Library root must be an existing directory')
        if args.apply:
            if sys.platform!='linux' or os.geteuid()!=0:
                raise ValueError('--apply requires root on a Linux systemd host')
            import grp,pwd
            for group in ('docker',*args.media_group):grp.getgrnam(group)
            for tool in ('useradd','usermod','systemctl','docker','java'):
                if not shutil.which(tool):raise ValueError('Required host tool missing: '+tool)
            check_runtime_java()
            subprocess.run([args.python,'-c','import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)'],check=True)
            subprocess.run(['docker','image','inspect',args.image],check=True,stdout=subprocess.DEVNULL)
            stage=Path('/')
        else:
            requested=args.stage_dir or ROOT/'build/install-root'
            if requested.is_symlink():raise ValueError('Refusing symlink stage directory')
            stage=requested.resolve()
            if stage.exists() and any(stage.iterdir()):raise ValueError('Stage directory must be empty')
        # Verify the supplied transport from its independent source hash lock first.
        subprocess.run([sys.executable,str(ROOT/'transport/verify_dist.py')],check=True)
        app=destination(stage,args.prefix);state=destination(stage,args.state_dir);config=destination(stage,args.config_dir)
        service=destination(stage,'/etc/systemd/system/jellyfin-hue-sync.service')
        if app.exists():application_paths(app)
        for name in ('ambilight','static','transport-dist'):application_paths(ROOT/name)
        if args.apply:
            try:pwd.getpwnam(args.user)
            except KeyError:
                subprocess.run(['useradd','--system','--user-group','--home-dir',args.state_dir,'--shell','/usr/sbin/nologin',args.user],check=True)
            account=pwd.getpwnam(args.user)
            if account.pw_uid==0:raise ValueError('Service account must not be root')
            subprocess.run(['usermod','-aG',','.join(['docker',*args.media_group]),args.user],check=True)
        app.mkdir(parents=True,exist_ok=True,mode=0o755)
        for name in ('ambilight','static','transport-dist'):
            shutil.copytree(ROOT/name,app/name,dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
        shutil.copyfile(ROOT/'LICENSE',app/'LICENSE')
        normalize_application(app)
        for directory in (state,config):
            directory.mkdir(parents=True,exist_ok=True,mode=0o700);directory.chmod(0o700)
        keypath=config/'api-key'
        if keypath.is_symlink():raise ValueError('Refusing symlink credential destination')
        fd=os.open(keypath,os.O_WRONLY|os.O_CREAT|os.O_TRUNC|getattr(os,'O_NOFOLLOW',0),0o600)
        with os.fdopen(fd,'w',encoding='utf-8') as stream:stream.write(key+'\n')
        keypath.chmod(0o600)
        service.parent.mkdir(parents=True,exist_ok=True)
        service.write_text(unit,encoding='utf-8');service.chmod(0o644)
        if args.apply:
            for path in (state,config,keypath):os.chown(path,account.pw_uid,account.pw_gid)
            subprocess.run(['systemctl','daemon-reload'],check=True)
        print('Installation '+('applied' if args.apply else 'staged at '+str(stage))+'. No services were started or restarted.')
        print('WARNING: Docker group/socket access is root-equivalent host privilege, including from this service.')
        if args.bind=='0.0.0.0':print('WARNING: Host allowlisting is not authentication. Restrict the port to your trusted LAN with a firewall.')
        print('After reviewing the installation: sudo systemctl enable --now jellyfin-hue-sync.service')
        return 0
    except (OSError,ValueError,KeyError,subprocess.SubprocessError):
        print('Installation failed. Check private input files, paths, transport build and host prerequisites; no credential contents are logged.',file=sys.stderr)
        return 1


if __name__=='__main__':raise SystemExit(main())

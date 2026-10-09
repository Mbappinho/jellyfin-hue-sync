"""Start the independent trusted-LAN admin interface and server color sync."""
import argparse
import ipaddress
import re
import signal
import threading
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit
from .service import build_engine, run


def parse_args(argv=None):
    parser=argparse.ArgumentParser(description='Jellyfin to Hue Entertainment, without TV capture')
    root=Path(__file__).resolve().parents[1]
    parser.add_argument('--bind',default='127.0.0.1',help='Loopback or private LAN IPv4; wildcard needs --allowed-host')
    parser.add_argument('--allowed-host',action='append',default=[],help='Bare admin IPv4 address or hostname; repeatable')
    parser.add_argument('--port',type=int,default=3187)
    parser.add_argument('--state-dir',type=Path,default=Path.home()/'.local'/'state'/'jellyfin-hue-sync')
    parser.add_argument('--library-root',type=Path,required=True,help='Host library mounted read-only for extraction')
    credentials=parser.add_mutually_exclusive_group(required=True)
    credentials.add_argument('--api-key-file',type=Path,help='Private owner-readable Jellyfin API key FILE; never a CLI secret')
    credentials.add_argument('--settings-file',type=Path,help='Optional legacy Jellyseerr settings.json integration')
    parser.add_argument('--transport-dir',type=Path,default=root/'transport-dist')
    parser.add_argument('--jellyfin-url',default='http://127.0.0.1:8096')
    parser.add_argument('--image',required=True,help='Preloaded FFmpeg image: sha256 image ID or repository@sha256 digest')
    parser.add_argument('--decoder',choices=('cpu','vaapi'),default='cpu')
    parser.add_argument('--media-prefix',default='/media',help='Absolute Jellyfin prefix mapped to --library-root')
    args=parser.parse_args(argv)
    try:
        from .extraction import validate_image
        validate_image(args.image)
        address=ipaddress.IPv4Address(args.bind)
        lan=any(address in ipaddress.IPv4Network(net) for net in ('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16'))
        if not (address.is_loopback or lan or args.bind=='0.0.0.0'):
            raise ValueError('Bind must be a loopback or private LAN IPv4 address')
        if args.bind=='0.0.0.0' and not args.allowed_host:
            raise ValueError('Wildcard binding requires explicit --allowed-host entries and a LAN-only firewall')
        if not args.allowed_host:
            args.allowed_host=[args.bind]+(['localhost'] if address.is_loopback else [])
        for name in args.allowed_host:
            if name=='0.0.0.0' or not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?',name):
                raise ValueError('Allowed hosts must be bare addresses or hostnames, never wildcard, URL or port')
        prefix=PurePosixPath(args.media_prefix)
        if not prefix.is_absolute() or '..' in prefix.parts:
            raise ValueError('Media prefix must be an absolute POSIX path without traversal')
        if not 1<=args.port<=65535:
            raise ValueError('Port must be between 1 and 65535')
        url=urlsplit(args.jellyfin_url)
        if url.scheme not in ('http','https') or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError('Jellyfin URL must be HTTP(S), without credentials, query or fragment')
    except ValueError as error:
        parser.error(str(error))
    return args


def main():
    args=parse_args()
    from .web import make_server
    engine=build_engine(args.state_dir,args.library_root,args.settings_file,args.transport_dir,args.jellyfin_url,args.image,
                        api_key_file=args.api_key_file,decoder=args.decoder,media_prefix=args.media_prefix)
    try:
        server=make_server(engine,args.bind,args.port,allowed_hosts=args.allowed_host)
    except Exception:
        engine.close()
        raise SystemExit('Admin server could not bind. Check the address and port.') from None
    stop=threading.Event()
    for kind in (signal.SIGINT,signal.SIGTERM):signal.signal(kind,lambda *_:stop.set())
    print('Jellyfin Hue Sync: local admin interface started.',flush=True)
    try:run(engine,server,stop)
    except Exception:raise SystemExit('Shutdown incomplete. Check local resources and the restoration journal.') from None


if __name__=='__main__':main()

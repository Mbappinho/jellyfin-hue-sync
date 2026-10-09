"""Read-only Jellyfin session access using the existing local server integration."""
def choose_session(sessions,device_id,wall_time=None):
    import time
    from datetime import datetime
    now=time.time() if wall_time is None else wall_time
    def checked_at(session):
        value=session.get('LastPlaybackCheckIn') or session.get('LastActivityDate')
        try: return datetime.fromisoformat(value.replace('Z','+00:00')).timestamp()
        except (ValueError,AttributeError,TypeError): return 0
    matches=[s for s in sessions if s.get('DeviceId')==device_id and -10 <= now-checked_at(s) <= 40]
    return max(matches,key=checked_at) if matches else None

class JellyfinClient:
    def __init__(self,settings_path=None,base_url='http://127.0.0.1:8096',*,api_key_file=None):
        if (settings_path is None)==(api_key_file is None):
            raise ValueError('Select exactly one API key file or legacy settings file')
        self.settings_path=settings_path; self.base_url=base_url;self.api_key_file=api_key_file

    def _token(self):
        if self.api_key_file is None:
            from .storage import read_json
            return read_json(self.settings_path)['jellyfin']['apiKey']
        import os,stat
        fd=os.open(self.api_key_file,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))
        with os.fdopen(fd,'rb') as stream:
            info=os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size>4096:
                raise ValueError('Invalid API key file')
            if os.name=='posix' and (info.st_mode & 0o077 or info.st_uid!=os.geteuid()):
                raise ValueError('API key file must be owner-readable only and owned by this user')
            return stream.read(4097).decode('utf-8').rstrip('\r\n')
    request_timeout = 4.0

    def sessions(self):
        import http.client,json,socket,threading,time,urllib.request
        deadline=time.monotonic()+self.request_timeout
        timers=[]
        class DeadlineConnection:
            def connect(connection):
                super().connect()
                transport=connection.sock
                def expire():
                    try: transport.shutdown(socket.SHUT_RDWR)
                    except OSError: pass
                remaining=deadline-time.monotonic()
                if remaining<=0:
                    expire();raise TimeoutError('Jellyfin request deadline exceeded')
                transport.settimeout(remaining)
                timer=threading.Timer(remaining,expire);timer.daemon=True
                timers.append(timer);timer.start()
        class HTTPConnection(DeadlineConnection,http.client.HTTPConnection): pass
        class HTTPSConnection(DeadlineConnection,http.client.HTTPSConnection): pass
        class HTTPHandler(urllib.request.HTTPHandler):
            def http_open(handler,request): return handler.do_open(HTTPConnection,request)
        class HTTPSHandler(urllib.request.HTTPSHandler):
            def https_open(handler,request): return handler.do_open(HTTPSConnection,request,context=handler._context)
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self,*args,**kwargs): return None
        try:
            token=self._token()
            if not isinstance(token,str) or not token or any(c in token for c in '\r\n"'):
                raise ValueError('Invalid Jellyfin token')
            request=urllib.request.Request(self.base_url.rstrip('/')+'/Sessions',headers={'Authorization':'MediaBrowser Token="'+token+'"','Accept':'application/json'})
            opener=urllib.request.build_opener(NoRedirect,HTTPHandler,HTTPSHandler)
            with opener.open(request,timeout=max(.001,deadline-time.monotonic())) as response:
                raw=response.read(1024*1024+1)
            if time.monotonic()>=deadline: raise TimeoutError('Jellyfin request deadline exceeded')
            if len(raw)>1024*1024: raise ValueError('Response too large')
            result=json.loads(raw)
            if not isinstance(result,list): raise ValueError('Invalid sessions')
            return result
        except Exception:
            raise RuntimeError('Jellyfin access unavailable. Check the server and private API key file.') from None
        finally:
            for timer in timers: timer.cancel()

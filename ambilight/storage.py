"""Owner-only atomic JSON storage for local bridge credentials and color caches."""
def atomic_json(path,value):
    import json,os,tempfile
    from pathlib import Path
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    content=json.dumps(value,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode()
    fd,tmp=tempfile.mkstemp(prefix='.'+path.name+'.',dir=path.parent)
    try:
        os.chmod(tmp,0o600)
        with os.fdopen(fd,'wb') as out:
            out.write(content);out.flush();os.fsync(out.fileno())
        os.replace(tmp,path)
    finally:
        Path(tmp).unlink(missing_ok=True)

def read_json(path,max_bytes=1024*1024):
    import json
    from pathlib import Path
    with Path(path).open('rb') as source: data=source.read(max_bytes+1)
    if len(data)>max_bytes: raise ValueError('JSON file too large')
    return json.loads(data)

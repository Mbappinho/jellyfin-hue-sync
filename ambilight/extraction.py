"""Read-only file analysis in a CPU-limited, network-isolated container."""
from pathlib import Path, PurePosixPath

def validate_image(image):
    import re
    if not isinstance(image,str) or not re.fullmatch(r'(?:sha256:|[a-zA-Z0-9][a-zA-Z0-9._:/-]*@sha256:)[a-f0-9]{64}',image):
        raise ValueError('Extraction image must be a local sha256 image ID or repository@sha256 digest')
    return image

def media_relative(container_path,media_prefix='/media'):
    prefix=PurePosixPath(media_prefix)
    path=PurePosixPath(container_path)
    if not prefix.is_absolute() or '..' in prefix.parts or not path.is_absolute() or '..' in path.parts:
        raise ValueError('Invalid media path or prefix')
    try:
        return path.relative_to(prefix)
    except ValueError:
        raise ValueError('Invalid media path') from None

def resolve_media(container_path, library_root, media_prefix='/media'):
    root=Path(library_root).resolve()
    try:
        source=(root / media_relative(container_path,media_prefix)).resolve()
    except (ValueError, TypeError):
        raise ValueError('Invalid media path') from None
    if not source.is_relative_to(root) or not source.is_file():
        raise ValueError('Media file unavailable or outside library')
    return source

def make_ffmpeg_command(container_path, library_root, start, duration, hdr=False, mode="vaapi", name="hue-extract-test", image=None, media_prefix="/media"):
    import math
    validate_image(image)
    input_path=str(PurePosixPath("/media")/media_relative(container_path,media_prefix))
    if not math.isfinite(start) or start < 0 or not math.isfinite(duration) or not 0 < duration <= 60:
        raise ValueError('Invalid extraction window')
    if mode not in ('vaapi','cpu'):
        raise ValueError('Unsupported decoder')
    command=['docker','run','--name',name,'--rm','--pull=never','--network','none','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges','--cpus','1.5','--memory','512m','--memory-swap','512m','--pids-limit','64','-v',str(library_root)+':/media:ro']
    if mode=='vaapi': command+=['--device','/dev/dri/renderD128']
    command+=['--entrypoint','/usr/lib/jellyfin-ffmpeg/ffmpeg',image,'-hide_banner','-loglevel','error','-nostdin','-threads','2','-filter_threads','1']
    if mode=='vaapi':
        command+=['-hwaccel','vaapi','-hwaccel_device','/dev/dri/renderD128','-hwaccel_output_format','vaapi']
        vf='scale_vaapi=w=96:h=54:format=p010,hwdownload,format=p010,fps=fps=4:start_time=0:round=near'
    else: vf='fps=fps=4:start_time=0:round=near,scale=96:54'
    if hdr: vf+=',zscale=transfer=linear:npl=100,format=gbrpf32le,tonemap=tonemap=hable:desat=0,zscale=primaries=bt709:transfer=bt709:matrix=bt709:range=full'
    vf+=',format=rgb24'
    return command+['-ss',str(float(start)),'-i',input_path,'-t',str(float(duration)),'-vf',vf,'-an','-sn','-dn','-f','rawvideo','-pix_fmt','rgb24','pipe:1']

def decode_rgb_stream(stream,width=96,height=54,sampler=None,progress=None):
    if sampler is None:
        from .core import sample_rgb
        sampler=sample_rgb
    size=width*height*3; frames=[]
    if size<=0: raise ValueError('Invalid frame dimensions')
    while True:
        data=stream.read(size)
        if not data: break
        if len(data)!=size: raise ValueError('Truncated RGB frame')
        frames.append([list(x) for x in sampler(data,width,height)])
        if progress is not None: progress(len(frames))
    return frames

class ColorChunk:
    def __init__(self,start,fps,frames):
        self.start=start; self.fps=fps; self.frames=frames
    def at(self,position):
        import math
        if not math.isfinite(position): raise ValueError("Invalid playback position")
        index=math.floor((position-self.start)*self.fps)
        return self.frames[index] if 0 <= index < len(self.frames) else None

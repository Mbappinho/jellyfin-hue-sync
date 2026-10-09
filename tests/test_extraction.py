from pathlib import Path
import tempfile
import unittest
from ambilight.extraction import resolve_media

class MediaPathTests(unittest.TestCase):
    def test_container_path_maps_to_existing_readonly_library_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); (root/'series').mkdir(); source=root/'series'/'sample.mkv'; source.write_bytes(b'explicit test fixture')
            self.assertEqual(resolve_media('/media/series/sample.mkv', root), source.resolve())

    def test_paths_must_exist_and_stay_inside_library(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'library'; root.mkdir()
            for source in ['/media/../private.mkv', '/etc/passwd', '/media/missing.mkv']:
                with self.subTest(source=source), self.assertRaises(ValueError):
                    resolve_media(source, root)

class CommandTests(unittest.TestCase):
    def test_image_must_be_immutable_and_container_resources_are_bounded(self):
        from ambilight.extraction import make_ffmpeg_command
        image='sha256:'+'a'*64
        cmd=make_ffmpeg_command('/media/movie.mkv','/library',0,30,image=image,mode='cpu')
        self.assertEqual(cmd[cmd.index('--memory')+1],'512m')
        self.assertEqual(cmd[cmd.index('--memory-swap')+1],'512m')
        self.assertEqual(cmd[cmd.index('--pids-limit')+1],'64')
        for invalid in (None,'jellyfin:latest','sha256:abc','--privileged'):
            with self.subTest(image=invalid),self.assertRaises(ValueError):
                make_ffmpeg_command('/media/movie.mkv','/library',0,30,image=invalid)

    def test_hdr_extraction_uses_limited_readonly_isolated_container(self):
        from ambilight.extraction import make_ffmpeg_command
        cmd=make_ffmpeg_command('/media/movie with spaces.mkv','/library',30,30,hdr=True,image='sha256:'+'a'*64)
        self.assertIn('--pull=never',cmd)
        self.assertEqual(cmd[cmd.index('--network')+1],'none')
        self.assertIn('--read-only',cmd)
        self.assertEqual(cmd[cmd.index('--cpus')+1],'1.5')
        self.assertIn('/library:/media:ro',cmd)
        self.assertEqual(cmd[cmd.index('-i')+1],'/media/movie with spaces.mkv')
        self.assertIn('fps=fps=4',cmd[cmd.index('-vf')+1])
        self.assertIn('tonemap=hable',cmd[cmd.index('-vf')+1])
        self.assertEqual(cmd[-1],'pipe:1')
        self.assertIn('/dev/dri/renderD128',cmd)

class ChunkTests(unittest.TestCase):
    def test_frame_lookup_tracks_clip_time_and_never_clamps_outside_clip(self):
        from ambilight.extraction import ColorChunk
        frames=[[[255,0,0],[0,0,255]],[[0,255,0],[255,255,255]]]
        chunk=ColorChunk(30,4,frames)
        self.assertEqual(chunk.at(30.1),frames[0])
        self.assertEqual(chunk.at(30.25),frames[1])
        self.assertIsNone(chunk.at(29.99))
        self.assertIsNone(chunk.at(30.5))

class StreamTests(unittest.TestCase):
    def test_stream_preserves_frame_boundaries_and_rejects_truncation(self):
        import io
        from ambilight.extraction import decode_rgb_stream
        fixture=b'\xff\0\0\0\0\xff'
        sampler=lambda data,w,h: [list(data[:3]),list(data[3:6])]
        self.assertEqual(decode_rgb_stream(io.BytesIO(fixture*2),2,1,sampler),[[[255,0,0],[0,0,255]]]*2)
        with self.assertRaises(ValueError): decode_rgb_stream(io.BytesIO(fixture+b'\0'),2,1,sampler)

if __name__=='__main__': unittest.main()

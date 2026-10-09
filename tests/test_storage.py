import json, os
from pathlib import Path
import tempfile, unittest
from ambilight.storage import atomic_json, read_json

class StorageTests(unittest.TestCase):
    def test_atomic_replacement_persists_only_owner_readable_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'private'/'state.json'
            atomic_json(path,{'fixture':'first'})
            atomic_json(path,{'fixture':'second'})
            self.assertEqual(json.loads(path.read_text()),{'fixture':'second'})
            self.assertEqual(list(path.parent.iterdir()),[path])
            if os.name=='posix': self.assertEqual(path.stat().st_mode&0o777,0o600)

    def test_bounded_reads_accept_valid_json_and_reject_oversize(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'state.json'; path.write_text('{"x":1}')
            self.assertEqual(read_json(path,20),{'x':1})
            with self.assertRaisesRegex(ValueError, '^JSON file too large$'): read_json(path,3)

if __name__=='__main__': unittest.main()

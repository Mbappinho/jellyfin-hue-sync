import json,unittest
from pathlib import Path
class DependencyPolicyTests(unittest.TestCase):
    def test_only_vendor_fixed_bouncy_castle_release_is_in_runtime_classpath(self):
        lib=Path(__file__).resolve().parents[1]/'transport-dist'/'lib'
        actual=sorted(p.name for p in lib.glob('*.jar'))
        # Versions match the vendor bctls 1.86.2 Maven dependency graph.
        expected=sorted(['bcprov-jdk18on-1.86.jar','bctls-jdk18on-1.86.2.jar','bcutil-jdk18on-1.86.jar'])
        self.assertEqual(actual,expected)
if __name__=='__main__': unittest.main()

"""Run the portable suite with process-local network auditing enabled."""
import sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def deny_network(event,args):
    if event in {'socket.connect','socket.getaddrinfo'}:
        raise RuntimeError('network_not_allowed_in_portable_unit_tests')
sys.addaudithook(deny_network)
suite=unittest.defaultTestLoader.discover(str(ROOT/'tests'),pattern='test_*.py')
result=unittest.TextTestRunner(verbosity=1).run(suite)
if result.testsRun!=320 or result.skipped:raise SystemExit('unexpected_test_count_or_skip')
raise SystemExit(0 if result.wasSuccessful() else 1)

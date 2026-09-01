# Packaging shim for the public repository: the runtime subpackages
# (observability, sandbox) are imported as top-level modules by the tests,
# as in the original development layout. No behavioural change.
import os, sys
_here = os.path.dirname(os.path.abspath(__file__))
for p in (_here, os.path.join(_here, "runtime")):
    if p not in sys.path:
        sys.path.insert(0, p)

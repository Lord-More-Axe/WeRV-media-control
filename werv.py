#!/usr/bin/env python3
"""WeRV -- remote media control.

Kept at the top level so ``python werv.py`` still works, and so the frozen executable has
a single obvious entry point.  The implementation lives in the ``werv`` package.
"""

from werv.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())

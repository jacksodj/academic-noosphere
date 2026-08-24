"""PyInstaller entry point for the frozen noosphere-core sidecar (ticket #22).

Thin by design: everything real lives in noosphere.server.main. freeze_support
guards against any future multiprocessing use re-executing the binary.
"""

import multiprocessing

from noosphere.server import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()

import sys
import os

os.environ.setdefault("CACHE_DIR", os.path.join(os.path.dirname(__file__), "cache"))

from bot import main

if __name__ == "__main__":
    main()

from __future__ import annotations

import sys

from text2speech.pipeline_app import main

if __name__ == "__main__":
    print("make_mp3.py is deprecated; use pipeline.py instead.", file=sys.stderr)
    raise SystemExit(main())

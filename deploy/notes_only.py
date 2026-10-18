"""Set the Obsidian sync tool to download notes only (no images, audio, PDFs or video).

The tool's command line cannot express "no attachment types", but its settings file can: an empty `allowTypes`.
Usage: python deploy/notes_only.py <sync settings folder>
"""

import json
import sys
from pathlib import Path

changed = 0
for config in Path(sys.argv[1]).glob("*/config.json"):
    settings = json.loads(config.read_text(encoding="utf-8"))
    settings["allowTypes"] = []
    config.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    changed += 1
print(f"notes only: updated {changed} sync setting file(s)")
if changed != 1:
    sys.exit("expected exactly one sync setting file")

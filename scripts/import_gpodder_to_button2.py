from pathlib import Path
import re
import shutil
import urllib.parse

SOURCE = Path.home() / "Library/Application Support/gPodder/Downloads/Northwoods Baseball Sleep Radio - Fake Baseball for Sleeping"
DEST = Path.home() / "dev_work/nightstand-audio/media/buttons/button-2"

DEST.mkdir(parents=True, exist_ok=True)

def clean_name(name: str) -> str:
    name = urllib.parse.unquote(name)
    name = name.replace("_", " ").replace("-", " ")
    name = re.sub(r"^\d+\s*episode\s*", "", name, flags=re.I)
    name = re.sub(r"\.mp3$", "", name, flags=re.I)
    name = re.sub(r"\s+", " ", name).strip()
    return name.title() + ".mp3"

for src in sorted(SOURCE.glob("*.mp3")):
    dst = DEST / clean_name(src.name)
    print(f"{src.name} -> {dst.name}")
    shutil.copy2(src, dst)

print(f"Done. Copied to {DEST}")

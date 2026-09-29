"""Stage a self-contained Flower app directory: this project + the declared libraries.

SwarmAuth and SwarmMind are separate, pre-existing libraries (github.com/hiveflowai/swarmauth, /swarmmind).
SuperGrid nodes must not need extra installs, so the FAB carries a copy of the installed versions. The copy
lives only in the staging folder (build/fab-stage), never in this repository.

    uv run python -m director.stage        # then: cd build/fab-stage && flwr build   (or flwr app publish .)
"""

from __future__ import annotations

import importlib
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STAGE = ROOT / "build" / "fab-stage"
LIBS = ("swarmauth", "swarmmind")


def stage() -> Path:
    if STAGE.exists():
        shutil.rmtree(STAGE)
    STAGE.mkdir(parents=True)
    for name in ("LICENSE", "README.md", ".gitignore"):
        shutil.copy2(ROOT / name, STAGE / name)
    # The staged app carries the libraries as source, so its pyproject must not point at git URLs
    # (Flower Hub rebuilds the FAB from the published sources).
    lines = (ROOT / "pyproject.toml").read_text().splitlines()
    kept = [l for l in lines if not any(f'"{lib} @ git+' in l for lib in LIBS)]
    text = "\n".join(kept) + "\n"
    text = text.replace("[tool.hatch.metadata]\nallow-direct-references = true\n\n", "")
    text = text.replace('packages = ["agent", "director"]', 'packages = ["agent", "swarmauth", "swarmmind"]')
    (STAGE / "pyproject.toml").write_text(text)
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    shutil.copytree(ROOT / "agent", STAGE / "agent", ignore=ignore)
    for lib in LIBS:
        src = Path(importlib.import_module(lib).__file__).parent
        shutil.copytree(src, STAGE / lib, ignore=ignore)
    return STAGE


if __name__ == "__main__":
    print(stage())

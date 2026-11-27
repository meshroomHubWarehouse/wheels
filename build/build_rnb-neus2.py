#!/usr/bin/env python3
"""Build the rnb-neus2 wheel (Python pipeline of meshroomHubWarehouse/RNb-NeuS2).

Run by the build job in .github/workflows/rnb-neus2.yml with these environment
variables:

    TAG         RNb-NeuS2 git tag/ref               e.g. v2.0.3
    REPOSITORY  GitHub repository to build from     e.g. meshroomHubWarehouse/RNb-NeuS2
    OUTPUT_DIR  directory to leave the finished wheel in

The package is pure Python: one ``py3-none-any`` wheel serves every platform
and Python version, so it is built once, on Linux. It is published here because
installing it from git checks the whole repository out, and the repository
vendors its C++ dependencies with file paths too long for a default Windows
setup (``Filename too long`` in ``dependencies/neus2_tcnn/dependencies/cutlass``).

The wheel only holds the ``rnb_neus2`` package and the ``run_pipeline`` module.
The compiled testbed they drive is distributed separately, in the releases of
the RNb-NeuS2 repository.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


def run(cmd, **kwargs) -> None:
    """subprocess.run with the command echoed to the log first, and check=True."""
    print("+ " + " ".join(map(str, cmd)), flush=True)
    subprocess.run([str(c) for c in cmd], check=True, **kwargs)


def requireEnv(name: str) -> str:
    """Fetch an env var or fail fast with a clear message (vs. a KeyError later)."""
    value = os.environ.get(name, "").strip()
    if not value:
        sys.exit(f"missing required env var {name}")
    return value


def main() -> int:
    tag = requireEnv("TAG")
    repository = requireEnv("REPOSITORY")
    outputDir = Path(requireEnv("OUTPUT_DIR"))

    py = sys.executable
    print(f"python={py} repository={repository} tag={tag}")
    run([py, "--version"])
    run([py, "-m", "pip", "install", "--upgrade", "pip", "build"])

    src = Path(tempfile.mkdtemp()) / "RNb-NeuS2"
    # No --recursive: the vendored dependencies are regular files of the
    # repository, and none of them is needed to build the Python package.
    run(["git", "clone", "--depth", "1", "--branch", tag,
         f"https://github.com/{repository}", src])

    stage = Path(tempfile.mkdtemp())
    run([py, "-m", "build", "--wheel", "--outdir", stage], cwd=src)

    wheels = sorted(stage.glob("*.whl"))
    if len(wheels) != 1:
        sys.exit(f"expected exactly one wheel in {stage}, found {[w.name for w in wheels]}")
    builtWheel = wheels[0]
    # A platform or interpreter tag here would mean the package gained a
    # compiled extension: one Linux build would no longer serve Windows.
    if not builtWheel.name.endswith("-py3-none-any.whl"):
        sys.exit(f"expected a pure-Python wheel (py3-none-any), got {builtWheel.name}")
    with zipfile.ZipFile(builtWheel) as content:
        names = content.namelist()
    for expected in ("rnb_neus2/__init__.py", "rnb_neus2/pipeline.py", "run_pipeline.py"):
        if expected not in names:
            sys.exit(f"{expected} is missing from {builtWheel.name}")

    outputDir.mkdir(parents=True, exist_ok=True)
    finalWheel = outputDir / builtWheel.name
    shutil.copy2(builtWheel, finalWheel)

    # Install with its dependencies and import it as a smoke test: catches a
    # broken wheel here rather than downstream in a consumer's environment.
    run([py, "-m", "pip", "install", "--force-reinstall", finalWheel])
    run([py, "-c", "import rnb_neus2.pipeline, run_pipeline"], cwd=tempfile.mkdtemp())
    print(f"::notice::built {finalWheel.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

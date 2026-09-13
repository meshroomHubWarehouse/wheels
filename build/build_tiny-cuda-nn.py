#!/usr/bin/env python3
"""Build one tinycudann (NVlabs/tiny-cuda-nn PyTorch bindings) wheel.

One script for both Linux and Windows. Run by the linux / windows jobs in
.github/workflows/tiny-cuda-nn.yml with these environment variables:

    TAG                 tiny-cuda-nn git tag/ref          e.g. v2.0
    TORCH               torch version                     e.g. 2.5.1
    CUDA                CUDA version installed on runner  e.g. 12.4.1
    CUDA_ARCH           SM list (';' / ',' / space)       e.g. 7.0;7.5;8.0;8.6;8.9;9.0
    OUTPUT_DIR          directory to leave the finished wheel in
    SETUPTOOLS_VERSION  setuptools pin (workflow-level env, not a dispatch input)

Steps: install the matching CUDA build of torch, clone tiny-cuda-nn, run its
PyTorch-bindings build with the requested SM list, and leave the wheel in
``OUTPUT_DIR`` unmodified.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
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
    torch = requireEnv("TORCH")
    cuda = requireEnv("CUDA")
    cudaArch = requireEnv("CUDA_ARCH")
    outputDir = Path(requireEnv("OUTPUT_DIR"))
    setuptoolsVersion = requireEnv("SETUPTOOLS_VERSION")

    cudaShort = ".".join(cuda.split(".")[:2]) # 12.4.1 -> 12.4
    cudaFlat = "cu" + cudaShort.replace(".", "") # -> cu124, PyTorch's wheel-index naming

    # Two formats because tiny-cuda-nn and PyTorch's cpp_extension each read a
    # different env var, with different separator/decimal conventions.
    arches = [a for a in re.split(r"[;,\s]+", cudaArch.strip()) if a]
    archSpaced = " ".join(arches) # "7.0 7.5 8.0" -> TORCH_CUDA_ARCH_LIST
    archCompact = ";".join(a.replace(".", "") for a in arches) # "70;75;80" -> TCNN_CUDA_ARCHITECTURES

    py = sys.executable
    print(f"python={py} torch={torch} cuda={cuda} ({cudaFlat}) arch='{archSpaced}'")
    run([py, "--version"])

    run([py, "-m", "pip", "install", "--upgrade",
         "pip", "build", "wheel", "ninja", f"setuptools=={setuptoolsVersion}"])
    # torch's own index only serves the cu-tagged torch wheel itself; its
    # dependencies (sympy, filelock, ...) come from PyPI.
    run([py, "-m", "pip", "install", f"torch=={torch}",
         "--index-url", f"https://download.pytorch.org/whl/{cudaFlat}",
         "--extra-index-url", "https://pypi.org/simple"])

    src = Path(tempfile.mkdtemp()) / "tiny-cuda-nn"
    run(["git", "clone", "--recursive", "--depth", "1", "--branch", tag,
         "https://github.com/NVlabs/tiny-cuda-nn", src])

    buildEnv = dict(os.environ)
    # Only Windows sets CUDA_HOME/CUDA_PATH (via GITHUB_ENV)
    # if present, also prepend its bin dir to PATH as a safety net.
    cudaHome = buildEnv.get("CUDA_HOME") or buildEnv.get("CUDA_PATH")
    if cudaHome:
        buildEnv["CUDA_HOME"] = cudaHome
        buildEnv["PATH"] = str(Path(cudaHome) / "bin") + os.pathsep + buildEnv["PATH"]
    buildEnv["TCNN_CUDA_ARCHITECTURES"] = archCompact
    buildEnv["TORCH_CUDA_ARCH_LIST"] = archSpaced
    buildEnv["MAX_JOBS"] = str(os.cpu_count() or 2)  # parallelism for the ninja backend below
    # Diagnostic only: printed to the CI log to confirm nvcc resolved correctly.
    subprocess.run(["nvcc", "--version"], env=buildEnv, check=False)

    stage = Path(tempfile.mkdtemp())
    # --no-isolation: reuse the torch/setuptools/ninja already installed above,
    # instead of pip resolving a fresh (non-CUDA-matched) build environment.
    run([py, "-m", "build", "--wheel", "--no-isolation", "--outdir", stage],
        cwd=src / "bindings" / "torch", env=buildEnv)

    wheels = sorted(stage.glob("*.whl"))
    if len(wheels) != 1:
        sys.exit(f"expected exactly one wheel in {stage}, found {[w.name for w in wheels]}")
    builtWheel = wheels[0]
    outputDir.mkdir(parents=True, exist_ok=True)
    finalWheel = outputDir / builtWheel.name
    shutil.copy2(builtWheel, finalWheel)

    # Install as a smoke test: catches a broken/incompatible wheel here rather
    # than downstream in a consumer's environment.
    run([py, "-m", "pip", "install", "--force-reinstall", "--no-deps", finalWheel])
    print(f"::notice::built {finalWheel.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

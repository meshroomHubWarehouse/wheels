#!/usr/bin/env python3
"""Build one nerfacc wheel with its CUDA extension compiled ahead of time.

One script for both Linux and Windows. Run by the linux / windows jobs in
.github/workflows/nerfacc.yml with these environment variables:

    VERSION             nerfacc version on PyPI           e.g. 0.3.3
    TORCH               torch version                     e.g. 2.5.1
    CUDA                CUDA version installed on runner  e.g. 12.4.1
    CUDA_ARCH           SM list (';' / ',' / space)       e.g. 7.5;8.0;8.6;8.9;9.0
    OUTPUT_DIR          directory to leave the finished wheel in
    SETUPTOOLS_VERSION  setuptools pin (workflow-level env, not a dispatch input)

nerfacc 0.3.x only ships as a pure-Python package that JIT-compiles its CUDA
sources (``torch.utils.cpp_extension.load``) on first use, which needs nvcc and
a host C++ compiler at runtime. So the sdist is patched before building:

- a ``setup.py`` is added that builds ``nerfacc/cuda/csrc/*.cu`` into the
  ``nerfacc.cuda._C`` CUDAExtension (``pybind.cu`` declares its module as
  ``TORCH_EXTENSION_NAME``, which BuildExtension sets to ``_C``).
- upstream's ``nerfacc/cuda/_backend.py`` is moved, unchanged, to
  ``_backend_jit.py``, and a new ``_backend.py`` imports the prebuilt ``_C``,
  falling back to the JIT module only if that import fails.

The Python API and package version are left untouched, so ``nerfacc==0.3.3``
resolves to the resulting ``cpXY-cpXY-<platform>`` wheel.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

SETUP_PY = '''\
"""Added by meshroomHubWarehouse/wheels: build the CUDA extension ahead of time."""

import glob
import os

from setuptools import find_packages, setup
from torch.utils.cpp_extension import BuildExtension, CUDAExtension

CSRC = os.path.join("nerfacc", "cuda", "csrc")

setup(
    # Passing ext_modules turns off setuptools' package auto-discovery.
    packages=find_packages(include=["nerfacc", "nerfacc.*"]),
    ext_modules=[
        CUDAExtension(
            name="nerfacc.cuda._C",
            sources=sorted(glob.glob(os.path.join(CSRC, "*.cu"))),
            include_dirs=[os.path.abspath(os.path.join(CSRC, "include"))],
            extra_compile_args={"cxx": ["-O3"], "nvcc": ["-O3"]},
        )
    ],
    cmdclass={"build_ext": BuildExtension},
)
'''

BACKEND_PY = '''\
"""
Copyright (c) 2022 Ruilong Li, UC Berkeley.

Patched by meshroomHubWarehouse/wheels: load the CUDA extension prebuilt into
this wheel (nerfacc.cuda._C), which needs neither nvcc nor a C++ compiler.
Upstream's JIT loader is kept unchanged in _backend_jit.py and only used if
the prebuilt extension can't be imported.
"""

import os
import warnings

import torch  # noqa: F401 -- loads the libtorch/c10 libraries _C links against

try:
    from . import _C
except ImportError as exc:
    if any(f.startswith("_C.") for f in os.listdir(os.path.dirname(__file__))):
        warnings.warn(
            f"NerfAcc: the prebuilt CUDA extension failed to load ({exc}); "
            "falling back to JIT compilation."
        )
    from ._backend_jit import _C


__all__ = ["_C"]
'''


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


def fetchSdist(version: str, dest: Path) -> Path:
    """Download nerfacc's sdist from PyPI (sha256-checked) and return its extracted root.

    Fetched directly rather than with ``pip download``, which would build the
    sdist's metadata with its pinned (Python 3.12-incompatible) setuptools.
    """
    with urllib.request.urlopen(f"https://pypi.org/pypi/nerfacc/{version}/json", timeout=30) as resp:
        files = json.load(resp)["urls"]
    sdists = [f for f in files if f["packagetype"] == "sdist"]
    if len(sdists) != 1:
        sys.exit(f"expected exactly one sdist for nerfacc {version}, found {len(sdists)}")
    sdist = sdists[0]

    archive = dest / sdist["filename"]
    print(f"downloading {sdist['url']}", flush=True)
    with urllib.request.urlopen(sdist["url"], timeout=60) as resp:
        archive.write_bytes(resp.read())
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    if digest != sdist["digests"]["sha256"]:
        sys.exit(f"sha256 mismatch for {archive.name}: {digest} != {sdist['digests']['sha256']}")

    with tarfile.open(archive) as tar:
        if hasattr(tarfile, "data_filter"):
            tar.extractall(dest, filter="data")
        else:
            tar.extractall(dest)
    return dest / f"nerfacc-{version}"


def patchSources(src: Path) -> None:
    """Add the ahead-of-time CUDAExtension build and the prebuilt-first loader."""
    if (src / "setup.py").exists():
        sys.exit(f"{src} already has a setup.py; the patch targets the setup.py-less 0.3.x sdists")
    backend = src / "nerfacc" / "cuda" / "_backend.py"
    original = backend.read_text(encoding="utf-8")
    if "_C = load(" not in original:
        sys.exit(f"{backend} doesn't JIT-load _C as expected; review the patch for this version")

    (src / "setup.py").write_text(SETUP_PY, encoding="utf-8")
    backend.rename(backend.with_name("_backend_jit.py"))
    backend.write_text(BACKEND_PY, encoding="utf-8")


def main() -> int:
    version = requireEnv("VERSION")
    torch = requireEnv("TORCH")
    cuda = requireEnv("CUDA")
    cudaArch = requireEnv("CUDA_ARCH")
    outputDir = Path(requireEnv("OUTPUT_DIR"))
    setuptoolsVersion = requireEnv("SETUPTOOLS_VERSION")

    cudaShort = ".".join(cuda.split(".")[:2]) # 12.4.1 -> 12.4
    cudaFlat = "cu" + cudaShort.replace(".", "") # -> cu124, PyTorch's wheel-index naming

    # PyTorch's cpp_extension reads TORCH_CUDA_ARCH_LIST.
    archSpaced = " ".join(a for a in re.split(r"[;,\s]+", cudaArch.strip()) if a)

    py = sys.executable
    print(f"python={py} nerfacc={version} torch={torch} cuda={cuda} ({cudaFlat}) arch='{archSpaced}'")
    run([py, "--version"])

    run([py, "-m", "pip", "install", "--upgrade",
         "pip", "build", "wheel", "ninja", f"setuptools=={setuptoolsVersion}"])
    # torch's own index only serves the cu-tagged torch wheel itself; its
    # dependencies (sympy, filelock, ...) come from PyPI.
    run([py, "-m", "pip", "install", f"torch=={torch}",
         "--index-url", f"https://download.pytorch.org/whl/{cudaFlat}",
         "--extra-index-url", "https://pypi.org/simple"])

    src = fetchSdist(version, Path(tempfile.mkdtemp()))
    patchSources(src)

    buildEnv = dict(os.environ)
    # Only Windows sets CUDA_HOME/CUDA_PATH (via GITHUB_ENV)
    # if present, also prepend its bin dir to PATH as a safety net.
    cudaHome = buildEnv.get("CUDA_HOME") or buildEnv.get("CUDA_PATH")
    if cudaHome:
        buildEnv["CUDA_HOME"] = cudaHome
        buildEnv["PATH"] = str(Path(cudaHome) / "bin") + os.pathsep + buildEnv["PATH"]
    buildEnv["TORCH_CUDA_ARCH_LIST"] = archSpaced
    buildEnv["MAX_JOBS"] = str(os.cpu_count() or 2)  # parallelism for the ninja backend
    if os.name == "nt":
        # The workflow already exported the vcvarsall env; torch's BuildExtension
        # refuses to build unless told to use it instead of re-activating MSVC.
        buildEnv["DISTUTILS_USE_SDK"] = "1"
    # Diagnostic only: printed to the CI log to confirm nvcc resolved correctly.
    subprocess.run(["nvcc", "--version"], env=buildEnv, check=False)

    stage = Path(tempfile.mkdtemp())
    # --no-isolation: reuse the torch/setuptools/ninja already installed above,
    # instead of pip resolving a fresh (non-CUDA-matched) build environment.
    # --skip-dependency-check: the sdist pins setuptools==65.5.0, which doesn't
    # run on Python 3.12; SETUPTOOLS_VERSION is used instead.
    run([py, "-m", "build", "--wheel", "--no-isolation", "--skip-dependency-check",
         "--outdir", stage],
        cwd=src, env=buildEnv)

    wheels = sorted(stage.glob("*.whl"))
    if len(wheels) != 1:
        sys.exit(f"expected exactly one wheel in {stage}, found {[w.name for w in wheels]}")
    builtWheel = wheels[0]
    if builtWheel.name.endswith("-none-any.whl"):
        sys.exit(f"{builtWheel.name} is not platform-specific: the CUDA extension wasn't built")
    outputDir.mkdir(parents=True, exist_ok=True)
    finalWheel = outputDir / builtWheel.name
    shutil.copy2(builtWheel, finalWheel)

    # Install as a smoke test: catches a broken/incompatible wheel here rather
    # than downstream in a consumer's environment. Also checks the loader picks
    # the prebuilt extension (run outside the source tree so it isn't shadowed).
    run([py, "-m", "pip", "install", "--force-reinstall", "--no-deps", finalWheel])
    run([py, "-c",
         "import nerfacc.cuda._backend as b, nerfacc.cuda._C as c; "
         "assert b._C is c and hasattr(c, 'ray_marching'); print('prebuilt _C:', c.__file__)"],
        cwd=tempfile.mkdtemp())
    print(f"::notice::built {finalWheel.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

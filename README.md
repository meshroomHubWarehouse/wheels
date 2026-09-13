# ![Meshroom Hub Warehouse - Wheels](/docs/banner-warehouse-wheels.png)

An index and a build environment for **prebuilt wheels of [Meshroom](https://github.com/alicevision/meshroom) plugin dependencies**.

Wheels are built on demand by GitHub Actions and attached to a GitHub Release. A second workflow then regenerates a static [PEP 503](https://peps.python.org/pep-0503/) / [PEP 691](https://peps.python.org/pep-0691/) package index and publishes it to GitHub Pages, so those release assets can be installed directly with `pip` or `uv`.

**Index:** https://meshroomHubWarehouse.github.io/wheels/

## Installing a wheel

Packages with no *variant* live directly at the index root:

```
https://meshroomHubWarehouse.github.io/wheels/
```

Packages with a *variant* (e.g. a CUDA/PyTorch combo such as `cu124-pt25`) live one level down:

```
https://meshroomHubWarehouse.github.io/wheels/<variant>/
```

The [index page](https://meshroomHubWarehouse.github.io/wheels/) lists every package currently published, with its variant (if any) and the matching install command.

**pip**
```bash
pip install PACKAGE --extra-index-url https://meshroomHubWarehouse.github.io/wheels/
pip install PACKAGE --extra-index-url https://meshroomHubWarehouse.github.io/wheels/cu124-pt25/
```

**uv** (with `pyproject.toml`)
```toml
[[tool.uv.index]]
name = "meshroomHubWarehouse"
url = "https://meshroomHubWarehouse.github.io/wheels/"

[[tool.uv.index]]
name = "meshroomHubWarehouse-cu124-pt25"
url = "https://meshroomHubWarehouse.github.io/wheels/cu124-pt25/"
```

## How it works

1. **`.github/workflows/<package>.yml`**: a manually-triggered (`workflow_dispatch`) build for one package, with inputs for whatever that package needs (Python/CUDA/Torch version, compute capabilities, upstream git tag, etc). It builds the wheel on Linux and/or Windows, attaches it to a Release tagged `<variant>/<package>` (or just `<package>`), and triggers the index refresh.
2. **`build/build_<package>.py`**: the actual build logic for that package, run by its workflow.
3. **`scripts/generateIndex.py`**: reads every non-draft Release in this repo and regenerates the `public/` index tree from their `.whl` assets. Deployed to GitHub Pages by `.github/workflows/publish-index.yml`.

## Adding a package

Open a PR that adds a new package following this pattern:

1. Add **`build/build_<package>.py`**: a script reading whatever inputs it needs from environment variables and leaving the finished wheel(s) in `OUTPUT_DIR`.
2. Add **`.github/workflows/<package>.yml`**: a `workflow_dispatch` workflow exposing those inputs, calling your build script, then creating/updating a Release tagged `<variant>/<package>` (or just `<package>`) with the built wheel(s) attached, and finishing with `gh workflow run publish-index.yml`.

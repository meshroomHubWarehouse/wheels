# Meshroom Hub Warehouse - Wheels

An index and a build environment for **prebuilt wheels of [Meshroom](https://github.com/alicevision/meshroom) plugin dependencies**.

Wheels are built on demand by GitHub Actions and attached to a GitHub Release. A second workflow then regenerates a static [PEP 503](https://peps.python.org/pep-0503/) / [PEP 691](https://peps.python.org/pep-0691/) package index and publishes it to GitHub Pages, so those release assets can be installed directly with `pip` or `uv`.

**Index:** https://meshroomHubWarehouse.github.io/wheels/

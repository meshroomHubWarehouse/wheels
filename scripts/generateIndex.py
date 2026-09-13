"""Generate a PEP 503 / PEP 691 index from this repo's GitHub Releases.

Every ``.whl`` attached to any non-draft release becomes an entry. Wheels are served from
their Release asset URL, so the published tree stays tiny:

    public/
      .nojekyll
      index.html                            landing page + links to each variant
      <variant>/index.html                  PEP 503 root (one <a> per package)
      <variant>/<package>/index.html        PEP 503 page  (one <a> per wheel)
      <variant>/<package>/index.json        PEP 691 payload
      <package>/index.html                  same, for a package with no variant
      <package>/index.json

``<variant>`` is an opaque string each package's workflow chooses (e.g. a
CUDA / PyTorch combo like ``cu124-pt25``) and exposes as the part of the
Release *tag* before the first ``/``: tags are ``<variant>/<package>``, e.g.
``cu124-pt25/tiny-cuda-nn``. A tag with no ``/`` has no variant, and that
package's index is published directly at the repository root instead of
under a variant directory.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from packaging.utils import canonicalize_name, parse_wheel_filename

_PAGE = (
    '<!DOCTYPE html><html><head><meta charset="utf-8">'
    '<meta name="pypi:repository-version" content="1.0">'
    "<title>{title}</title></head><body>{body}</body></html>\n"
)


@dataclass(frozen=True)
class WheelFile:
    filename: str
    url: str
    sha256: str | None

    def toIndexEntry(self) -> dict:
        """This file as a PEP 691 ``files[]`` entry."""
        return {
            "filename": self.filename,
            "url": self.url,
            "hashes": {"sha256": self.sha256} if self.sha256 else {},
        }


# variant (tag's prefix before "/") -> canonical package name -> its wheels
Index = dict[str, dict[str, list[WheelFile]]]


def fetchReleases(repo: str, token: str | None) -> list[dict]:
    import requests

    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    releases: list[dict] = []
    url = f"https://api.github.com/repos/{repo}/releases"
    params = {"per_page": 100}
    while url:
        resp = requests.get(url, headers=headers, params=params, timeout=30)
        resp.raise_for_status()
        releases.extend(resp.json())
        url = resp.links.get("next", {}).get("url")
        params = None
    return releases


def _variantFromTag(tag: str) -> str:
    """The variant is the part of a Release tag before the first ``/``."""
    variant, sep, _package = tag.partition("/")
    return variant if sep else ""


def _wheelsInRelease(release: dict) -> Iterator[tuple[str, WheelFile]]:
    """Yield ``(canonical package name, WheelFile)`` for each ``.whl`` asset."""
    for asset in release.get("assets", []) or []:
        filename = asset.get("name", "")
        if not filename.endswith(".whl"):
            continue
        try:
            rawName, _version, _build, _tags = parse_wheel_filename(filename)
        except Exception as exc:  # malformed filename -> skip, keep going
            print(f"::warning::skipping {filename}: {exc}", file=sys.stderr)
            continue
        package = str(canonicalize_name(rawName))
        digest = asset.get("digest") or ""
        sha256 = digest.split(":", 1)[1] if digest.startswith("sha256:") else None

        yield package, WheelFile(filename, asset.get("browser_download_url", ""), sha256)


def buildIndex(releases: list[dict]) -> Index:
    """Group every wheel across all non-draft releases by variant, then by package."""
    index: Index = {}
    for release in releases:
        if release.get("draft"):
            continue
        variant = _variantFromTag(release.get("tag_name") or "")
        for package, wheel in _wheelsInRelease(release):
            index.setdefault(variant, {}).setdefault(package, []).append(wheel)

    for packages in index.values():
        for wheels in packages.values():
            wheels.sort(key=lambda w: w.filename)
    return {
        variant: dict(sorted(packages.items())) for variant, packages in sorted(index.items())
    }


def _wheelCount(index: Index) -> int:
    return sum(len(wheels) for packages in index.values() for wheels in packages.values())


def _packagePage(package: str, wheels: list[WheelFile]) -> str:
    rows = []
    for wheel in wheels:
        fragment = f"#sha256={wheel.sha256}" if wheel.sha256 else ""
        rows.append(f'<a href="{wheel.url}{fragment}">{wheel.filename}</a><br>')
    return _PAGE.format(title=package, body="".join(rows))


def _rootPage(title: str, entries: list[str]) -> str:
    rows = [f'<a href="{e}/">{e}</a><br>' for e in entries]
    return _PAGE.format(title=title, body="".join(rows))


def _landingPage(index: Index, repo: str) -> str:
    owner = repo.split("/")[0] if repo else "OWNER"
    name = repo.split("/")[1] if "/" in repo else "wheels"
    baseUrl = f"https://{owner}.github.io/{name}"
    packageNames = {package for packages in index.values() for package in packages}

    blocks = []
    rootPackages = index.get("", {})
    if rootPackages:
        items = "".join(
            f'<li><a href="{package}/"><code>{package}</code></a> &mdash; {len(wheels)} wheel(s)</li>'
            for package, wheels in rootPackages.items()
        )
        blocks.append(
            f"<h2>{baseUrl} packages</h2>"
            f"<pre>pip install PACKAGE --extra-index-url {baseUrl}/</pre>"
            f"<ul>{items}</ul>"
        )
    for variant, packages in index.items():
        if not variant:
            continue
        items = "".join(
            f"<li><code>{package}</code> &mdash; {len(wheels)} wheel(s)</li>"
            for package, wheels in packages.items()
        )
        blocks.append(
            f'<h2><a href="{variant}/"><code>{variant}</code></a></h2>'
            f"<pre>pip install PACKAGE --extra-index-url {baseUrl}/{variant}/</pre>"
            f"<ul>{items}</ul>"
        )
    blocksText = "".join(blocks)

    body = (
        "<h1>Meshroom plugin dependency wheels</h1>"
        "<p>Prebuilt wheels for dependencies of Meshroom plugins.</p>"
        f"<p>{_wheelCount(index)} wheel(s) across {len(packageNames)} package(s)</p>"
        f"{blocksText}"
    )
    return _PAGE.format(title="Meshroom plugin dependency wheels", body=body)


def writeIndex(index: Index, outDir: str | Path, repo: str = "") -> None:
    outDir = Path(outDir)
    outDir.mkdir(parents=True, exist_ok=True)
    (outDir / ".nojekyll").write_text("", encoding="utf-8")

    for variant, packages in index.items():
        variantDir = outDir / variant if variant else outDir
        variantDir.mkdir(parents=True, exist_ok=True)
        if variant:
            (variantDir / "index.html").write_text(
                _rootPage(variant, list(packages)), encoding="utf-8"
            )

        for package, wheels in packages.items():
            packageDir = variantDir / package
            packageDir.mkdir(parents=True, exist_ok=True)
            (packageDir / "index.html").write_text(
                _packagePage(package, wheels), encoding="utf-8"
            )
            (packageDir / "index.json").write_text(
                json.dumps(
                    {
                        "meta": {"api-version": "1.0"},
                        "name": package,
                        "files": [wheel.toIndexEntry() for wheel in wheels],
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

    (outDir / "index.html").write_text(_landingPage(index, repo), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="public", type=Path)
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument(
        "--releases-json",
        type=Path,
        help="read releases from a file instead of the GitHub API (offline / tests)",
    )
    args = parser.parse_args(argv)

    if args.releases_json:
        payload = json.loads(args.releases_json.read_text())
        releases = payload["releases"] if isinstance(payload, dict) else payload
    else:
        if not args.repo:
            print("::error::--repo or $GITHUB_REPOSITORY required", file=sys.stderr)
            return 2
        releases = fetchReleases(args.repo, os.environ.get("GITHUB_TOKEN"))

    index = buildIndex(releases)
    writeIndex(index, args.out, args.repo)
    print(f"wrote {_wheelCount(index)} wheel(s) across {len(index)} variant(s) to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

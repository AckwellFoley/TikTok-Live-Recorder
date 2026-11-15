"""Export actual installed license texts and a runtime dependency manifest."""
import importlib.metadata as metadata
import json
from pathlib import Path
import sys
from lock_dependencies import ROOT, runtime_packages


def main():
    folder = ROOT / "THIRD-PARTY-LICENSES"
    folder.mkdir(exist_ok=True)
    packages = []
    for name in runtime_packages():
        dist = metadata.distribution(name)
        packages.append({"name": dist.metadata["Name"], "version": dist.version,
            "license": dist.metadata.get("License-Expression") or dist.metadata.get("License") or "See included license text",
            "homepage": dist.metadata.get("Home-page") or dist.metadata.get_all("Project-URL", [])})
        for entry in dist.files or []:
            if any(token in Path(str(entry)).name.lower() for token in ("license", "copying", "notice")):
                source = dist.locate_file(entry)
                if source.is_file() and source.stat().st_size < 2 * 1024 * 1024:
                    filename = name + "-" + str(entry).replace("/", "_").replace("\\", "_")
                    (folder / filename).write_bytes(source.read_bytes())
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if python_license.is_file():
        (folder / "Python-LICENSE.txt").write_bytes(python_license.read_bytes())
    for name in ("tcl8.6", "tk8.6"):
        source = Path(sys.base_prefix) / "tcl" / name / "license.terms"
        if source.is_file():
            (folder / (name + "-LICENSE.txt")).write_bytes(source.read_bytes())
    (ROOT / "DEPENDENCIES.json").write_text(json.dumps(packages, indent=2), encoding="utf-8")
    print(f"Collected notices for {len(packages)} runtime distributions")


if __name__ == "__main__":
    main()

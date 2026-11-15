"""Record the installed runtime closure; run only in the isolated build venv."""
import importlib.metadata as metadata
from pathlib import Path
import tomllib
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[1]


def runtime_packages():
    pending = [Requirement(r) for r in tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["dependencies"]]
    visited, names = set(), set()
    while pending:
        requirement = pending.pop()
        name = canonicalize_name(requirement.name)
        key = (name, tuple(sorted(requirement.extras)))
        if key in visited:
            continue
        visited.add(key)
        names.add(name)
        for text in metadata.requires(requirement.name) or []:
            child = Requirement(text)
            extras = requirement.extras or {""}
            if child.marker is None or any(child.marker.evaluate({"extra": extra}) for extra in extras):
                pending.append(child)
    return sorted(names)


if __name__ == "__main__":
    packages = runtime_packages()
    (ROOT / "requirements-lock.txt").write_text("\n".join(f"{n}=={metadata.version(n)}" for n in packages) + "\n", encoding="utf-8")
    build = [f"{d.metadata['Name']}=={d.version}" for d in metadata.distributions()]
    (ROOT / "requirements-build.txt").write_text("\n".join(sorted(build, key=str.casefold)) + "\n", encoding="utf-8")
    print(f"Locked {len(packages)} runtime distributions")

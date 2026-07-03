"""Package core — expose la version de l'application moteur_agents."""
import sys
from pathlib import Path
from importlib.metadata import version as _pkg_version, PackageNotFoundError

if sys.version_info >= (3, 11):
    import tomllib
else:
    tomllib = None


def _get_version() -> str:
    try:
        return _pkg_version("moteur_agents")
    except PackageNotFoundError:
        pass

    if tomllib:
        pyproject_path = Path(__file__).resolve().parent.parent / "pyproject.toml"
        if pyproject_path.exists():
            try:
                with open(pyproject_path, "rb") as f:
                    data = tomllib.load(f)
                    return data.get("project", {}).get("version", "unknown")
            except Exception:
                pass

    return "unknown"


__version__ = _get_version()

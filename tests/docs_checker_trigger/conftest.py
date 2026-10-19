# tests/docs_checker_trigger/conftest.py
from pathlib import Path
import importlib.util
import sys

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))


def load_script(name: str, rel: str):
    """Import a script by path, registered under `name` (dataclasses resolve the module by name)."""
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod

"""Load reconcile.py out of the ConfigMap that ships it.

The script has exactly one home - the ConfigMap - so there is no second copy to
drift. Tests extract it and import it as a module.
"""
import importlib.util
import pathlib
import tempfile

import pytest
import yaml

REPO = pathlib.Path(__file__).resolve().parents[2]
CONFIGMAP = REPO / "base-apps" / "wan-ip-monitor" / "configmap-reconcile.yaml"


@pytest.fixture(scope="session")
def reconcile():
    source = yaml.safe_load(CONFIGMAP.read_text())["data"]["reconcile.py"]
    path = pathlib.Path(tempfile.mkdtemp()) / "reconcile.py"
    path.write_text(source)
    spec = importlib.util.spec_from_file_location("reconcile", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

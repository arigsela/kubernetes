"""Load render.py out of the ConfigMap that ships it.

The script has exactly one home - the ConfigMap - so there is no second copy to
drift. Tests extract it and import it as a module. Mirrors tests/wan_ip/conftest.py.
"""
import importlib.util
import pathlib
import tempfile

import pytest
import yaml

REPO = pathlib.Path(__file__).resolve().parents[2]
CONFIGMAP = REPO / "base-apps" / "argo-workflow-tasks" / "configmap-cve-report.yaml"


@pytest.fixture(scope="session")
def render():
    source = yaml.safe_load(CONFIGMAP.read_text())["data"]["render.py"]
    path = pathlib.Path(tempfile.mkdtemp()) / "render.py"
    path.write_text(source)
    spec = importlib.util.spec_from_file_location("render", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

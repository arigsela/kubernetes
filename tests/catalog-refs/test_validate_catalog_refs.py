import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "validate-catalog-refs.py"
_spec = importlib.util.spec_from_file_location("validate_catalog_refs", _SCRIPT)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _taxonomy(root: Path) -> None:
    _write(
        root,
        "catalog/platform-entities.yaml",
        """
apiVersion: backstage.io/v1alpha1
kind: Group
metadata:
  name: platform
spec:
  type: team
  children: []
---
apiVersion: backstage.io/v1alpha1
kind: System
metadata:
  name: chores-tracker
spec:
  owner: platform
""".lstrip(),
    )


def test_all_references_resolve(tmp_path):
    _taxonomy(tmp_path)
    _write(
        tmp_path,
        "base-apps/app/catalog-info.yaml",
        """
apiVersion: backstage.io/v1alpha1
kind: Component
metadata:
  name: app
  namespace: chores-tracker
spec:
  type: service
  owner: group:default/platform
  system: default/chores-tracker
""".lstrip(),
    )
    _defined, unresolved = mod.scan(str(tmp_path))
    assert unresolved == []


def test_missing_system_is_flagged(tmp_path):
    _taxonomy(tmp_path)
    _write(
        tmp_path,
        "base-apps/app/catalog-info.yaml",
        """
apiVersion: backstage.io/v1alpha1
kind: Component
metadata:
  name: app
  namespace: chores-tracker
spec:
  type: service
  owner: group:default/platform
  system: default/does-not-exist
""".lstrip(),
    )
    _defined, unresolved = mod.scan(str(tmp_path))
    assert any(ref == "default/does-not-exist" for _p, field, ref in unresolved if field == "system")


def test_provides_and_consumes_api_resolve(tmp_path):
    _taxonomy(tmp_path)
    _write(
        tmp_path,
        "catalog/api-entities.yaml",
        """
apiVersion: backstage.io/v1alpha1
kind: API
metadata:
  name: app-api
spec:
  type: openapi
  owner: group:default/platform
  system: default/chores-tracker
  definition:
    $text: http://app.chores-tracker.svc.cluster.local/openapi.json
""".lstrip(),
    )
    _write(
        tmp_path,
        "base-apps/app/catalog-info.yaml",
        """
apiVersion: backstage.io/v1alpha1
kind: Component
metadata:
  name: app
  namespace: chores-tracker
spec:
  type: service
  owner: group:default/platform
  system: default/chores-tracker
  providesApis:
    - default/app-api
""".lstrip(),
    )
    _defined, unresolved = mod.scan(str(tmp_path))
    assert unresolved == []


def test_missing_api_is_flagged(tmp_path):
    _taxonomy(tmp_path)
    _write(
        tmp_path,
        "base-apps/app/catalog-info.yaml",
        """
apiVersion: backstage.io/v1alpha1
kind: Component
metadata:
  name: app
  namespace: chores-tracker
spec:
  type: service
  owner: group:default/platform
  system: default/chores-tracker
  consumesApis:
    - default/ghost-api
""".lstrip(),
    )
    _defined, unresolved = mod.scan(str(tmp_path))
    assert any(ref == "default/ghost-api" for _p, field, ref in unresolved if field == "consumesApis")


def test_malformed_yaml_does_not_crash(tmp_path):
    _taxonomy(tmp_path)
    _write(
        tmp_path,
        "catalog/broken.yaml",
        """
":
  - [unclosed
""".lstrip(),
    )
    mod.scan(str(tmp_path))

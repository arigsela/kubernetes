import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "render-new-app.py"
_spec = importlib.util.spec_from_file_location("render_new_app", _SCRIPT)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)


def test_templated_paths_and_content(tmp_path):
    skel = tmp_path / "skel"
    (skel / "base-apps" / "${{ values.name }}").mkdir(parents=True)
    (skel / "base-apps" / "${{ values.name }}.yaml").write_text("name: ${{ values.name }}\n")
    (skel / "base-apps" / "${{ values.name }}" / "f.yaml").write_text(
        "{% if values.on %}on: true{% endif %}\n")
    out = tmp_path / "out"
    mod.render([skel], {"name": "foo", "on": True}, out)
    assert (out / "base-apps" / "foo.yaml").read_text() == "name: foo\n"
    assert (out / "base-apps" / "foo" / "f.yaml").read_text() == "on: true\n"

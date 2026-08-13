"""Contract tests for appsets/managed-apps/*.yaml.

These configs drive base-apps/managed-apps.yaml's git files generator. A
malformed config does not fail loudly at apply time -- goTemplateOptions
missingkey=error stops the render for ALL apps in the set and the existing
Applications simply stop reconciling. These tests are the loud failure.
"""
import pathlib

import pytest
import yaml

from conftest import expand

REQUIRED_KEYS = {"name", "sourcePath", "namespace", "syncOptions"}

EXPECTED_APPS = {
    "agent-audit-aws-infrastructure",
    "argo-rollouts-config",
    "argo-workflow-tasks",
    "argo-workflows-aws-infrastructure",
    "argo-workflows-config",
    "crossplane-aws-provider",
    "crossplane-compositions",
    "crossplane-functions",
    "crossplane-system",
    "ecr-auth",
    "kyverno-policies",
    "loki-aws-infrastructure",
}


def test_expected_configs_present(configs):
    assert set(configs) == EXPECTED_APPS


def test_every_config_has_exactly_the_required_keys(configs):
    for stem, cfg in configs.items():
        assert set(cfg) == REQUIRED_KEYS, f"{stem}: keys are {sorted(cfg)}"


def test_field_types(configs):
    for stem, cfg in configs.items():
        assert isinstance(cfg["name"], str) and cfg["name"], stem
        assert isinstance(cfg["sourcePath"], str) and cfg["sourcePath"], stem
        assert isinstance(cfg["namespace"], str) and cfg["namespace"], stem
        assert isinstance(cfg["syncOptions"], list), stem
        for opt in cfg["syncOptions"]:
            assert isinstance(opt, str), f"{stem}: syncOption {opt!r} is not a string"


def test_filename_matches_name(configs):
    for stem, cfg in configs.items():
        assert cfg["name"] == stem, f"{stem}: name is {cfg['name']!r}"


def test_names_are_unique(configs):
    names = [cfg["name"] for cfg in configs.values()]
    assert len(names) == len(set(names))


def test_source_path_is_a_non_empty_directory(configs, repo_root):
    for stem, cfg in configs.items():
        d = repo_root / cfg["sourcePath"]
        assert d.is_dir(), f"{stem}: {cfg['sourcePath']} is not a directory"
        assert any(d.glob("*.yaml")), f"{stem}: {cfg['sourcePath']} has no manifests"


def test_golden_equivalence(configs, goldens):
    """Every generated spec must equal the spec of the file it replaces."""
    assert set(configs) == set(goldens)
    for stem, cfg in configs.items():
        assert expand(cfg) == goldens[stem], f"{stem}: spec would change"

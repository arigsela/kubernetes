# Ansible Host Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an `ansible/` layer to this repo that baselines and patches the libvirt hypervisor and the three k3s VMs, and replaces `scripts/install-authn-config.sh` with a playbook that keeps every guard.

**Architecture:** Three roles (`common`, `hypervisor`, `k3s_node`) applied by `site.yml`; two patch playbooks (`patch.yml` drains and reboots VMs one at a time, `patch-hypervisor.yml` shuts the VMs down in order and reboots the host); `k3s-authn.yml` validates the kube-apiserver authn files in a throwaway k3s container before installing them with backup and rollback. Tests run the real playbooks against a throwaway Ubuntu container through the `community.docker.docker` connection plugin, with `kubectl` and `systemctl` replaced by recording stubs.

**Tech Stack:** ansible-core 2.21.4, ansible-lint 26.9.0, collections ansible.posix / community.general / community.docker / community.libvirt / kubernetes.core, pytest, Docker (colima on the workstation).

**Spec:** `docs/superpowers/specs/2026-09-29-ansible-host-management-design.md`

## Global Constraints

- Pip pins: `ansible-core==2.21.4`, `ansible-lint==26.9.0`, `kubernetes==36.0.3`, `pytest==8.3.3`, `pyyaml==6.0.2`. Collection floors in `ansible/requirements.yml`: ansible.posix ≥2.2.2, community.general ≥13.4.0, community.docker ≥5.3.0, community.libvirt ≥2.3.0, kubernetes.core ≥6.6.0.
- The validation image is `rancher/k3s:v1.36.4-k3s1`, the cluster's version. Bump it with the cluster.
- Never commit the k3s join token or anything read from `/etc/systemd/system/k3s*.service.env` on the nodes.
- No `virsh destroy`, no forced VM stop, anywhere. A VM that does not shut off fails the play.
- Every serial operation runs workers first, then the control plane.
- Every Ansible YAML file starts with `---`. `ansible-lint --offline` (profile `moderate`) must pass. `systemctl`, `kubectl`, `netplan`, `mv` are deliberately `command`/`shell` tasks so the tests can stub them; mark each with `# noqa: command-instead-of-module` and give it `changed_when`.
- Run every `ansible-*` command from the `ansible/` directory so `ansible.cfg` is picked up. Tests set `cwd=ANSIBLE`.
- All four hosts are already `Etc/UTC`, so the spec's timezone task is omitted (YAGNI).
- Commit messages follow the repo style (`feat(ansible): …`, `test(ansible): …`, `docs: …`) and end with the attribution lines from the session's system reminder.
- The k3s nodes' Kubernetes node names equal their inventory hostnames (`k3s-control-01`, `k3s-worker-01`, `k3s-worker-02`). `patch.yml` relies on that.

## Review Focus

1. `ansible-playbook playbooks/patch.yml --limit k3s-worker-02`: `--limit` also drops `localhost`, so a pre-flight written as a `hosts: localhost` play would silently not run. The pre-flight is therefore a per-node task delegated to localhost. Pinned by `test_patch_has_no_localhost_play` (Task 7).
2. `k3s-authn.yml` with `authn_src` pointing at a directory missing a file: the operator expects "missing …", not a Jinja traceback. Pinned by `test_a_missing_source_file_is_a_clear_error` (Task 5).
3. `patch-hypervisor.yml -e confirm=true` (boolean, not the string `yes`): the operator expects the gate to accept it. Pinned by `test_confirm_accepts_true_and_yes` (Task 8).
4. A rendered netplan file that `netplan generate` rejects: the previous file must be restored and nothing applied. Pinned by `test_a_rejected_netplan_config_is_restored` (Task 4).
5. `k3s-authn.yml` when Docker is stopped: the operator expects "Docker is not running", not a connection traceback, and nothing touching the node. Pinned by `test_docker_down_is_a_clear_error` (Task 5).

---

### Task 1: Scaffold `ansible/`, inventory, and the lint gate

**Files:**
- Create: `ansible/ansible.cfg`, `ansible/requirements.yml`, `ansible/.ansible-lint`, `ansible/.yamllint`, `ansible/README.md`
- Create: `ansible/inventory/hosts.yml`, `ansible/inventory/group_vars/all.yml`, `ansible/inventory/group_vars/k3s_control.yml`, `ansible/inventory/group_vars/k3s_workers.yml`, `ansible/inventory/group_vars/k3s_nodes.yml`
- Create: `ansible/inventory/host_vars/asela-k8s.yml`, `ansible/inventory/host_vars/k3s-control-01.yml`, `ansible/inventory/host_vars/k3s-worker-01.yml`, `ansible/inventory/host_vars/k3s-worker-02.yml`
- Create: `tests/ansible/__init__.py` (empty), `tests/ansible/test_lint.py`
- Modify: `.gitignore` (append)

**Interfaces:**
- Produces: inventory groups `hypervisors`, `k3s_control`, `k3s_workers`, `k3s_nodes` (parent of the last two); vars `k3s_unit`, `k3s_node_gateway`, `k3s_node_nameservers`, `k3s_node_static_ip`, `k3s_node_mac`, `vm_name`, `vm_shutdown_order`, `drain_timeout`, `reboot_timeout`, `k3s_api_url`. Every later task reads these names.

- [ ] **Step 1: Install the workstation tooling**

```bash
uv tool install "ansible-core==2.21.4" --with "kubernetes==36.0.3" --with requests
uv tool install "ansible-lint==26.9.0"
ansible --version | head -1     # expect: ansible [core 2.21.4]
ansible-lint --version | head -1
```

- [ ] **Step 2: Write the failing lint test**

`tests/ansible/test_lint.py`:

```python
"""Lint and syntax gates for the ansible/ tree. The ansible-validate CI job runs exactly this."""
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
ANSIBLE = REPO / "ansible"
PLAYBOOKS = sorted((ANSIBLE / "playbooks").glob("*.yml")) if (ANSIBLE / "playbooks").is_dir() else []


def run(*args):
    return subprocess.run(args, cwd=ANSIBLE, capture_output=True, text=True)


def test_inventory_has_the_four_hosts():
    r = run("ansible-inventory", "--graph")
    assert r.returncode == 0, r.stderr
    for host in ("asela-k8s", "k3s-control-01", "k3s-worker-01", "k3s-worker-02"):
        assert host in r.stdout, r.stdout
    assert "@k3s_nodes:" in r.stdout, "k3s_control and k3s_workers must be children of k3s_nodes"


def test_ansible_lint_is_clean():
    r = run("ansible-lint", "--offline")
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.parametrize("playbook", PLAYBOOKS, ids=lambda p: p.name)
def test_playbook_syntax(playbook):
    r = run("ansible-playbook", "--syntax-check", str(playbook))
    assert r.returncode == 0, r.stderr
```

- [ ] **Step 3: Run it to see it fail**

Run: `python -m pytest tests/ansible/test_lint.py -v`
Expected: FAIL (`ansible-inventory` cannot find `inventory/hosts.yml`; cwd does not exist).

- [ ] **Step 4: Write the config files**

`ansible/ansible.cfg`:

```ini
[defaults]
inventory = inventory/hosts.yml
roles_path = roles
host_key_checking = True
retry_files_enabled = False
forks = 5
interpreter_python = auto_silent

[ssh_connection]
pipelining = True
ssh_args = -o ControlMaster=auto -o ControlPersist=60s -o IdentitiesOnly=yes
```

`ansible/requirements.yml`:

```yaml
---
collections:
  - name: ansible.posix
    version: ">=2.2.2,<3.0.0"
  - name: community.general
    version: ">=13.4.0,<14.0.0"
  - name: community.docker
    version: ">=5.3.0,<6.0.0"
  - name: community.libvirt
    version: ">=2.3.0,<3.0.0"
  - name: kubernetes.core
    version: ">=6.6.0,<7.0.0"
```

`ansible/.ansible-lint`:

```yaml
---
profile: moderate
exclude_paths:
  - .cache/
  - .ansible/
```

`ansible/.yamllint` (ansible-lint reads it from the directory it runs in; mirrors the repo's `.yamllint.yaml`):

```yaml
---
extends: default
rules:
  line-length:
    max: 200
    level: warning
  truthy:
    check-keys: false
  comments:
    min-spaces-from-content: 1
```

Install the collections: `cd ansible && ansible-galaxy collection install -r requirements.yml`

- [ ] **Step 5: Write the inventory**

`ansible/inventory/hosts.yml`:

```yaml
---
# Hosts below the cluster. The k3s node names here are the Kubernetes node names too:
# playbooks/patch.yml drains and uncordons by inventory_hostname.
all:
  children:
    hypervisors:
      hosts:
        asela-k8s:
          ansible_host: 10.0.1.101
    k3s_nodes:
      children:
        k3s_control:
          hosts:
            k3s-control-01:
              ansible_host: 10.0.1.50
        k3s_workers:
          hosts:
            k3s-worker-01:
              ansible_host: 10.0.1.5
            k3s-worker-02:
              ansible_host: 10.0.1.108
```

`ansible/inventory/group_vars/all.yml`:

```yaml
---
ansible_user: asela
ansible_ssh_private_key_file: ~/.ssh/ari_sela_key
ansible_python_interpreter: /usr/bin/python3

# playbooks/patch.yml and patch-hypervisor.yml
drain_timeout: 300     # seconds kubectl drain may take per node
reboot_timeout: 600    # seconds a host may take to come back
k3s_api_url: https://10.0.1.50:6443
```

`ansible/inventory/group_vars/k3s_nodes.yml`:

```yaml
---
# Static-address settings for roles/k3s_node (workers only; see host_vars). Mirrors k3s-control-01's
# hand-written netplan.
k3s_node_gateway: 10.0.1.1
k3s_node_nameservers:
  - 8.8.8.8
  - 1.1.1.1
```

`ansible/inventory/group_vars/k3s_control.yml`:

```yaml
---
k3s_unit: k3s
```

`ansible/inventory/group_vars/k3s_workers.yml`:

```yaml
---
k3s_unit: k3s-agent
```

`ansible/inventory/host_vars/asela-k8s.yml`:

```yaml
---
# libvirt domain names, in the order patch-hypervisor.yml shuts them down: workers first, the
# control plane last. Every running VM must be listed or the playbook refuses to reboot the host.
vm_shutdown_order:
  - k3s-worker-01
  - k3s-worker-02
  - k3s-control-01
```

`ansible/inventory/host_vars/k3s-control-01.yml`:

```yaml
---
vm_name: k3s-control-01
# No k3s_node_static_ip: the control node's netplan was written static at install time and is
# left alone (roles/k3s_node only pins hosts that define k3s_node_static_ip).
```

`ansible/inventory/host_vars/k3s-worker-01.yml`:

```yaml
---
vm_name: k3s-worker-01
# The address this VM held by DHCP on 2026-09-29, pinned static by roles/k3s_node.
k3s_node_static_ip: 10.0.1.5/24
k3s_node_mac: "52:54:00:67:81:26"
```

`ansible/inventory/host_vars/k3s-worker-02.yml`:

```yaml
---
vm_name: k3s-worker-02
k3s_node_static_ip: 10.0.1.108/24
k3s_node_mac: "52:54:00:b2:24:6b"
```

- [ ] **Step 6: Write the short README and ignore the caches**

`ansible/README.md`:

```markdown
# ansible/ — the host layer

Manages the Ubuntu hypervisor (`asela-k8s`, 10.0.1.101) and the three k3s VMs at the OS level.
Terraform (`terraform/`) owns AWS and the Argo CD bootstrap; Argo CD (`base-apps/`) owns everything
in the cluster; system-upgrade-controller owns the k3s version. This tree owns what is left:
packages, sudo, sshd, unattended-upgrades, the workers' static addresses, reboots, and the control
plane's kube-apiserver authentication config.

## Install (workstation)

    uv tool install "ansible-core==2.21.4" --with "kubernetes==36.0.3" --with requests
    uv tool install "ansible-lint==26.9.0"
    cd ansible && ansible-galaxy collection install -r requirements.yml

Run every command from this directory (ansible.cfg lives here). Playbooks arrive in later tasks;
see the "Runbook" section once they exist.
```

Append to `.gitignore`:

```
# ansible-lint / ansible-galaxy caches
ansible/.cache/
ansible/.ansible/
```

- [ ] **Step 7: Run the tests to see them pass**

Run: `python -m pytest tests/ansible/test_lint.py -v`
Expected: 2 passed (the syntax test has no playbooks yet and is collected as zero cases).

- [ ] **Step 8: Commit**

```bash
git add -A ansible tests/ansible .gitignore
git commit -m "feat(ansible): scaffold the host layer — config, inventory, lint gate"
```

---

### Task 2: Test container, `common` role, `bootstrap.yml`, `site.yml`

**Files:**
- Create: `tests/ansible/conftest.py`, `tests/ansible/Dockerfile.node`, `tests/ansible/systemctl`, `tests/ansible/test_common.py`
- Create: `ansible/roles/common/defaults/main.yml`, `ansible/roles/common/tasks/main.yml`, `ansible/roles/common/tasks/sudoers.yml`, `ansible/roles/common/handlers/main.yml`
- Create: `ansible/playbooks/bootstrap.yml`, `ansible/playbooks/site.yml`

**Interfaces:**
- Produces (tests): fixtures `node` (container name), `inventory_for(group)` (path to an inventory placing the container in `group`), helpers `node_exec(name, cmd, stdin=None)`, `node_write(name, path, content)`, `node_read(name, path)`, `run_playbook(playbook, inventory, *args, env=None, check=False)`, marker `needs_docker`. Tasks 4, 5, 8 import these.
- Produces (ansible): role `common` with var `common_admin_user` (default `{{ ansible_user }}`) and task file `sudoers.yml`; `site.yml` plays tagged `common`, `hypervisor`, `k3s_node` (the last two are added in Tasks 3 and 4).

- [ ] **Step 1: Write the test container**

`tests/ansible/Dockerfile.node`:

```dockerfile
# A throwaway Ubuntu that stands in for a host. Reached through the community.docker.docker
# connection plugin, so no sshd listener or key is needed. Contents, and why:
#   python3, sudo        Ansible's needs (plays use become: true; sudo as root needs no password)
#   python3-apt          the apt module refuses to run without it
#   openssh-server       so `sshd -t` can validate the hardening drop-in
#   unattended-upgrades  so the role's apt task is a no-op instead of a network install
#   netplan.io           so `netplan generate` can validate a rendered file
#   systemctl (stub)     records what it was asked; there is no systemd here
FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
      python3 python3-apt sudo openssh-server unattended-upgrades netplan.io ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && ssh-keygen -A && mkdir -p /run/sshd
RUN mkdir -p /etc/rancher/k3s/config.yaml.d \
    && printf 'disable:\n  - traefik\n' > /etc/rancher/k3s/config.yaml
COPY systemctl /usr/bin/systemctl
RUN chmod 0755 /usr/bin/systemctl
CMD ["sleep", "infinity"]
```

`tests/ansible/systemctl`:

```sh
#!/bin/sh
# Stub systemctl for the test container: records every call to /var/log/systemctl.log and
# answers the reads the playbooks make. Everything else succeeds silently.
echo "$*" >> /var/log/systemctl.log
case "$*" in
  "cat k3s") printf '[Service]\nExecStart=/usr/local/bin/k3s server\n' ;;
  "is-active k3s"|"is-active k3s-agent") echo active ;;
esac
exit 0
```

- [ ] **Step 2: Write the fixtures**

`tests/ansible/conftest.py`:

```python
"""Shared fixtures: a throwaway Ubuntu container that stands in for a host (see Dockerfile.node)."""
import os
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
ANSIBLE = REPO / "ansible"
HERE = Path(__file__).parent
IMAGE = "ansible-test-node:local"


def docker_available():
    return shutil.which("docker") is not None and \
        subprocess.run(["docker", "info"], capture_output=True).returncode == 0


needs_docker = pytest.mark.skipif(not docker_available(), reason="needs Docker")


@pytest.fixture(scope="session")
def node_image():
    r = subprocess.run(["docker", "build", "-q", "-t", IMAGE, "-f", str(HERE / "Dockerfile.node"), str(HERE)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return IMAGE


@pytest.fixture
def node(node_image):
    name = f"ansible-test-{uuid.uuid4().hex[:8]}"
    subprocess.run(["docker", "run", "-d", "--name", name, node_image], check=True, capture_output=True)
    yield name
    subprocess.run(["docker", "rm", "-fv", name], capture_output=True)


def node_exec(name, cmd, stdin=None):
    return subprocess.run(["docker", "exec", "-i", name, "sh", "-c", cmd], input=stdin,
                          capture_output=True, text=True)


def node_write(name, path, content):
    r = node_exec(name, f"mkdir -p $(dirname {path}) && cat > {path}", stdin=content)
    assert r.returncode == 0, r.stderr


def node_read(name, path):
    r = node_exec(name, f"cat {path}")
    return r.stdout if r.returncode == 0 else ""


@pytest.fixture
def inventory_for(node, tmp_path):
    """Inventory placing the container in one group; the other groups exist but are empty."""
    def make(group):
        groups = {g: "{}" for g in ("hypervisors", "k3s_control", "k3s_workers")}
        groups[group] = f"\n        {node}:\n          ansible_host: {node}"
        inv = tmp_path / f"inventory-{group}.yml"
        inv.write_text(
            "all:\n  children:\n"
            f"    hypervisors:\n      hosts: {groups['hypervisors']}\n"
            "    k3s_nodes:\n      children:\n"
            f"        k3s_control:\n          hosts: {groups['k3s_control']}\n"
            f"        k3s_workers:\n          hosts: {groups['k3s_workers']}\n"
            "  vars:\n"
            "    ansible_connection: community.docker.docker\n"
            "    ansible_python_interpreter: /usr/bin/python3\n"
            "    ansible_user: root\n"
            "    k3s_unit: k3s\n"
            "    k3s_node_gateway: 10.0.1.1\n"
            "    k3s_node_nameservers: [8.8.8.8, 1.1.1.1]\n"
        )
        return inv
    return make


def run_playbook(playbook, inventory, *args, env=None, check=False):
    cmd = ["ansible-playbook", "-i", str(inventory), str(ANSIBLE / "playbooks" / playbook), *args]
    if check:
        cmd.append("--check")
    full_env = {**os.environ, "ANSIBLE_FORCE_COLOR": "0", "ANSIBLE_NOCOLOR": "1", **(env or {})}
    return subprocess.run(cmd, cwd=ANSIBLE, capture_output=True, text=True, env=full_env, timeout=900)
```

- [ ] **Step 3: Write the failing role test**

`tests/ansible/test_common.py`:

```python
"""roles/common through playbooks/site.yml --tags common, against the test container."""
import re

from conftest import needs_docker, node_exec, node_read, run_playbook


@needs_docker
def test_common_role_applies_and_is_idempotent(node, inventory_for):
    inv = inventory_for("k3s_control")
    r = run_playbook("site.yml", inv, "--tags", "common", "-e", "common_admin_user=asela")
    assert r.returncode == 0, r.stdout + r.stderr
    assert node_read(node, "/etc/sudoers.d/asela") == "asela ALL=(ALL) NOPASSWD:ALL\n"
    hardening = node_read(node, "/etc/ssh/sshd_config.d/10-hardening.conf")
    assert "PasswordAuthentication no" in hardening and "PermitRootLogin prohibit-password" in hardening
    assert node_exec(node, "sshd -t").returncode == 0, "the drop-in must leave sshd's config valid"
    assert "reload ssh" in node_read(node, "/var/log/systemctl.log"), "sshd must be reloaded after the drop-in"
    policy = node_read(node, "/etc/apt/apt.conf.d/52unattended-upgrades-local")
    assert 'Automatic-Reboot "false"' in policy and 'Remove-Unused-Dependencies "true"' in policy
    assert 'Unattended-Upgrade "1"' in node_read(node, "/etc/apt/apt.conf.d/20auto-upgrades")

    again = run_playbook("site.yml", inv, "--tags", "common", "-e", "common_admin_user=asela")
    assert again.returncode == 0, again.stdout + again.stderr
    assert re.search(r"changed=0\s", again.stdout), "second run must change nothing:\n" + again.stdout


@needs_docker
def test_bootstrap_installs_only_the_sudoers_dropin(node, inventory_for):
    r = run_playbook("bootstrap.yml", inventory_for("hypervisors"), "-e", "common_admin_user=asela")
    assert r.returncode == 0, r.stdout + r.stderr
    assert node_read(node, "/etc/sudoers.d/asela") == "asela ALL=(ALL) NOPASSWD:ALL\n"
    assert node_read(node, "/etc/ssh/sshd_config.d/10-hardening.conf") == "", "bootstrap must not harden sshd"
```

- [ ] **Step 4: Run it to see it fail**

Run: `colima start` (if needed), then `python -m pytest tests/ansible/test_common.py -v`
Expected: FAIL, `playbooks/site.yml` not found.

- [ ] **Step 5: Write the role**

`ansible/roles/common/defaults/main.yml`:

```yaml
---
# The account that gets passwordless sudo. Defaults to the connecting user (asela on the real
# hosts); the tests connect as root and pass -e common_admin_user=asela.
common_admin_user: "{{ ansible_user }}"
```

`ansible/roles/common/tasks/sudoers.yml`:

```yaml
---
# Split out so playbooks/bootstrap.yml can run just this with --ask-become-pass.
- name: Passwordless sudo for {{ common_admin_user }}
  ansible.builtin.copy:
    dest: "/etc/sudoers.d/{{ common_admin_user }}"
    content: "{{ common_admin_user }} ALL=(ALL) NOPASSWD:ALL\n"
    owner: root
    group: root
    mode: "0440"
    validate: /usr/sbin/visudo -cf %s
```

`ansible/roles/common/tasks/main.yml`:

```yaml
---
- name: Sudoers
  ansible.builtin.import_tasks: sudoers.yml

- name: Harden sshd with a drop-in that sorts before the cloud-init ones
  ansible.builtin.copy:
    dest: /etc/ssh/sshd_config.d/10-hardening.conf
    content: |
      # Managed by Ansible (ansible/roles/common). sshd keeps the FIRST value it reads, and
      # sshd_config includes sshd_config.d/* before its own lines, so this file wins over
      # 50-cloud-init.conf and 60-cloudimg-settings.conf.
      PasswordAuthentication no
      KbdInteractiveAuthentication no
      PermitRootLogin prohibit-password
    owner: root
    group: root
    mode: "0644"
    validate: /usr/sbin/sshd -t -f %s
  notify: Reload sshd

- name: Unattended-upgrades is installed
  ansible.builtin.apt:
    name: unattended-upgrades
    state: present

- name: Unattended-upgrades installs security patches but never reboots
  ansible.builtin.copy:
    dest: /etc/apt/apt.conf.d/52unattended-upgrades-local
    content: |
      // Managed by Ansible (ansible/roles/common). Reboots are done by playbooks/patch.yml and
      // playbooks/patch-hypervisor.yml, never by unattended-upgrades.
      Unattended-Upgrade::Automatic-Reboot "false";
      Unattended-Upgrade::Remove-Unused-Dependencies "true";
    owner: root
    group: root
    mode: "0644"

- name: Refresh package lists and run unattended-upgrades daily
  ansible.builtin.copy:
    dest: /etc/apt/apt.conf.d/20auto-upgrades
    content: |
      APT::Periodic::Update-Package-Lists "1";
      APT::Periodic::Unattended-Upgrade "1";
    owner: root
    group: root
    mode: "0644"

- name: QEMU guest agent on KVM guests
  ansible.builtin.apt:
    name: qemu-guest-agent
    state: present
  when: ansible_virtualization_role == 'guest' and ansible_virtualization_type == 'kvm'

- name: QEMU guest agent running
  ansible.builtin.service:
    name: qemu-guest-agent
    enabled: true
    state: started
  when: ansible_virtualization_role == 'guest' and ansible_virtualization_type == 'kvm'
```

`ansible/roles/common/handlers/main.yml`:

```yaml
---
- name: Reload sshd
  ansible.builtin.command: systemctl reload ssh  # noqa: command-instead-of-module (stubbed in tests)
  changed_when: true
```

- [ ] **Step 6: Write the playbooks**

`ansible/playbooks/bootstrap.yml`:

```yaml
---
# bootstrap.yml — one-time: passwordless sudo for the admin user on hosts that still ask for a
# password (the hypervisor). Run once, with the sudo password:
#   ansible-playbook playbooks/bootstrap.yml --ask-become-pass
# After this, site.yml and the patch playbooks run without prompts.
- name: Bootstrap passwordless sudo
  hosts: hypervisors
  become: true
  gather_facts: false
  tasks:
    - name: Install the sudoers drop-in
      ansible.builtin.include_role:
        name: common
        tasks_from: sudoers
```

`ansible/playbooks/site.yml`:

```yaml
---
# site.yml — the baseline for every host. Idempotent; re-run any time.
#   ansible-playbook playbooks/site.yml --check --diff   # preview
#   ansible-playbook playbooks/site.yml                  # apply
#   ansible-playbook playbooks/site.yml --tags common    # one role
- name: Baseline every host
  hosts: all
  become: true
  roles:
    - role: common
      tags: [common]
```

- [ ] **Step 7: Run the tests to see them pass**

Run: `python -m pytest tests/ansible/ -v`
Expected: all pass (lint, syntax for both playbooks, the two container tests).

- [ ] **Step 8: Commit**

```bash
git add ansible tests/ansible
git commit -m "feat(ansible): common role, bootstrap and site playbooks, container test harness"
```

---

### Task 3: `hypervisor` role

**Files:**
- Create: `ansible/roles/hypervisor/defaults/main.yml`, `ansible/roles/hypervisor/tasks/main.yml`
- Modify: `ansible/playbooks/site.yml` (append a play)

**Interfaces:**
- Consumes: `vm_shutdown_order` from `host_vars/asela-k8s.yml` (Task 1).
- Produces: nothing other tasks import. Verified by lint, syntax, and a check-mode run against the real host.

- [ ] **Step 1: Write the role**

`ansible/roles/hypervisor/defaults/main.yml`:

```yaml
---
# VMs that must start with the host. Defaults to the shutdown list so the two never drift.
hypervisor_vms: "{{ vm_shutdown_order }}"
```

`ansible/roles/hypervisor/tasks/main.yml`:

```yaml
---
- name: Virtualization packages
  ansible.builtin.apt:
    name:
      - qemu-kvm
      - libvirt-daemon-system
      - libvirt-clients
      - python3-libvirt        # community.libvirt.virt runs on the host and needs it
      - smartmontools
    state: present

- name: Admin user may manage VMs without sudo
  ansible.builtin.user:
    name: "{{ ansible_user }}"
    groups:
      - libvirt
      - kvm
    append: true

- name: Every cluster VM starts with the host
  community.libvirt.virt:
    name: "{{ item }}"
    autostart: true
    uri: qemu:///system
  loop: "{{ hypervisor_vms }}"
```

- [ ] **Step 2: Wire it into site.yml**

Append to `ansible/playbooks/site.yml`:

```yaml

- name: Hypervisor
  hosts: hypervisors
  become: true
  roles:
    - role: hypervisor
      tags: [hypervisor]
```

- [ ] **Step 3: Lint and syntax**

Run: `python -m pytest tests/ansible/test_lint.py -v`
Expected: PASS.

- [ ] **Step 4: Check mode against the real host (sudo still asks for a password until the rollout runs bootstrap)**

Run: `cd ansible && ansible-playbook playbooks/site.yml --limit asela-k8s --tags hypervisor --check --diff --ask-become-pass`
Expected: `ok` for the packages (all present), `ok` for the groups (asela is already in libvirt and kvm), `ok` for the three autostart tasks. `changed=0`. Anything else means the role does not encode the host's real state; fix the role, not the host.

- [ ] **Step 5: Commit**

```bash
git add ansible
git commit -m "feat(ansible): hypervisor role — libvirt packages, groups, VM autostart"
```

---

### Task 4: `k3s_node` role (static addresses) and the authn files move

**Files:**
- Create: `ansible/roles/k3s_node/defaults/main.yml`, `ansible/roles/k3s_node/tasks/main.yml`, `ansible/roles/k3s_node/templates/netplan-static.yaml.j2`
- Move: `node-config/k3s-control-01/authn-config.yaml` → `ansible/roles/k3s_node/files/authn/authn-config.yaml`; `node-config/k3s-control-01/config.yaml.d/10-authn.yaml` → `ansible/roles/k3s_node/files/authn/config.yaml.d/10-authn.yaml`
- Modify: `ansible/playbooks/site.yml` (append a play); `tests/node-config/test_install_authn_config.py:17-18` (`SRC` path, so the old suite keeps passing until Task 5 deletes it)
- Create: `tests/ansible/test_k3s_node.py`

**Interfaces:**
- Consumes: `k3s_node_static_ip`, `k3s_node_mac`, `k3s_node_gateway`, `k3s_node_nameservers` (Task 1); fixtures from Task 2.
- Produces: `ansible/roles/k3s_node/files/authn/` as the source of truth Task 5's playbook reads; role vars `k3s_node_netplan_file`, `k3s_node_netplan_apply`, `k3s_node_interface`.

- [ ] **Step 1: Write the failing tests**

`tests/ansible/test_k3s_node.py`:

```python
"""roles/k3s_node through playbooks/site.yml --tags k3s_node, against the test container."""
from conftest import needs_docker, node_read, node_write, run_playbook

DHCP = "network:\n  version: 2\n  ethernets:\n    enp1s0:\n      dhcp4: true\n"
STATIC_VARS = ("-e", "k3s_node_static_ip=10.0.1.5/24", "-e", "k3s_node_mac=52:54:00:67:81:26",
               "-e", "k3s_node_netplan_apply=false")   # no networkd in a container


@needs_docker
def test_a_worker_gets_a_static_netplan_and_cloud_init_is_disabled(node, inventory_for):
    node_write(node, "/etc/netplan/50-cloud-init.yaml", DHCP)
    r = run_playbook("site.yml", inventory_for("k3s_workers"), "--tags", "k3s_node", *STATIC_VARS)
    assert r.returncode == 0, r.stdout + r.stderr
    rendered = node_read(node, "/etc/netplan/50-cloud-init.yaml")
    assert "- 10.0.1.5/24" in rendered and 'macaddress: "52:54:00:67:81:26"' in rendered
    assert "via: 10.0.1.1" in rendered and "- 8.8.8.8" in rendered and "dhcp4" not in rendered
    assert node_read(node, "/etc/cloud/cloud.cfg.d/99-disable-network-config.cfg") == "network: {config: disabled}\n"


@needs_docker
def test_a_host_without_a_static_ip_is_left_alone(node, inventory_for):
    node_write(node, "/etc/netplan/50-cloud-init.yaml", DHCP)
    r = run_playbook("site.yml", inventory_for("k3s_control"), "--tags", "k3s_node")
    assert r.returncode == 0, r.stdout + r.stderr
    assert node_read(node, "/etc/netplan/50-cloud-init.yaml") == DHCP
    assert node_read(node, "/etc/cloud/cloud.cfg.d/99-disable-network-config.cfg") == ""


@needs_docker
def test_a_rejected_netplan_config_is_restored(node, inventory_for):
    """Review Focus 4: an address without a prefix makes `netplan generate` fail; the previous file
    must come back and the play must fail loudly."""
    node_write(node, "/etc/netplan/50-cloud-init.yaml", DHCP)
    r = run_playbook("site.yml", inventory_for("k3s_workers"), "--tags", "k3s_node",
                     "-e", "k3s_node_static_ip=10.0.1.5", "-e", "k3s_node_mac=52:54:00:67:81:26",
                     "-e", "k3s_node_netplan_apply=false")
    assert r.returncode != 0
    assert "netplan rejected" in r.stdout, r.stdout
    assert node_read(node, "/etc/netplan/50-cloud-init.yaml") == DHCP
```

- [ ] **Step 2: Run them to see them fail**

Run: `python -m pytest tests/ansible/test_k3s_node.py -v`
Expected: FAIL (no `k3s_node` play in site.yml; tag matches nothing, netplan file unchanged in test 1).

- [ ] **Step 3: Write the role**

`ansible/roles/k3s_node/defaults/main.yml`:

```yaml
---
k3s_node_netplan_file: /etc/netplan/50-cloud-init.yaml
k3s_node_interface: enp1s0
# Set false where there is no networkd to talk to (the test container).
k3s_node_netplan_apply: true
```

`ansible/roles/k3s_node/templates/netplan-static.yaml.j2`:

```
# Managed by Ansible (ansible/roles/k3s_node). Static address pinned from the DHCP lease this VM
# held on 2026-09-29; cloud-init network config is disabled so this file is not regenerated.
network:
  version: 2
  ethernets:
    {{ k3s_node_interface }}:
      match:
        macaddress: "{{ k3s_node_mac }}"
      addresses:
        - {{ k3s_node_static_ip }}
      routes:
        - to: default
          via: {{ k3s_node_gateway }}
      nameservers:
        addresses:
{% for ns in k3s_node_nameservers %}
          - {{ ns }}
{% endfor %}
      set-name: "{{ k3s_node_interface }}"
```

`ansible/roles/k3s_node/tasks/main.yml`:

```yaml
---
# The control plane's kube-apiserver authn files live in files/authn/ so this role is the single
# source for node-level k3s config, but ONLY playbooks/k3s-authn.yml installs them: installing
# restarts the API server and needs the validation that playbook does first.

- name: Pin the address as static netplan (hosts that define k3s_node_static_ip)
  when: k3s_node_static_ip is defined
  block:
    - name: Stop cloud-init from regenerating the netplan file
      ansible.builtin.copy:
        dest: /etc/cloud/cloud.cfg.d/99-disable-network-config.cfg
        content: "network: {config: disabled}\n"
        owner: root
        group: root
        mode: "0644"

    - name: Write the static netplan config
      ansible.builtin.template:
        src: netplan-static.yaml.j2
        dest: "{{ k3s_node_netplan_file }}"
        owner: root
        group: root
        mode: "0600"
        backup: true
      register: netplan

    - name: Validate, then apply
      when: netplan.changed and not ansible_check_mode
      block:
        - name: Netplan must accept the rendered config
          ansible.builtin.command: netplan generate  # noqa: command-instead-of-module
          changed_when: false

        - name: Apply (the address does not change, so the session survives)
          ansible.builtin.command: netplan apply  # noqa: command-instead-of-module
          changed_when: true
          when: k3s_node_netplan_apply | bool

        - name: The host must still answer
          ansible.builtin.wait_for_connection:
            delay: 5
            timeout: 90
          when: k3s_node_netplan_apply | bool
      rescue:
        - name: Restore the previous netplan file
          ansible.builtin.copy:
            remote_src: true
            src: "{{ netplan.backup_file }}"
            dest: "{{ k3s_node_netplan_file }}"
            owner: root
            group: root
            mode: "0600"
          when: netplan.backup_file is defined

        - name: Fail loudly
          ansible.builtin.fail:
            msg: >-
              netplan rejected the rendered config on {{ inventory_hostname }}; the previous file was
              restored and nothing was applied. Check k3s_node_static_ip / k3s_node_mac in host_vars.
```

`netplan generate` only renders files and needs no running networkd, so it works inside the container (netplan.io is in the image). If it fails there, fix the container image; do not make the validation task skippable.

- [ ] **Step 4: Wire it into site.yml**

Append to `ansible/playbooks/site.yml`:

```yaml

- name: K3s nodes
  hosts: k3s_nodes
  become: true
  roles:
    - role: k3s_node
      tags: [k3s_node]
```

- [ ] **Step 5: Move the authn files and update their header comments**

```bash
mkdir -p ansible/roles/k3s_node/files/authn/config.yaml.d
git mv node-config/k3s-control-01/authn-config.yaml ansible/roles/k3s_node/files/authn/authn-config.yaml
git mv node-config/k3s-control-01/config.yaml.d/10-authn.yaml ansible/roles/k3s_node/files/authn/config.yaml.d/10-authn.yaml
rmdir -p node-config/k3s-control-01/config.yaml.d 2>/dev/null; rm -rf node-config
```

In `ansible/roles/k3s_node/files/authn/authn-config.yaml`, replace the header's third and fourth lines

```
# /etc/rancher/k3s/authn-config.yaml by scripts/install-authn-config.sh, NEVER by hand:
# the installer validates it in a throwaway k3s first. docs/troubleshooting/kubectl-oidc.md.
```

with

```
# /etc/rancher/k3s/authn-config.yaml by ansible/playbooks/k3s-authn.yml, NEVER by hand:
# the playbook validates it in a throwaway k3s first. docs/troubleshooting/kubectl-oidc.md.
```

In `ansible/roles/k3s_node/files/authn/config.yaml.d/10-authn.yaml`, replace

```
# Installed to /etc/rancher/k3s/config.yaml.d/10-authn.yaml by scripts/install-authn-config.sh
# (a change here needs a k3s restart; the installer does it and waits for /readyz).
```

with

```
# Installed to /etc/rancher/k3s/config.yaml.d/10-authn.yaml by ansible/playbooks/k3s-authn.yml
# (a change here needs a k3s restart; the playbook does it and waits for /readyz).
```

In `tests/node-config/test_install_authn_config.py` line 18 change `SRC = REPO / "node-config" / "k3s-control-01"` to `SRC = REPO / "ansible" / "roles" / "k3s_node" / "files" / "authn"` so the old suite still passes until Task 5 removes it.

- [ ] **Step 6: Run the tests**

Run: `python -m pytest tests/ansible/ tests/node-config/ -v`
Expected: all pass (the Docker-marked node-config cases may skip if colima is stopped; `test_shipped_files_pass_the_static_guards` must pass with the new path).

- [ ] **Step 7: Commit**

```bash
git add ansible tests node-config
git commit -m "feat(ansible): k3s_node role pins worker addresses; authn files move under the role"
```

---

### Task 5: `k3s-authn.yml` play 1 — validate in a throwaway k3s

**Files:**
- Create: `ansible/playbooks/k3s-authn.yml` (play 1 only; Task 6 appends play 2)
- Create: `tests/ansible/test_k3s_authn.py` (the static-guard and Docker-validation cases; Task 6 adds the install cases)

**Interfaces:**
- Consumes: `ansible/roles/k3s_node/files/authn/` (Task 4); `run_playbook` (Task 2).
- Produces: play 1 tagged `validate`, its static-guard tasks additionally tagged `static`, its container tasks additionally tagged `container`; variable `authn_src` (overridable with `-e`). Task 6 relies on `--skip-tags validate` to run play 2 alone and on `any_errors_fatal` stopping the playbook when play 1 fails.

- [ ] **Step 1: Write the failing tests**

`tests/ansible/test_k3s_authn.py`:

```python
"""playbooks/k3s-authn.yml. It changes the control plane's kube-apiserver config, so these pin
what must NEVER happen: a file the API server rejects reaching the node, a config without the
`anonymous` block (k3s would silently ENABLE anonymous auth), an --oidc-* flag slipping past a
refusal guard. Validation uses a real k3s in Docker; the "node" is the test container.
"""
import os
import stat

import pytest

from conftest import ANSIBLE, needs_docker, node_exec, node_read, node_write, run_playbook

SRC = ANSIBLE / "roles" / "k3s_node" / "files" / "authn"
SHIPPED_AUTHN = (SRC / "authn-config.yaml").read_text()
SHIPPED_DROPIN = (SRC / "config.yaml.d" / "10-authn.yaml").read_text()


def source_dir(tmp_path, authn=SHIPPED_AUTHN, dropin=SHIPPED_DROPIN):
    src = tmp_path / "authn"
    (src / "config.yaml.d").mkdir(parents=True)
    if authn is not None:
        (src / "authn-config.yaml").write_text(authn)
    if dropin is not None:
        (src / "config.yaml.d" / "10-authn.yaml").write_text(dropin)
    return src


def no_node_inventory(tmp_path):
    inv = tmp_path / "inventory-empty.yml"
    inv.write_text("all:\n  children:\n    k3s_control:\n      hosts: {}\n")
    return inv


def stub_on_path(tmp_path, name, body):
    d = tmp_path / "bin"
    d.mkdir(exist_ok=True)
    p = d / name
    p.write_text(body)
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return {"PATH": f"{d}:{os.environ['PATH']}"}


# ---- static guards (no Docker) --------------------------------------------------------------

def test_config_without_the_anonymous_block_is_refused(tmp_path):
    """Without it, k3s drops --anonymous-auth=false and anonymous becomes ENABLED."""
    src = source_dir(tmp_path, authn="apiVersion: apiserver.config.k8s.io/v1\nkind: AuthenticationConfiguration\njwt: []\n")
    r = run_playbook("k3s-authn.yml", no_node_inventory(tmp_path), "--tags", "static", "-e", f"authn_src={src}")
    assert r.returncode != 0 and "anonymous" in r.stdout, r.stdout + r.stderr


def test_an_oidc_flag_in_the_dropin_is_refused_but_a_comment_is_not(tmp_path):
    src = source_dir(tmp_path, dropin="kube-apiserver-arg+:\n  - oidc-issuer-url=https://x\n")
    r = run_playbook("k3s-authn.yml", no_node_inventory(tmp_path), "--tags", "static", "-e", f"authn_src={src}")
    assert r.returncode != 0 and "oidc" in r.stdout, r.stdout + r.stderr
    src = source_dir(tmp_path / "ok", dropin="# no --oidc-* flags here, by design\nkube-apiserver-arg+:\n  - authentication-config=/etc/rancher/k3s/authn-config.yaml\n")
    r = run_playbook("k3s-authn.yml", no_node_inventory(tmp_path), "--tags", "static", "-e", f"authn_src={src}")
    assert r.returncode == 0, r.stdout + r.stderr


def test_shipped_files_pass_the_static_guards(tmp_path):
    r = run_playbook("k3s-authn.yml", no_node_inventory(tmp_path), "--tags", "static")
    assert r.returncode == 0, r.stdout + r.stderr


def test_a_missing_source_file_is_a_clear_error(tmp_path):
    """Review Focus 2."""
    src = source_dir(tmp_path, dropin=None)
    r = run_playbook("k3s-authn.yml", no_node_inventory(tmp_path), "--tags", "static", "-e", f"authn_src={src}")
    assert r.returncode != 0 and "missing" in r.stdout, r.stdout + r.stderr
    assert "Traceback" not in r.stderr


def test_docker_down_is_a_clear_error(tmp_path):
    """Review Focus 5: a stub `docker` whose `info` fails stands in for a stopped colima."""
    env = stub_on_path(tmp_path, "docker", "#!/bin/sh\n[ \"$1\" = info ] && exit 1\nexit 0\n")
    r = run_playbook("k3s-authn.yml", no_node_inventory(tmp_path), "--tags", "validate", env=env)
    assert r.returncode != 0 and "Docker is not running" in r.stdout, r.stdout + r.stderr


# ---- the throwaway k3s (Docker) -------------------------------------------------------------

@needs_docker
def test_a_file_the_api_server_rejects_fails_validation(tmp_path):
    broken = ("apiVersion: apiserver.config.k8s.io/v1\nkind: AuthenticationConfiguration\n"
              "anonymous:\n  enabled: false\njwt:\n  - issuer:\n      url: http://not-https.example\n"
              "      audiences: [kubernetes]\n    claimMappings:\n      username:\n        claim: sub\n")
    src = source_dir(tmp_path, authn=broken)
    r = run_playbook("k3s-authn.yml", no_node_inventory(tmp_path), "--tags", "validate", "-e", f"authn_src={src}")
    assert r.returncode != 0 and "VALIDATION FAILED" in r.stdout, r.stdout + r.stderr


@needs_docker
def test_the_shipped_files_validate(tmp_path):
    r = run_playbook("k3s-authn.yml", no_node_inventory(tmp_path), "--tags", "validate")
    assert r.returncode == 0 and "anonymous → 401" in r.stdout, r.stdout + r.stderr
```

- [ ] **Step 2: Run them to see them fail**

Run: `python -m pytest tests/ansible/test_k3s_authn.py -v`
Expected: FAIL, playbook not found.

- [ ] **Step 3: Write play 1**

`ansible/playbooks/k3s-authn.yml`:

```yaml
---
# k3s-authn.yml — install kube-apiserver's structured authentication config on the control plane.
# Port of scripts/install-authn-config.sh (removed 2026-09); every guard kept. Source of truth:
# roles/k3s_node/files/authn/{authn-config.yaml,config.yaml.d/10-authn.yaml}.
#
#   ansible-playbook playbooks/k3s-authn.yml --check   # the old --dry-run: validate, read the node, report
#   ansible-playbook playbooks/k3s-authn.yml           # install
#
# Play 1 (workstation): static guards, then the files are mounted into a throwaway k3s of the
#   cluster's version: the API must come up, load the config, and answer anonymous with 401.
#   A broken file stops here and never reaches the node. Needs Docker (colima start).
# Play 2 (control plane): refuse if the node sets --oidc-*; checksum; back up; install by
#   temp+rename (0600). Drop-in changed → restart k3s, wait for /readyz, ROLL BACK on timeout.
#   Only authn-config.yaml changed → the API server hot-reloads it; wait for the reload counter.
#   Post-check: anonymous is still 401. /readyz and /metrics go through kubectl and your
#   kubeconfig because anonymous cannot read them.
#
# Tags: validate (play 1: static + container), install (play 2). The tests run play 2 alone
# with --skip-tags validate; an operator must not.
- name: Validate the authn files in a throwaway k3s
  hosts: localhost
  connection: local
  gather_facts: false
  any_errors_fatal: true   # a failure here must stop play 2
  check_mode: false        # validation runs for real under --check
  tags: [validate]
  vars:
    authn_src: "{{ playbook_dir }}/../roles/k3s_node/files/authn"
    authn_files:
      - authn-config.yaml
      - config.yaml.d/10-authn.yaml
    k3s_image: rancher/k3s:v1.36.4-k3s1   # the cluster's version; bump with it
    authn_validate_name: "install-authn-validate-{{ 1000000 | random }}"
    # colima and Docker Desktop only share $HOME with their VM: stage the files there.
    authn_stage: "{{ lookup('env', 'HOME') }}/.cache/install-authn-config/{{ authn_validate_name }}"
  tasks:
    - name: The source files must exist
      ansible.builtin.stat:
        path: "{{ authn_src }}/{{ item }}"
      loop: "{{ authn_files }}"
      register: src_stat
      tags: [static]

    - name: Refuse on a missing source file
      ansible.builtin.assert:
        that: src_stat.results | map(attribute='stat.exists') | list == [true, true]
        fail_msg: "missing {{ src_stat.results | rejectattr('stat.exists') | map(attribute='item') | join(', ') }} under {{ authn_src }}"
        quiet: true
      tags: [static]

    - name: Read the candidate files
      ansible.builtin.set_fact:
        authn_cfg: "{{ lookup('file', authn_src + '/authn-config.yaml') | from_yaml }}"
        dropin_text: "{{ lookup('file', authn_src + '/config.yaml.d/10-authn.yaml') }}"
      tags: [static]

    - name: REFUSED unless authn-config.yaml carries anonymous disabled
      ansible.builtin.assert:
        that:
          - authn_cfg.apiVersion | default('') == 'apiserver.config.k8s.io/v1'
          - authn_cfg.kind | default('') == 'AuthenticationConfiguration'
          - authn_cfg.anonymous is defined
          - authn_cfg.anonymous.enabled is defined
          - authn_cfg.anonymous.enabled is sameas false
          - authn_cfg.jwt | default([]) is not string
          - authn_cfg.jwt | default([]) is sequence
        fail_msg: >-
          REFUSED: authn-config.yaml must carry `anonymous: {enabled: false}`. k3s stops passing
          --anonymous-auth=false when given this file, and the default is ENABLED.
        quiet: true
      tags: [static]

    - name: REFUSED if the drop-in sets an --oidc-* flag
      ansible.builtin.assert:
        that: dropin_text | regex_replace('(?m)^\s*#.*$', '') is not search('oidc-')
        fail_msg: "REFUSED: 10-authn.yaml sets an --oidc-* flag (exclusive with --authentication-config)"
        quiet: true
      tags: [static]

    - name: Static guards passed
      ansible.builtin.debug:
        msg: "1. static guards: ok"
      tags: [static]

    - name: Is Docker running?
      ansible.builtin.command: docker info
      register: docker_info
      changed_when: false
      failed_when: false
      tags: [container]

    - name: Docker is required for the validation
      ansible.builtin.fail:
        msg: "Docker is not running (colima start). Nothing changed on the node."
      when: docker_info.rc != 0
      tags: [container]

    - name: Validate in a throwaway k3s
      tags: [container]
      block:
        - name: Stage the files under $HOME
          ansible.builtin.copy:
            src: "{{ authn_src }}/{{ item }}"
            dest: "{{ authn_stage }}/{{ item }}"
            mode: "0644"
          loop: "{{ authn_files }}"

        - name: Start the validation k3s
          community.docker.docker_container:
            name: "{{ authn_validate_name }}"
            image: "{{ k3s_image }}"
            privileged: true
            published_ports:
              - "127.0.0.1::6443"
            volumes:
              - "{{ authn_stage }}/authn-config.yaml:/etc/rancher/k3s/authn-config.yaml:ro"
              - "{{ authn_stage }}/config.yaml.d:/etc/rancher/k3s/config.yaml.d:ro"
            command: server --disable traefik --disable metrics-server --disable local-storage --disable servicelb
            state: started
          register: val

        - name: Wait for the validation API server
          ansible.builtin.command: docker exec {{ authn_validate_name }} kubectl get --raw /readyz
          register: val_ready
          until: val_ready.stdout == 'ok'
          retries: 60
          delay: 2
          changed_when: false

        - name: The API server must have loaded the config
          ansible.builtin.command: docker exec {{ authn_validate_name }} kubectl get --raw /metrics
          register: val_metrics
          changed_when: false
          failed_when: "'apiserver_authentication_config_controller_last_config_info' not in val_metrics.stdout"

        - name: Anonymous must get 401
          ansible.builtin.uri:
            url: "https://127.0.0.1:{{ val.container.NetworkSettings.Ports['6443/tcp'][0].HostPort }}/version"
            validate_certs: false
            status_code: [401]

        - name: Validated
          ansible.builtin.debug:
            msg: "2. validated in {{ k3s_image }}: API up, config loaded, anonymous → 401"
      rescue:
        - name: Last log lines of the validation k3s
          ansible.builtin.command: docker logs --tail 15 {{ authn_validate_name }}
          register: val_logs
          changed_when: false
          failed_when: false

        - name: Stop
          ansible.builtin.fail:
            msg: |
              VALIDATION FAILED: the API server does not start with these files, did not load the
              config, or answered anonymous with something other than 401. Nothing changed on the node.
              {{ (val_logs.stdout | default('')) ~ (val_logs.stderr | default('')) }}
      always:
        - name: Remove the validation k3s and its volumes
          community.docker.docker_container:
            name: "{{ authn_validate_name }}"
            state: absent
            keep_volumes: false

        - name: Remove the staged files
          ansible.builtin.file:
            path: "{{ authn_stage }}"
            state: absent
```

Notes for the implementer: `published_ports: "127.0.0.1::6443"` asks Docker for a random loopback port, exactly what the script's `-p 127.0.0.1::6443` did; if the collection rejects the form, use `"127.0.0.1:0:6443"`. The `until` loop fails the task when retries run out, which is what sends control to `rescue`.

- [ ] **Step 4: Run the tests to see them pass**

Run: `python -m pytest tests/ansible/test_k3s_authn.py tests/ansible/test_lint.py -v`
Expected: all pass (the two Docker cases take a minute or two while k3s boots; they skip if colima is stopped, so start it).

- [ ] **Step 5: Commit**

```bash
git add ansible/playbooks/k3s-authn.yml tests/ansible/test_k3s_authn.py
git commit -m "feat(ansible): k3s-authn play 1 — static guards and throwaway-k3s validation"
```

---

### Task 6: `k3s-authn.yml` play 2 — install with backup, rollback, hot reload; retire the script

**Files:**
- Modify: `ansible/playbooks/k3s-authn.yml` (append play 2)
- Modify: `tests/ansible/test_k3s_authn.py` (append the install cases)
- Delete: `scripts/install-authn-config.sh`, `tests/node-config/test_install_authn_config.py` (and the now-empty `tests/node-config/`)

**Interfaces:**
- Consumes: fixtures `node`, `inventory_for`, `node_write`, `node_read`, `run_playbook` (Task 2); play 1 tags (Task 5).
- Produces: play 2 tagged `install`; variables `k3s_api_url` (from group_vars, overridable), `ready_timeout` (240), `reload_timeout` (90), `backup_dir` under `/etc/rancher/k3s/authn-backup/<UTC stamp>/`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/ansible/test_k3s_authn.py`:

```python
# ---- play 2: the node ------------------------------------------------------------------------
# kubectl is replaced by a stub on PATH: /readyz answers ok once $READYZ_OK_AFTER calls were made;
# /metrics reports a success counter of 5, plus 1 once the NEW authn-config.yaml is on the node
# (an "instant" reload, the regression this suite pins). Anonymous checks go to a local HTTP
# server that answers 401 to everything.
import hashlib
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

KUBECTL_STUB = r"""#!/usr/bin/env bash
case "$*" in
  *"get --raw /readyz"*)
    n=$(( $(cat "$CALLS" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$CALLS"
    if [ "$n" -ge "${READYZ_OK_AFTER:-1}" ]; then echo ok; else echo "[+]ping failed"; exit 1; fi ;;
  *"get --raw /metrics"*)
    cur=$(docker exec "$NODE" sha256sum /etc/rancher/k3s/authn-config.yaml 2>/dev/null | cut -d' ' -f1)
    if [ "$cur" = "$NEW_SHA" ]; then c=6; else c=5; fi
    echo "apiserver_authentication_config_controller_automatic_reloads_total{apiserver_id_hash=\"x\",status=\"success\"} $c"
    echo "apiserver_authentication_config_controller_automatic_reloads_total{apiserver_id_hash=\"x\",status=\"failure\"} 0" ;;
esac
"""


@pytest.fixture
def api_401():
    class Deny(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(401)
            self.end_headers()

        def log_message(self, *args):
            pass

    srv = HTTPServer(("127.0.0.1", 0), Deny)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


@pytest.fixture
def node_env(tmp_path, node):
    env = stub_on_path(tmp_path, "kubectl", KUBECTL_STUB)
    env.update({"CALLS": str(tmp_path / "calls"), "NODE": node,
                "NEW_SHA": hashlib.sha256(SHIPPED_AUTHN.encode()).hexdigest()})
    return env


def install_args(api_401, **extra):
    args = ["--skip-tags", "validate", "-e", f"k3s_api_url={api_401}"]
    for k, v in extra.items():
        args += ["-e", f"{k}={v}"]
    return args


@needs_docker
def test_a_file_the_api_server_rejects_never_reaches_the_node(tmp_path, node, inventory_for, node_env):
    broken = ("apiVersion: apiserver.config.k8s.io/v1\nkind: AuthenticationConfiguration\n"
              "anonymous:\n  enabled: false\njwt:\n  - issuer:\n      url: http://not-https.example\n"
              "      audiences: [kubernetes]\n    claimMappings:\n      username:\n        claim: sub\n")
    src = source_dir(tmp_path, authn=broken)
    r = run_playbook("k3s-authn.yml", inventory_for("k3s_control"), "-e", f"authn_src={src}", env=node_env)
    assert r.returncode != 0 and "VALIDATION FAILED" in r.stdout, r.stdout + r.stderr
    assert node_read(node, "/etc/rancher/k3s/authn-config.yaml") == "", "the broken file reached the node"
    assert node_read(node, "/var/log/systemctl.log") == "", "k3s was touched"


@needs_docker
def test_dry_run_validates_then_only_reads_the_node(tmp_path, node, inventory_for, node_env, api_401):
    r = run_playbook("k3s-authn.yml", inventory_for("k3s_control"), "-e", f"k3s_api_url={api_401}",
                     env=node_env, check=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "anonymous → 401" in r.stdout and "DRY:" in r.stdout and "restart k3s" in r.stdout
    assert node_read(node, "/etc/rancher/k3s/authn-config.yaml") == ""
    assert node_read(node, "/var/log/systemctl.log") == ""
    assert node_read(node, "/etc/rancher/k3s/authn-backup") == ""


@needs_docker
def test_an_oidc_flag_already_on_the_node_is_refused(tmp_path, node, inventory_for, node_env, api_401):
    """Regression (B21): the guard once passed exactly when it should fire. The match sits after
    200 comment lines and before 8000 non-comment lines, the shape that hid the old bug."""
    cfg = "".join(f"# line {i}\n" for i in range(200)) + \
        "kube-apiserver-arg:\n  - oidc-issuer-url=https://dex.example\n" + \
        "".join(f"extra-setting-{i:06d}: padding padding padding\n" for i in range(8000))
    node_write(node, "/etc/rancher/k3s/config.yaml", cfg)
    r = run_playbook("k3s-authn.yml", inventory_for("k3s_control"), *install_args(api_401), env=node_env)
    assert r.returncode != 0 and "already sets an --oidc-*" in r.stdout, r.stdout + r.stderr
    assert node_read(node, "/etc/rancher/k3s/authn-config.yaml") == ""


@needs_docker
def test_already_installed_does_nothing(tmp_path, node, inventory_for, node_env, api_401):
    node_write(node, "/etc/rancher/k3s/authn-config.yaml", SHIPPED_AUTHN)
    node_write(node, "/etc/rancher/k3s/config.yaml.d/10-authn.yaml", SHIPPED_DROPIN)
    r = run_playbook("k3s-authn.yml", inventory_for("k3s_control"), *install_args(api_401), env=node_env)
    assert r.returncode == 0 and "already installed" in r.stdout, r.stdout + r.stderr
    assert node_read(node, "/var/log/systemctl.log") == ""
    assert node_read(node, "/etc/rancher/k3s/authn-backup") == ""


@needs_docker
def test_an_instant_hot_reload_is_seen(tmp_path, node, inventory_for, node_env, api_401):
    """Regression: a config with no issuers reloads in under a second, before an installer that
    read its baseline AFTER writing the file could look. The baseline must come first."""
    node_write(node, "/etc/rancher/k3s/config.yaml.d/10-authn.yaml", SHIPPED_DROPIN)   # drop-in unchanged
    r = run_playbook("k3s-authn.yml", inventory_for("k3s_control"), *install_args(api_401), env=node_env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "reloaded (success counter 6)" in r.stdout, r.stdout
    assert node_read(node, "/etc/rancher/k3s/authn-config.yaml") == SHIPPED_AUTHN
    assert "restart" not in node_read(node, "/var/log/systemctl.log"), "a config-only change must not restart k3s"
    assert "anonymous still denied" in r.stdout


@needs_docker
def test_a_dropin_change_restarts_and_the_api_returns(tmp_path, node, inventory_for, node_env, api_401):
    node_write(node, "/etc/rancher/k3s/authn-config.yaml", SHIPPED_AUTHN)
    node_write(node, "/etc/rancher/k3s/config.yaml.d/10-authn.yaml", "# old drop-in\n")
    r = run_playbook("k3s-authn.yml", inventory_for("k3s_control"), *install_args(api_401), env=node_env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert node_read(node, "/etc/rancher/k3s/config.yaml.d/10-authn.yaml") == SHIPPED_DROPIN
    assert node_read(node, "/var/log/systemctl.log").count("restart k3s") == 1
    backups = node_exec(node, "ls /etc/rancher/k3s/authn-backup").stdout.split()
    assert len(backups) == 1
    assert node_read(node, f"/etc/rancher/k3s/authn-backup/{backups[0]}/config.yaml.d/10-authn.yaml") == "# old drop-in\n"
    assert node_exec(node, "stat -c %a /etc/rancher/k3s/config.yaml.d/10-authn.yaml").stdout.strip() == "600"


@needs_docker
def test_a_dropin_change_rolls_back_when_the_api_does_not_return(tmp_path, node, inventory_for, node_env, api_401):
    node_write(node, "/etc/rancher/k3s/authn-config.yaml", SHIPPED_AUTHN)
    node_write(node, "/etc/rancher/k3s/config.yaml.d/10-authn.yaml", "# old drop-in\n")
    node_env["READYZ_OK_AFTER"] = "999"
    r = run_playbook("k3s-authn.yml", inventory_for("k3s_control"), *install_args(api_401, ready_timeout=3), env=node_env)
    assert r.returncode != 0, r.stdout + r.stderr
    assert "ROLLBACK DID NOT RESTORE THE API" in r.stdout, r.stdout
    assert node_read(node, "/etc/rancher/k3s/config.yaml.d/10-authn.yaml") == "# old drop-in\n", "rollback must restore the old drop-in"
    assert node_read(node, "/var/log/systemctl.log").count("restart k3s") == 2, "install restart + rollback restart"


@needs_docker
def test_rollback_reports_when_the_api_comes_back(tmp_path, node, inventory_for, node_env, api_401):
    """ready_timeout=3 → retries 1 → 2 /readyz attempts per wait. The first wait's 2 attempts fail,
    the rollback's first attempt (call 3) succeeds."""
    node_write(node, "/etc/rancher/k3s/authn-config.yaml", SHIPPED_AUTHN)
    node_write(node, "/etc/rancher/k3s/config.yaml.d/10-authn.yaml", "# old drop-in\n")
    node_env["READYZ_OK_AFTER"] = "3"
    r = run_playbook("k3s-authn.yml", inventory_for("k3s_control"), *install_args(api_401, ready_timeout=3), env=node_env)
    assert r.returncode != 0 and "rolled back; the API is back on the previous config" in r.stdout, r.stdout + r.stderr
    assert node_read(node, "/etc/rancher/k3s/config.yaml.d/10-authn.yaml") == "# old drop-in\n"
```

The top-of-file import already brings in `node_exec`, `node_read`, `node_write`; the fixtures `node`, `inventory_for` are discovered from conftest.py and need no import.

- [ ] **Step 2: Run them to see them fail**

Run: `python -m pytest tests/ansible/test_k3s_authn.py -v -k "node or dry_run or installed or reload or dropin or rollback"`
Expected: FAIL (play 2 does not exist; `--skip-tags validate` runs nothing and the assertions on the node fail).

- [ ] **Step 3: Write play 2**

Append to `ansible/playbooks/k3s-authn.yml`:

```yaml

- name: Install the authn config on the control plane
  hosts: k3s_control
  become: true
  gather_facts: false
  tags: [install]
  vars:
    authn_src: "{{ playbook_dir }}/../roles/k3s_node/files/authn"
    authn_files:
      - authn-config.yaml
      - config.yaml.d/10-authn.yaml
    k3s_dir: /etc/rancher/k3s
    ready_timeout: 240    # seconds to wait for /readyz after a restart
    reload_timeout: 90    # seconds to wait for a hot reload
    # Lazily evaluated against whatever `metrics` currently holds (baseline, then each poll).
    _reloads: "{{ metrics.stdout_lines | select('match', '^apiserver_authentication_config_controller_automatic_reloads_total') }}"
    reloads_ok: "{{ _reloads | select('search', 'status=\"success\"') | map('regex_search', '\\S+$') | map('float') | sum | int }}"
    reloads_fail: "{{ _reloads | select('search', 'status=\"failure\"') | map('regex_search', '\\S+$') | map('float') | sum | int }}"
  tasks:
    # ---- 3. node preflight (read-only) ------------------------------------------------------
    - name: Read the node's k3s config and unit
      ansible.builtin.shell: >-
        cat {{ k3s_dir }}/config.yaml {{ k3s_dir }}/config.yaml.d/*.yaml 2>/dev/null;
        systemctl cat k3s 2>/dev/null; true
      register: node_cfg
      changed_when: false
      check_mode: false

    - name: REFUSED if the node already sets an --oidc-* flag
      ansible.builtin.assert:
        that: node_cfg.stdout | regex_replace('(?m)^\s*#.*$', '') is not search('oidc-')
        fail_msg: "REFUSED: the node's k3s config or unit already sets an --oidc-* flag"
        quiet: true

    - name: Checksum the installed files
      ansible.builtin.stat:
        path: "{{ k3s_dir }}/{{ item }}"
        checksum_algorithm: sha256
      loop: "{{ authn_files }}"
      register: installed
      check_mode: false

    - name: Decide what changed
      ansible.builtin.set_fact:
        changed_authn: "{{ (installed.results[0].stat.checksum | default('')) != (lookup('file', authn_src + '/authn-config.yaml', rstrip=False) | hash('sha256')) }}"
        changed_dropin: "{{ (installed.results[1].stat.checksum | default('')) != (lookup('file', authn_src + '/config.yaml.d/10-authn.yaml', rstrip=False) | hash('sha256')) }}"
        backup_dir: "{{ k3s_dir }}/authn-backup/{{ lookup('pipe', 'date -u +%Y%m%dT%H%M%SZ') }}"

    - name: Node preflight
      ansible.builtin.debug:
        msg: "3. node preflight: ok (changed: authn-config.yaml={{ changed_authn }} 10-authn.yaml={{ changed_dropin }})"

    - name: Already installed
      ansible.builtin.debug:
        msg: "already installed; nothing to do"
      when: not (changed_authn | bool) and not (changed_dropin | bool)

    - name: Stop when there is nothing to do
      ansible.builtin.meta: end_host
      when: not (changed_authn | bool) and not (changed_dropin | bool)

    # Hot-reload path: the baseline is taken BEFORE installing. A file with no JWT issuers reloads
    # in well under a second, i.e. before a post-install read (B21's second finding).
    - name: Read the reload counters before installing (config-only change)
      ansible.builtin.command: kubectl get --raw /metrics  # noqa: command-instead-of-module (stubbed in tests)
      delegate_to: localhost
      become: false
      register: metrics
      changed_when: false
      check_mode: false
      when: not (changed_dropin | bool)

    - name: Remember the baseline
      ansible.builtin.set_fact:
        reloads_ok_before: "{{ reloads_ok }}"
        reloads_fail_before: "{{ reloads_fail }}"
      when: not (changed_dropin | bool)

    # ---- 4. back up, then install by temp + rename -------------------------------------------
    - name: Back up the current files (a marker records one that did not exist)
      ansible.builtin.shell: >-
        mkdir -p {{ backup_dir }}/config.yaml.d &&
        for f in {{ authn_files | join(' ') }}; do
          if [ -e {{ k3s_dir }}/$f ]; then cp -p {{ k3s_dir }}/$f {{ backup_dir }}/$f;
          else touch {{ backup_dir }}/.no-$(basename $f); fi;
        done
      changed_when: true

    - name: Install changed files to a temp name (0600)
      ansible.builtin.copy:
        src: "{{ authn_src }}/{{ item.name }}"
        dest: "{{ k3s_dir }}/{{ item.name }}.tmp"
        owner: root
        group: root
        mode: "0600"
      loop:
        - {name: authn-config.yaml, changed: "{{ changed_authn }}"}
        - {name: config.yaml.d/10-authn.yaml, changed: "{{ changed_dropin }}"}
      loop_control:
        label: "{{ item.name }}"
      when: item.changed | bool

    - name: Rename into place
      ansible.builtin.command: mv {{ k3s_dir }}/{{ item.name }}.tmp {{ k3s_dir }}/{{ item.name }}  # noqa: command-instead-of-module
      loop:
        - {name: authn-config.yaml, changed: "{{ changed_authn }}"}
        - {name: config.yaml.d/10-authn.yaml, changed: "{{ changed_dropin }}"}
      loop_control:
        label: "{{ item.name }}"
      when: item.changed | bool
      changed_when: true

    - name: Installed
      ansible.builtin.debug:
        msg: "4. backed up to {{ backup_dir }}; installed"
      when: not ansible_check_mode

    - name: DRY RUN — what would happen next
      ansible.builtin.debug:
        msg: >-
          4. (dry run) would back up to {{ backup_dir }} and install the changed files, then
          {{ 'DRY: would restart k3s and wait up to ' ~ ready_timeout ~ 's for /readyz (auto-rollback on timeout)'
             if changed_dropin | bool else
             'DRY: would wait up to ' ~ reload_timeout ~ 's for the API server to hot-reload authn-config.yaml' }},
          then check anonymous {{ k3s_api_url }}/api → 401
      when: ansible_check_mode

    # ---- 5a. drop-in changed: restart, wait, roll back on timeout ----------------------------
    - name: Restart k3s and wait for the API (drop-in changed)
      when: changed_dropin | bool and not ansible_check_mode
      block:
        - name: Restart k3s (control-plane blip; workloads keep running)
          ansible.builtin.command: systemctl restart k3s  # noqa: command-instead-of-module (stubbed in tests)
          changed_when: true

        - name: Wait for /readyz
          ansible.builtin.command: kubectl get --raw /readyz  # noqa: command-instead-of-module
          delegate_to: localhost
          become: false
          register: readyz
          until: readyz.stdout == 'ok'
          retries: "{{ ready_timeout | int // 3 }}"
          delay: 3
          changed_when: false

        - name: API ready
          ansible.builtin.debug:
            msg: "5. k3s restarted; API ready"
      rescue:
        - name: API NOT ready — ROLLING BACK
          ansible.builtin.shell: >-
            for f in {{ authn_files | join(' ') }}; do
              if [ -e {{ backup_dir }}/.no-$(basename $f) ]; then rm -f {{ k3s_dir }}/$f;
              else cp -p {{ backup_dir }}/$f {{ k3s_dir }}/$f; fi;
            done && systemctl restart k3s
          changed_when: true

        - name: Wait for /readyz after the rollback
          ansible.builtin.command: kubectl get --raw /readyz  # noqa: command-instead-of-module
          delegate_to: localhost
          become: false
          register: readyz_rb
          until: readyz_rb.stdout == 'ok'
          retries: "{{ ready_timeout | int // 3 }}"
          delay: 3
          changed_when: false
          ignore_errors: true

        - name: Report the rollback
          ansible.builtin.fail:
            msg: >-
              {{ 'rolled back; the API is back on the previous config'
                 if (readyz_rb.stdout | default('')) == 'ok' else
                 'ROLLBACK DID NOT RESTORE THE API: fix on the node by hand' }}
              (backup: {{ backup_dir }})

    # ---- 5b. config only: wait for the hot reload --------------------------------------------
    - name: Wait for the API server to hot-reload authn-config.yaml (config-only change)
      when: not (changed_dropin | bool) and not ansible_check_mode
      block:
        - name: Poll the reload counters
          ansible.builtin.command: kubectl get --raw /metrics  # noqa: command-instead-of-module
          delegate_to: localhost
          become: false
          register: metrics
          until: (reloads_ok | int) > (reloads_ok_before | int) or (reloads_fail | int) > (reloads_fail_before | int)
          retries: "{{ reload_timeout | int // 3 }}"
          delay: 3
          changed_when: false
      rescue:
        - name: No reload
          ansible.builtin.fail:
            msg: "no reload observed within {{ reload_timeout }}s (success counter was {{ reloads_ok_before }} before the install)"

    - name: The API server must not have REJECTED the file
      ansible.builtin.assert:
        that: (reloads_fail | int) == (reloads_fail_before | int)
        fail_msg: "the API server REJECTED the new file on reload (it keeps the old config). Check its log."
        quiet: true
      when: not (changed_dropin | bool) and not ansible_check_mode

    - name: Reloaded
      ansible.builtin.debug:
        msg: "5. reloaded (success counter {{ reloads_ok }})"
      when: not (changed_dropin | bool) and not ansible_check_mode

    # ---- 6. post-check ----------------------------------------------------------------------
    - name: POST-CHECK — anonymous must still be denied
      ansible.builtin.uri:
        url: "{{ k3s_api_url }}/api"
        validate_certs: false
        status_code: [401]
      delegate_to: localhost
      become: false
      when: not ansible_check_mode

    - name: Done
      ansible.builtin.debug:
        msg: "6. anonymous still denied (401). Done."
      when: not ansible_check_mode
```

- [ ] **Step 4: Run the whole suite**

Run: `python -m pytest tests/ansible/ -v`
Expected: all pass. If `test_rollback_reports_when_the_api_comes_back` fails on the call count, print `cat calls` from the tmp dir: `retries: 1` gives two attempts per wait in ansible-core 2.21; adjust `READYZ_OK_AFTER` to the first call of the rollback wait, and say so in the test's docstring.

- [ ] **Step 5: Retire the script and its tests**

```bash
git rm scripts/install-authn-config.sh tests/node-config/test_install_authn_config.py
rmdir tests/node-config 2>/dev/null || true
grep -rn "install-authn-config" --exclude-dir=.git . | grep -v "^./docs/superpowers/\|^./SPEC.md\|^./ansible/playbooks/k3s-authn.yml"
```

The grep should list only `.github/workflows/validate.yaml` (fixed in Task 9), `docs/troubleshooting/kubectl-oidc.md` and `docs/plans/k8s-136-features-implementation-plan.md` (Task 9), and the two authn file headers (already say "port of").

- [ ] **Step 6: Commit**

```bash
git add -A ansible tests scripts
git commit -m "feat(ansible): k3s-authn play 2 — backup, temp+rename install, restart with rollback, hot-reload wait; retire install-authn-config.sh"
```

---

### Task 7: `patch.yml` — VM patching with drained serial reboots

**Files:**
- Create: `ansible/playbooks/patch.yml`, `ansible/playbooks/tasks/patch-node.yml`
- Create: `tests/ansible/test_patch.py`

**Interfaces:**
- Consumes: `k3s_unit`, `drain_timeout`, `reboot_timeout` (Task 1); the kubeconfig at `~/.kube/config` pointing at `https://10.0.1.50:6443` (already on the workstation).
- Produces: nothing other tasks import.

- [ ] **Step 1: Write the failing structural test**

`tests/ansible/test_patch.py`:

```python
"""playbooks/patch.yml shape. The drain/reboot path needs a real cluster; it is verified in check
mode during rollout. What can be pinned without one is pinned here."""
import yaml

from conftest import ANSIBLE

PATCH = ANSIBLE / "playbooks" / "patch.yml"


def test_patch_has_no_localhost_play():
    """Review Focus 1: --limit also drops localhost, so a `hosts: localhost` pre-flight would silently
    not run. The pre-flight is a delegated task inside each node play."""
    plays = yaml.safe_load(PATCH.read_text())
    assert [p["hosts"] for p in plays] == ["k3s_workers", "k3s_control"], "workers first, control last"
    assert all(p.get("serial") == 1 for p in plays), "one node at a time"


def test_every_node_play_starts_with_the_preflight():
    tasks = yaml.safe_load((ANSIBLE / "playbooks" / "tasks" / "patch-node.yml").read_text())
    assert tasks[0]["name"].startswith("Pre-flight")
    assert tasks[0]["delegate_to"] == "localhost"
```

- [ ] **Step 2: Run it to see it fail**

Run: `python -m pytest tests/ansible/test_patch.py -v`
Expected: FAIL, file not found.

- [ ] **Step 3: Write the playbook**

`ansible/playbooks/patch.yml`:

```yaml
---
# patch.yml — OS patching for the k3s VMs: apt full-upgrade, then a drained serial reboot of any
# node that needs one. Workers first, the control plane last, one node at a time. Run from the
# workstation: the pre-flight, drain, uncordon and readiness checks use your kubeconfig.
#   ansible-playbook playbooks/patch.yml                        # all nodes
#   ansible-playbook playbooks/patch.yml --limit k3s-worker-02  # one node
#   ansible-playbook playbooks/patch.yml --check                # preview (drain/reboot simulated)
# The control node is a single SQLite server: its reboot takes the API down for about a minute;
# every API call after that retries. Pods pinned to it by nodeSelector sit Pending until uncordon.
# A run that stops mid-way leaves that node cordoned; the next run's pre-flight refuses until you
# `kubectl uncordon` it by hand.
- name: Patch the workers
  hosts: k3s_workers
  serial: 1
  become: true
  tasks:
    - name: Patch this node
      ansible.builtin.import_tasks: tasks/patch-node.yml

- name: Patch the control plane
  hosts: k3s_control
  serial: 1
  become: true
  tasks:
    - name: Patch this node
      ansible.builtin.import_tasks: tasks/patch-node.yml
```

`ansible/playbooks/tasks/patch-node.yml`:

```yaml
---
# One node: pre-flight, upgrade, and (only if the OS asks for it) drain → reboot → uncordon.
- name: Pre-flight — every node Ready and none cordoned
  kubernetes.core.k8s_info:
    kind: Node
  register: cluster_nodes
  delegate_to: localhost
  become: false

- name: Refuse to patch an unhealthy or partly cordoned cluster
  ansible.builtin.assert:
    that:
      - cluster_nodes.resources | length > 0
      - cluster_nodes.resources | selectattr('spec.unschedulable', 'defined') | selectattr('spec.unschedulable') | list | length == 0
      - cluster_nodes.resources | map(attribute='status.conditions') | map('selectattr', 'type', 'equalto', 'Ready') | map('map', attribute='status') | map('first') | list | unique == ['True']
    fail_msg: "cluster is not healthy (a node is NotReady or cordoned); fix that by hand, then re-run"
    quiet: true

- name: Upgrade packages
  ansible.builtin.apt:
    update_cache: true
    upgrade: full
    autoremove: true

- name: Does the OS want a reboot?
  ansible.builtin.stat:
    path: /var/run/reboot-required
  register: reboot_required

- name: No reboot required
  ansible.builtin.debug:
    msg: "{{ inventory_hostname }}: packages up to date, no reboot required"
  when: not reboot_required.stat.exists

- name: Drain, reboot, uncordon
  when: reboot_required.stat.exists
  block:
    - name: Cordon and drain {{ inventory_hostname }}
      kubernetes.core.k8s_drain:
        name: "{{ inventory_hostname }}"
        state: drain
        delete_options:
          ignore_daemonsets: true
          delete_emptydir_data: true
          wait_timeout: "{{ drain_timeout }}"
      delegate_to: localhost
      become: false

    - name: Reboot
      ansible.builtin.reboot:
        reboot_timeout: "{{ reboot_timeout }}"
        post_reboot_delay: 10

    - name: Wait for {{ k3s_unit }}
      ansible.builtin.command: systemctl is-active {{ k3s_unit }}  # noqa: command-instead-of-module
      register: unit
      until: unit.stdout == 'active'
      retries: 30
      delay: 5
      changed_when: false

    # Retries while the API is down after the control-plane reboot.
    - name: Uncordon {{ inventory_hostname }}
      kubernetes.core.k8s_drain:
        name: "{{ inventory_hostname }}"
        state: uncordon
      delegate_to: localhost
      become: false
      register: uncordon
      until: uncordon is succeeded
      retries: 40
      delay: 5

    - name: Wait for Ready on {{ inventory_hostname }}
      kubernetes.core.k8s_info:
        kind: Node
        name: "{{ inventory_hostname }}"
      register: this_node
      delegate_to: localhost
      become: false
      until: >-
        this_node.resources | default([]) | length == 1 and
        (this_node.resources[0].status.conditions | selectattr('type', 'equalto', 'Ready') | map(attribute='status') | first) == 'True'
      retries: 60
      delay: 5
```

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/ansible/test_patch.py tests/ansible/test_lint.py -v`
Expected: PASS (lint includes the new playbook; `tasks/patch-node.yml` is not a playbook and is not syntax-checked on its own, `patch.yml` imports it).

- [ ] **Step 5: Check mode against the real cluster, one worker**

Run: `cd ansible && ansible-playbook playbooks/patch.yml --limit k3s-worker-02 --check`
Expected: pre-flight `ok`; apt reports the packages it would upgrade; `reboot-required` exists so the drain task reports what it would do in check mode and the reboot is skipped; the run ends `failed=0`. Nothing on the node or in the cluster changes (`kubectl get nodes` shows no `SchedulingDisabled`).

- [ ] **Step 6: Commit**

```bash
git add ansible tests/ansible/test_patch.py
git commit -m "feat(ansible): patch.yml — apt full-upgrade with drained serial reboots, workers first"
```

---

### Task 8: `patch-hypervisor.yml` — confirmed host reboot with ordered VM shutdown

**Files:**
- Create: `ansible/playbooks/patch-hypervisor.yml`
- Create: `tests/ansible/test_patch_hypervisor.py`

**Interfaces:**
- Consumes: `vm_shutdown_order` (Task 1), `reboot_timeout` (Task 1), `recovery/pre-shutdown-backup.sh` (exists).
- Produces: nothing other tasks import.

- [ ] **Step 1: Write the failing gate tests**

`tests/ansible/test_patch_hypervisor.py`:

```python
"""playbooks/patch-hypervisor.yml's gate. The shutdown/reboot path needs libvirt; verified by a
check-mode run during rollout."""
from conftest import run_playbook


def empty_inventory(tmp_path):
    inv = tmp_path / "inventory-empty.yml"
    inv.write_text("all:\n  children:\n    hypervisors:\n      hosts: {}\n    k3s_nodes:\n      hosts: {}\n")
    return inv


def test_refuses_without_confirm(tmp_path):
    r = run_playbook("patch-hypervisor.yml", empty_inventory(tmp_path), "--tags", "gate")
    assert r.returncode != 0 and "REFUSED" in r.stdout and "confirm=yes" in r.stdout, r.stdout + r.stderr


def test_confirm_accepts_true_and_yes(tmp_path):
    """Review Focus 3: -e confirm=true arrives as a boolean, -e confirm=yes as a string."""
    for value in ("yes", "true"):
        r = run_playbook("patch-hypervisor.yml", empty_inventory(tmp_path), "--tags", "gate", "-e", f"confirm={value}")
        assert r.returncode == 0, value + "\n" + r.stdout + r.stderr


def test_a_wrong_value_is_refused(tmp_path):
    r = run_playbook("patch-hypervisor.yml", empty_inventory(tmp_path), "--tags", "gate", "-e", "confirm=maybe")
    assert r.returncode != 0 and "REFUSED" in r.stdout
```

- [ ] **Step 2: Run them to see them fail**

Run: `python -m pytest tests/ansible/test_patch_hypervisor.py -v`
Expected: FAIL, playbook not found.

- [ ] **Step 3: Write the playbook**

`ansible/playbooks/patch-hypervisor.yml`:

```yaml
---
# patch-hypervisor.yml — patch the hypervisor and, only if the OS asks for it, reboot it: every
# VM is ACPI-shut-down first (workers, then control), the host reboots, libvirt autostart brings
# the VMs back, and the last play waits for the cluster. The whole lab is down meanwhile.
#   ansible-playbook playbooks/patch-hypervisor.yml -e confirm=yes
#   ansible-playbook playbooks/patch-hypervisor.yml -e confirm=yes -e velero_backup=yes   # backup first
#   ansible-playbook playbooks/patch-hypervisor.yml -e confirm=yes --check               # preview
# A VM that does not shut off within 300 s FAILS the play; nothing is ever forced.
- name: Gate
  hosts: localhost
  connection: local
  gather_facts: false
  any_errors_fatal: true
  tags: [gate]
  tasks:
    - name: REFUSED without -e confirm=yes
      ansible.builtin.assert:
        that: (confirm | default('') | string | lower) in ['yes', 'true']
        fail_msg: "REFUSED: this shuts down every VM and reboots the hypervisor. Re-run with -e confirm=yes."
        quiet: true

    - name: Velero pre-shutdown backup (opt-in with -e velero_backup=yes)
      ansible.builtin.command: "{{ playbook_dir }}/../../recovery/pre-shutdown-backup.sh"
      when: (velero_backup | default('no') | string | lower) in ['yes', 'true']
      changed_when: true

- name: Patch and reboot the hypervisor
  hosts: hypervisors
  become: true
  tasks:
    - name: Upgrade packages
      ansible.builtin.apt:
        update_cache: true
        upgrade: full
        autoremove: true

    - name: Does the OS want a reboot?
      ansible.builtin.stat:
        path: /var/run/reboot-required
      register: reboot_required

    - name: No reboot required
      ansible.builtin.debug:
        msg: "{{ inventory_hostname }}: packages up to date, no reboot required"
      when: not reboot_required.stat.exists

    - name: Shut the VMs down, reboot, wait for them
      when: reboot_required.stat.exists
      block:
        - name: Running VMs
          community.libvirt.virt:
            command: list_vms
            state: running
            uri: qemu:///system
          register: running

        - name: Every running VM must be in vm_shutdown_order (anything else would be killed by the reboot)
          ansible.builtin.assert:
            that: running.list_vms | difference(vm_shutdown_order) | length == 0
            fail_msg: "running VMs not in vm_shutdown_order: {{ running.list_vms | difference(vm_shutdown_order) }}. Add them to host_vars or stop them by hand."
            quiet: true

        - name: ACPI shutdown, workers first
          community.libvirt.virt:
            name: "{{ item }}"
            state: shutdown
            uri: qemu:///system
          loop: "{{ vm_shutdown_order }}"

        - name: Wait until every VM is shut off (never forced)
          community.libvirt.virt:
            command: list_vms
            state: running
            uri: qemu:///system
          register: still_running
          until: still_running.list_vms | intersect(vm_shutdown_order) | length == 0
          retries: 60
          delay: 5

        - name: Reboot the hypervisor
          ansible.builtin.reboot:
            reboot_timeout: "{{ reboot_timeout }}"
            post_reboot_delay: 15

        - name: Wait for libvirt autostart to bring every VM back
          community.libvirt.virt:
            command: list_vms
            state: running
            uri: qemu:///system
          register: back
          until: vm_shutdown_order | difference(back.list_vms) | length == 0
          retries: 60
          delay: 5
      rescue:
        - name: Stopped
          ansible.builtin.fail:
            msg: >-
              stopped before or during the reboot: a VM did not shut off within 300 s (it was NOT
              forced), or the host or its VMs did not come back in time. Check `virsh list --all` on
              {{ inventory_hostname }} and recovery/POST-RESTART-WALKTHROUGH.md.

- name: Wait for the cluster
  hosts: localhost
  connection: local
  gather_facts: false
  tasks:
    - name: Every k3s node Ready
      kubernetes.core.k8s_info:
        kind: Node
      register: nodes
      until: >-
        nodes.resources | default([]) | length == groups['k3s_nodes'] | length and
        (nodes.resources | map(attribute='status.conditions') | map('selectattr', 'type', 'equalto', 'Ready') | map('map', attribute='status') | map('first') | list | unique == ['True'])
      retries: 120
      delay: 5
```

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/ansible/test_patch_hypervisor.py tests/ansible/test_lint.py -v`
Expected: PASS.

- [ ] **Step 5: Check mode against the real host**

Run: `cd ansible && ansible-playbook playbooks/patch-hypervisor.yml -e confirm=yes --check`
Expected: the gate passes; apt lists the packages it would upgrade; the VM tasks report what they would do; nothing changes (`virsh list --all` on the host still shows all three running). The last play's node wait succeeds immediately.

- [ ] **Step 6: Commit**

```bash
git add ansible tests/ansible/test_patch_hypervisor.py
git commit -m "feat(ansible): patch-hypervisor.yml — confirmed host reboot with ordered, never-forced VM shutdown"
```

---

### Task 9: CI job, docs, README runbook, recovery IPs

**Files:**
- Modify: `.github/workflows/validate.yaml` (`changed-files` job outputs + script; replace `node-config-validate` at lines 195-206)
- Modify: `ansible/README.md` (replace with the full version), `CLAUDE.md`, `index.md`, `scripts/gen-okf.py:176`, `SPEC.md:176`
- Modify: `docs/troubleshooting/kubectl-oidc.md:13-16`, `base-apps/dex/docs.md:56`, `base-apps/dex/docs/index.md:56`, `base-apps/cluster-rbac/oidc-admins.yaml:4`, `docs/plans/k8s-136-features-implementation-plan.md:144,393`
- Modify: `recovery/post-restart-restore.sh:36`, `recovery/CLUSTER-RECOVERY.md:345-346`, `recovery/POST-RESTART-WALKTHROUGH.md:109,119,406-407`
- Create: `ansible/index.md`

- [ ] **Step 1: Gate the CI job on ansible changes**

In `.github/workflows/validate.yaml`, `changed-files` job: add an output and compute it. Under `outputs:` add:

```yaml
      ansible_any_changed: ${{ steps.changed.outputs.ansible_any_changed }}
```

At the end of the `Get changed YAML files` run script (after the `yaml_any_changed` if/else), append:

```bash
          # ansible/ and its tests gate the ansible-validate job (Docker-heavy; skip when untouched).
          if [ "${{ github.event_name }}" = "push" ]; then
            ANSIBLE_CHANGED=$(git diff --name-only HEAD~1 HEAD -- 'ansible/' 'tests/ansible/' | head -1)
          else
            ANSIBLE_CHANGED=$(git diff --name-only origin/main...HEAD -- 'ansible/' 'tests/ansible/' | head -1)
          fi
          if [ -n "$ANSIBLE_CHANGED" ]; then
            echo "ansible_any_changed=true" >> "$GITHUB_OUTPUT"
          else
            echo "ansible_any_changed=false" >> "$GITHUB_OUTPUT"
          fi
```

Replace the whole `node-config-validate` job with:

```yaml
  ansible-validate:
    needs: changed-files
    if: needs.changed-files.outputs.ansible_any_changed == 'true'
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Install Ansible, lint, and test deps
        run: |
          pip install ansible-core==2.21.4 ansible-lint==26.9.0 pytest==8.3.3 pyyaml==6.0.2 kubernetes==36.0.3
          ansible-galaxy collection install -r ansible/requirements.yml
      - name: Lint, syntax-check, and run the playbook tests (Ubuntu node + real k3s in Docker)
        run: python -m pytest tests/ansible/ -v
```

- [ ] **Step 2: Write the full README**

Replace `ansible/README.md` with:

```markdown
# ansible/ — the host layer

Manages the Ubuntu hypervisor (`asela-k8s`, 10.0.1.101) and the three k3s VMs at the OS level.
Terraform (`terraform/`) owns AWS and the Argo CD bootstrap; Argo CD (`base-apps/`) owns everything
in the cluster; system-upgrade-controller owns the k3s version. This tree owns what is left:
packages, sudo, sshd, unattended-upgrades, the workers' static addresses, reboots, and the control
plane's kube-apiserver authentication config.

Design: `docs/superpowers/specs/2026-09-29-ansible-host-management-design.md`.

## Install (workstation)

    uv tool install "ansible-core==2.21.4" --with "kubernetes==36.0.3" --with requests
    uv tool install "ansible-lint==26.9.0"
    cd ansible && ansible-galaxy collection install -r requirements.yml

Run every command from this directory (ansible.cfg lives here). `k3s-authn.yml` needs Docker
(`colima start`); `patch*.yml` use your kubeconfig for the cluster.

## Layout

| path | what |
|---|---|
| `inventory/hosts.yml` | `hypervisors` (asela-k8s); `k3s_nodes` = `k3s_control` (k3s-control-01) + `k3s_workers` (01, 02). Node names match Kubernetes node names. |
| `inventory/host_vars/` | per-VM static address + MAC (workers), `vm_shutdown_order` (hypervisor) |
| `roles/common` | sudoers drop-in, sshd hardening drop-in, unattended-upgrades (security only, no auto-reboot), qemu-guest-agent on VMs |
| `roles/hypervisor` | libvirt packages, groups, VM autostart |
| `roles/k3s_node` | static netplan for hosts with `k3s_node_static_ip`; `files/authn/` is the control plane's kube-apiserver authn config (installed only by `k3s-authn.yml`) |

## Runbook

| do | command |
|---|---|
| First run on a host that still asks for a sudo password | `ansible-playbook playbooks/bootstrap.yml --ask-become-pass` |
| Preview / apply the baseline | `ansible-playbook playbooks/site.yml --check --diff` then without `--check` |
| Patch the VMs (apt full-upgrade, drained serial reboot, workers first) | `ansible-playbook playbooks/patch.yml` (`--limit k3s-worker-02` for one) |
| Patch and reboot the hypervisor (all VMs shut down in order, never forced) | `ansible-playbook playbooks/patch-hypervisor.yml -e confirm=yes` (`-e velero_backup=yes` to back up first) |
| Change the API server's authn config | edit `roles/k3s_node/files/authn/`, then `ansible-playbook playbooks/k3s-authn.yml --check`, then without `--check` |

If `patch.yml` stops mid-way, that node is left cordoned and the next run's pre-flight refuses:
`kubectl uncordon <node>` once you have looked, then re-run.

## Tests

`python -m pytest tests/ansible/ -v` — lint, syntax, and the playbooks against a throwaway Ubuntu
container (`tests/ansible/Dockerfile.node`); `k3s-authn.yml`'s validation runs a real k3s in
Docker. The same command runs in CI (`ansible-validate` in `.github/workflows/validate.yaml`).

## Not here (yet)

Recovery after a hypervisor power loss (`recovery/`), in-cluster scheduling of patch runs,
Vault-backed secrets, hypervisor netplan, VM creation.
```

- [ ] **Step 3: Directory index and repo navigation**

`ansible/index.md`:

```markdown
---
type: "Directory Index"
title: "Ansible"
description: "Directory listing for the Ansible tree: the host layer (hypervisor and k3s VMs) below the cluster."
tags: [ansible, infrastructure, hosts]
---

# ansible Index

| path | purpose |
|---|---|
| `inventory/` | Hosts, groups, per-host addresses and VM names |
| `playbooks/site.yml` | Baseline for every host (roles common, hypervisor, k3s_node) |
| `playbooks/patch.yml` | VM patching with drained serial reboots |
| `playbooks/patch-hypervisor.yml` | Confirmed hypervisor reboot with ordered VM shutdown |
| `playbooks/k3s-authn.yml` | Validate and install the control plane's kube-apiserver authn config |
| `roles/` | `common`, `hypervisor`, `k3s_node` (holds `files/authn/`) |
```

`scripts/gen-okf.py` line 176: change `("index.md", "terraform/index.md", "docs/index.md")` to `("index.md", "terraform/index.md", "ansible/index.md", "docs/index.md")`.

`index.md`: after the `- **terraform/** — …` bullet (line 27) add:

```
- **ansible/** — host layer: the libvirt hypervisor (`asela-k8s`) and the k3s VMs; baseline, patching, and the control plane's kube-apiserver authn config. See `ansible/README.md`.
```

After the `| Infrastructure | …terraform… |` row (line 55) add:

```
| Hosts (hypervisor, VMs, node-level k3s config) | `ansible/` |
```

On line 62 add `[`ansible/index.md`](ansible/index.md), ` before `[`docs/index.md`]`.

`CLAUDE.md`: in "Directory Structure", after the `/terraform/` block add:

```
- `/ansible/` - Host layer: the Ubuntu hypervisor and the three k3s VMs (see `ansible/README.md`)
  - `/playbooks/` - `site.yml` (baseline), `patch.yml` (VMs), `patch-hypervisor.yml`, `k3s-authn.yml`
  - `/roles/` - `common`, `hypervisor`, `k3s_node` (holds the control plane's authn files)
```

After the "Run Terraform Commands" section add:

````
### Run Ansible (host layer)
```bash
cd ansible
ansible-playbook playbooks/site.yml --check --diff   # preview the baseline
ansible-playbook playbooks/patch.yml                 # patch + drained serial reboot of the VMs
ansible-playbook playbooks/k3s-authn.yml --check     # validate the API server authn config
```
````

- [ ] **Step 4: Repoint the authn references**

- `docs/troubleshooting/kubectl-oidc.md` lines 13-14: replace `node-config/k3s-control-01/` with `ansible/roles/k3s_node/files/authn/` in both paths. Line 16: replace "Install only with `scripts/install-authn-config.sh` (`--dry-run` first)." with "Install only with `ansible-playbook playbooks/k3s-authn.yml` from `ansible/` (`--check` first)."
- `base-apps/dex/docs.md:56` and `base-apps/dex/docs/index.md:56`: `node-config/k3s-control-01/authn-config.yaml` → `ansible/roles/k3s_node/files/authn/authn-config.yaml`.
- `base-apps/cluster-rbac/oidc-admins.yaml:4`: same path substitution in the comment.
- `docs/plans/k8s-136-features-implementation-plan.md` lines 144 and 393: same path substitution, and append to line 144: " (moved to `ansible/roles/k3s_node/files/authn/` and ported to `ansible/playbooks/k3s-authn.yml` on 2026-09-29)".
- `SPEC.md:176` (V81): load the `ck:caveman` skill first (the repo's SPEC.md convention), then change only the two path tokens: `node-config/<node>/` → `ansible/roles/k3s_node/files/authn/` and `scripts/install-authn-config.sh` → `ansible/playbooks/k3s-authn.yml`. Leave B21 (history) alone.

- [ ] **Step 5: Fix the stale worker IPs in recovery/**

The workers are `10.0.1.5` (k3s-worker-01) and `10.0.1.108` (k3s-worker-02); the files still say `.51`/`.52`:

```bash
sed -i '' -e 's/10\.0\.1\.51/10.0.1.5/g' -e 's/10\.0\.1\.52/10.0.1.108/g' \
  recovery/post-restart-restore.sh recovery/CLUSTER-RECOVERY.md recovery/POST-RESTART-WALKTHROUGH.md
grep -rn '10\.0\.1\.5[12]\b' recovery/   # expect nothing
```

- [ ] **Step 6: Verify**

```bash
python -m pytest tests/ansible/ tests/okf/ -v         # ansible suite + the OKF generator still runs
python scripts/gen-okf.py --help >/dev/null            # imports fine
grep -rn 'node-config\|install-authn-config' --exclude-dir=.git . | grep -v '^./docs/superpowers/\|^./SPEC.md:297\|^./ansible/playbooks/k3s-authn.yml\|^./ansible/roles/k3s_node/files'
```

Expected: tests pass; the grep prints nothing.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "docs(ansible): CI job, README runbook, repo navigation, authn references, recovery IPs"
```

---

## Rollout (after all tasks; from the workstation, in this order)

Each step is a gate. Do not continue past a surprise.

1. `ansible-playbook playbooks/bootstrap.yml --ask-become-pass` → then `ssh asela@10.0.1.101 sudo -n true` succeeds.
2. `ansible-playbook playbooks/site.yml --check --diff`. Expected changes: the two sudoers drop-ins that do not exist yet (VMs already have cloud-init's; the new file is additive), the sshd hardening drop-in on all four, the unattended-upgrades policy file on all four, qemu-guest-agent on the three VMs, the workers' netplan. No router change is needed: the role pins the address on the host itself. Optional hardening: reserve 10.0.1.5 and 10.0.1.108 in the DHCP server so it never hands them to another device. If that ever happens the worker flaps NotReady while the other device is online; move the worker to a free address then. Then `ansible-playbook playbooks/site.yml --limit k3s_workers`, watch the workers stay reachable, then `--limit k3s_control`, then `--limit hypervisors`.
3. `colima start`, then `ansible-playbook playbooks/k3s-authn.yml --check`. Expected: play 1 validates; play 2 re-installs both files (only their comments changed in the 2026-09-29 move, so the sha256 differs) and restarts k3s once (about 20 s of API blip); `--check` shows `DRY: would restart k3s` first.
4. `ansible-playbook playbooks/patch.yml --limit k3s-worker-02`. Watch `kubectl get nodes -w` in another terminal: cordon, NotReady during the reboot, Ready, uncordon. Then `ansible-playbook playbooks/patch.yml` for the rest; the control node's step takes the API down for about a minute.
5. When a full lab restart is acceptable: `ansible-playbook playbooks/patch-hypervisor.yml -e confirm=yes -e velero_backup=yes`.

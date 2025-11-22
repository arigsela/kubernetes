# K3s Multi-Node Cluster Deployment Guide (2025)

## Overview

This guide will help you deploy a production-ready K3s Kubernetes cluster with:
- **1 Control Plane** node (k3s-control-01)
- **2 Worker** nodes (k3s-worker-01, k3s-worker-02)
- Ubuntu 24.04 LTS (Noble) on libvirt/KVM
- Bridged networking (br0) with DHCP
- K3s v1.31.6+/v1.32.2+ (latest Feb 2025)

**Estimated Time:** 45 minutes
**Difficulty:** Medium

---

## Prerequisites

Your environment needs:
- Ubuntu server at 10.0.1.101
- libvirt/KVM/virsh already installed
- Bridge networking (br0) configured
- Access via SSH with key: `~/.ssh/ari_sela_key`
- Internet connectivity for downloads

---

## Phase 1: Cleanup Talos Infrastructure (5 minutes) ✅ COMPLETED

First, let's completely remove the Talos setup:

```bash
ssh -i ~/.ssh/ari_sela_key asela@10.0.1.101 << 'EOF'
echo "=== Stopping Talos VMs ==="
sudo virsh destroy talos-control-01 2>/dev/null || true
sudo virsh destroy talos-worker-01 2>/dev/null || true
sudo virsh destroy talos-worker-02 2>/dev/null || true

echo "=== Undefining Talos VMs ==="
sudo virsh undefine talos-control-01 --nvram 2>/dev/null || true
sudo virsh undefine talos-worker-01 --nvram 2>/dev/null || true
sudo virsh undefine talos-worker-02 --nvram 2>/dev/null || true

echo "=== Removing Talos disk images ==="
sudo rm -f /var/lib/libvirt/images/talos/*.qcow2

echo "=== Removing Talos configuration files ==="
sudo rm -f /var/lib/libvirt/images/talos/*.yaml
sudo rm -f /var/lib/libvirt/images/talos/talosconfig
sudo rm -f /var/lib/libvirt/images/talos/*.iso
sudo rm -rf /tmp/talos-reconfig/

echo "=== Cleanup complete ==="
sudo virsh list --all | grep talos || echo "No Talos VMs remaining"
EOF
```

---

## Phase 2: Prepare Environment (10 minutes) ✅ COMPLETED

### Step 1: Create working directory and download Ubuntu cloud image

```bash
ssh -i ~/.ssh/ari_sela_key asela@10.0.1.101 << 'EOF'
# Create K3s working directory
sudo mkdir -p /var/lib/libvirt/images/k3s
cd /var/lib/libvirt/images/k3s

# Download Ubuntu 24.04 LTS (Noble) cloud image
echo "Downloading Ubuntu 24.04 cloud image..."
sudo wget -O ubuntu-24.04-server-cloudimg-amd64.img \
  https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img

# Verify download
ls -lh ubuntu-24.04-server-cloudimg-amd64.img
EOF
```

### Step 2: Get your SSH public key

```bash
# Check if you have an SSH key
cat ~/.ssh/ari_sela_key.pub

# If the above fails, generate one:
ssh-keygen -t ed25519 -f ~/.ssh/ari_sela_key -C "k3s-cluster" -N ""
```

Copy the output - you'll need this for cloud-init.

### Step 3: Configure QEMU bridge permissions

```bash
ssh -i ~/.ssh/ari_sela_key asela@10.0.1.101 << 'EOF'
# Allow QEMU to use br0 bridge
echo "allow br0" | sudo tee /etc/qemu/bridge.conf
sudo chmod 0644 /etc/qemu/bridge.conf
EOF
```

---

## Phase 3: Create VMs with Cloud-Init (15 minutes) ✅ COMPLETED

I'll create a complete script that creates all 3 VMs. **Replace `YOUR_SSH_PUBLIC_KEY` with your actual public key from Step 2 above.**

```bash
ssh -i ~/.ssh/ari_sela_key asela@10.0.1.101 << 'SCRIPT'
cd /var/lib/libvirt/images/k3s

# Your SSH public key (REPLACE THIS!)
SSH_PUB_KEY="ssh-ed25519 AAAA... your-email@example.com"

# Create cloud-init user-data for control plane
cat > user-data-control.yaml << EOF
#cloud-config
hostname: k3s-control-01
fqdn: k3s-control-01.local
manage_etc_hosts: true

users:
  - name: asela
    sudo: ALL=(ALL) NOPASSWD:ALL
    groups: sudo
    shell: /bin/bash
    ssh_authorized_keys:
      - ${SSH_PUB_KEY}

package_update: true
package_upgrade: true

packages:
  - curl
  - wget
  - vim
  - ufw

runcmd:
  - systemctl enable ssh
  - systemctl start ssh
EOF

# Create cloud-init user-data for workers (same config, different hostname)
for i in 01 02; do
cat > user-data-worker-${i}.yaml << EOF
#cloud-config
hostname: k3s-worker-${i}
fqdn: k3s-worker-${i}.local
manage_etc_hosts: true

users:
  - name: asela
    sudo: ALL=(ALL) NOPASSWD:ALL
    groups: sudo
    shell: /bin/bash
    ssh_authorized_keys:
      - ${SSH_PUB_KEY}

package_update: true
package_upgrade: true

packages:
  - curl
  - wget
  - vim
  - ufw

runcmd:
  - systemctl enable ssh
  - systemctl start ssh
EOF
done

# Create disk images (50GB each, resized from cloud image)
echo "Creating disk images..."
for vm in control-01 worker-01 worker-02; do
  sudo qemu-img create -f qcow2 -F qcow2 \
    -b ubuntu-24.04-server-cloudimg-amd64.img \
    k3s-${vm}.qcow2 50G
done

# Create VMs with virt-install
echo "Creating control plane VM..."
sudo virt-install \
  --name k3s-control-01 \
  --memory 16384 \
  --vcpus 4 \
  --disk path=/var/lib/libvirt/images/k3s/k3s-control-01.qcow2,format=qcow2,bus=virtio \
  --network bridge=br0,model=virtio \
  --os-variant ubuntu24.04 \
  --cloud-init user-data=user-data-control.yaml \
  --import \
  --noautoconsole

echo "Creating worker VMs..."
for i in 01 02; do
  sudo virt-install \
    --name k3s-worker-${i} \
    --memory 16384 \
    --vcpus 4 \
    --disk path=/var/lib/libvirt/images/k3s/k3s-worker-${i}.qcow2,format=qcow2,bus=virtio \
    --network bridge=br0,model=virtio \
    --os-variant ubuntu24.04 \
    --cloud-init user-data=user-data-worker-${i}.yaml \
    --import \
    --noautoconsole
done

echo "=== Waiting for VMs to boot (60 seconds) ==="
sleep 60

echo "=== VM Status ==="
sudo virsh list --all | grep k3s

echo "=== Checking for DHCP leases ==="
echo "Scan your network or check pfSense DHCP leases for IPs"
sudo nmap -sn 10.0.1.0/24 | grep -A 2 "k3s"
SCRIPT
```

### Step 4: Find VM IP addresses

```bash
ssh -i ~/.ssh/ari_sela_key asela@10.0.1.101 "sudo nmap -sn 10.0.1.0/24 | grep -B 2 'k3s-control\\|k3s-worker'"
```

**Note the IP addresses!** You'll need them for K3s installation. For this guide, I'll assume:
- k3s-control-01: `10.0.1.50`
- k3s-worker-01: `10.0.1.51`
- k3s-worker-02: `10.0.1.52`

**Replace these IPs with your actual IPs in the commands below.**

---

## Phase 4: Install K3s (10 minutes) ✅ COMPLETED

### Step 1: Install K3s on Control Plane

```bash
ssh -i ~/.ssh/ari_sela_key asela@10.0.1.50 << 'EOF'
# Install K3s server
curl -sfL https://get.k3s.io | sh -s - server \
  --write-kubeconfig-mode=644

# Wait for K3s to be ready
echo "Waiting for K3s to start..."
sleep 30

# Verify K3s is running
sudo systemctl status k3s

# Check node status
sudo k3s kubectl get nodes

# Get the node token for workers
echo "=== Node Token (save this!) ==="
sudo cat /var/lib/rancher/k3s/server/node-token
EOF
```

**Copy the node token** from the output above!

### Step 2: Configure Firewall on Control Plane

```bash
ssh -i ~/.ssh/ari_sela_key asela@10.0.1.50 << 'EOF'
# Enable and configure UFW
sudo ufw --force enable
sudo ufw allow 22/tcp      # SSH
sudo ufw allow 6443/tcp    # K3s API
sudo ufw allow 10250/tcp   # Kubelet
sudo ufw allow 8472/udp    # Flannel VXLAN
sudo ufw allow from 10.42.0.0/16  # Pod network
sudo ufw allow from 10.43.0.0/16  # Service network
sudo ufw status
EOF
```

### Step 3: Install K3s Agent on Workers

**Replace `YOUR_NODE_TOKEN` with the token from Step 1 above, and use your actual control plane IP.**

```bash
# Worker 01
ssh -i ~/.ssh/ari_sela_key asela@10.0.1.51 << 'EOF'
curl -sfL https://get.k3s.io | K3S_URL=https://10.0.1.50:6443 \
  K3S_TOKEN="YOUR_NODE_TOKEN" sh -
EOF

# Worker 02
ssh -i ~/.ssh/ari_sela_key asela@10.0.1.52 << 'EOF'
curl -sfL https://get.k3s.io | K3S_URL=https://10.0.1.50:6443 \
  K3S_TOKEN="YOUR_NODE_TOKEN" sh -
EOF
```

### Step 4: Configure Firewall on Workers

```bash
for IP in 10.0.1.51 10.0.1.52; do
  ssh -i ~/.ssh/ari_sela_key asela@${IP} << 'EOF'
sudo ufw --force enable
sudo ufw allow 22/tcp
sudo ufw allow 10250/tcp
sudo ufw allow 8472/udp
sudo ufw allow from 10.42.0.0/16
sudo ufw allow from 10.43.0.0/16
sudo ufw status
EOF
done
```

---

## Phase 5: Verification (5 minutes) ✅ COMPLETED

### Check cluster status from control plane:

```bash
ssh -i ~/.ssh/ari_sela_key asela@10.0.1.50 "sudo k3s kubectl get nodes -o wide"
```

Expected output:
```
NAME              STATUS   ROLES                  AGE   VERSION
k3s-control-01    Ready    control-plane,master   5m    v1.31.6+k3s1
k3s-worker-01     Ready    <none>                 2m    v1.31.6+k3s1
k3s-worker-02     Ready    <none>                 2m    v1.31.6+k3s1
```

### Test cluster functionality:

```bash
ssh -i ~/.ssh/ari_sela_key asela@10.0.1.50 << 'EOF'
# Create test pod
sudo k3s kubectl run nginx --image=nginx
sleep 10
sudo k3s kubectl get pods
sudo k3s kubectl delete pod nginx
EOF
```

---

## Phase 6: Access from Local Machine ✅ COMPLETED

### Copy kubeconfig to your local machine:

```bash
# Copy kubeconfig from control plane
scp -i ~/.ssh/ari_sela_key asela@10.0.1.50:/etc/rancher/k3s/k3s.yaml ~/.kube/k3s-config

# Edit the server address
sed -i '' 's/127.0.0.1/10.0.1.50/g' ~/.kube/k3s-config

# Test access
kubectl --kubeconfig ~/.kube/k3s-config get nodes
```

### Set as default kubeconfig (optional):

```bash
export KUBECONFIG=~/.kube/k3s-config
kubectl get nodes
```

---

## Summary

You now have a working K3s cluster with:
- Production-ready configuration
- Proper firewall rules
- 3-node setup (1 control + 2 workers)
- Network access from your local machine

**Next Steps:**
1. Set up DHCP reservations in pfSense for static IPs
2. Deploy ArgoCD using your existing manifests
3. Restore Vault and other applications

---

## Troubleshooting

### VMs not getting IP addresses
```bash
# Check VM status
ssh -i ~/.ssh/ari_sela_key asela@10.0.1.101 "sudo virsh list --all"

# Restart a VM
ssh -i ~/.ssh/ari_sela_key asela@10.0.1.101 "sudo virsh reboot k3s-control-01"

# Check console
ssh -i ~/.ssh/ari_sela_key asela@10.0.1.101 "sudo virsh console k3s-control-01"
```

### Workers not joining cluster
```bash
# Check K3s agent status on worker
ssh -i ~/.ssh/ari_sela_key asela@10.0.1.51 "sudo systemctl status k3s-agent"

# Check logs
ssh -i ~/.ssh/ari_sela_key asela@10.0.1.51 "sudo journalctl -u k3s-agent -f"

# Verify token and URL are correct
ssh -i ~/.ssh/ari_sela_key asela@10.0.1.50 "sudo cat /var/lib/rancher/k3s/server/node-token"
```

### Firewall issues
```bash
# Test connectivity
ping 10.0.1.50
curl -k https://10.0.1.50:6443

# Check UFW rules
ssh -i ~/.ssh/ari_sela_key asela@10.0.1.50 "sudo ufw status numbered"
```

---

## References

- [K3s Official Documentation](https://docs.k3s.io/)
- [K3s Installation Guide](https://docs.k3s.io/installation)
- [K3s Requirements](https://docs.k3s.io/installation/requirements)
- [Ubuntu Cloud Images](https://cloud-images.ubuntu.com/)
- Research conducted: January 2025

---

**Note:** This guide is based on K3s official documentation and 2025 best practices. Much simpler than Talos!

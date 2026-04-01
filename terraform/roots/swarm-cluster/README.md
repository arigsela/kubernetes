# Docker Swarm Cluster - Terraform Root

This root provisions a 3-node Docker Swarm cluster on AWS, spread across 3 availability zones, fronted by an Application Load Balancer with a Route53 DNS record.

## Architecture

```
                    Route53 (swarm.arigsela.com)
                              |
                        [ ALB (HTTP) ]
                       /      |       \
                 AZ-1         AZ-2         AZ-3
              [Manager]    [Worker-1]    [Worker-2]
              Ubuntu 24     AL2023        AL2023
              Swarm Init   Swarm Join    Swarm Join
```

- **Manager** (AZ-1): Ubuntu 24.04 - initializes the Docker Swarm and acts as the cluster leader
- **Worker 1** (AZ-2): Amazon Linux 2023 - joins the swarm using the manager's join token
- **Worker 2** (AZ-3): Amazon Linux 2023 - joins the swarm using the manager's join token
- All 3 instances are registered as targets in the ALB target group

## Modules Used

| Module | Source | Purpose |
|--------|--------|---------|
| `networking` | `../../modules/networking` | VPC, 3 public subnets (one per AZ), internet gateway, route table |
| `alb` | `../../modules/alb` | Application Load Balancer, target group, HTTP listener |
| `ec2-instance` | `../../modules/ec2-instance` | Reusable EC2 instance (used 3 times: 1 manager + 2 workers) |

### Module Dependency Chain

```
networking --> alb (needs vpc_id, subnet_ids)
networking --> ec2-instance (needs subnet_ids)
ec2-instance (manager) --> ec2-instance (workers) (workers need manager's private_ip)
alb --> target_group_attachments (needs target_group_arn)
```

## Root-Level Resources

Resources that remain in the root (not in modules) and why:

| File | Resources | Why not in a module? |
|------|-----------|---------------------|
| `security-groups.tf` | `aws_security_group.swarm`, `aws_security_group.alb` | The swarm SG uses `self = true` rules for inter-node communication - all nodes must share the same SG for this to work |
| `ec2.tf` | `tls_private_key`, `aws_key_pair` | Shared across all instances, only created once |
| `route53.tf` | `aws_route53_record` | Single record, depends on both ALB output and root-level variables |
| `main.tf` | `aws_lb_target_group_attachment` (x3) | Glue between modules - connects ec2-instance outputs to alb outputs |

## Deployment (Two-Phase Apply)

The swarm join token cannot be extracted from Terraform, so deployment happens in two phases.

### Phase 1: Manager + Infrastructure

```bash
cd terraform/roots/swarm-cluster
terraform init
terraform apply
```

This creates the VPC, ALB, Route53 record, security groups, and the manager node. The manager's `user_data` automatically runs `docker swarm init`.

### Phase 2: Get Token and Deploy Workers

```bash
# Save the SSH key
terraform output -raw ssh_private_key > swarm-key.pem
chmod 400 swarm-key.pem

# SSH into the manager
ssh -i swarm-key.pem ubuntu@$(terraform output -raw manager_public_ip)

# Get the worker join token
docker swarm join-token worker -q
```

Update `terraform.tfvars` with the token:

```hcl
swarm_join_token = "SWMTKN-1-xxxxx..."
```

Then apply again to bring up the workers:

```bash
terraform apply
```

The workers' `user_data` will install Docker and automatically join the swarm using the token and the manager's private IP.

### Verify the Cluster

```bash
# On the manager
docker node ls
```

You should see 3 nodes: 1 Leader + 2 Workers.

## Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `aws_region` | AWS region | `us-east-1` |
| `vpc_cidr` | VPC CIDR block | `10.12.0.0/16` |
| `public_subnet_cidrs` | Subnet CIDRs (one per AZ) | `["10.12.1.0/24", "10.12.2.0/24", "10.12.3.0/24"]` |
| `instance_type` | EC2 instance type | `t3.micro` |
| `manager_ami` | Ubuntu 24.04 AMI ID | `ami-0ec10929233384c7f` |
| `worker_ami` | Amazon Linux 2023 AMI ID | `ami-08b09ec6240624138` |
| `swarm_join_token` | Worker join token (sensitive) | `""` |
| `domain_name` | Route53 hosted zone | `arigsela.com` |
| `hostname` | Subdomain for the ALB | `swarm` |
| `my_ip` | Your IP for SSH access (CIDR) | `0.0.0.0/32` |

## Outputs

| Output | Description |
|--------|-------------|
| `manager_public_ip` | Manager's public IP for SSH access |
| `manager_private_ip` | Manager's private IP (used by workers to join swarm) |
| `alb_dns_name` | ALB DNS name |
| `swarm_url` | Full URL (http://swarm.arigsela.com) |
| `ssh_private_key` | SSH private key (sensitive) |
| `ssh_command` | Ready-to-use SSH command |
| `worker_*_ips` | Worker public/private IPs |

## Security Groups

**Swarm SG** (shared by all 3 instances):
- SSH (port 22) - restricted to `my_ip`
- HTTP (port 80) - open (ALB health checks + traffic)
- Swarm management (port 2377/tcp) - `self` (inter-node only)
- Node communication (port 7946/tcp+udp) - `self` (inter-node only)
- VXLAN overlay (port 4789/udp) - `self` (inter-node only)

**ALB SG**:
- HTTP (port 80) - open to internet

## Teardown

```bash
terraform destroy
```

# Apply workflow (Atlantis is apply-BEFORE-merge):
# On a terraform PR, run the gated "Terraform Apply" GitHub Action (or comment
# `atlantis apply -p asela-cluster`) while the PR is still OPEN, then merge.
# Merged PRs are closed and cannot be applied — a merged-but-unapplied change
# becomes drift that the next PR's plan will surface.

terraform {
  required_version = ">= 1.11.0"

  backend "s3" {
    bucket  = "asela-terraform-states"
    key     = "asela-cluster"
    region  = "us-east-2"
    encrypt = true
  }

  required_providers {
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 2.0"
    }
    helm = {
      source  = "hashicorp/helm"
      version = "~> 2.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = "us-east-2"
}

provider "kubernetes" {
  host = var.host

  client_certificate     = base64decode(var.client_certificate)
  client_key             = base64decode(var.client_key)
  cluster_ca_certificate = base64decode(var.cluster_ca_certificate)
}

provider "helm" {
  kubernetes {
    host                   = var.host
    client_certificate     = base64decode(var.client_certificate)
    client_key             = base64decode(var.client_key)
    cluster_ca_certificate = base64decode(var.cluster_ca_certificate)
  }
}
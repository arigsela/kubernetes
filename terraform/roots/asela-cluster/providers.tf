terraform {

  backend "s3" {
    bucket = "asela-terraform-states"
    key    = "asela-cluster"
    region = "us-east-2"
    profile = "default"
    encrypt = true
  }

  required_providers {
    kubernetes = {
      source = "hashicorp/kubernetes"
    }
  }
}

provider aws {
  region = "us-east-2"
  profile = "default"
}

provider "kubernetes" {
  host = var.host

  client_certificate     = base64decode(var.client_certificate)
  client_key             = base64decode(var.client_key)
  cluster_ca_certificate = base64decode(var.cluster_ca_certificate)
}

provider "helm" {
  kubernetes {
    config_path = "~/.kube/config"
  }
}

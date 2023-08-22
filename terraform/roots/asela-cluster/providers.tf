terraform {

  backend "s3" {
    bucket = "asela-tf-states"
    key    = "asela-cluster"
    region = "us-west-2"
    profile = "infra-asela"
    encrypt = true
  }

  required_providers {
    kubernetes = {
      source = "hashicorp/kubernetes"
    }
  }
}

provider aws {
  region = "us-west-2"
  profile = "infra-asela"
}

provider "kubernetes" {
  host = var.host

  client_certificate     = base64decode(var.client_certificate)
  client_key             = base64decode(var.client_key)
  cluster_ca_certificate = base64decode(var.cluster_ca_certificate)
}
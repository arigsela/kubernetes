terraform {
  required_providers {
   aws = "~> 5.78"
   helm = "~> 2.16.1"
   kubernetes = "2.34.0"
   kubectl = {
    source = "gavinbunney/kubectl"
    version = "1.16.0"
   }
  }
}
terraform {
  required_providers {
   aws = "~> 5.0"
   kubernetes = "2.23.0"
   kubectl = {
    source = "gavinbunney/kubectl"
    version = "1.14.0"
   }
  }
}
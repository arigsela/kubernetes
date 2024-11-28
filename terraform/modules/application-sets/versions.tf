terraform {
  required_providers {
   kubernetes = "2.34.0"
   kubectl = {
    source = "gavinbunney/kubectl"
    version = "1.16.0"
   }
  }
}
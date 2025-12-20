# module "applications_project" {
#   source  = "project-octal/argocd-project/kubernetes"
#   version = "2.0.0"
#   namespace = "argo-cd"
#   name             = "applications"
#   description      = "Applications managed by Developers"
  
#   destinations = [
#     {
#       server    ="*"
#       name = "EKS cluster"
#       namespace = "*"
#     }
#   ]

#     source_repos = ["*"]

#   namespace_resource_whitelist = [
#     {
#         kind = "*",
#         group = "*"
#     }
#   ]

# cluster_resource_whitelist = [
#     {
#         kind = "*"
#         group = "*"
#     }
# ]
# }

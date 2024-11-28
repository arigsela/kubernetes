# resource "kubernetes_deployment" "mysite" {

#   timeouts {}

#   metadata {
#     name = "mysite"
#     namespace = "nginx"
#     labels = {
#       app = "mysite"
#     }
#   }

#   spec {
#     replicas = 2

#     selector {
#       match_labels = {
#         app = "mysite"
#       }
#     }

#     template {
#       metadata {
#         labels = {
#           app = "mysite"
#         }
#       }

#       spec {
#         automount_service_account_token = false
#         enable_service_links = false
#         scheduler_name = "default-scheduler"
        
#         container {
#           image = "nginx"
#           name  = "mysite"
#           image_pull_policy = "Always"
#           termination_message_policy = "File"
          
#           port {
#             container_port = "80"
#             protocol = "TCP"
#           }

#           resources {
#             limits = {}
#             requests = {}
#           }
#         }
#       }
#     }
#   }
# }
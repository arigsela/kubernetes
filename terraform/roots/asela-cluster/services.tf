# resource "kubernetes_service" "mysite" {
#   timeouts {}

#   metadata {
#     name = "mysite"
#     namespace = "nginx"
    
#     labels = {
#         name = "mysite"
#     }
#   }
#   spec {
#     selector = {
#       app = kubernetes_deployment.mysite.metadata.0.labels.app
#     }
#     session_affinity = "None"
    
#     port {
#       protocol = "TCP"
#       name = "http"
#       port        = 80
#       target_port = 80
#       node_port = 30090
#     }

#     type = "NodePort"
#   }
# }
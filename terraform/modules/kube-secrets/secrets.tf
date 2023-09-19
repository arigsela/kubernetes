resource "kubernetes_secret" aws_credentials {
    metadata {
        name = var.name
        namespace = var.namespace
    }

    data = var.data
    type = var.type
}
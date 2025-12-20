variable "name" {
    type      = string
    description   = "Name of the secret"
}

variable "namespace" {
    type      = string
    description   = "Namespace where secret wil reside"
}

variable "data" {
    type      =  map(string)
}

variable "type" {
    type      = string
    description = "Type of secret to store"
}

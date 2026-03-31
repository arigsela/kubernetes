data "aws_route53_zone" "main" {
  name = var.domain_name
}

resource "aws_route53_record" "swarm" {
  zone_id = data.aws_route53_zone.main.zone_id
  name    = "${var.hostname}.${var.domain_name}"
  type    = "A"

  alias {
    name                   = module.alb.alb_dns_name
    zone_id                = module.alb.alb_zone_id
    evaluate_target_health = true
  }
}

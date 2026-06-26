output "nat_egress_ip" {
  value       = google_compute_address.nat_egress.address
  description = "Stable outbound egress IP for all private-node traffic to the public internet. Document/allowlist this where needed. Distinct from the inbound ingress IP 34.56.76.244."
}

output "router_name" {
  value       = google_compute_router.nat_router.name
  description = "Cloud Router hosting the NAT config."
}

output "nat_name" {
  value       = google_compute_router_nat.nat.name
  description = "Cloud NAT gateway name."
}

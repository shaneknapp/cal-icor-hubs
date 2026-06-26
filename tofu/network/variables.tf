variable "project" {
  type        = string
  default     = "cal-icor-hubs"
  description = "GCP project hosting the spring-2025 cluster."
}

variable "region" {
  type        = string
  default     = "us-central1"
  description = "Region for the Cloud Router, Cloud NAT, and egress IP."
}

variable "network" {
  type        = string
  default     = "default"
  description = "VPC network the spring-2025 cluster runs on."
}

variable "router_name" {
  type        = string
  default     = "spring-2025-nat-router"
  description = "Name of the Cloud Router that hosts the NAT config."
}

variable "nat_name" {
  type        = string
  default     = "spring-2025-nat"
  description = "Name of the Cloud NAT gateway."
}

variable "nat_egress_ip_name" {
  type        = string
  default     = "spring-2025-nat-egress"
  description = "Name of the reserved static egress IP used by Cloud NAT (outbound only)."
}

# Reusable GKE cluster module. The cluster ships with its default pool removed;
# node pools are separate units. Dev and prod share this config; node VM size is
# set in the node-pool units.

resource "google_compute_subnetwork" "cluster" {
  name          = coalesce(var.subnet_name, "${var.cluster_name}-subnet")
  region        = var.region
  network       = var.network
  ip_cidr_range = var.node_cidr_block

  private_ip_google_access = true

  secondary_ip_range {
    range_name    = "${var.cluster_name}-pods"
    ip_cidr_range = var.pod_cidr_block
  }
}

resource "google_container_cluster" "cluster" {
  name           = var.cluster_name
  location       = var.location
  node_locations = var.node_locations

  network    = var.network
  subnetwork = google_compute_subnetwork.cluster.name

  remove_default_node_pool = true
  initial_node_count       = 1

  networking_mode           = "VPC_NATIVE"
  default_max_pods_per_node = var.max_pods_per_node

  # Pods use the subnet secondary range; services stay GKE-managed.
  ip_allocation_policy {
    cluster_secondary_range_name = "${var.cluster_name}-pods"
  }

  release_channel {
    channel = var.release_channel
  }

  enable_shielded_nodes = true

  addons_config {
    gce_persistent_disk_csi_driver_config {
      enabled = true
    }
  }

  logging_service    = "logging.googleapis.com/kubernetes"
  monitoring_service = "monitoring.googleapis.com/kubernetes"

  resource_labels = var.resource_labels

  deletion_protection = var.deletion_protection
}

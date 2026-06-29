# Root Terragrunt configuration, included by every unit under tofu/.
#
# It centralizes the two things plain OpenTofu cannot share across separate
# state files:
#
#   1. Remote state. One GCS bucket, with each unit's state key derived from its
#      path under tofu/ (e.g. tofu/spring-2025/network -> prefix
#      "spring-2025/network"). Units never hand-write a backend block, and the
#      derived key matches the prefixes the modules used before this conversion,
#      so existing state is reused rather than orphaned.
#
#   2. Common inputs. project and region are identical for every unit, so they
#      are declared once here and merged into each unit's inputs instead of being
#      repeated as variable defaults in every module.
#
# Per-unit specifics (cluster name, node zone, labels, module source) live in the
# unit's own terragrunt.hcl.

# Keep the provider lock file with the module code (modules/<name>/) instead of
# letting Terragrunt copy a duplicate into every unit. Units that source the same
# module then share one pinned set of provider versions rather than drifting into
# separate per-unit lock files. Relies on units including this config with
# merge_strategy = "deep" so this terraform block combines with their `source`.
terraform {
  copy_terraform_lock_file = false
}

remote_state {
  backend = "gcs"

  generate = {
    path      = "backend.tf"
    if_exists = "overwrite_terragrunt"
  }

  config = {
    bucket = "cal-icor-hubs-tofu-state"
    prefix = path_relative_to_include()
  }
}

inputs = {
  project = "cal-icor-hubs"
  region  = "us-central1"
}

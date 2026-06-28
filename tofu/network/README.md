# tofu/network: Cloud Router + NAT for spring-2025

Phase A of the public-to-private node migration. This root module creates the
**outbound** egress path that private nodes need: a Cloud Router, a Cloud NAT
gateway, and a reserved static egress IP.

This is the first OpenTofu-managed resource in the repo (new-resources-first
adoption). The cluster and node pools remain out-of-band for now.

## What this does and does not touch

- Creates: `spring-2025-nat-router`, `spring-2025-nat`, and the reserved egress
  IP `spring-2025-nat-egress`.
- Does **not** touch the inbound ingress IP `34.56.76.244`
  (`wildcard-dns-target-spring-2025`), the ingress-nginx LoadBalancer, the
  `support/values.yaml` `loadBalancerIP`, or the Infoblox wildcard DNS. Those
  are inbound and independent of NAT.
- Additive only: nodes are still public until later phases. Creating NAT now is
  safe and changes nothing about existing traffic.

## IAP SSH firewall rule

`firewall.tf` adds `spring-2025-allow-iap-ssh`: an INGRESS rule permitting
Google's IAP TCP forwarding range (`35.235.240.0/20`) to reach tcp:22 on cluster
nodes (target tag `hub-cluster`).

This is **additive and changes nothing while nodes are public** — direct
`gcloud compute ssh` over a node's external IP keeps working via the existing
broad `allow-ssh` rule. The IAP rule matters once a pool goes private: a private
node has no external IP, so SSH must tunnel through IAP
(`gcloud compute ssh <node> --zone=us-central1-b --tunnel-through-iap`, plus
`roles/iap.tunnelResourceAccessor`). Scoping the rule to the IAP range and the
node tag also lets the broad `allow-ssh` rule be narrowed or removed later in the
access lock-down without losing operator SSH.

## NAT egress IP

`MANUAL_ONLY` pinned to the reserved static IP `spring-2025-nat-egress`, so the
cluster has one stable, documentable outbound source IP. Read it after apply
with `tofu output nat_egress_ip`.

## First-time bootstrap (run once, requires approval)

The GCS backend needs its bucket to exist before `tofu init`:

```sh
gcloud storage buckets create gs://cal-icor-hubs-tofu-state \
  --project=cal-icor-hubs --location=us-central1 \
  --uniform-bucket-level-access \
  --labels=hub=networking
gcloud storage buckets update gs://cal-icor-hubs-tofu-state --versioning
```

The `hub=networking` label keeps the state bucket in the same billing rollup as
the rest of the network plumbing. The bucket cannot be created by this module
(it has to exist before `tofu init`), so the label is set here at bootstrap.

## Normal workflow

```sh
cd tofu/network
tofu init      # wires the gcs backend, downloads the google provider
tofu plan      # expect: 3 to add, 0 to change, 0 to destroy
tofu apply     # creates router + NAT + egress IP (requires approval)
tofu output nat_egress_ip
```

## Rollback

Nothing depends on the NAT yet (nodes are still public), so removal is clean:

```sh
tofu destroy   # removes NAT, router, and the egress IP
```

# cluster-template

A template, not a live unit. Copy it to stand up a new cluster:

```bash
cp -r tofu/cluster-template tofu/<cluster-name>
# edit tofu/<cluster-name>/cluster/terragrunt.hcl: set cluster_name and inputs
cd tofu/<cluster-name>/cluster
terragrunt init
terragrunt plan
terragrunt apply
```

`cluster/` sources `modules/cluster`, which manages the cluster's subnet
(`node_cidr_block` for node IPs, `pod_cidr_block` for the pod secondary range).
The same config serves two cases:

- Dev cluster: fresh, non-colliding ranges; smaller node VMs (set in the
  node-pool units); `deletion_protection = false`.
- Redeploy: reuse the live cluster's ranges so the `jupyterhub-home-nfs`
  allowlist still matches; `deletion_protection = true`.

Add other units alongside `cluster/` as needed: a `network/` unit
(`modules/network`) for the Cloud NAT, then one node-pool unit per pool
(`modules/nodepools`).

One subnet per cluster, not one per pool: all node pools share the cluster's
single pod secondary range. If two clusters ever share the VPC, give each its
own subnet with non-colliding ranges (the dev and redeploy cases above already
do).

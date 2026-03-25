# canvas-k8s

Small helper repo for deploying Canvas LMS on a single EC2 host with `k3s`.

## Prerequisites

- `k3s` installed on the host
- `kubectl` available
- AWS security group allows inbound TCP `30080`
- DNS for `canvas.io.vn` points to the EC2 public IP

## One-time shell setup

```bash
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
```

Or the scripts will auto-use that path if it exists.

## Deploy commands

Fresh install:

```bash
./reset-and-bootstrap.sh
```

First install without deleting namespace:

```bash
./deploy.sh bootstrap
```

Normal update:

```bash
./deploy.sh
```

## Verify deployment

```bash
kubectl get all -n canvas
curl http://127.0.0.1:30080
```

Public URL:

```text
http://canvas.io.vn:30080
```

## Create an admin API token

```bash
./create-admin-token.sh
```

If your admin login changes:

```bash
ADMIN_LOGIN=admin@canvas.local ./create-admin-token.sh
```

Use the output as:

```http
Authorization: Bearer <token>
```

## Notes

- Browser login over plain HTTP may still be limited by modern cookie policy.
- API testing with a bearer token works better than browser login in this setup.

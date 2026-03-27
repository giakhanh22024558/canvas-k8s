# canvas-k8s

Small helper repo for deploying Canvas LMS on a single EC2 host with `k3s`.

## Prerequisites

- `k3s` installed on the host
- `kubectl` available
- AWS security group allows inbound TCP `30080`
- DNS for `canvas.io.vn` points to the EC2 public IP

## Cluster startup

Start the cluster and load the right kubeconfig automatically:

```bash
./start-cluster.sh
```

This starts `k3s`, waits for the API to become ready, and prints node and Canvas namespace status.

## One-time shell setup

```bash
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
```

Most helper scripts in this repo auto-use that path if it exists.

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
http://canvas.io.vn
```

## Typical cluster flow

Start the cluster:

```bash
./start-cluster.sh
```

Fresh deploy:

```bash
./reset-and-bootstrap.sh
```

Normal redeploy:

```bash
./deploy.sh
```

Quick health check:

```bash
kubectl get all -n canvas
curl http://127.0.0.1:30080
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

## Load testing and metrics flow

Testing files are grouped under:

```text
testing/
```

Save your local testing token and defaults once:

```bash
chmod +x ./testing/setup-env.sh
./testing/setup-env.sh
```

This writes your local values to `testing/testing.env`, which is ignored by git and reused by the seed, un-seed, and load-test scripts.
It can also store your `rclone` remote name and Google Drive folder ID for result uploads.

Apply Prometheus and cAdvisor:

```bash
./testing/apply-monitoring.sh
```

Prometheus will be exposed on:

```text
http://canvas.io.vn:30090
```

Run a load test and send k6 metrics to Prometheus:

```bash
./testing/run-load-test.sh
```

The load test summary is saved under:

```text
testing/results/<testid>/
```

## Seed realistic load-test data

Before load testing, seed a larger set of users and course content so the API is working against a more realistic dataset.

Recommended profile:

- `COURSE_COUNT=12`
- `TEACHER_POOL_SIZE=8`
- `STUDENT_POOL_SIZE=250`
- `TEACHERS_PER_COURSE=2`
- `STUDENTS_PER_COURSE=40`
- `ASSIGNMENTS_PER_COURSE=8`
- `PAGES_PER_COURSE=4`
- `DISCUSSIONS_PER_COURSE=3`

The seeder uses the Canvas REST API and creates:

- teacher and student user pools
- multiple published courses
- enrollments that reuse users across courses
- assignments, wiki pages, and discussion topics per course

Ubuntu shell:

```bash
SEED_PREFIX=lt-batch-01 \
./testing/run-seed-data.sh
```

You can scale the dataset up or down with environment variables. For example:

```bash
SEED_PREFIX=lt-batch-02 \
COURSE_COUNT=20 \
STUDENT_POOL_SIZE=600 \
STUDENTS_PER_COURSE=80 \
./testing/run-seed-data.sh
```

Notes:

- Use a fresh `SEED_PREFIX` for each run to avoid login collisions.
- This is best run against a fresh or dedicated load-test environment because repeated runs add more data.
- Python is required on the machine running the script. The wrappers try `python3` first, then `python`.

Remove previously seeded data by prefix:

Ubuntu shell:

```bash
SEED_PREFIX=lt-batch-01 \
./testing/run-unseed-data.sh
```

The un-seed flow deletes matching seeded courses first, then matching seeded users.

Generate charts from Prometheus metrics:

```bash
chmod +x ./testing/charts/setup-python.sh
./testing/charts/setup-python.sh
source ./testing/charts/.venv/bin/activate
python3 testing/charts/plot_prometheus.py --prometheus-url http://127.0.0.1:30090 --minutes 15
```

Charts are written to:

```text
testing/charts/output
```

Upload the latest run log and chart outputs to your Google Drive folder:

```bash
chmod +x ./testing/upload-results.sh
./testing/upload-results.sh
```

Or upload a specific test run:

```bash
TEST_ID=canvas-20260327-120000 ./testing/upload-results.sh
```

This uses `rclone`, so configure your Google Drive remote once with `rclone config` on the EC2 host.

## Notes

- Browser login over plain HTTP may still be limited by modern cookie policy.
- API testing with a bearer token works better than browser login in this setup.
- The main Canvas URL in this repo is `http://canvas.io.vn`.

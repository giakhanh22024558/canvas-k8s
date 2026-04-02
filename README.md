# canvas-k8s

Deploy Canvas LMS on a single Ubuntu EC2 instance with `k3s`, then run load tests with `k6`, collect metrics in Prometheus, generate charts with Python, and publish result bundles to a separate Git repo.

## What this repo does

- deploys Canvas LMS on `k3s`
- exposes Canvas at `http://canvas.io.vn`
- provides helper scripts for cluster start, bootstrap, token creation, seeding, load testing, charting, and publishing results

## Public endpoints

- Canvas: `http://canvas.io.vn`
- Prometheus: `http://canvas.io.vn:30090`
- Grafana: `http://canvas.io.vn:30091`

## EC2 prerequisites

Before using this repo on a fresh Ubuntu EC2 instance, make sure you have:

- an EC2 instance with Ubuntu
- DNS `A` record for `canvas.io.vn` pointing to the EC2 public IP
- AWS security group inbound rules for:
  - TCP `80`
  - TCP `30080`
  - TCP `30090`
  - TCP `30091`
- `k3s` installed
- `git`, `curl`, and `kubectl` available

Optional but recommended:

- `python3`
- `python3-venv`
- `k6`

## Clone repo

```bash
git clone <your-canvas-k8s-repo-url>
cd ~/canvas-k8s
find . -type f -name "*.sh" -exec chmod +x {} +
```

## Install Ubuntu packages

Install the packages commonly needed by the helper scripts:

```bash
sudo apt update
sudo apt install -y git curl python3 python3-pip python3-venv
```

If `k6` is not installed yet, install it before running load tests.

## Install k3s

If `k3s` is not already installed:

```bash
curl -sfL https://get.k3s.io | sh -
```

After install, this repo expects kubeconfig at:

```text
/etc/rancher/k3s/k3s.yaml
```

## Start cluster

Use the helper:

```bash
./start-cluster.sh
```

This script:

- starts `k3s`
- waits for the API to become ready
- sets `/etc/rancher/k3s/k3s.yaml` readable
- prints cluster status

If you want the kubeconfig in the current shell too:

```bash
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
```

## Fresh deployment on a new EC2 instance

If this is a brand-new environment and you want a clean install:

```bash
./reset-and-bootstrap.sh
```

This:

- deletes namespace `canvas` if it exists
- runs `./deploy.sh bootstrap`

If you do not want to delete the namespace first:

```bash
./deploy.sh bootstrap
```

For experiment 1 baseline deployment:

```bash
./deploy.sh baseline
```

For baseline web-only deployment without delayed jobs:

```bash
BASELINE_DISABLE_JOBS=true ./deploy.sh baseline
```

For experiment 2 HPA deployment:

```bash
./deploy.sh hpa
```

For later updates with HPA enabled:

```bash
./deploy.sh
```

## Verify deployment

Check resources:

```bash
kubectl get all -n canvas
kubectl get svc -n canvas
```

Check app reachability from the host:

```bash
curl http://127.0.0.1:30080
curl http://canvas.io.vn
```

## Create admin API token

Create a token for API usage:

```bash
./create-admin-token.sh
```

If your admin login is different:

```bash
ADMIN_LOGIN=admin@canvas.local ./create-admin-token.sh
```

Use the token as:

```http
Authorization: Bearer <token>
```

Quick verification:

```bash
curl -i -H "Authorization: Bearer <token>" http://canvas.io.vn/api/v1/accounts/self/courses
```

Expected result:

- `200 OK` if token is valid

## Testing folder layout

All testing-related files live under:

```text
testing/
```

Important scripts:

- `testing/setup-env.sh`
- `testing/apply-monitoring.sh`
- `testing/collect-k8s-snapshots.sh`
- `testing/capture-cluster-env.sh`
- `testing/reset-test-env.sh`
- `testing/run-seed-data.sh`
- `testing/run-unseed-data.sh`
- `testing/run-load-test.sh`
- `testing/run-experiment-matrix.sh`
- `testing/charts/setup-python.sh`
- `testing/charts/analyze_experiments.py`
- `testing/publish-results.sh`

## Save local testing config once

Run this once per EC2 instance:

```bash
./testing/setup-env.sh
```

It writes local settings to:

```text
testing/testing.env
```

This file is ignored by git and reused by:

- seed script
- un-seed script
- load test script
- publish script

It stores:

- `BASE_URL`
- `API_TOKEN`
- `PROM_URL`
- `PROMETHEUS_URL`
- `RESULTS_REPO_URL`
- `RESULTS_REPO_DIR`
- `TEST_TYPE`
- `TEST_LOGIN_EMAIL`
- `TEST_LOGIN_PASSWORD`
- `SUBMISSION_API_TOKEN`
- `RUNS_PER_SCENARIO`
- `COOLDOWN_SECONDS`

## Deploy monitoring

Apply Prometheus and cAdvisor:

```bash
./testing/apply-monitoring.sh
```

Verify:

```bash
kubectl get all -n canvas-monitoring
```

Prometheus should be available at:

```text
http://canvas.io.vn:30090
```

## Grafana

Grafana is deployed as part of the monitoring stack and exposed on:

```text
http://canvas.io.vn:30091
```

Default login:

```text
username: admin
password: admin
```

The Prometheus data source is provisioned automatically.

To use the Canvas load-testing dashboard, import this JSON:

```text
testing/grafana/canvas-local-dashboard.json
```

The dashboard includes:

- request throughput
- error rate
- response time percentiles `p50`, `p95`, `p99`
- VU count
- Canvas web CPU per pod
- Canvas web memory per pod
- Canvas jobs memory per pod
- live deployment replica count
- live pod restart count
- live HPA current and desired replicas
- stat panels for current p95, error rate, and VUs

Notes:

- The dashboard filters by `testid`, so you can switch between load-test runs.
- Current live panels rely on metrics exposed by Prometheus, cAdvisor, and `kube-state-metrics`.

## Live Kubernetes state in Grafana

The monitoring stack now deploys `kube-state-metrics`, and Prometheus scrapes it automatically.

This adds live Grafana visibility for:

- deployment replica counts
- pod restart counts
- HPA current replicas
- HPA desired replicas

To apply the updated monitoring stack:

```bash
./testing/apply-monitoring.sh
```

You can verify the monitoring components with:

```bash
kubectl get all -n canvas-monitoring
```

## Seed test data

Before load testing, seed data so the API has realistic content.

Interactive mode:

```bash
./testing/run-seed-data.sh
```

It will ask for:

- dataset size: Small, Medium, or Large
- API token if not already saved
- `SEED_PREFIX`

Example explicit run:

```bash
SEED_PREFIX=lt-batch-01 ./testing/run-seed-data.sh
```

Recommended medium-sized dataset:

- `COURSE_COUNT=12`
- `TEACHER_POOL_SIZE=8`
- `STUDENT_POOL_SIZE=250`
- `TEACHERS_PER_COURSE=2`
- `STUDENTS_PER_COURSE=40`
- `ASSIGNMENTS_PER_COURSE=8`
- `PAGES_PER_COURSE=4`
- `DISCUSSIONS_PER_COURSE=3`
- `MODULES_PER_COURSE=4`
- `QUIZZES_PER_COURSE=2`
- `ANNOUNCEMENTS_PER_COURSE=2`

Use a unique prefix for every run to avoid collisions.

The seeded dataset now includes:

- users for teacher and student pools
- published courses
- enrollments
- assignments
- pages
- discussion topics
- announcements
- modules
- module items linked to seeded course content
- quizzes

If you want to exercise the optional session-login flow, use a seeded student account such as:

```text
<seed-prefix>-student-001@seed.local
```

with password:

```text
ChangeMe123!
```

If you want a richer dataset for manual API validation, you can scale it up explicitly, for example:

```bash
SEED_PREFIX=thesis-seed-02 \
COURSE_COUNT=16 \
STUDENT_POOL_SIZE=400 \
ASSIGNMENTS_PER_COURSE=10 \
PAGES_PER_COURSE=6 \
DISCUSSIONS_PER_COURSE=4 \
MODULES_PER_COURSE=6 \
QUIZZES_PER_COURSE=3 \
ANNOUNCEMENTS_PER_COURSE=3 \
./testing/run-seed-data.sh
```

## Remove seeded data

Delete previously seeded data by prefix:

```bash
SEED_PREFIX=lt-batch-01 ./testing/run-unseed-data.sh
```

This deletes matching seeded courses first, then matching seeded users.

## Run load test

Run:

```bash
./testing/run-load-test.sh
```

Or choose a named profile:

```bash
TEST_TYPE=smoke ./testing/run-load-test.sh
TEST_TYPE=load ./testing/run-load-test.sh
TEST_TYPE=stress ./testing/run-load-test.sh
TEST_TYPE=soak ./testing/run-load-test.sh
```

The script:

- loads `testing/testing.env`
- uses your saved API token
- applies a named test profile
- sends metrics to Prometheus remote write
- saves run output locally

During startup it prints:

- base URL
- Prometheus write URL
- test profile
- test ID
- masked token preview

Results are stored under:

```text
testing/results/<testid>/
```

Files include:

- `k6-summary.txt`
- `metadata.env`
- `k8s-snapshots.csv`
- `environment.env`

The k6 workload is no longer a single endpoint. It now mixes:

- `GET /api/v1/dashboard/dashboard_cards`
- `GET /api/v1/accounts/self/courses`
- `GET /api/v1/courses/{id}/modules`
- `GET /api/v1/courses/{id}/quizzes`
- optional `POST /login/canvas`
- optional `POST /api/v1/courses/{id}/assignments/{id}/submissions`

Optional flows:

- set `TEST_LOGIN_EMAIL` and `TEST_LOGIN_PASSWORD` to enable session login checks
- set `SUBMISSION_API_TOKEN` to enable assignment submission traffic with a student-scoped token

## Horizontal Pod Autoscaling

This repo now includes simple CPU-based HPAs for:

- `canvas-web`
- `canvas-jobs`

Deployment modes:

- `./deploy.sh baseline`: migrate DB, deploy fixed replicas, remove HPAs
- `BASELINE_DISABLE_JOBS=true ./deploy.sh baseline`: same as baseline, but scales `canvas-jobs` to `0`
- `./deploy.sh hpa`: migrate DB, deploy with HPAs enabled
- `./deploy.sh`: same as `./deploy.sh hpa`
- `./deploy.sh bootstrap`: initialize DB, then deploy with HPAs enabled

The HPA manifest used by `hpa` mode is:

```text
deployment/hpa.yaml
```

To verify after deployment:

```bash
kubectl get hpa -n canvas
```

Note:

- HPA requires Kubernetes metrics collection such as `metrics-server`

Suggested thesis experiment set:

- baseline with `./deploy.sh baseline`
- baseline web-only with `BASELINE_DISABLE_JOBS=true ./deploy.sh baseline` if delayed jobs destabilize the single-node host
- HPA enabled under the same workload profile with `./deploy.sh hpa`
- compare latency, throughput, and pod CPU over time

## Generate charts

Set up the Python environment once:

```bash
./testing/charts/setup-python.sh
```

If Ubuntu says `ensurepip is not available`, install:

```bash
sudo apt install -y python3-venv
```

If your AMI specifically asks for a versioned package, install that instead, for example:

```bash
sudo apt install -y python3.12-venv
```

Then generate charts:

```bash
source ./testing/charts/.venv/bin/activate
python3 testing/charts/plot_prometheus.py --prometheus-url http://127.0.0.1:30090 --minutes 15
```

Generate charts for one specific run using its saved run window:

```bash
source ./testing/charts/.venv/bin/activate
python3 testing/charts/plot_prometheus.py --prometheus-url http://127.0.0.1:30090 --testid exp01-baseline-load
```

Generate a comparison chart across multiple runs:

```bash
source ./testing/charts/.venv/bin/activate
python3 testing/charts/plot_prometheus.py \
  --prometheus-url http://127.0.0.1:30090 \
  --compare-testids exp01-baseline-load,exp02-hpa-load,exp03-baseline-stress,exp04-hpa-stress \
  --compare-labels baseline_load,hpa_load,baseline_stress,hpa_stress
```

Charts are written to:

```text
testing/charts/output
```

Current chart outputs include:

- response time timeline with `p50`, `p95`, `p99`
- throughput vs error rate
- VU load profile
- web CPU with replica count
- pod restart count
- scale latency for HPA runs
- comparison p95 latency summary

The load-test runner also saves `k8s-snapshots.csv` for each run, which records:

- web and jobs replica counts over time
- HPA current and desired replicas
- pod restart totals

## Run repeated experiment matrix

This repo can now execute repeated thesis-style experiments and keep a manifest for every run.

Default repeated-run plan:

- `baseline` and `hpa`
- `smoke`, `load`, `stress`, and `soak`
- `9` runs per scenario

Use the same seeded dataset for all repeated runs:

```bash
SEED_PREFIX=thesis-seed-01 ./testing/run-seed-data.sh
```

Then run the matrix:

```bash
SEED_PREFIX=thesis-seed-01 EXPERIMENT_NAME=thesis ./testing/run-experiment-matrix.sh
```

The runner will:

- deploy the correct mode
- restart application pods between runs
- optionally flush Redis between runs
- wait for cooldown
- verify pod readiness
- save per-run charts
- append a row to the experiment manifest
- run statistical analysis after the matrix finishes

Useful environment variables:

- `RUNS_PER_SCENARIO=9`
- `COOLDOWN_SECONDS=600`
- `FLUSH_REDIS_BETWEEN_RUNS=true|false`
- `MATRIX_MODES=baseline,hpa`
- `MATRIX_SCENARIOS=smoke,load,stress,soak`
- `EXPERIMENT_NAME=thesis`

Manifest output:

```text
testing/results/experiment-manifest-<experiment>.csv
```

Analysis output:

```text
testing/results/analysis/<manifest-name>/
```

Analysis files include:

- `group_summary.csv`
- `outliers.csv`
- `t_tests.csv`

The manifest stores:

- experiment name
- mode
- scenario
- run number
- test ID
- seed prefix
- started and ended timestamps
- acceptance flag
- notes
- cooldown setting
- Redis flush setting
- basic environment conditions

## Publish results to the results repo

This repo publishes load-test output and charts to:

```text
https://github.com/giakhanh22024558/canvas-k8s-results.git
```

Publish the latest run:

```bash
./testing/publish-results.sh
```

Publish a specific run:

```bash
TEST_ID=canvas-20260327-120000 ./testing/publish-results.sh
```

The publish script:

- clones or updates the results repo locally
- copies:
  - `testing/results/<testid>/`
  - `testing/charts/output/`
- commits under:
  - `runs/<testid>/`
- pushes to GitHub

## GitHub authentication for publishing

Publishing will fail unless the EC2 host can push to GitHub.

### Option 1: HTTPS with GitHub PAT

Configure your Git identity:

```bash
git config --global user.name "Your Name"
git config --global user.email "your-email@example.com"
```

Enable stored credentials:

```bash
git config --global credential.helper store
rm -f ~/.git-credentials
```

Then when push prompts for credentials:

- Username: your GitHub username
- Password: paste a GitHub Personal Access Token, not your GitHub account password

The token must have repository write access to:

```text
giakhanh22024558/canvas-k8s-results
```

### Option 2: SSH

Generate a key:

```bash
ssh-keygen -t ed25519 -C "your-email@example.com"
cat ~/.ssh/id_ed25519.pub
```

Add the public key to GitHub, then switch the results repo remote to SSH:

```bash
git -C /home/ubuntu/canvas-k8s-result remote set-url origin git@github.com:giakhanh22024558/canvas-k8s-results.git
ssh -T git@github.com
git -C /home/ubuntu/canvas-k8s-result push -u origin main
```

## Full end-to-end flow on a fresh EC2 instance

Use this order:

```bash
cd ~/canvas-k8s
find . -type f -name "*.sh" -exec chmod +x {} +
./start-cluster.sh
./reset-and-bootstrap.sh
./create-admin-token.sh
./testing/setup-env.sh
./testing/apply-monitoring.sh
SEED_PREFIX=lt-batch-01 ./testing/run-seed-data.sh
./testing/run-load-test.sh
./testing/charts/setup-python.sh
source ./testing/charts/.venv/bin/activate
python3 testing/charts/plot_prometheus.py --prometheus-url http://127.0.0.1:30090 --minutes 15
./testing/publish-results.sh
```

## Typical repeat flow on the same EC2 instance

For later runs:

```bash
cd ~/canvas-k8s
./start-cluster.sh
./deploy.sh
./testing/run-load-test.sh
source ./testing/charts/.venv/bin/activate
python3 testing/charts/plot_prometheus.py --prometheus-url http://127.0.0.1:30090 --minutes 15
./testing/publish-results.sh
```

## Troubleshooting

### `401 Unauthorized` on API

- token is missing or invalid
- create a new token with `./create-admin-token.sh`

### `python3 -m venv` fails with `ensurepip is not available`

Install:

```bash
sudo apt install -y python3-venv
```

### `publish-results.sh` fails with GitHub password error

GitHub does not support account passwords for Git push over HTTPS.
Use a PAT or SSH.

### `publish-results.sh` fails with `403`

- token exists but does not have permission to push
- create a PAT with repo write access for the correct account

### Browser login issues over plain HTTP

This setup is better for API-token-based testing than browser login because modern cookie policy can make HTTP login unreliable.

## Notes

- Main Canvas URL: `http://canvas.io.vn`
- Internal NodePort health check: `http://127.0.0.1:30080`
- Prometheus URL: `http://canvas.io.vn:30090`

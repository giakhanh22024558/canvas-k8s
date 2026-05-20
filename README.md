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

## Full load-test pipeline (copy-paste playbook)

End-to-end flow with k6 on the load gen, collectors and charts on the SUT, and auto-trigger between them. Once one-time setup is done, every test is just two short blocks of commands.

### One-time setup (per stack rebuild)

**On the load gen** — `~/canvas-k8s/testing/testing.env`:

```bash
SUT_SSH_HOST=ubuntu@<SUT_PRIVATE_IP>
```

**On the SUT** — `~/canvas-k8s/testing/testing.env`:

```bash
LOADGEN_SSH_HOST=ubuntu@<LOADGEN_PRIVATE_IP>
```

**Bidirectional SSH keys** (paste each host's `~/.ssh/id_ed25519.pub` into the other's `~/.ssh/authorized_keys`). Verify both:

```bash
# On SUT
ssh ubuntu@<LOADGEN_PRIVATE_IP> "echo ok"
# On load gen
ssh ubuntu@<SUT_PRIVATE_IP> "echo ok"
```

### Per-test execution (3 commands total)

**1. On the SUT — start the 3 collectors (jobs queue, Postgres, Redis):**

```bash
cd ~/canvas-k8s
git pull origin khanh-dev/testing
bash testing/start-collectors.sh
```

**2. On the load gen — run k6 (auto-triggers chart publish on SUT when k6 finishes):**

```bash
cd ~/canvas-k8s
git pull origin khanh-dev/testing
EXPERIMENT_NAME=stage5-hpa-tuned-run01 \
  TEST_TYPE=staircase \
  bash testing/run-load-test.sh
```

`TEST_TYPE` options: `smoke` (30s), `load` (5m), `staircase` (~23m, ramp 10→30→60 with 5-min holds), `breakpoint` (~20m, ramps 1→100 to find capacity), `soak` (30m). The legacy alias `long-stress` still maps to `staircase`.

**3. On the SUT — stop collectors, fold their CSVs into the run folder, regenerate charts:**

```bash
bash testing/stop-collectors.sh && \
  RUN_ID=$(ls -t testing/results/ | grep '^canvas-' | head -1) && \
  cp $(ls -td /tmp/collectors-* | head -1)/*.csv testing/results/$RUN_ID/ && \
  TEST_ID=$RUN_ID bash testing/publish-results.sh
```

### Verify

```bash
# All chart PNGs (incl. jobs / db / redis) should be present
ls testing/results/$RUN_ID/*.png

# Summary CSV should contain the new web/jobs/db/redis fields
cat testing/results/$RUN_ID/summary_*.csv
```

Expected charts: `latency_*.png`, `throughput_error_*.png`, `cpu_replicas_*.png`, `memory_*.png`, `hpa_cpu_*.png`, `restart_counts_*.png`, `scale_latency_*.png`, `jobs_queue_*.png`, `db_health_*.png`, `redis_health_*.png`.

### Cross-run aggregate (after 3+ runs of the same experiment)

```bash
EXPERIMENT_NAME=stage5-hpa-tuned PUSH_GIT=true \
  bash testing/aggregate-timeseries.sh
```

Outputs `testing/results/analysis-<experiment>/timeseries_*.png` (mean ± std bands) plus `aggregate_stats_<experiment>.csv` and box plots.

### Skip the auto-trigger (manual mode)

Leave `SUT_SSH_HOST` unset on the load gen. After k6 finishes, run on the SUT:

```bash
TEST_ID=<test-id-from-load-gen-output> bash testing/publish-results.sh
```

It will rsync the run folder via `LOADGEN_SSH_HOST` and produce charts as usual.

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

### Two-host workflow: rsync the raw data, publish to a separate results repo

When k6 runs on a separate EC2 instance, raw output stays on the load-gen disk and charts/Prometheus queries run on the SUT. To keep the main `canvas-k8s` repo small, we use **rsync** to move the run folder between hosts (small, fast, LAN-local) and a **separate `canvas-k8s-results` repo** for chart artifacts.

```
load gen (k6)  --rsync-->  SUT  --git push-->  canvas-k8s-results
                             |
                             +-- queries Prometheus, generates charts
```

Setup once on the **load gen** in `testing/testing.env`:

```bash
SUT_SSH_HOST=ubuntu@172.31.27.241
# Optional overrides:
# SUT_SSH_KEY=/home/ubuntu/.ssh/id_ed25519
# SUT_REPO_DIR=/home/ubuntu/canvas-k8s
```

Setup once on the **SUT** in `testing/testing.env`:

```bash
LOADGEN_SSH_HOST=ubuntu@172.31.6.227
# Optional overrides:
# LOADGEN_SSH_KEY=/home/ubuntu/.ssh/id_ed25519
# LOADGEN_RESULTS_DIR=/home/ubuntu/canvas-k8s/testing/results

RESULTS_REPO_URL=https://github.com/<you>/canvas-k8s-results.git
RESULTS_REPO_DIR=/home/ubuntu/canvas-k8s-results
```

SSH keys in both directions:

```bash
# On load gen → can SSH into SUT to trigger publish
ssh-keygen -t ed25519 -N '' -f ~/.ssh/id_ed25519
ssh-copy-id ubuntu@172.31.27.241

# On SUT → can SSH into load gen to rsync the run folder
ssh-keygen -t ed25519 -N '' -f ~/.ssh/id_ed25519
ssh-copy-id ubuntu@172.31.6.227
```

Now every `bash testing/run-load-test.sh` on the load gen:

1. Runs k6 — raw data lands in `testing/results/<test-id>/` on the load gen.
2. SSHes into the SUT and triggers `TEST_ID=<id> bash testing/publish-results.sh` remotely.
3. That script on the SUT pulls the latest plotting code, rsyncs the run folder from the load gen, queries Prometheus, generates charts, then commits and pushes the chart bundle to **`canvas-k8s-results`** (the dedicated results repo).

One command on the load gen → full report published. The main `canvas-k8s` repo never grows with run data. Leave `SUT_SSH_HOST` unset to keep the manual flow (run `publish-results.sh` on the SUT yourself when ready).

## Horizontal Pod Autoscaling

This repo now includes simple CPU-based HPAs for:

- `canvas-web`
- `canvas-jobs`

Deployment modes:

- `./deploy.sh baseline` — migrate DB, fixed replicas (web=1, jobs=1), no HPA. Used for **Stage 1**.
- `BASELINE_DISABLE_JOBS=true ./deploy.sh baseline` — same as baseline but scales `canvas-jobs` to `0`.
- `./deploy.sh prescaled` — migrate DB, fixed replica fleet, no HPA, VPA resources. Used for **Stage 2** (breakpoint characterisation).
- `./deploy.sh hpa` — migrate DB, HPA enabled (stock CPU target + accelerated scale-down), VPA resources. Used for **Stage 3**.
- `./deploy.sh` — alias of `./deploy.sh hpa`.
- `./deploy.sh bootstrap` — initialize a fresh DB, then deploy with HPA enabled.

HPA manifest:

- `deployment/hpa.yaml` — the HPA applied by `hpa` mode: web/jobs `averageUtilization: 80%` with stock scale-up and an accelerated scale-down (`stabilizationWindowSeconds: 60`, `1 pod / 30s`) for shorter, observable test runs.

`baseline`, `prescaled` and `hpa` reuse the same `deployment-web.yaml` and `deployment-jobs.yaml` so cross-stage comparisons isolate the scaling strategy from resource sizing.

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

## Thesis 4-stage experimental framework

Four stages chained so each one's output is the next one's input:

| Stage | Goal | Output consumed by |
|-------|------|---------------------|
| 1 — Baseline + VPA Profiling | Find the per-pod resource footprint (CPU + memory request/limit) under realistic load | feeds resource numbers into Stages 2-4 |
| 2 — Breakpoint Test | Find the absolute capacity ceiling of the SUT (max VUs before error rate >1% or latency >5s) | gives `MAX_VUS` used to drive Stages 3-4 |
| 3 — HPA Naive (stock config) | Show how an untuned default HPA behaves at full capacity load — slow reaction, latency spikes, possible drops | comparison baseline for Stage 4 |
| 4 — HPA Tuned | Demonstrate that lowering the target and tuning `behavior:` keeps the system stable under the same load | the thesis claim |

Each stage isolates exactly one variable from the previous one so cross-stage deltas attribute cleanly. Stages 3 and 4 use **identical resources, replica caps, and load profile**; only the HPA configuration differs.

### Stage 1 — Baseline + VPA Profiling

**Goal**: characterise resource footprint of one web pod and one jobs pod under realistic load. Output: `requests/limits` numbers for CPU and memory that feed every later stage.

**Setup**:
- Deploy with `./deploy.sh baseline` — exactly 1 web pod and 1 jobs pod, naive resource values, no HPA.
- Install VPA recommender + apply observe-only VPA CRs: `bash testing/vpa-recommend.sh setup`. It collects usage stats while the test runs but never mutates the pods (`updateMode: "Off"`).

**Action**: ramp k6 load until the pod's CPU sits at ~70-80%. The `load` profile (10 VUs, 5 min) is usually enough for a single pod to reach that range; if not, switch to `staircase` for a longer hold at the highest VU level.

```bash
./deploy.sh baseline
bash testing/vpa-recommend.sh setup

SEED_PREFIX=thesis \
  RUNS_PER_SCENARIO=3 \
  MATRIX_MODES=baseline \
  MATRIX_SCENARIOS=staircase \
  EXPERIMENT_NAME=stage1-baseline-vpa \
  COOLDOWN_SECONDS=300 \
  bash testing/run-experiment-matrix.sh

# Wait ~8 min after the runs end so VPA has enough samples, then read:
bash testing/vpa-recommend.sh           # human-readable
bash testing/vpa-recommend.sh save      # also write env file + summary
```

**Expected outcome**: `vpa-recommend.sh` prints suggested CPU/memory `requests` and `limits` for `canvas-web` and `canvas-jobs`. Edit `deployment/deployment-web.yaml` and `deployment/deployment-jobs.yaml` with those values before continuing.

### Stage 2 — Breakpoint Test

**Goal**: find `MAX_VUS` — the highest virtual-user count the SUT can sustain on the new instance type with VPA-sized resources, before error rate exceeds 1 % or p95 latency exceeds 5 s.

**Setup**:
- Disable HPA (`prescaled` deploy mode does this — fixed replica counts, no HPA).
- Manually scale to as many pods as the node can comfortably hold given the Stage 1 resource numbers (typically 6-8 web pods + 3 jobs pods on `m6a.2xlarge`).
- Resources are the VPA-recommended values from Stage 1.

**Action**: run k6 with the `breakpoint` profile, which ramps from 1 to 100 VUs over 20 minutes, holding each level for 2 minutes.

```bash
# Pick replica counts based on Stage 1 footprint and node capacity, e.g. 8 web + 3 jobs.
PRESCALED_WEB_REPLICAS=8 PRESCALED_JOBS_REPLICAS=3 ./deploy.sh prescaled

SEED_PREFIX=thesis \
  RUNS_PER_SCENARIO=3 \
  MATRIX_MODES=prescaled \
  MATRIX_SCENARIOS=breakpoint \
  EXPERIMENT_NAME=stage2-breakpoint \
  COOLDOWN_SECONDS=300 \
  SKIP_DEPLOY=true \
  bash testing/run-experiment-matrix.sh
```

**Expected outcome**: a `MAX_VUS` number visible from the breakpoint chart's saturation point — the VU level at which error rate first crosses 1 % or p95 first crosses 5 s. Use that number to build the load profile for Stages 3-4.

### Stage 3 — HPA (stock Kubernetes default)

**Goal**: characterise how Kubernetes' stock HPA behaves under the load level identified in Stage 2 — scale-up latency, ramp-transition error bumps, scale-down dynamics. The stage is labelled simply "HPA" (no "naive" qualifier) because pilot runs showed the stock default config already produces a stable elastic system; tuning further provides no material improvement and the comparison becomes uninformative. The thesis therefore contrasts HPA against a prescaled fleet at matched workload (Stage 4) rather than against a tuned-HPA variant.

**Setup**:
- HPA enabled: `targetAverageUtilization: 80%`. Scale-up is left at stock Kubernetes defaults (no `scaleUp` behavior block — 0-second stabilization, max +100% per 60s). Scale-down carries an accelerated `behavior` block (`stabilizationWindowSeconds: 60`, `1 pod / 30s`) so the cooldown is observable within a short idle hold.
- Resources: VPA-recommended values from Stage 1.
- Load profile: `staircase-tuned` — 10/30/60/70 VU plateaus (cap = Stage 2 saturation point, validated by passenger-status Max pool size × 3 web pods = 18 workers ≈ λ_max at VU≈70) + 10-min slow ramp-down 70→10 + 8-min idle hold to observe HPA scale-down across the 60-second stabilization window.

```bash
./deploy.sh hpa   # applies the Stage 3 HPA (deployment/hpa.yaml)

# staircase-tuned caps at 70 VUs (Stage 2 saturation point) + slow ramp-down
# tail to observe HPA scale-down dynamics. 46 min per run.
SEED_PREFIX=thesis \
  RUNS_PER_SCENARIO=3 \
  MATRIX_MODES=hpa \
  MATRIX_SCENARIOS=staircase-tuned \
  EXPERIMENT_NAME=stage3-hpa \
  COOLDOWN_SECONDS=300 \
  SKIP_DEPLOY=true \
  bash testing/run-experiment-matrix.sh
```

**Observations to record**:
- Time from VU ramp to first new pod becoming `Ready` (scale-out latency).
- Whether existing pods are OOMKilled or hung before reinforcements arrive.
- Error-rate spikes during ramp transitions.
- HPA target threshold (80 %) being crossed before scale-out begins.

### Stage 4 — Prescaled Staircase (workload-matched baseline)

**Goal**: isolate the marginal effect of HPA versus a same-shape static deployment. Stage 4 runs the identical `staircase-tuned` profile against a prescaled fleet (web=3, jobs=2 — matching Stage 3 HPA maxReplicas) so the only delta between Stage 3 and Stage 4 is the presence of the HPA controller in the loop.

**Setup**:
- Prescaled deployment via `./deploy.sh prescaled` adjusted to web=3, jobs=2.
- Same VPA-recommended resources, same replica caps, same `staircase-tuned` load profile as Stage 3.

```bash
./deploy.sh prescaled

SEED_PREFIX=thesis \
  RUNS_PER_SCENARIO=3 \
  MATRIX_MODES=prescaled \
  MATRIX_SCENARIOS=staircase-tuned \
  EXPERIMENT_NAME=stage4-prescaled \
  COOLDOWN_SECONDS=300 \
  SKIP_DEPLOY=true \
  bash testing/run-experiment-matrix.sh
```

**Expected outcome**: ramp-transition error bumps disappear (no scale-up latency); p95 latency more stable across the staircase plateaus. Resource-time area (core·min, GiB·min) is higher because the fleet is pinned at max throughout the run — the cost side of the trade-off HPA pays for. The Stage 3 → Stage 4 delta quantifies this trade-off under identical workload.

### Optional — Prescaled comparison stage (legacy)

The earlier 5-stage version of this thesis included a "prescaled" stage with fixed N pods (no HPA) at the same load level as the HPA stages, showing that even an over-provisioned static deployment could not handle dynamic load gracefully. It is no longer part of the main narrative because Stage 2 (Breakpoint with prescaled max pods) already demonstrates the static ceiling. If you want to run it for completeness:

```bash
PRESCALED_WEB_REPLICAS=5 PRESCALED_JOBS_REPLICAS=3 ./deploy.sh prescaled

SEED_PREFIX=thesis \
  RUNS_PER_SCENARIO=5 \
  MATRIX_MODES=prescaled \
  MATRIX_SCENARIOS=staircase \
  EXPERIMENT_NAME=optional-prescaled \
  COOLDOWN_SECONDS=300 \
  SKIP_DEPLOY=true \
  bash testing/run-experiment-matrix.sh
```

### Useful matrix runner variables

| Variable | Purpose |
|----------|---------|
| `START_RUN=2` | Resume from a specific run number after interruption |
| `RUNS_PER_SCENARIO=5` | Number of repeats per (mode × scenario) cell |
| `SKIP_DEPLOY=true` | Skip `deploy.sh` between runs (use when resources have been manually patched) |
| `COOLDOWN_SECONDS=300` | Sleep between runs to let CPU/memory settle |
| `MATRIX_MODES=hpa,prescaled` | Comma-separated list of `deploy.sh` modes |
| `MATRIX_SCENARIOS=staircase,breakpoint` | Comma-separated k6 profiles |
| `EXPERIMENT_NAME=stage5-hpa-tuned` | Prefix used in `test_id` and manifest filename |
| `FLUSH_REDIS_BETWEEN_RUNS=true` | Redis FLUSHALL before each run |

## VPA profiling (Stage 2)

VPA (Vertical Pod Autoscaler) observes resource usage of running pods and recommends sized requests/limits. We use it in **observe-only mode** — the recommendations are read once, then manually applied to deployment YAMLs.

```bash
# Install VPA components (one-time)
bash testing/vpa-recommend.sh install

# Apply observe-only VPA objects (defined in deployment/vpa-recommendation.yaml)
bash testing/vpa-recommend.sh apply

# Run real load while VPA observes (any of the test profiles works; staircase is best)
TEST_TYPE=staircase TEST_ID=stage2-vpa-profile bash testing/run-load-test.sh

# After the load test, read recommendations
bash testing/vpa-recommend.sh show

# Save recommendations as text + machine-readable env file
bash testing/vpa-recommend.sh save
# -> writes:
#    testing/results/stage2-vpa-profile/vpa-recommendations.txt
#    testing/results/stage2-vpa-profile/vpa-profile.env
```

After saving, edit `deployment/deployment-web.yaml` and `deployment/deployment-jobs.yaml` resource blocks to match VPA's `target` values. Cap CPU requests if the total exceeds the node's allocatable cores (5 × 2281m = 11.4 cores doesn't fit a single 8-vCPU node — manually capped to 1200m on canvas-web).

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

If Prometheus is missing the k6 percentile series for a run, the chart exporter falls back to parsing the saved `k6-summary.txt` so per-run summary CSVs still contain usable latency values.

The load-test runner also saves `k8s-snapshots.csv` for each run, which records:

- web and jobs replica counts over time
- HPA current and desired replicas
- pod restart totals

In normal use you don't call `plot_prometheus.py` directly — `testing/publish-results.sh` wraps it with the right test ID, generates charts, commits, and pushes:

```bash
TEST_ID=stage5-hpa-tuned-hpa-long-stress-run01 bash testing/publish-results.sh
```

## Cross-run aggregate analysis

After a stage's runs complete, two scripts produce cross-run views:

### `testing/aggregate-results.sh` — statistics + box plots

Computes mean / std / min / max / median across all runs of an experiment and writes:

- `analysis-<exp>/aggregate_stats_<exp>.csv`
- `analysis-<exp>/boxplot_<metric>.png` (one per metric)
- `analysis-<exp>/barplot_summary_<exp>.png`

Box plots use `showfliers=False` so the scatter overlay (one dot per run) is not double-counted.

```bash
EXPERIMENT_NAME=stage5-hpa-tuned PUSH_GIT=true bash testing/aggregate-results.sh
```

### `testing/aggregate-timeseries.sh` — mean ± std time-series charts

Produces per-run-style time-series charts but each line is the **mean across runs at that point in time**, with a shaded ±1 standard deviation band:

- `analysis-<exp>/timeseries_throughput_error.png`
- `analysis-<exp>/timeseries_latency.png`
- `analysis-<exp>/timeseries_cpu_replicas.png`
- `analysis-<exp>/timeseries_memory.png`

Uses Prometheus for k6 + cAdvisor metrics and reads each run's `k8s-snapshots.csv` for replica counts (so this works even after Prometheus retention has expired for the metric series).

```bash
EXPERIMENT_NAME=stage5-hpa-tuned PUSH_GIT=true bash testing/aggregate-timeseries.sh
```

Tight bands = reproducible behaviour at that moment; wide bands = run-to-run variance. A common pattern in this repo's data: tight band on `p50` and `p95`, wide band on `p99` because tail latency is sensitive to scale-out timing.

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

## Database bottleneck verification

A common reviewer critique of single-node experiments is "your throughput ceiling is the database, not the application." We rule this out by sampling Postgres CPU and connection state during peak load.

### Quick spot-check during a peak VU phase

```bash
# Postgres pod resource usage at this moment
kubectl top pod -n canvas -l app=postgres

# Connection state and lock contention
kubectl exec -n canvas deployment/postgres -- psql -U canvas -d canvas_production -c "
SELECT
  count(*) FILTER (WHERE state = 'active')                              AS active,
  count(*) FILTER (WHERE state = 'idle')                                AS idle,
  count(*) FILTER (WHERE state = 'idle in transaction')                 AS idle_in_tx,
  count(*) FILTER (WHERE state = 'active' AND wait_event_type = 'Lock') AS real_lock_waits,
  count(*) FILTER (WHERE state = 'active' AND now() - query_start > interval '1 second') AS slow_queries
FROM pg_stat_activity
WHERE datname = 'canvas_production';"
```

Interpretation:

| Postgres CPU during peak load | Verdict |
|------------|---------|
| < 50% of one vCPU | application-tier bound — HPA / web pods are the limit |
| 50–80% | mixed — DB contributes but is not sole bottleneck |
| > 80% | database bound — throughput plateau is the DB, not the app |

For the test workload in this repo, observed values: **~5% CPU, 1 active query, zero lock waits**. Postgres is decisively not the bottleneck.

### Continuous logging (records full timeline)

`testing/collect-postgres-metrics.sh` polls every 5s and writes a CSV with timestamp, CPU, memory, and connection counts. Run it in a separate SSH session during a Stage 4 or Stage 5 run:

```bash
mkdir -p testing/results/postgres-bottleneck-check
bash testing/collect-postgres-metrics.sh \
  testing/results/postgres-bottleneck-check/postgres-during-stage5.csv
# Ctrl+C to stop after the run completes
```

Then compute a one-line verdict:

```bash
awk -F',' 'NR>1 {
  if ($2 > max_cpu) max_cpu = $2
  if ($4 > max_active) max_active = $4
  if ($7 > 0) slow_count++
  total += $2; n++
} END {
  printf "Mean CPU: %dm  Peak CPU: %dm  Peak active conns: %d  Slow queries: %d\n",
    total/n, max_cpu, max_active, slow_count
}' testing/results/postgres-bottleneck-check/postgres-during-stage5.csv
```

## Why DB scaling is out of scope (validation)

The thesis evaluates web and jobs tier autoscaling on a single Postgres instance and a single Redis instance — the database tier is intentionally not scaled. To defend this scope, we measure the database and cache through every load test and verify they remain well within their capacity ceilings, eliminating them as confounding variables.

### Saturation invariants

A run is considered "DB-clean" — meaning bottlenecks observed are application-tier, not database-tier — when *all* of the following hold throughout the test window:

| Tier | Invariant | Threshold | Source |
|---|---|---|---|
| Postgres | CPU | < 70% of one vCPU peak | `kubectl top` via `collect-postgres-metrics.sh` |
| Postgres | Memory | < 80% of container limit | same |
| Postgres | Active connections | < 50% of `max_connections` | `pg_stat_activity` |
| Postgres | Lock waits (`wait_event_type IS NOT NULL`) | = 0 | `pg_stat_activity` |
| Postgres | Slow queries (>1s) | = 0 | `pg_stat_activity` |
| Postgres | Cache hit ratio | > 99% | `pg_stat_database.blks_hit / (blks_hit+blks_read)` |
| Redis | CPU | < 50% of one vCPU peak | `kubectl top` via `collect-redis-metrics.sh` |
| Redis | Memory | < 80% of `maxmemory` | `INFO memory` |
| Redis | Hit ratio (rolling, per 5s window) | > 90% (LMS workload baseline) | derived from `INFO stats.keyspace_hits / misses` deltas between consecutive samples |
| Redis | Evictions | = 0 (cumulative) | `INFO stats.evicted_keys` |

If any invariant is violated, the run is excluded from autoscaling claims and the bottleneck is reported as the DB tier instead.

### Collectors

Two pollers run on the SUT in parallel with `run-load-test.sh`. They poll Postgres and Redis every 5s and write CSVs into the run folder.

```bash
# Start collectors before the load test (run on SUT, terminal 1):
RUN_DIR=$(ls -td testing/results/canvas-* | head -1)   # latest run
# (or set RUN_DIR explicitly)

bash testing/collect-postgres-metrics.sh "$RUN_DIR/postgres-health.csv" &
PG_PID=$!
bash testing/collect-redis-metrics.sh    "$RUN_DIR/redis-health.csv"    &
REDIS_PID=$!

# When the test finishes (k6 done on load gen):
kill $PG_PID $REDIS_PID
```

`postgres-health.csv` schema (11 cols): `timestamp, postgres_cpu_millicores, postgres_memory_mib, active_conns, idle_conns, idle_in_tx_conns, waiting_on_locks, slow_queries_over_1s, max_connections, cache_hit_ratio_percent, xact_commit_cumulative`

`redis-health.csv` schema (10 cols): `timestamp, redis_cpu_millicores, redis_memory_used_mb, redis_memory_max_mb, connected_clients, blocked_clients, ops_per_sec, keyspace_hits_cumulative, keyspace_misses_cumulative, evicted_keys_cumulative`

### Charts

`publish-results.sh` reads both CSVs and generates per-run charts:

- `db_health_<label>.png` — 4 panels: Postgres CPU+memory, connection-pool utilization, cache hit ratio + slow queries, lock waits + idle-in-transaction.
- `redis_health_<label>.png` — 2 panels: Redis CPU+memory vs maxmemory, hit ratio + ops/sec + evictions.

### Summary CSV invariant fields

`summary_*.csv` adds 11 columns derived from the above CSVs:

| Field | Meaning | Pass threshold |
|---|---|---|
| `peak_postgres_cpu_millicores` | max CPU during run | < 700 (≈70% of 1 vCPU) |
| `peak_postgres_memory_mib` | max RSS during run | < 80% of limit |
| `peak_active_conns` | max active connections | < 50% of `max_connections` |
| `max_db_lock_waits` | max concurrent lock waiters | = 0 |
| `max_db_idle_in_tx` | max idle-in-transaction connections | low (≤ 2) |
| `total_slow_queries_over_1s` | snapshots with at least one slow query | = 0 |
| `min_cache_hit_ratio_percent` | min cache hit ratio | > 99 |
| `peak_redis_cpu_millicores` | max Redis CPU | < 500 |
| `peak_redis_memory_mb` | max Redis memory used | < 80% of maxmemory |
| `min_redis_hit_ratio_percent` | min rolling-window Redis hit ratio | > 90 |
| `redis_evictions_total` | cumulative evictions seen | = 0 |

### Live monitoring during defense

The Grafana dashboard's "Database & Cache — Saturation Invariants" section shows these threshold-coloured live (background turns red when an invariant is violated). For demos, open that section before starting the test — green panels throughout the run are visual proof that the database is not the bottleneck.

## Metric methodology and data integrity

This is the audit reference for what each summary metric actually measures and how to defend it. Every summary CSV row has fields whose values come from different sources — they are not all equivalent statistics, even when the column names look similar.

### Summary metrics — provenance table

| Field in `summary_*.csv` | Source | Method |
|---|---|---|
| `avg_throughput_rps` | `k6-summary.txt` | true overall RPS = total requests / test duration |
| `avg_error_rate_percent` | `k6-summary.txt` | true overall error % across all requests |
| `avg_p50_ms`, `avg_p95_ms` | `k6-summary.txt` | **true population** percentiles across every request |
| `avg_p99_ms` (post-fix runs) | `k6-summary.txt` (k6 `summaryTrendStats` includes `p(99)`) | true population p99 |
| `avg_p99_ms` (pre-fix runs) | Prometheus | `max-over-time of avg(k6_http_req_duration_p99{...})` — see caveat below |
| `avg_web_memory_mb`, `avg_jobs_memory_mb` | Prometheus (`container_memory_working_set_bytes`) | sum across Running pods, time-averaged across the test |
| `max_hpa_cpu_percent` | Prometheus | max-over-time of `100 * sum(rate(cpu)) / sum(cpu_request)` |
| `max_web_restart_total`, `max_jobs_restart_total` | `k8s-snapshots.csv` | final cumulative restart count during the test |
| `max_vus` | Prometheus (`k6_vus`) | peak VU count seen |
| `scale_out_events`, `scale_in_events` | derived from `k8s-snapshots.csv` | count of `desiredReplicas` increases / decreases |
| `oscillation_count` | derived from `k8s-snapshots.csv` | number of times `desiredReplicas` direction reversed |
| `avg_scale_out_latency_seconds` | derived from `k8s-snapshots.csv` | time from desired-change to ready-replicas-reach-target |
| `peak_queue_depth`, `avg_queue_depth` | `jobs-queue.csv` | `count(*)` of pending `delayed_jobs` rows, polled every 5s |
| `peak_job_age_sec`, `avg_job_age_sec` | `jobs-queue.csv` | `now() - min(run_at)` of pending jobs **scheduled within the last hour** — long-stranded periodic jobs (Delayed::Periodic) are excluded so this reflects test-induced backlog, not pre-existing scheduled tasks |
| `peak_jobs_per_minute`, `avg_jobs_per_minute` | `jobs-queue.csv` | derived from diff of `pg_stat_user_tables.n_tup_del` for `delayed_jobs` |
| `total_jobs_processed` | `jobs-queue.csv` | last - first value of cumulative `n_tup_del` counter |
| `peak_postgres_cpu_millicores`, `peak_postgres_memory_mib` | `postgres-health.csv` | max of `kubectl top` samples during run |
| `peak_active_conns` | `postgres-health.csv` | max of `count(*) state='active'` from `pg_stat_activity` |
| `max_db_lock_waits`, `max_db_idle_in_tx` | `postgres-health.csv` | max counts from `pg_stat_activity` |
| `total_slow_queries_over_1s` | `postgres-health.csv` | max snapshot count of queries running > 1s |
| `min_cache_hit_ratio_percent` | `postgres-health.csv` | min observed `blks_hit / (blks_hit+blks_read)` × 100 |
| `peak_redis_cpu_millicores`, `peak_redis_memory_mb` | `redis-health.csv` | max of `kubectl top` and `INFO memory.used_memory` samples |
| `min_redis_hit_ratio_percent` | `redis-health.csv` | min **rolling-window** `keyspace_hits / (hits+misses)` × 100 — derived from per-sample deltas, *not* cumulative since pod start |
| `redis_evictions_total` | `redis-health.csv` | last value of `INFO stats.evicted_keys` cumulative counter |

### Time-series chart provenance

Per-run charts (e.g., `response_time_timeline.png`, `memory_long_stress.png`) and cross-run charts (`timeseries_*.png`) come from Prometheus queries — *not* from k6 summary text. For most metrics this is identical to the summary value (memory, CPU, throughput). For percentile latency it is **not**: the chart shows `avg-across-groups of windowed percentile`, which differs from k6's true-population percentile.

**Practical guidance**: read magnitudes from the summary CSV; use the chart for *shape* (when scaling reacted, oscillations, the timing of memory growth). Don't quote a number you measured by eye off a chart.

### Known data-integrity caveat — pre-fix `avg_p99_ms`

Runs created before the `summaryTrendStats: ['p(99)']` fix to `testing/load_test/canvas-load.js` cannot have a true-population p99 retroactively computed — k6's text summary doesn't contain it. The chart pipeline falls back to `max-over-time of avg(k6_http_req_duration_p99{testid=...})`, which is **not** the same statistic and can produce values lower than the row's `avg_p95_ms` when slow events are rare and scattered (e.g., Stage 3 cold starts).

To get fully comparable p99 across stages, re-run experiments after the fix. Older runs remain valid for `p50`, `p95`, throughput, error rate, memory, oscillation count, and all `k8s-snapshots.csv`-derived metrics.

### Sanity-check invariants

When reviewing a summary CSV, assert:

- `avg_p50_ms ≤ avg_p95_ms ≤ avg_p99_ms` — percentile monotonicity. Violation indicates a measurement-method mismatch.
- `avg_error_rate_percent` between 0 and 100.
- `oscillation_count ≥ 0`, `scale_out_events ≥ oscillation_count` (a reversal requires both a scale-out and a scale-in).
- `max_web_restart_total = 0` for any run that completed without OOMKills.

A failed invariant means the data needs investigation, not interpretation.

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

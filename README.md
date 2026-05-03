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

- `./deploy.sh baseline` — migrate DB, fixed replicas (web=1, jobs=1), no HPA. Used for **Stage 1**.
- `BASELINE_DISABLE_JOBS=true ./deploy.sh baseline` — same as baseline but scales `canvas-jobs` to `0`.
- `./deploy.sh prescaled` — migrate DB, fixed replicas (web=5, jobs=3), no HPA, VPA resources. Used for **Stage 3** to isolate "more pods" from "HPA-managed pods".
- `./deploy.sh hpa-naive` — migrate DB, HPA enabled with **stock** behavior (no `behavior:` block, Kubernetes defaults), VPA resources. Used for **Stage 4** — untuned HPA.
- `./deploy.sh hpa` — migrate DB, HPA enabled with **tuned** behavior (`stabilizationWindowSeconds`, capped scale rates), VPA resources. Used for **Stage 5** — tuned HPA.
- `./deploy.sh` — alias of `./deploy.sh hpa`.
- `./deploy.sh bootstrap` — initialize a fresh DB, then deploy with tuned HPA.

HPA manifests:

- `deployment/hpa.yaml` — tuned HPA used by `hpa` mode.
- `deployment/hpa-naive.yaml` — stock HPA used by `hpa-naive` mode (no `behavior:` block).

`hpa-naive` and `hpa` reuse the same `deployment-web.yaml` and `deployment-jobs.yaml` so cross-stage comparisons isolate HPA tuning from resource sizing.

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

## Thesis 5-stage experimental framework

Each stage isolates a single variable so cross-stage differences attribute cleanly to one cause:

| Stage | Mode | Variable changed vs previous | Question answered |
|-------|------|------------------------------|--------------------|
| 1 — baseline | `baseline` | — | What does the naive default do? |
| 2 — VPA profiling | `vpa-recommend.sh` | observe | What resources does the workload need? |
| 3 — prescaled | `prescaled` | resources right-sized + 5 fixed pods | Does over-provisioning solve it? |
| 4 — HPA-naive | `hpa-naive` | replace fixed pods with stock HPA | Does HPA alone help? |
| 5 — HPA-tuned | `hpa` | tune HPA `behavior:` block | Does HPA tuning help further? |

Stages 4 and 5 share resources and replica caps; only the HPA `behavior:` block differs. This lets you attribute Stage 4→5 deltas to tuning alone.

### Stage 1 — Baseline (1 web pod, naive resources, no HPA)

```bash
./deploy.sh baseline
SEED_PREFIX=thesis \
  RUNS_PER_SCENARIO=5 \
  MATRIX_MODES=baseline \
  MATRIX_SCENARIOS=long-stress \
  EXPERIMENT_NAME=stage1-baseline \
  COOLDOWN_SECONDS=300 \
  bash testing/run-experiment-matrix.sh
```

### Stage 2 — VPA profiling (observe-only, no autoscaling)

VPA in observe-only mode profiles the workload under load to recommend right-sized CPU and memory. See [VPA profiling](#vpa-profiling-stage-2) below for the procedure. Apply the recommended values to `deployment/deployment-web.yaml` and `deployment/deployment-jobs.yaml` before running Stage 3.

### Stage 3 — Prescaled (5 web + 3 jobs fixed, no HPA, VPA resources)

```bash
./deploy.sh prescaled
SEED_PREFIX=thesis \
  RUNS_PER_SCENARIO=5 \
  MATRIX_MODES=prescaled \
  MATRIX_SCENARIOS=long-stress \
  EXPERIMENT_NAME=stage3-prescaled \
  COOLDOWN_SECONDS=300 \
  SKIP_DEPLOY=true \
  bash testing/run-experiment-matrix.sh
```

### Stage 4 — HPA naive (stock HPA config, VPA resources)

```bash
./deploy.sh hpa-naive
SEED_PREFIX=thesis \
  RUNS_PER_SCENARIO=5 \
  MATRIX_MODES=hpa-naive \
  MATRIX_SCENARIOS=long-stress \
  EXPERIMENT_NAME=stage4-hpa-naive \
  COOLDOWN_SECONDS=300 \
  SKIP_DEPLOY=true \
  bash testing/run-experiment-matrix.sh
```

### Stage 5 — HPA tuned (tuned HPA behavior, VPA resources)

```bash
./deploy.sh hpa
SEED_PREFIX=thesis \
  RUNS_PER_SCENARIO=5 \
  MATRIX_MODES=hpa \
  MATRIX_SCENARIOS=long-stress \
  EXPERIMENT_NAME=stage5-hpa-tuned \
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
| `MATRIX_SCENARIOS=long-stress,breakpoint` | Comma-separated k6 profiles |
| `EXPERIMENT_NAME=stage5-hpa-tuned` | Prefix used in `test_id` and manifest filename |
| `FLUSH_REDIS_BETWEEN_RUNS=true` | Redis FLUSHALL before each run |

## VPA profiling (Stage 2)

VPA (Vertical Pod Autoscaler) observes resource usage of running pods and recommends sized requests/limits. We use it in **observe-only mode** — the recommendations are read once, then manually applied to deployment YAMLs.

```bash
# Install VPA components (one-time)
bash testing/vpa-recommend.sh install

# Apply observe-only VPA objects (defined in deployment/vpa-recommendation.yaml)
bash testing/vpa-recommend.sh apply

# Run real load while VPA observes (any of the test profiles works; long-stress is best)
TEST_TYPE=long-stress TEST_ID=stage2-vpa-profile bash testing/run-load-test.sh

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

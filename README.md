# canvas-k8s

Canvas LMS on a single-node k3s cluster with a Prometheus + Grafana monitoring
stack, designed for a three-stage autoscaling experiment (baseline →
breakpoint → HPA).

This README is the **demo playbook** — the commands you'll actually run. For
full setup-from-scratch, monitoring internals, the two-host SSH plumbing and
troubleshooting, see [`docs/REFERENCE.md`](docs/REFERENCE.md).

## URLs

| Service     | URL                                      | Credentials                        |
| ----------- | ---------------------------------------- | ---------------------------------- |
| Canvas web  | `http://canvas.io.vn`                    | `admin@canvas.local` / `Admin123!` |
| Grafana     | `http://canvas.io.vn:30091`              | `admin` / `admin`                  |
| Prometheus  | `http://canvas.io.vn:30090`              | —                                  |

## Cluster

```bash
./start-cluster.sh                       # start k3s
kubectl get nodes                        # cluster up
kubectl get pods -n canvas               # app pods ready
kubectl get pods -n canvas-monitoring    # observability stack ready
./create-admin-token.sh                  # mint a new API token (if needed)
```

## Switch system mode

Each mode maps to one thesis stage. **Patch resources first**, then deploy.

```bash
# Stage 1 — Baseline (naive resources, 1 web + 1 jobs, no HPA)
bash testing/patch-resources-to-naive.sh
./deploy.sh baseline

# Stage 2 — Breakpoint (VPA-tuned, 3 web + 2 jobs prescaled, no HPA)
bash testing/patch-resources-to-vpa.sh
./deploy.sh prescaled

# Stage 3 — HPA (VPA-tuned + HPA enabled, web 1–3 / jobs 1–2)
bash testing/patch-resources-to-vpa.sh
./deploy.sh hpa
```

Other `deploy.sh` modes:

- `./deploy.sh bootstrap` — fresh DB init + HPA deploy
- `./deploy.sh migrate` — run DB migrations + redeploy with HPA (alias `./deploy.sh`)

## Monitoring stack

```bash
bash testing/apply-monitoring.sh         # apply / update Prom + Grafana + cAdvisor + KSM
kubectl get pods -n canvas-monitoring
```

## Seed test data (once per fresh DB)

```bash
bash testing/run-seed-data.sh                       # default seed
SEED_PREFIX=mybatch bash testing/run-seed-data.sh   # custom prefix
bash testing/run-unseed-data.sh                     # wipe seed
```

## VPA recommendation (Stage 1 only)

```bash
bash testing/vpa-recommend.sh setup      # install recommender + apply VPA objects
kubectl describe vpa canvas-web-vpa -n canvas    # read recommendation after a load run
```

## Run a load test (from the load-generator host)

```bash
# Stage 1 — staircase 10/30/60 VU (~23 min)
EXPERIMENT_NAME=stage1-baseline TEST_TYPE=staircase \
  bash testing/run-load-test.sh

# Stage 2 — breakpoint ramp 0 → 200 VU (~18 min)
EXPERIMENT_NAME=stage2-breakpoint TEST_TYPE=breakpoint \
  bash testing/run-load-test.sh

# Stage 3 — staircase-tuned 10/30/60/70 VU + ramp-down tail (~38 min)
EXPERIMENT_NAME=stage3-hpa TEST_TYPE=staircase-tuned \
  bash testing/run-load-test.sh
```

Run folder lands at `testing/results/<experiment>-runNN-<timestamp>/` and is
auto-synced from the SUT after k6 finishes.

Run a 3-run batch (matrix mode):

```bash
RUNS_PER_SCENARIO=3 \
  MATRIX_MODES=hpa \
  MATRIX_SCENARIOS=staircase-tuned \
  EXPERIMENT_NAME=stage3-hpa \
  bash testing/run-experiment-matrix.sh
```

## Reset between runs

```bash
bash testing/reset-test-env.sh                                  # rolling restart web + jobs
FLUSH_REDIS_BETWEEN_RUNS=true bash testing/reset-test-env.sh    # + flush Redis cache
```

## Aggregate results across runs

```bash
EXPERIMENT_NAME=stage3-hpa PUSH_GIT=true \
  bash testing/aggregate-timeseries.sh
```

Output: `testing/results/analysis-<experiment>/` — five `timeseries_*.png`
cross-run charts (throughput + error, latency, CPU + replicas, memory,
jobs-queue) and `aggregate_stats_<experiment>.csv`.

## During the demo — quick checks

```bash
kubectl get pods -n canvas -w                          # live pod state
kubectl get hpa -n canvas -w                           # HPA replicas + current metric
kubectl top pods -n canvas                             # CPU / memory snapshot
kubectl logs -n canvas -l app=canvas-web --tail=50 -f  # tail web logs
```

If something looks stuck:

```bash
kubectl describe pod -n canvas <pod-name>              # show events
kubectl rollout status deployment/canvas-web -n canvas
```

---

Full reference (EC2 bootstrap from scratch, monitoring internals, two-host SSH
setup, troubleshooting): [`docs/REFERENCE.md`](docs/REFERENCE.md).

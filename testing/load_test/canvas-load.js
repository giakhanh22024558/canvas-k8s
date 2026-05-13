import http from "k6/http";
import { check, sleep } from "k6";

const baseUrl = (__ENV.BASE_URL || "http://canvas.io.vn").replace(/\/+$/, "");
const apiToken = __ENV.API_TOKEN || "";
const submissionApiToken = __ENV.SUBMISSION_API_TOKEN || "";
const loginEmail = __ENV.TEST_LOGIN_EMAIL || "";
const loginPassword = __ENV.TEST_LOGIN_PASSWORD || "";
const thinkTimeMin = Number(__ENV.THINK_TIME_MIN || 0.5);
const thinkTimeMax = Number(__ENV.THINK_TIME_MAX || 2.0);
const maxContextCourses = Number(__ENV.MAX_CONTEXT_COURSES || 8);
const profileName = (__ENV.TEST_TYPE || "load").toLowerCase();
const accountCoursePath = "/api/v1/accounts/self/courses";

const defaultThresholds = {
  http_req_failed: ["rate<0.05"],
  http_req_duration: ["p(95)<3000"],
};

const profilePresets = {
  smoke: { vus: 1, duration: "30s" },
  load: { vus: 10, duration: "5m" },
  stress: {
    stages: [
      { duration: "2m", target: 10 },
      { duration: "3m", target: 30 },
      { duration: "3m", target: 60 },
      { duration: "2m", target: 0 },
    ],
  },
  // staircase — ramp-and-hold load profile. Three discrete VU levels with
  // 5-min holds, giving HPA stable steady-state windows to converge on each
  // step. The shape (low → mid → high → ramp-down) lets us observe scale-out
  // latency and the cooldown stabilization-window behaviour separately. Total
  // ~23 min. "long-stress" is kept as a backward-compatible alias so old
  // experiment manifests still resolve.
  staircase: {
    // ORIGINAL staircase — 3 levels (10 / 30 / 60 VUs), 5-min holds, total
    // ~23 min. Designed before Stage 2 breakpoint data was available; its
    // ceiling of 60 VUs assumed the system saturated around that load.
    // Stage 2 200-VU breakpoint testing showed the actual saturation
    // thresholds are higher (peak RPS @ 70 VUs, SLO breach @ 145 VUs),
    // so this profile is now retained for backward compatibility with
    // Stage 1 reproducibility but does not stress the cluster's full
    // capacity envelope.
    stages: [
      { duration: "2m", target: 10 },
      { duration: "5m", target: 10 },
      { duration: "2m", target: 30 },
      { duration: "5m", target: 30 },
      { duration: "2m", target: 60 },
      { duration: "5m", target: 60 },
      { duration: "2m", target: 0 },
    ],
  },
  "staircase-tuned": {
    // TUNED staircase — 4 levels bracketing the throughput saturation
    // knee identified in Stage 2 (peak RPS at VU≈70 / minute 7 of the
    // breakpoint test). Caps at 70 VUs — exactly the saturation point —
    // because beyond this λ has already plateaued, and pushing further
    // adds no new HPA-specific information (the cluster is at
    // maxReplicas with Little's-Law-driven W growth, which Stage 2
    // already characterised in detail).
    //   L1 = 10 VUs  — idle baseline; HPA expected at minReplicas (1)
    //   L2 = 30 VUs  — mild load; HPA scale-up zone (per-pod CPU
    //                  approaches 80 % target → fires to ~3 pods)
    //   L3 = 60 VUs  — just below throughput knee
    //   L4 = 70 VUs  — at the throughput saturation knee (Stage 2 peak)
    // 5-min holds at each level (≥ HPA stabilizationWindow default of
    // 300 s) so HPA has at least one full evaluation cycle of clean
    // metrics at each plateau.
    //
    // SCALE-DOWN OBSERVATION TAIL: after L4 the load is ramped DOWN
    // slowly (10 min linear 70→10) instead of dropping straight to 0.
    // The slow descent exposes the HPA scale-down gradient — we get to
    // observe whether HPA reacts to the falling CPU smoothly or sits
    // pinned at maxReplicas waiting for the 5-min stabilization window.
    // A final 8-min hold at 10 VUs ensures we cross the stabilization
    // window with low utilisation and capture the actual scale-down
    // events (pod count returning to minReplicas).
    // Total: 46 min (4 ramps × 2 min + 4 holds × 5 min + 10 min slow
    // ramp-down + 8 min idle observation).
    stages: [
      { duration: "2m", target: 10 },
      { duration: "5m", target: 10 },
      { duration: "2m", target: 30 },
      { duration: "5m", target: 30 },
      { duration: "2m", target: 60 },
      { duration: "5m", target: 60 },
      { duration: "2m", target: 70 },
      { duration: "5m", target: 70 },
      { duration: "10m", target: 10 },
      { duration: "8m", target: 10 },
    ],
  },
  breakpoint: {
    // Reaches 200 VUs to push past the throughput knee observed in
    // Stage 2 (capped-at-100 run01 showed only graceful degradation).
    // Coarser steps below 100 because that region is already known
    // healthy; finer steps above (130, 160, 200) so the saturation
    // knee has more sample points. Kept in sync with the STAGES_JSON
    // default in testing/run-load-test.sh — see that file for the
    // rationale. 9 stages × 2 min = 18 min total.
    stages: [
      { duration: "2m", target: 20 },
      { duration: "2m", target: 40 },
      { duration: "2m", target: 60 },
      { duration: "2m", target: 80 },
      { duration: "2m", target: 100 },
      { duration: "2m", target: 130 },
      { duration: "2m", target: 160 },
      { duration: "2m", target: 200 },
      { duration: "2m", target: 0 },
    ],
  },
  soak: { vus: 15, duration: "30m" },
};

// Backward-compat alias map: old TEST_TYPE values mapped to their new names.
// Lets pre-existing scripts and experiment manifests keep working unchanged.
const profileAliases = {
  "long-stress": "staircase",
};

function buildOptions() {
  const resolvedName = profileAliases[profileName] || profileName;
  const preset = profilePresets[resolvedName] || profilePresets.load;
  const options = {
    thresholds: defaultThresholds,
    setupTimeout: "120s",
    // Include p(99) so it appears in k6's end-of-test summary text. The
    // summary parser in plot_prometheus.py reads this for avg_p99_ms.
    // Without this, p99 falls back to averaging Prometheus windowed values,
    // which produces an apples-to-oranges comparison with p95 (true
    // population statistic) and can yield p99 < p95 for workloads with rare
    // tail spikes (cold starts, occasional GC pauses, etc.).
    summaryTrendStats: ["avg", "min", "med", "max", "p(90)", "p(95)", "p(99)"],
  };

  if (__ENV.STAGES_JSON) {
    try {
      options.stages = JSON.parse(__ENV.STAGES_JSON);
    } catch (_error) {
      if (preset.stages) {
        options.stages = preset.stages;
      }
    }
  } else if (preset.stages) {
    options.stages = preset.stages;
  } else {
    options.vus = Number(__ENV.VUS || preset.vus);
    options.duration = __ENV.DURATION || preset.duration;
  }

  return options;
}

export const options = buildOptions();

function authHeaders(token = apiToken) {
  const headers = {
    Accept: "application/json",
  };

  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }

  return headers;
}

function safeJson(response) {
  try {
    return response.json();
  } catch (_error) {
    return null;
  }
}

function buildQuery(params) {
  return Object.entries(params)
    .map(([key, value]) => `${encodeURIComponent(key)}=${encodeURIComponent(String(value))}`)
    .join("&");
}

function logFailure(label, response) {
  const bodyPreview = (response.body || "").slice(0, 200).replace(/\s+/g, " ");
  console.error(
    `[${label}] ${response.request.method} ${response.request.url} returned ${response.status}. ` +
      `Body preview: ${bodyPreview}`
  );
}

function getJson(path, params = {}, token = apiToken) {
  const query = buildQuery(params);
  const url = query ? `${baseUrl}${path}?${query}` : `${baseUrl}${path}`;
  const response = http.get(url, { headers: authHeaders(token) });
  const ok = check(response, {
    [`${path} status is 200`]: (res) => res.status === 200,
  });

  if (!ok) {
    logFailure(path, response);
    return [];
  }

  const parsed = safeJson(response);
  return Array.isArray(parsed) ? parsed : [];
}

function setupCourseContext() {
  const courses = getJson(accountCoursePath, {
    per_page: maxContextCourses,
    enrollment_state: "active",
  });

  return courses.slice(0, maxContextCourses).map((course) => {
    const assignments = getJson(`/api/v1/courses/${course.id}/assignments`, { per_page: 10 });
    const quizzes = getJson(`/api/v1/courses/${course.id}/quizzes`, { per_page: 10 });

    return {
      id: course.id,
      name: course.name,
      assignmentIds: assignments.map((assignment) => assignment.id),
      quizIds: quizzes.map((quiz) => quiz.id),
    };
  });
}

export function setup() {
  return {
    courses: setupCourseContext(),
  };
}

function chooseRandom(items) {
  if (!items || items.length === 0) {
    return null;
  }

  return items[Math.floor(Math.random() * items.length)];
}

function chooseOperation(context) {
  const operations = [
    "assignments",
    "courseList",
    "modules",
    "quizzes",
  ];

  if (loginEmail && loginPassword) {
    operations.push("login");
  }

  if (submissionApiToken && context.courses.some((course) => course.assignmentIds.length > 0)) {
    operations.push("submitAssignment");
  }

  return chooseRandom(operations);
}

function requestAssignments(course) {
  const response = http.get(`${baseUrl}/api/v1/courses/${course.id}/assignments?per_page=10`, {
    headers: authHeaders(),
  });
  check(response, {
    "assignments status is 200": (res) => res.status === 200,
  }) || logFailure("assignments", response);
}

function requestCourseList() {
  const response = http.get(`${baseUrl}${accountCoursePath}?per_page=20&enrollment_state=active`, {
    headers: authHeaders(),
  });
  check(response, {
    "courses status is 200": (res) => res.status === 200,
  }) || logFailure("courses", response);
}

function requestModules(course) {
  const response = http.get(`${baseUrl}/api/v1/courses/${course.id}/modules?per_page=10`, {
    headers: authHeaders(),
  });
  check(response, {
    "modules status is 200": (res) => res.status === 200,
  }) || logFailure("modules", response);
}

function requestQuizzes(course) {
  const response = http.get(`${baseUrl}/api/v1/courses/${course.id}/quizzes?per_page=10`, {
    headers: authHeaders(),
  });
  check(response, {
    "quizzes status is 200": (res) => res.status === 200,
  }) || logFailure("quizzes", response);
}

function submitAssignment(course) {
  const assignmentId = chooseRandom(course.assignmentIds);
  if (!assignmentId) {
    requestCourseList();
    return;
  }

  const payload = {
    "submission[submission_type]": "online_text_entry",
    "submission[body]": `Load-test submission from VU ${__VU} iteration ${__ITER}`,
  };

  const response = http.post(
    `${baseUrl}/api/v1/courses/${course.id}/assignments/${assignmentId}/submissions`,
    payload,
    {
      headers: {
        ...authHeaders(submissionApiToken),
        "Content-Type": "application/x-www-form-urlencoded",
      },
    }
  );

  check(response, {
    "submission status is successful": (res) => [200, 201].includes(res.status),
  }) || logFailure("submission", response);
}

function extractAuthenticityToken(body) {
  const match = body.match(/name="authenticity_token"\s+value="([^"]+)"/);
  return match ? match[1] : "";
}

function loginCanvas() {
  const loginPage = http.get(`${baseUrl}/login/canvas`);
  const authenticityToken = extractAuthenticityToken(loginPage.body || "");

  check(loginPage, {
    "login page available": (res) => res.status === 200,
    "login token found": () => Boolean(authenticityToken),
  }) || logFailure("login-page", loginPage);

  if (!authenticityToken) {
    return;
  }

  const response = http.post(
    `${baseUrl}/login/canvas`,
    {
      authenticity_token: authenticityToken,
      "pseudonym_session[unique_id]": loginEmail,
      "pseudonym_session[password]": loginPassword,
    },
    {
      redirects: 0,
      headers: {
        Accept: "text/html,application/xhtml+xml",
        "Content-Type": "application/x-www-form-urlencoded",
      },
    }
  );

  check(response, {
    "login accepted": (res) => [302, 303].includes(res.status),
  }) || logFailure("login-post", response);
}

export default function (context) {
  const course = chooseRandom(context.courses);
  const operation = chooseOperation(context);

  switch (operation) {
    case "assignments":
      if (course) {
        requestAssignments(course);
      } else {
        requestCourseList();
      }
      break;
    case "courseList":
      requestCourseList();
      break;
    case "modules":
      if (course) {
        requestModules(course);
      } else {
        requestCourseList();
      }
      break;
    case "quizzes":
      if (course) {
        requestQuizzes(course);
      } else {
        requestCourseList();
      }
      break;
    case "submitAssignment":
      if (course) {
        submitAssignment(course);
      } else {
        requestCourseList();
      }
      break;
    case "login":
      loginCanvas();
      break;
    default:
      requestCourseList();
  }

  sleep(Math.random() * (thinkTimeMax - thinkTimeMin) + thinkTimeMin);
}

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
  "long-stress": {
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
  breakpoint: {
    stages: [
      { duration: "2m", target: 10 },
      { duration: "2m", target: 20 },
      { duration: "2m", target: 30 },
      { duration: "2m", target: 40 },
      { duration: "2m", target: 50 },
      { duration: "2m", target: 60 },
      { duration: "2m", target: 80 },
      { duration: "2m", target: 100 },
      { duration: "2m", target: 0 },
    ],
  },
  soak: { vus: 15, duration: "30m" },
};

function buildOptions() {
  const preset = profilePresets[profileName] || profilePresets.load;
  const options = { thresholds: defaultThresholds, setupTimeout: "120s" };

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
    "dashboardCards",
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

function requestDashboardCards() {
  const response = http.get(`${baseUrl}/api/v1/dashboard/dashboard_cards`, {
    headers: authHeaders(),
  });
  check(response, {
    "dashboard cards status is 200": (res) => res.status === 200,
  }) || logFailure("dashboard-cards", response);
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
    case "dashboardCards":
      requestDashboardCards();
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

import http from "k6/http";
import { check, sleep } from "k6";

const acceptableStatuses = http.expectedStatuses(200);

export const options = {
  vus: Number(__ENV.VUS || 10),
  duration: __ENV.DURATION || "1m",
  thresholds: {
    http_req_failed: ["rate<0.05"],
    http_req_duration: ["p(95)<3000"],
  },
};

const baseUrl = __ENV.BASE_URL || "http://canvas.io.vn";
const apiToken = __ENV.API_TOKEN || "";

export default function () {
  const headers = {
    Accept: "application/json",
  };

  if (apiToken) {
    headers.Authorization = `Bearer ${apiToken}`;
  }

  const res = http.get(`${baseUrl}/api/v1/courses`, {
    headers,
    responseCallback: acceptableStatuses,
  });

  if (res.status !== 200) {
    const bodyPreview = (res.body || "").slice(0, 200).replace(/\s+/g, " ");
    console.error(
      `[load-test] GET /api/v1/courses returned ${res.status} for ${baseUrl}. ` +
        `Auth header present=${Boolean(apiToken)}. Body preview: ${bodyPreview}`
    );
  }

  check(res, {
    "status is 200": (r) => r.status === 200,
  });

  sleep(1);
}

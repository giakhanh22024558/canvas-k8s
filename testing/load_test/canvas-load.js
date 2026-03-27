import http from "k6/http";
import { check, sleep } from "k6";

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

  const res = http.get(`${baseUrl}/api/v1/courses`, { headers });

  check(res, {
    "status is acceptable": (r) => [200, 401].includes(r.status),
  });

  sleep(1);
}

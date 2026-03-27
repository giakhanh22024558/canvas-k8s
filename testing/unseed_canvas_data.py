#!/usr/bin/env python3
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request


def env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise SystemExit(f"{name} must be an integer, got {raw!r}") from exc
    if value < 1:
        raise SystemExit(f"{name} must be >= 1, got {value}")
    return value


BASE_URL = os.environ.get("BASE_URL", "http://canvas.io.vn").rstrip("/")
API_TOKEN = os.environ.get("API_TOKEN", "").strip()
SEED_PREFIX = os.environ.get("SEED_PREFIX", "").strip()
ACCOUNT_ID = os.environ.get("ACCOUNT_ID", "self")
REQUEST_TIMEOUT = env_int("REQUEST_TIMEOUT", 30)
PER_PAGE = env_int("PER_PAGE", 100)


def require_inputs() -> None:
    if not API_TOKEN:
        raise SystemExit("API_TOKEN is required")
    if not SEED_PREFIX:
        raise SystemExit("SEED_PREFIX is required")


def api_request(method: str, path: str, params=None):
    url = f"{BASE_URL}{path}"
    body = None
    headers = {
        "Authorization": f"Bearer {API_TOKEN}",
        "Accept": "application/json",
    }

    if method.upper() == "GET" and params:
        url = f"{url}?{urllib.parse.urlencode(params, doseq=True)}"
    elif params is not None:
        body = urllib.parse.urlencode(params, doseq=True).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"

    req = urllib.request.Request(url, data=body, method=method.upper(), headers=headers)

    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method.upper()} {path} failed: HTTP {exc.code} {details}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"{method.upper()} {path} failed: {exc.reason}") from exc


def list_all(path: str, params):
    page = 1
    items = []
    while True:
        batch = api_request("GET", path, {**params, "page": page, "per_page": PER_PAGE})
        if not batch:
            break
        if isinstance(batch, dict):
            break
        items.extend(batch)
        if len(batch) < PER_PAGE:
            break
        page += 1
    return items


def seed_user_match(user):
    prefix = f"{SEED_PREFIX}-"
    fields = [
        str(user.get("login_id", "")),
        str(user.get("sis_user_id", "")),
        str(user.get("name", "")),
        str(user.get("sortable_name", "")),
        str(user.get("short_name", "")),
        str(user.get("primary_email", "")),
        str(user.get("email", "")),
    ]
    return any(prefix in field for field in fields)


def seed_course_match(course):
    prefix = SEED_PREFIX.upper()
    fields = [
        str(course.get("name", "")),
        str(course.get("course_code", "")),
        str(course.get("sis_course_id", "")),
    ]
    return any(prefix in field for field in fields)


def find_seed_courses():
    search_term = SEED_PREFIX[:8].upper()
    courses = list_all(
        f"/api/v1/accounts/{ACCOUNT_ID}/courses",
        {
            "search_term": search_term,
            "include[]": ["term", "teachers"],
        },
    )
    return [course for course in courses if seed_course_match(course)]


def find_seed_users():
    users = list_all(
        f"/api/v1/accounts/{ACCOUNT_ID}/users",
        {
            "search_term": SEED_PREFIX,
            "include[]": ["email"],
        },
    )
    return [user for user in users if seed_user_match(user)]


def delete_course(course):
    api_request("DELETE", f"/api/v1/courses/{course['id']}", {"event": "delete"})


def delete_user(user):
    api_request("DELETE", f"/api/v1/accounts/{ACCOUNT_ID}/users/{user['id']}")


def main():
    require_inputs()

    courses = find_seed_courses()
    users = find_seed_users()

    deleted_courses = []
    deleted_users = []

    for course in courses:
        delete_course(course)
        deleted_courses.append({"id": course["id"], "name": course.get("name", "")})
        print(f"Deleted course {course['id']}: {course.get('name', '')}", flush=True)

    for user in users:
        delete_user(user)
        deleted_users.append({"id": user["id"], "name": user.get("name", "")})
        print(f"Deleted user {user['id']}: {user.get('name', '')}", flush=True)

    summary = {
        "seed_prefix": SEED_PREFIX,
        "deleted_course_count": len(deleted_courses),
        "deleted_user_count": len(deleted_users),
        "base_url": BASE_URL,
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)

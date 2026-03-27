#!/usr/bin/env python3
import json
import os
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone


FIRST_NAMES = [
    "Alex",
    "Avery",
    "Bailey",
    "Cameron",
    "Casey",
    "Dakota",
    "Emerson",
    "Harper",
    "Jordan",
    "Kai",
    "Logan",
    "Morgan",
    "Parker",
    "Quinn",
    "Reese",
    "Riley",
    "Rowan",
    "Sawyer",
    "Taylor",
    "Sydney",
]

LAST_NAMES = [
    "Anderson",
    "Bennett",
    "Carter",
    "Diaz",
    "Foster",
    "Garcia",
    "Hayes",
    "Kelly",
    "Lee",
    "Martinez",
    "Nguyen",
    "Patel",
    "Reed",
    "Robinson",
    "Singh",
    "Taylor",
    "Tran",
    "Walker",
    "Young",
    "Zhang",
]

SUBJECTS = [
    "Algebra",
    "Biology",
    "Business Writing",
    "Chemistry",
    "Computer Science",
    "Data Analytics",
    "Economics",
    "English Composition",
    "History",
    "Physics",
    "Psychology",
    "Statistics",
]

TERMS = ["Spring", "Summer", "Fall"]

ASSIGNMENT_TYPES = [
    "Reflection",
    "Lab Report",
    "Quiz",
    "Project Milestone",
    "Reading Response",
    "Case Study",
    "Peer Review",
    "Problem Set",
]

PAGE_TOPICS = [
    "Welcome",
    "Weekly Agenda",
    "Study Guide",
    "FAQ",
    "Exam Prep",
    "Office Hours",
]

DISCUSSION_TOPICS = [
    "Introductions",
    "Week 1 Discussion",
    "Midterm Review",
    "Project Ideas",
    "Final Reflection",
]


def env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise SystemExit(f"{name} must be an integer, got {raw!r}") from exc
    if value < 0:
        raise SystemExit(f"{name} must be >= 0, got {value}")
    return value


BASE_URL = os.environ.get("BASE_URL", "http://canvas.io.vn:30080").rstrip("/")
API_TOKEN = os.environ.get("API_TOKEN", "").strip()
SEED_PREFIX = os.environ.get("SEED_PREFIX", f"lt-{int(time.time())}")
ACCOUNT_ID = os.environ.get("ACCOUNT_ID", "self")
PASSWORD = os.environ.get("SEED_PASSWORD", "ChangeMe123!")
COURSE_COUNT = env_int("COURSE_COUNT", 12)
TEACHER_POOL_SIZE = env_int("TEACHER_POOL_SIZE", 8)
STUDENT_POOL_SIZE = env_int("STUDENT_POOL_SIZE", 250)
TEACHERS_PER_COURSE = env_int("TEACHERS_PER_COURSE", 2)
STUDENTS_PER_COURSE = env_int("STUDENTS_PER_COURSE", 40)
ASSIGNMENTS_PER_COURSE = env_int("ASSIGNMENTS_PER_COURSE", 8)
PAGES_PER_COURSE = env_int("PAGES_PER_COURSE", 4)
DISCUSSIONS_PER_COURSE = env_int("DISCUSSIONS_PER_COURSE", 3)
REQUEST_TIMEOUT = env_int("REQUEST_TIMEOUT", 30)
RANDOM_SEED = env_int("RANDOM_SEED", 42)


def require_token() -> None:
    if not API_TOKEN:
        raise SystemExit("API_TOKEN is required")


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
        encoded = urllib.parse.urlencode(params, doseq=True).encode("utf-8")
        body = encoded
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


def pick_name(index: int):
    first = FIRST_NAMES[index % len(FIRST_NAMES)]
    last = LAST_NAMES[(index // len(FIRST_NAMES)) % len(LAST_NAMES)]
    return f"{first} {last}"


def make_login(role: str, index: int) -> str:
    return f"{SEED_PREFIX}-{role}-{index:03d}@seed.local"


def create_user(role: str, index: int):
    name = pick_name(index)
    login = make_login(role, index)
    params = {
        "user[name]": f"{name} ({role.title()} {index:03d})",
        "pseudonym[unique_id]": login,
        "pseudonym[password]": PASSWORD,
        "communication_channel[type]": "email",
        "communication_channel[address]": login,
        "communication_channel[skip_confirmation]": "true",
    }
    data = api_request("POST", f"/api/v1/accounts/{ACCOUNT_ID}/users", params)
    return {
        "id": data["id"],
        "name": data.get("name", params["user[name]"]),
        "login": login,
    }


def create_course(index: int):
    subject = SUBJECTS[index % len(SUBJECTS)]
    term = TERMS[index % len(TERMS)]
    year = 2026 + (index // len(TERMS))
    course_name = f"{subject} {term} {year} {SEED_PREFIX.upper()}-{index + 1:02d}"
    course_code = f"{subject[:4].upper()}{index + 1:03d}-{SEED_PREFIX[:8].upper()}"
    params = {
        "course[name]": course_name,
        "course[course_code]": course_code,
        "offer": "true",
    }
    data = api_request("POST", f"/api/v1/accounts/{ACCOUNT_ID}/courses", params)
    return {
        "id": data["id"],
        "name": data.get("name", course_name),
        "code": data.get("course_code", course_code),
        "subject": subject,
    }


def enroll_user(course_id: int, user_id: int, enrollment_type: str):
    params = {
        "enrollment[user_id]": str(user_id),
        "enrollment[type]": enrollment_type,
        "enrollment[enrollment_state]": "active",
        "notify": "false",
    }
    api_request("POST", f"/api/v1/courses/{course_id}/enrollments", params)


def create_assignment(course_id: int, course_name: str, index: int, due_at: datetime):
    label = ASSIGNMENT_TYPES[index % len(ASSIGNMENT_TYPES)]
    params = {
        "assignment[name]": f"{label} {index + 1}",
        "assignment[description]": f"<p>{course_name} {label.lower()} for week {index + 1}.</p>",
        "assignment[points_possible]": str(10 + ((index % 5) * 10)),
        "assignment[published]": "true",
        "assignment[due_at]": due_at.isoformat().replace("+00:00", "Z"),
        "assignment[submission_types][]": "online_text_entry",
    }
    api_request("POST", f"/api/v1/courses/{course_id}/assignments", params)


def create_page(course_id: int, course_name: str, index: int):
    topic = PAGE_TOPICS[index % len(PAGE_TOPICS)]
    params = {
        "wiki_page[title]": f"{topic} {index + 1}",
        "wiki_page[body]": (
            f"<h2>{topic}</h2><p>{course_name} resources, timelines, and next steps.</p>"
        ),
        "wiki_page[published]": "true",
    }
    api_request("POST", f"/api/v1/courses/{course_id}/pages", params)


def create_discussion(course_id: int, course_name: str, index: int):
    topic = DISCUSSION_TOPICS[index % len(DISCUSSION_TOPICS)]
    params = {
        "title": f"{topic} {index + 1}",
        "message": f"<p>{course_name}: share your thoughts on {topic.lower()}.</p>",
        "published": "true",
    }
    api_request("POST", f"/api/v1/courses/{course_id}/discussion_topics", params)


def choose_unique(rng: random.Random, pool, count: int):
    if not pool or count == 0:
        return []
    if count >= len(pool):
        return list(pool)
    return rng.sample(pool, count)


def main():
    require_token()
    rng = random.Random(RANDOM_SEED)

    teacher_pool = [create_user("teacher", i + 1) for i in range(TEACHER_POOL_SIZE)]
    student_pool = [create_user("student", i + 1) for i in range(STUDENT_POOL_SIZE)]

    created_courses = []
    start = datetime.now(timezone.utc)

    for course_index in range(COURSE_COUNT):
        course = create_course(course_index)
        created_courses.append(course)

        teachers = choose_unique(rng, teacher_pool, TEACHERS_PER_COURSE)
        students = choose_unique(rng, student_pool, STUDENTS_PER_COURSE)

        for teacher in teachers:
            enroll_user(course["id"], teacher["id"], "TeacherEnrollment")
        for student in students:
            enroll_user(course["id"], student["id"], "StudentEnrollment")

        for assignment_index in range(ASSIGNMENTS_PER_COURSE):
            due_at = start + timedelta(days=(assignment_index + 1) * 7 + course_index)
            create_assignment(course["id"], course["name"], assignment_index, due_at)

        for page_index in range(PAGES_PER_COURSE):
            create_page(course["id"], course["name"], page_index)

        for discussion_index in range(DISCUSSIONS_PER_COURSE):
            create_discussion(course["id"], course["name"], discussion_index)

        print(
            f"Seeded course {course_index + 1}/{COURSE_COUNT}: "
            f"{course['name']} with {len(teachers)} teachers and {len(students)} students",
            flush=True,
        )

    summary = {
        "seed_prefix": SEED_PREFIX,
        "teacher_pool_size": len(teacher_pool),
        "student_pool_size": len(student_pool),
        "courses_created": len(created_courses),
        "teachers_per_course": TEACHERS_PER_COURSE,
        "students_per_course": STUDENTS_PER_COURSE,
        "assignments_per_course": ASSIGNMENTS_PER_COURSE,
        "pages_per_course": PAGES_PER_COURSE,
        "discussions_per_course": DISCUSSIONS_PER_COURSE,
        "base_url": BASE_URL,
    }

    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)

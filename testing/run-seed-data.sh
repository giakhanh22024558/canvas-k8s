#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"
load_testing_env

BASE_URL="${BASE_URL:-http://canvas.io.vn}"

if command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
  PYTHON_BIN="python"
else
  echo "python3 or python is required"
  exit 1
fi

choose_profile() {
  echo "Choose a seed dataset size:"
  echo "1) Small"
  echo "2) Medium"
  echo "3) Large"
  read -r -p "Enter choice [1-3]: " profile_choice

  case "$profile_choice" in
    1)
      export COURSE_COUNT="${COURSE_COUNT:-2}"
      export TEACHER_POOL_SIZE="${TEACHER_POOL_SIZE:-2}"
      export STUDENT_POOL_SIZE="${STUDENT_POOL_SIZE:-10}"
      export TEACHERS_PER_COURSE="${TEACHERS_PER_COURSE:-1}"
      export STUDENTS_PER_COURSE="${STUDENTS_PER_COURSE:-5}"
      export ASSIGNMENTS_PER_COURSE="${ASSIGNMENTS_PER_COURSE:-2}"
      export PAGES_PER_COURSE="${PAGES_PER_COURSE:-1}"
      export DISCUSSIONS_PER_COURSE="${DISCUSSIONS_PER_COURSE:-1}"
      ;;
    2)
      export COURSE_COUNT="${COURSE_COUNT:-12}"
      export TEACHER_POOL_SIZE="${TEACHER_POOL_SIZE:-8}"
      export STUDENT_POOL_SIZE="${STUDENT_POOL_SIZE:-250}"
      export TEACHERS_PER_COURSE="${TEACHERS_PER_COURSE:-2}"
      export STUDENTS_PER_COURSE="${STUDENTS_PER_COURSE:-40}"
      export ASSIGNMENTS_PER_COURSE="${ASSIGNMENTS_PER_COURSE:-8}"
      export PAGES_PER_COURSE="${PAGES_PER_COURSE:-4}"
      export DISCUSSIONS_PER_COURSE="${DISCUSSIONS_PER_COURSE:-3}"
      ;;
    3)
      export COURSE_COUNT="${COURSE_COUNT:-20}"
      export TEACHER_POOL_SIZE="${TEACHER_POOL_SIZE:-15}"
      export STUDENT_POOL_SIZE="${STUDENT_POOL_SIZE:-600}"
      export TEACHERS_PER_COURSE="${TEACHERS_PER_COURSE:-3}"
      export STUDENTS_PER_COURSE="${STUDENTS_PER_COURSE:-80}"
      export ASSIGNMENTS_PER_COURSE="${ASSIGNMENTS_PER_COURSE:-10}"
      export PAGES_PER_COURSE="${PAGES_PER_COURSE:-5}"
      export DISCUSSIONS_PER_COURSE="${DISCUSSIONS_PER_COURSE:-4}"
      ;;
    *)
      echo "Invalid choice: $profile_choice"
      exit 1
      ;;
  esac
}

if [[ -z "${COURSE_COUNT:-}" ]]; then
  choose_profile
fi

if [[ -z "${API_TOKEN:-}" ]]; then
  read -r -s -p "Enter Canvas API token: " API_TOKEN
  echo
  export API_TOKEN
fi

if [[ -z "${SEED_PREFIX:-}" ]]; then
  default_prefix="lt-$(date +%Y%m%d-%H%M%S)"
  read -r -p "Enter seed prefix [$default_prefix]: " SEED_PREFIX
  export SEED_PREFIX="${SEED_PREFIX:-$default_prefix}"
fi

echo "Seeding Canvas data with prefix: $SEED_PREFIX"
echo "Base URL: $BASE_URL"
echo "Courses: ${COURSE_COUNT:-default}, Teachers: ${TEACHER_POOL_SIZE:-default}, Students: ${STUDENT_POOL_SIZE:-default}"

"$PYTHON_BIN" "$SCRIPT_DIR/seed_canvas_data.py"

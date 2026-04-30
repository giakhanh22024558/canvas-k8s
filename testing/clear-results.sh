#!/bin/bash
# clear-results.sh — safely remove test result folders
#
# Usage:
#   bash testing/clear-results.sh                     # interactive — pick what to delete
#   bash testing/clear-results.sh --all               # delete every run
#   bash testing/clear-results.sh --type breakpoint   # delete all runs of a test type
#   bash testing/clear-results.sh --id canvas-20260420-164022  # delete one specific run
#   bash testing/clear-results.sh --dry-run --all     # preview without deleting

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RESULTS_DIR="${RESULTS_DIR:-$SCRIPT_DIR/results}"

# ── colour helpers ────────────────────────────────────────────────────────────
RED='\033[0;31m'; YELLOW='\033[1;33m'; GREEN='\033[0;32m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

# ── argument parsing ──────────────────────────────────────────────────────────
MODE=""          # all | type | id | interactive
FILTER_TYPE=""
FILTER_ID=""
DRY_RUN=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --all)       MODE="all" ;;
    --type)      MODE="type";  FILTER_TYPE="${2:-}"; shift ;;
    --id)        MODE="id";    FILTER_ID="${2:-}";   shift ;;
    --dry-run)   DRY_RUN=true ;;
    -h|--help)
      echo "Usage: bash testing/clear-results.sh [OPTIONS]"
      echo ""
      echo "Options:"
      echo "  (no flags)          Interactive mode — choose what to delete"
      echo "  --all               Delete every result folder"
      echo "  --type <test_type>  Delete all runs of a specific type"
      echo "                      Types: smoke, load, stress, long-stress, breakpoint, soak"
      echo "  --id <test_id>      Delete one specific run folder"
      echo "  --dry-run           Preview what would be deleted without actually deleting"
      echo "  -h, --help          Show this help"
      exit 0
      ;;
    *) echo "Unknown option: $1. Use --help for usage."; exit 1 ;;
  esac
  shift
done

# ── guard: results dir must exist ─────────────────────────────────────────────
if [[ ! -d "$RESULTS_DIR" ]]; then
  echo -e "${YELLOW}Results directory not found: $RESULTS_DIR${RESET}"
  exit 0
fi

# ── helpers ───────────────────────────────────────────────────────────────────
get_test_type() {
  local dir="$1"
  local meta="$dir/metadata.env"
  if [[ -f "$meta" ]]; then
    grep -m1 "^test_type=" "$meta" 2>/dev/null | cut -d= -f2 || echo "unknown"
  else
    echo "unknown"
  fi
}

get_started_at() {
  local dir="$1"
  local meta="$dir/metadata.env"
  if [[ -f "$meta" ]]; then
    grep -m1 "^started_at=" "$meta" 2>/dev/null | cut -d= -f2 || echo ""
  else
    echo ""
  fi
}

folder_size() {
  du -sh "$1" 2>/dev/null | cut -f1
}

delete_folder() {
  local dir="$1"
  local id
  id="$(basename "$dir")"
  if [[ "$DRY_RUN" == true ]]; then
    echo -e "  ${CYAN}[dry-run]${RESET} would delete: $id"
  else
    rm -rf "$dir"
    echo -e "  ${GREEN}deleted:${RESET} $id"
  fi
}

# ── build list of all run folders ─────────────────────────────────────────────
mapfile -t ALL_RUNS < <(find "$RESULTS_DIR" -mindepth 1 -maxdepth 1 -type d | sort)

if [[ ${#ALL_RUNS[@]} -eq 0 ]]; then
  echo -e "${GREEN}Results folder is already empty.${RESET}"
  exit 0
fi

# ── interactive mode: show menu if no flags given ─────────────────────────────
if [[ -z "$MODE" ]]; then
  echo -e "${BOLD}Available test runs:${RESET}"
  echo ""
  printf "  %-35s %-15s %-22s %s\n" "TEST ID" "TYPE" "STARTED AT" "SIZE"
  printf "  %-35s %-15s %-22s %s\n" "-------" "----" "----------" "----"
  for dir in "${ALL_RUNS[@]}"; do
    id="$(basename "$dir")"
    type="$(get_test_type "$dir")"
    started="$(get_started_at "$dir")"
    size="$(folder_size "$dir")"
    printf "  %-35s %-15s %-22s %s\n" "$id" "$type" "${started:-n/a}" "$size"
  done
  echo ""
  echo -e "${BOLD}What do you want to clear?${RESET}"
  echo "  1) All results"
  echo "  2) All results of a specific test type"
  echo "  3) One specific run"
  echo "  4) Cancel"
  echo ""
  read -rp "Choice [1-4]: " CHOICE

  case "$CHOICE" in
    1) MODE="all" ;;
    2)
      MODE="type"
      read -rp "Test type (smoke/load/stress/long-stress/breakpoint/soak): " FILTER_TYPE
      ;;
    3)
      MODE="id"
      read -rp "Test ID (e.g. canvas-20260420-164022): " FILTER_ID
      ;;
    4|*) echo "Cancelled."; exit 0 ;;
  esac
fi

# ── build target list based on mode ──────────────────────────────────────────
TARGETS=()

case "$MODE" in
  all)
    TARGETS=("${ALL_RUNS[@]}")
    ;;
  type)
    if [[ -z "$FILTER_TYPE" ]]; then
      echo -e "${RED}Error: --type requires a test type value.${RESET}"; exit 1
    fi
    for dir in "${ALL_RUNS[@]}"; do
      if [[ "$(get_test_type "$dir")" == "$FILTER_TYPE" ]]; then
        TARGETS+=("$dir")
      fi
    done
    ;;
  id)
    if [[ -z "$FILTER_ID" ]]; then
      echo -e "${RED}Error: --id requires a test ID value.${RESET}"; exit 1
    fi
    TARGET_PATH="$RESULTS_DIR/$FILTER_ID"
    if [[ ! -d "$TARGET_PATH" ]]; then
      echo -e "${RED}Error: Run not found: $FILTER_ID${RESET}"; exit 1
    fi
    TARGETS=("$TARGET_PATH")
    ;;
esac

if [[ ${#TARGETS[@]} -eq 0 ]]; then
  echo -e "${YELLOW}No matching runs found.${RESET}"
  exit 0
fi

# ── confirm before deleting ───────────────────────────────────────────────────
echo ""
echo -e "${BOLD}The following runs will be deleted:${RESET}"
TOTAL_SIZE=0
for dir in "${TARGETS[@]}"; do
  id="$(basename "$dir")"
  type="$(get_test_type "$dir")"
  size="$(folder_size "$dir")"
  echo -e "  ${RED}✗${RESET}  $id  ${CYAN}[$type]${RESET}  $size"
done
echo ""

if [[ "$DRY_RUN" == true ]]; then
  echo -e "${CYAN}Dry-run mode — nothing will actually be deleted.${RESET}"
else
  read -rp "$(echo -e "${YELLOW}Confirm delete ${#TARGETS[@]} run(s)? [y/N]: ${RESET}")" CONFIRM
  if [[ "${CONFIRM,,}" != "y" ]]; then
    echo "Cancelled."
    exit 0
  fi
fi

# ── delete ────────────────────────────────────────────────────────────────────
echo ""
for dir in "${TARGETS[@]}"; do
  delete_folder "$dir"
done

echo ""
if [[ "$DRY_RUN" == true ]]; then
  echo -e "${CYAN}Dry-run complete. ${#TARGETS[@]} run(s) would have been deleted.${RESET}"
else
  echo -e "${GREEN}Done. ${#TARGETS[@]} run(s) deleted.${RESET}"
fi

#!/usr/bin/env bash
# B1 — Daily ingestion pipeline. Runs the schedule groups in dependency order,
# then the health check and metadata backup. Designed to be the single entry
# point a launchd/cron job fires each trading day.
#
# Groups run sequentially on purpose: the engine is pinned to workers=1 because
# mootdx is not fork-safe, and running one source-heavy group at a time avoids
# hammering the same upstream. A non-trading-day run is a cheap no-op (each
# `asl run daily` exits 0 with skipped_non_trading_day).
#
# One group failing does not abort the rest — we want as much of the day's data
# as possible — but any failure makes the pipeline exit non-zero after the
# health check reports it.
#
# Usage: scripts/daily_pipeline.sh [YYYY-MM-DD]
# Env: ASL_CONFIG, ASL_LOG_DIR, ASL_GROUPS (space-separated override),
#      ASL_GATE_GROUPS (space-separated; default "core" — failure ⇒ hard fail),
#      ASL_SOFT_FAIL_OK=1 (default) — gate OK 时东财/soft 失败只告警、exit 0；
#        设为 0 则 soft 失败仍 exit 1（国内全组日更可用），
#      ASL_TRADE_DATE (same as optional CLI arg — catch up a prior session).
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ASL="$REPO_ROOT/.venv/bin/asl"
CONFIG="${ASL_CONFIG:-$REPO_ROOT/configs/ashare-lake.toml}"
LOG_DIR="${ASL_LOG_DIR:-$REPO_ROOT/data/ashare-lake/logs}"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/daily-$(date +%Y%m%d).log"
TRADE_DATE="${1:-${ASL_TRADE_DATE:-}}"
DATE_ARGS=()
if [[ -n "$TRADE_DATE" ]]; then
  DATE_ARGS=(--trade-date "$TRADE_DATE")
fi

# Order mirrors configs/ashare-lake.toml [job.daily.groups] cadence
# (core 16:00 → research 18:30). Sequential, not by wall-clock time.
# NB: not named GROUPS — that is a reserved bash builtin (user group IDs).
GROUP_LIST="${ASL_GROUPS:-core capital signals fundamentals macro_risk research}"
GATE_GROUP_LIST="${ASL_GATE_GROUPS:-core}"
# Overseas Mac: expected EM lag must not paint the whole day red.
SOFT_FAIL_OK="${ASL_SOFT_FAIL_OK:-1}"

log() { echo "[$(date '+%H:%M:%S')] $*" | tee -a "$LOG"; }

_is_gate_group() {
  local g="$1" x
  for x in $GATE_GROUP_LIST; do
    [[ "$x" == "$g" ]] && return 0
  done
  return 1
}

log "==== daily pipeline start $(date '+%Y-%m-%d %H:%M:%S') trade_date=${TRADE_DATE:-today} ===="
failed_groups=()
gate_failed=()
soft_failed=()
# parallel arrays: group name → OK|FAILED|…  (bash 3.2 compatible, no assoc arrays)
summary_names=()
summary_status=()

for g in $GROUP_LIST; do
  log "--- group: $g ---"
  if "$ASL" run daily --group "$g" --config "$CONFIG" "${DATE_ARGS[@]}" >>"$LOG" 2>&1; then
    log "group $g OK"
    summary_names+=("$g")
    summary_status+=("OK")
  else
    log "group $g FAILED (see $LOG)"
    failed_groups+=("$g")
    summary_names+=("$g")
    summary_status+=("FAILED")
    if _is_gate_group "$g"; then
      gate_failed+=("$g")
    else
      soft_failed+=("$g")
    fi
  fi
done

# Health check (fires desktop notification on problems) and backup run
# regardless of group outcomes so we always get a status signal and a snapshot.
log "--- health check ---"
if ! "$REPO_ROOT/scripts/health_notify.sh" >>"$LOG" 2>&1; then
  log "health check reported problems"
fi

log "--- backup ---"
if ! "$REPO_ROOT/scripts/backup_meta.sh" >>"$LOG" 2>&1; then
  log "backup FAILED"
fi

# Staging is per-run scratch; once a run succeeded and compact merged it into
# curated it is pure duplication. Nothing ran this automatically before, so it
# grew to ~60% of the curated layer. `asl clean` only drops staging whose run
# succeeded *and* compacted (or is an unknown orphan past retention) — the
# staging of a failed run is resumable state and is always kept.
log "--- clean staging ---"
if ! "$ASL" clean --config "$CONFIG" >>"$LOG" 2>&1; then
  log "staging cleanup FAILED (non-fatal)"
fi

log "---- group summary (gate=${GATE_GROUP_LIST}) ----"
i=0
while [[ $i -lt ${#summary_names[@]} ]]; do
  g="${summary_names[$i]}"
  st="${summary_status[$i]}"
  kind="soft"
  _is_gate_group "$g" && kind="gate"
  log "  ${g}: ${st}  [${kind}]"
  i=$((i + 1))
done

if [[ ${#gate_failed[@]} -gt 0 ]]; then
  log "==== daily pipeline DONE — GATE FAILED: ${gate_failed[*]} (soft also: ${soft_failed[*]:-none}) ===="
  exit 1
fi
if [[ ${#soft_failed[@]} -gt 0 ]]; then
  if [[ "$SOFT_FAIL_OK" == "1" ]]; then
    log "==== daily pipeline DONE — gate OK, EM/soft FAILED (warn-only): ${soft_failed[*]} ===="
    exit 0
  fi
  log "==== daily pipeline DONE — gate OK, EM/soft FAILED: ${soft_failed[*]} ===="
  exit 1
fi
log "==== daily pipeline DONE ok ===="

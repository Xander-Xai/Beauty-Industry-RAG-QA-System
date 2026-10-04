#!/usr/bin/env bash
# Auditable, re-runnable governance for the `main` branch.
#
# Design rules, in priority order:
#
#   1. Never invent a required-check context. Every required context must be
#      proven to (a) exist as a real check-run name and (b) actually execute on
#      `pull_request`. A job that reports `skipped` on a PR head does NOT run
#      for pull requests, so requiring it would deadlock every merge.
#      `derive` recomputes the set from live check-runs and fails if it
#      disagrees with the declared set; `apply` refuses to write until they agree.
#   2. Never set an approval count the sole author cannot satisfy. The PR
#      requirement is enforced with `required_approving_review_count: 0`, so a
#      pull request is mandatory but self-merge needs no review and no
#      self-approval.
#   3. Prefer strict status checks. Without `strict`, CI may have passed on a
#      stale base and main can land red. `allow_update_branch` is enabled in
#      tandem so the "Update branch" button keeps that path a single click.
#
# Depends on `gh` (authenticated, `repo` scope) and `python3`. JSON is handled
# via gh's embedded jq and python3, so no external `jq` binary is needed.
# No workflow file is modified by this script.
#
# Usage:
#   scripts/branch_protection.sh verify    # read-only audit
#   scripts/branch_protection.sh derive    # print + check the derived context set
#   scripts/branch_protection.sh payload   # print the exact PUT payload
#   scripts/branch_protection.sh apply     # PUT the protection (idempotent)
#   scripts/branch_protection.sh snapshot  # machine-readable JSON snapshot

set -euo pipefail

REPO="${GITHUB_REPOSITORY:-Xander-Xai/Beauty-Industry-RAG-QA-System}"
BRANCH="${PROTECTED_BRANCH:-main}"
API="repos/${REPO}"

# Required status-check contexts, verified against real check-runs on
# origin/main f9e0aa9 (push event) and on PR heads 336e325 / 79b8ede / 91b3101
# (pull_request event). Identical on all five SHAs.
REQUIRED_CONTEXTS=(
  "Dockerfile 构建校验"
  "前端构建校验"
  "企业就绪配置校验"
  "测试套件 (3.10)"
  "测试套件 (3.11)"
  "评估确定性守卫"
  "Ruff 检查"
  "pip-audit 依赖漏洞扫描"
  "敏感信息扫描"
)

# Jobs that must never be required: opt-in or event-gated, so they do not
# execute for pull requests and would block every merge. Asserted by derive.
CONDITIONAL_JOBS=(
  "RAGAS evaluator smoke（需显式启用）"
)

log()  { printf '%s\n' "$*"; }
fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

require_tools() {
  command -v gh >/dev/null 2>&1 || fail "gh not found"
  command -v python3 >/dev/null 2>&1 || fail "python3 not found"
  gh auth status >/dev/null 2>&1 || fail "gh not authenticated"
}

# Build the PUT payload for PUT /repos/{repo}/branches/{branch}/protection.
# Contexts arrive on stdin, one per line, so array elements can never be
# re-split or word-mangled on the way into JSON.
build_payload() {
  printf '%s\n' "${REQUIRED_CONTEXTS[@]}" | STRICT="${STRICT_MODE:-true}" python3 -c '
import json, os, sys
contexts = [line for line in sys.stdin.read().split("\n") if line]
print(json.dumps({
    "required_status_checks": {
        "strict": os.environ["STRICT"] == "true",
        "contexts": contexts,
    },
    "enforce_admins": True,
    "required_pull_request_reviews": {
        "dismiss_stale_reviews": False,
        "require_code_owner_reviews": False,
        # 0 is deliberate: the PR requirement still forces a pull request,
        # but the sole author can merge without any reviewer.
        "required_approving_review_count": 0,
        "require_last_push_approval": False,
    },
    # `restrictions` must be present and null. Omitting it is rejected with
    # HTTP 422 ("restrictions was not supplied"): on this endpoint an empty
    # value is not the same as an absent one.
    "restrictions": None,
    "allow_force_pushes": False,
    "allow_deletions": False,
    "required_linear_history": False,
    "allow_fork_syncing": True,
    "block_creations": False,
    "lock_branch": False,
}, ensure_ascii=False, indent=2))
'
}

# Number of the most recently merged pull request, by `merged_at`.
#
# `GET /repos/{o}/{r}/pulls` only understands `state=open|closed|all`. Passing
# `state=merged` is silently ignored and returns unmerged PRs too, so merged-ness
# must never be inferred by filtering closed PRs client-side. The search
# endpoint's `is:merged` qualifier is a filter GitHub actually enforces, and it
# is applied server-side rather than guessed here.
#
# The result set is walked to the end instead of to a fixed page count, so the
# answer does not depend on how many recently-closed PRs happen to sit in front
# of the newest merge. Read-only: `gh api` GETs only.
#
# Exit codes: 0 = number on stdout, 3 = repository has never merged a pull
# request, anything else = the query itself failed. Callers must not report a
# transport failure as "no merged PR", which is a different fact.
latest_merged_pr_number() {
  local out
  # `set -o pipefail` makes a `gh` failure fail the pipeline, so a transport
  # error is never silently downgraded to an empty result.
  out=$(gh api --paginate \
    "search/issues?q=repo:${REPO}+is:pr+is:merged&sort=updated&order=desc&per_page=100" \
    | python3 -c '
import json, sys

decoder = json.JSONDecoder()
raw, pos, best, seen, total = sys.stdin.read(), 0, None, 0, 0

while pos < len(raw):
    while pos < len(raw) and raw[pos].isspace():
        pos += 1
    if pos >= len(raw):
        break
    page, pos = decoder.raw_decode(raw, pos)
    total = max(total, page.get("total_count") or 0)
    for item in page.get("items") or []:
        merged_at = (item.get("pull_request") or {}).get("merged_at")
        if merged_at and (best is None or merged_at > best[0]):
            best = (merged_at, item["number"])
        seen += 1

# Search stops at 1000 results. Report the truncation instead of presenting the
# newest merge among the first 1000 rows as the newest merge overall.
if total > seen:
    sys.stderr.write(
        "WARNING: %d merged pull requests match but search returned %d; "
        "the most recent merge may lie outside that window\n" % (total, seen))

if best is not None:
    print(best[1])
') || return 1

  [ -n "$out" ] || return 3
  printf '%s\n' "$out"
}

# Recompute the required set from a real pull-request head: every check-run
# GitHub actually reported as executed (i.e. not `skipped`).
derive_contexts() {
  local pr_number head_sha rc=0
  pr_number=$(latest_merged_pr_number) || rc=$?
  case "$rc" in
    0) ;;
    3) fail "no merged pull request exists in ${REPO}; nothing to derive from" ;;
    *) fail "could not query merged pull requests in ${REPO}" ;;
  esac

  head_sha=$(gh api "${API}/pulls/${pr_number}" --jq .head.sha)
  [ -n "$head_sha" ] && [ "$head_sha" != "null" ] \
    || fail "merged PR #${pr_number} reported no head SHA to derive contexts from"

  printf '# deriving from merged PR #%s head %s\n' "$pr_number" "$head_sha" >&2
  gh api "${API}/commits/${head_sha}/check-runs?per_page=100" \
    --jq '.check_runs[] | select(.conclusion != "skipped") | .name' | sort -u
}

cmd_derive() {
  require_tools
  local derived declared rc=0
  derived=$(derive_contexts | sort -u)
  declared=$(printf '%s\n' "${REQUIRED_CONTEXTS[@]}" | sort -u)

  log "# derived contexts (executed on pull_request):"
  printf '%s\n' "$derived" | sed 's/^/  /'

  # A conditional job must never appear in the required set.
  local job
  for job in "${CONDITIONAL_JOBS[@]}"; do
    if printf '%s\n' "$derived" | grep -qxF "$job"; then
      log "ERROR: conditional job '$job' executed on a PR head; re-evaluate it" >&2
      rc=1
    fi
  done

  if [ "$derived" = "$declared" ]; then
    log "OK: derived set matches the declared required set."
  else
    log "" >&2
    log "DRIFT: derived set differs from the declared set." >&2
    diff <(printf '%s\n' "$declared") <(printf '%s\n' "$derived") >&2 || true
    rc=1
  fi
  return $rc
}

cmd_apply() {
  require_tools
  log "# verifying declared contexts against live check-runs before writing"
  cmd_derive >/dev/null \
    || fail "context derivation drifted; refusing to write protection. Fix scripts/branch_protection.sh first."

  local payload
  payload=$(build_payload)
  log "# applying protection to ${REPO}:${BRANCH}"
  printf '%s\n' "$payload"

  # Pipe the payload explicitly. `--input -` reads whatever stdin happens to
  # hold, and inside a script that is empty, so GitHub schema-validates an
  # empty body and fails with the misleading
  # "For 'links/0/schema', nil is not an object".
  printf '%s\n' "$payload" \
    | gh api --method PUT "${API}/branches/${BRANCH}/protection" --input - >/dev/null

  # Companion repo setting for strict mode: without it the "Update branch"
  # button is unavailable and re-syncing a stale base becomes a local chore.
  log "# enabling allow_update_branch (companion to strict status checks)"
  gh api --method PATCH "${API}" -f allow_update_branch=true >/dev/null

  log "applied."
}

cmd_verify() {
  require_tools
  log "== repo     : ${REPO}"
  log "== branch   : ${BRANCH}"
  log "== main SHA : $(gh api "${API}/commits/${BRANCH}" --jq .sha)"

  log ""
  log "-- protection --"
  if gh api "${API}/branches/${BRANCH}/protection" >/dev/null 2>&1; then
    gh api "${API}/branches/${BRANCH}/protection" --jq '{
      status_checks_strict: .required_status_checks.strict,
      required_status_checks: [.required_status_checks.contexts[]],
      enforce_admins: .enforce_admins.enabled,
      allow_force_pushes: .allow_force_pushes.enabled,
      allow_deletions: .allow_deletions.enabled,
      pr_required: (.required_pull_request_reviews != null),
      required_approving_review_count: .required_pull_request_reviews.required_approving_review_count,
      require_last_push_approval: .required_pull_request_reviews.require_last_push_approval,
      require_code_owner_reviews: .required_pull_request_reviews.require_code_owner_reviews,
      lock_branch: .lock_branch.enabled
    }'
  else
    log "  UNPROTECTED"
  fi

  log ""
  log "-- merge settings --"
  gh api "${API}" --jq '{
    allow_squash_merge, allow_merge_commit, allow_rebase_merge,
    allow_auto_merge, delete_branch_on_merge, allow_update_branch
  }'

  log ""
  log "-- rulesets (must stay empty; no competing config) --"
  gh api "${API}/rulesets" --jq 'if length == 0 then "[]" else . end'
}

cmd_snapshot() {
  require_tools
  REPO="$REPO" BRANCH="$BRANCH" API="$API" python3 - <<'PY'
import json, os, subprocess

api, repo, branch = os.environ["API"], os.environ["REPO"], os.environ["BRANCH"]

def gh(*args, check=True):
    p = subprocess.run(["gh", "api", *args], capture_output=True, text=True)
    if p.returncode != 0:
        if not check:
            return None
        raise SystemExit(p.stderr.strip())
    return p.stdout

def gh_json(*args, default=None):
    out = gh(*args, check=False)
    if out is None or not out.strip():
        return default
    return json.loads(out)

protection = gh_json(f"{api}/branches/{branch}/protection", default=None)
settings = gh_json(api, default={})
rulesets = gh_json(f"{api}/rulesets", default=[])
head = gh_json(f"{api}/commits/{branch}", default={})

keep = ("allow_squash_merge", "allow_merge_commit", "allow_rebase_merge",
        "allow_auto_merge", "delete_branch_on_merge", "allow_update_branch")

print(json.dumps({
    "repo": repo,
    "branch": branch,
    "main_sha": head.get("sha"),
    "protection": None if protection is None else {
        "required_status_checks_strict": protection["required_status_checks"]["strict"],
        "required_status_checks": protection["required_status_checks"]["contexts"],
        "enforce_admins": protection["enforce_admins"]["enabled"],
        "allow_force_pushes": protection["allow_force_pushes"]["enabled"],
        "allow_deletions": protection["allow_deletions"]["enabled"],
        "pr_required": protection["required_pull_request_reviews"] is not None,
        "required_approving_review_count":
            protection["required_pull_request_reviews"]["required_approving_review_count"]
            if protection["required_pull_request_reviews"] else None,
        "require_last_push_approval":
            protection["required_pull_request_reviews"]["require_last_push_approval"]
            if protection["required_pull_request_reviews"] else None,
        "lock_branch": protection["lock_branch"]["enabled"],
    },
    "merge_settings": {k: settings.get(k) for k in keep},
    "rulesets": rulesets,
}, ensure_ascii=False, indent=2))
PY
}

case "${1:-verify}" in
  verify)   cmd_verify ;;
  derive)   cmd_derive ;;
  apply)    cmd_apply ;;
  payload)  build_payload ;;
  snapshot) cmd_snapshot ;;
  *) fail "unknown command: ${1} (expected verify|derive|apply|payload|snapshot)" ;;
esac
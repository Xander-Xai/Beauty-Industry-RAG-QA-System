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
#   scripts/branch_protection.sh verify    # gate: compare live state against
#                                         # the declared contract (read-only)
#   scripts/branch_protection.sh derive    # print + check the derived context set
#   scripts/branch_protection.sh payload   # print the exact PUT + PATCH payloads
#   scripts/branch_protection.sh apply     # PUT the protection (idempotent)
#   scripts/branch_protection.sh snapshot  # machine-readable JSON snapshot
#
# `verify` exit codes:
#   0  live state satisfies every item of the declared contract
#   1  drift: at least one item differs from the contract
#   2  live state could not be read (network/auth error, or the branch has no
#      protection at all, so there is nothing to compare against)
#
# 2 is deliberately distinct from 1: "drift" is a governance finding a human
# must reconcile, whereas "cannot read" means the audit produced no verdict and
# must not be mistaken for a clean run by a caller that only checks for zero.

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

# ---------------------------------------------------------------------------
# The declared governance contract.
#
# `verify` compares live GitHub state against exactly these values and exits
# non-zero on any deviation. They are the reference, not a report of whatever
# happens to be configured today: the whole point of the check is to fail when
# somebody ticks a box in the GitHub UI, so the expectations must be stated
# here where they can be reviewed, and must never be read back from the live
# API. Rationale for each value is recorded in docs/main-branch-governance.md.
# ---------------------------------------------------------------------------
EXPECT_STRICT=true               # CI must have passed against current main
EXPECT_ENFORCE_ADMINS=true       # rules must bind the admin too, or not bind
EXPECT_PR_REQUIRED=true          # main is reachable only through a PR
EXPECT_APPROVING_REVIEWS=0       # single-author repo; 1 would deadlock merges
EXPECT_LAST_PUSH_APPROVAL=false  # no self-approval needed
EXPECT_ALLOW_FORCE_PUSHES=false # no history rewrite
EXPECT_ALLOW_DELETIONS=false     # main cannot be deleted
EXPECT_LOCK_BRANCH=false         # keep main operable
EXPECT_ALLOW_UPDATE_BRANCH=true  # companion to strict mode: "Update branch"
EXPECT_RULESETS=0                # no competing rule set alongside protection

log()  { printf '%s\n' "$*"; }
fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

require_tools() {
  command -v gh >/dev/null 2>&1 || fail "gh not found"
  command -v python3 >/dev/null 2>&1 || fail "python3 not found"
  gh auth status >/dev/null 2>&1 || fail "gh not authenticated"
}

# Build the PUT payload for PUT /repos/{owner}/{repo}/branches/{branch}/protection.
# Contexts arrive on stdin, one per line, so array elements can never be
# re-split or word-mangled on the way into JSON.
build_payload() {
  # Defaults to the declared contract so `apply` and `verify` cannot disagree
  # about `strict`; STRICT_MODE stays as an explicit escape hatch. Without this
  # link, editing one and not the other would make `verify` unsatisfiable.
  printf '%s\n' "${REQUIRED_CONTEXTS[@]}" | STRICT="${STRICT_MODE:-$EXPECT_STRICT}" python3 -c '
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

# Build the body for PATCH /repos/{owner}/{repo}.
#
# `allow_update_branch` is a repository-level setting, not a member of the
# branch-protection object above, so it cannot ride along in the PUT and needs
# its own PATCH. The body is built with json.dumps for the same reason the PUT
# payload is: the value must reach GitHub as a JSON boolean, and only a real
# JSON encoder guarantees that.
#
# `gh api -f/--raw-field` cannot do this job. It is documented as "add a string
# parameter", and it behaves that way: `-f allow_update_branch=true` puts
# {"allow_update_branch":"true"} on the wire -- a quoted string -- where the
# API expects a boolean. `-F/--field` does emit a real boolean, but it gets
# there by inferring a type from the literal text "true", which breaks the
# moment the value is spelled any other way ("True", "1", "yes"). Encoding the
# boolean in python has no such failure mode.
build_update_branch_payload() {
  python3 -c '
import json
print(json.dumps({"allow_update_branch": True}, indent=2))
'
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
  #
  # Sent as an explicit JSON body rather than as a gh field flag, so that the
  # value arrives as a boolean. Echoed before sending, because this is the one
  # request in the script whose type is easy to get silently wrong, and
  # "true"/true is invisible once it has been through a flag parser.
  local update_branch_payload
  update_branch_payload=$(build_update_branch_payload)
  log "# enabling allow_update_branch (companion to strict status checks)"
  printf '%s\n' "$update_branch_payload"

  # The body carries that one key and nothing else, so this PATCH cannot
  # disturb the merge settings `verify` also asserts on. Repeating it against an
  # already-correct repository is a no-op: PATCH states the target value
  # rather than toggling, so `apply` stays idempotent.
  printf '%s\n' "$update_branch_payload" \
    | gh api --method PATCH "${API}" --input - >/dev/null

  log "applied."
}

# Compare live governance state against the declared contract.
#
# READ-ONLY BY CONTRACT. Every gh call below is a bare GET (no --method, no
# --input). This function must never repair what it finds: silently re-applying
# the contract would destroy the only evidence that the settings were changed,
# and would let `verify` pass in a job whose real job was to notice the change.
# Repair is `apply`'s job, and only after a human decides which side is wrong.
verify_against_contract() {
  # Newline-separated, because these contexts contain spaces and would be
  # re-split by any whitespace-joined handoff.
  EXP_CONTEXTS=$(printf '%s\n' "${REQUIRED_CONTEXTS[@]}")

  REPO="$REPO" BRANCH="$BRANCH" API="$API" \
  EXP_CONTEXTS="$EXP_CONTEXTS" \
  EXP_STRICT="$EXPECT_STRICT" \
  EXP_ENFORCE_ADMINS="$EXPECT_ENFORCE_ADMINS" \
  EXP_PR_REQUIRED="$EXPECT_PR_REQUIRED" \
  EXP_APPROVING_REVIEWS="$EXPECT_APPROVING_REVIEWS" \
  EXP_LAST_PUSH_APPROVAL="$EXPECT_LAST_PUSH_APPROVAL" \
  EXP_ALLOW_FORCE_PUSHES="$EXPECT_ALLOW_FORCE_PUSHES" \
  EXP_ALLOW_DELETIONS="$EXPECT_ALLOW_DELETIONS" \
  EXP_LOCK_BRANCH="$EXPECT_LOCK_BRANCH" \
  EXP_ALLOW_UPDATE_BRANCH="$EXPECT_ALLOW_UPDATE_BRANCH" \
  EXP_RULESETS="$EXPECT_RULESETS" \
  python3 - <<'PY'
import difflib, json, os, subprocess, sys

# Keep the verdict on stderr in its original position relative to the report on
# stdout; block-buffered stdout would otherwise surface the error first.
sys.stdout.reconfigure(line_buffering=True)

api, repo, branch = os.environ["API"], os.environ["REPO"], os.environ["BRANCH"]


def gh_get(path):
    """GET one API path. No --method, no --input: strictly read-only."""
    p = subprocess.run(["gh", "api", path], capture_output=True, text=True)
    if p.returncode != 0:
        # 404 on the protection endpoint is a legitimate answer ("this branch
        # has no protection"), not a transport failure, so it is not an error
        # here; the caller decides what an absent object means.
        if "HTTP 404" in p.stderr:
            return None
        sys.stderr.write("ERROR: cannot read %s\n%s\n" % (path, p.stderr.strip()))
        sys.exit(2)
    return json.loads(p.stdout)


def want_bool(name):
    return os.environ[name] == "true"


print("== verify: %s @ %s ==" % (repo, branch))
head = gh_get("%s/commits/%s" % (api, branch))
if head:
    print("live %s SHA: %s" % (branch, head["sha"]))
print("read-only audit: GET requests only, no GitHub state is modified.")
print("")

protection = gh_get("%s/branches/%s/protection" % (api, branch))
if protection is None:
    # Nothing to compare against. Every branch-protection expectation is
    # undefined here, so reporting each one as "false" would be a fabrication.
    sys.stderr.write(
        "ERROR: branch '%s' has no branch protection in %s.\n"
        "The contract requires protection on this branch; there is no live\n"
        "state to compare, so no verdict could be reached.\n" % (branch, repo)
    )
    sys.exit(2)

settings = gh_get(api)
if settings is None:
    sys.stderr.write("ERROR: cannot read repository settings for %s\n" % repo)
    sys.exit(2)

rulesets = gh_get("%s/rulesets" % api)
if rulesets is None:
    # Not defaulted to empty on purpose. An empty list is the state this
    # contract wants, but a 404 here can also mean the endpoint is invisible to
    # this token; guessing "no rulesets" would turn a visibility problem into a
    # passing audit.
    sys.stderr.write(
        "ERROR: cannot read rulesets for %s.\n"
        "Refusing to assume an empty rule set from an unreadable response.\n"
        % repo
    )
    sys.exit(2)
if not isinstance(rulesets, list):
    sys.stderr.write("ERROR: unexpected rulesets payload: %r\n" % (rulesets,))
    sys.exit(2)

# Absent sub-objects read as "unset", not as False: GitHub omits a block that
# has never been configured, and `None` is the honest value for it.
rsc = protection.get("required_status_checks") or {}
rpr = protection.get("required_pull_request_reviews") or {}


def enabled(key):
    block = protection.get(key)
    return None if block is None else block.get("enabled")


expected_contexts = sorted(
    line for line in os.environ["EXP_CONTEXTS"].split("\n") if line
)

# (label, expected, actual, kind). `kind` picks the reporting style only;
# every row is compared the same way -- equality against the declaration.
CHECKS = [
    ("required_status_checks.strict",
     want_bool("EXP_STRICT"), rsc.get("strict"), "scalar"),
    ("required_status_checks.contexts",
     expected_contexts, sorted(rsc.get("contexts") or []), "list"),
    ("enforce_admins",
     want_bool("EXP_ENFORCE_ADMINS"), enabled("enforce_admins"), "scalar"),
    ("pull_request_required",
     want_bool("EXP_PR_REQUIRED"),
     protection.get("required_pull_request_reviews") is not None, "scalar"),
    ("required_approving_review_count",
     int(os.environ["EXP_APPROVING_REVIEWS"]),
     rpr.get("required_approving_review_count"), "scalar"),
    ("require_last_push_approval",
     want_bool("EXP_LAST_PUSH_APPROVAL"),
     rpr.get("require_last_push_approval"), "scalar"),
    ("allow_force_pushes",
     want_bool("EXP_ALLOW_FORCE_PUSHES"), enabled("allow_force_pushes"), "scalar"),
    ("allow_deletions",
     want_bool("EXP_ALLOW_DELETIONS"), enabled("allow_deletions"), "scalar"),
    ("lock_branch",
     want_bool("EXP_LOCK_BRANCH"), enabled("lock_branch"), "scalar"),
    ("repo.allow_update_branch",
     want_bool("EXP_ALLOW_UPDATE_BRANCH"),
     settings.get("allow_update_branch"), "scalar"),
    ("repo.rulesets",
     int(os.environ["EXP_RULESETS"]), len(rulesets), "scalar"),
]

drifted = [(label, exp, act, kind)
           for label, exp, act, kind in CHECKS if exp != act]
# Labels are unique, so a set is enough to mark the summary rows.
failed = {label for label, _, _, _ in drifted}


def show(value, kind):
    if kind == "list":
        return "%d context(s)" % len(value)
    return json.dumps(value)


for label, exp, act, kind in CHECKS:
    print("  [%s] %-33s expected=%-16s actual=%s"
          % ("FAIL" if label in failed else " ok ", label,
             show(exp, kind), show(act, kind)))

if not drifted:
    print("")
    print("OK: %d/%d governance checks match the declared contract."
          % (len(CHECKS), len(CHECKS)))
    sys.exit(0)

print("")
print("drift: %d of %d checks disagree with the declared contract."
      % (len(drifted), len(CHECKS)))

for index, (label, exp, act, kind) in enumerate(drifted, start=1):
    print("")
    print("%d) %s" % (index, label))
    print("   expected: %s" % show(exp, kind))
    print("   actual:   %s" % show(act, kind))
    print("   diff:")
    if kind == "list":
        # `-` is declared-but-absent-from-live, `+` is live-but-undeclared:
        # either direction is drift, because the contract fixes the set exactly.
        diff = difflib.unified_diff(
            exp, act, fromfile="declared", tofile="live", lineterm="", n=0
        )
        for line in diff:
            if line.startswith(("---", "+++")):
                continue  # header lines; expected/actual are already printed
            print("     %s" % line)
    else:
        print("     -%s" % json.dumps(exp))
        print("     +%s" % json.dumps(act))

print("")
# `apply` reconciles branch protection and allow_update_branch only. Naming the
# exceptions keeps the hint from sending someone to a command that cannot fix
# what they are looking at.
NOT_APPLIABLE = {"repo.rulesets"}
manual = [label for label, _, _, _ in drifted if label in NOT_APPLIABLE]

sys.stderr.write(
    "FAIL: governance drifted from the declared contract in "
    "scripts/branch_protection.sh.\n"
    "Decide which side is wrong, then reconcile deliberately: edit the\n"
    "EXPECT_* / REQUIRED_CONTEXTS declarations and run `apply`, or change the\n"
    "live settings and re-run `verify`. verify never repairs drift itself.\n"
)
if manual:
    sys.stderr.write(
        "Note: `apply` does not manage %s -- reconcile that by hand (or drop\n"
        "the expectation if a rule set is intentional).\n" % ", ".join(manual)
    )
sys.exit(1)
PY
}

cmd_verify() {
  require_tools
  local rc=0
  # `|| rc=$?` keeps `set -e` from aborting before the status is captured, so
  # the documented 0/1/2 codes reach the caller intact.
  verify_against_contract || rc=$?
  return "$rc"
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

# Print every request body `apply` will send, in order, so the exact bytes can
# be reviewed -- and asserted against -- without touching the live repository.
cmd_payload() {
  log "# PUT ${API}/branches/${BRANCH}/protection"
  build_payload
  log ""
  log "# PATCH ${API}"
  build_update_branch_payload
}

case "${1:-verify}" in
  verify)   cmd_verify ;;
  derive)   cmd_derive ;;
  apply)    cmd_apply ;;
  payload)  cmd_payload ;;
  snapshot) cmd_snapshot ;;
  *) fail "unknown command: ${1} (expected verify|derive|apply|payload|snapshot)" ;;
esac
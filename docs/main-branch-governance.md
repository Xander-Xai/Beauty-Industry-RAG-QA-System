# main 分支治理策略 / main Branch Governance

本文件记录 `main` 分支的治理配置、每条规则的验证证据，以及已知限制。

治理配置本身由 `scripts/branch_protection.sh` 生成与校验，**不要手工在
GitHub UI 上勾选**：脚本里的 required context 是从真实 check-run 推导出来的，
手工勾选无法保证这一点。

```bash
scripts/branch_protection.sh verify    # 只读审计，输出当前治理快照
scripts/branch_protection.sh derive    # 从真实 check-run 重新推导 required context
scripts/branch_protection.sh payload   # 打印将要写入的 PUT payload
scripts/branch_protection.sh apply     # 幂等写入（写前强制校验 context 未漂移）
```

## 适用边界

- 本仓库是**单人仓库**（唯一有写权限的账号 `Xander-Xai`）。
- 因此治理目标是"**强制走 PR + 强制 CI 通过**"，而不是"强制他人审批"。
- 审批数固定为 `0`。这是刻意的：见下方「为什么审批数是 0」。

## 当前配置

| 项 | 值 | 理由 |
| --- | --- | --- |
| `required_status_checks.strict` | `true` | 要求分支与 `main` 同步后才可合并，保证 CI 校验的就是将要落地的树 |
| `required_status_checks.contexts` | 9 项，见下 | 全部经真实 check-run 验证 |
| `enforce_admins` | `true` | 规则对仓库管理员同样生效，否则保护形同虚设 |
| `allow_force_pushes` | `false` | 禁止 force push |
| `allow_deletions` | `false` | 禁止删除分支 |
| `required_pull_request_reviews` | 存在，`required_approving_review_count: 0` | 强制走 PR，但不要求任何审批 |
| `require_last_push_approval` | `false` | 不要求作者 approve 自己的 PR |
| `lock_branch` | `false` | 不锁定分支 |
| `allow_squash_merge` | `true` | 保持 squash merge 可用 |
| `delete_branch_on_merge` | `true` | 合并后自动删除分支 |
| `allow_update_branch` | `true` | `strict` 模式的必要配套，让 "Update branch" 按钮可用 |
| rulesets | 空（`[]`） | 不引入第二套并行规则，避免与 branch protection 冲突 |

## required context 的来源

**禁止猜测 required check 名称。** 下列 9 个名称取自真实 check-run API
（`GET /repos/{owner}/{repo}/commits/{sha}/check-runs`），并在 5 个不同 SHA 上
逐一比对一致：

| SHA | 事件 |
| --- | --- |
| `f9e0aa9` | `push` → `main`（任务执行时的 `main` HEAD） |
| `d303a37` | `push` → `main` |
| `336e325` | `pull_request`（PR #25 head） |
| `79b8ede` | `pull_request`（PR #23 head） |
| `91b3101` | `pull_request`（PR #21 head） |

必需项：

```
Dockerfile 构建校验
前端构建校验
企业就绪配置校验
测试套件 (3.10)
测试套件 (3.11)
评估确定性守卫
Ruff 检查
pip-audit 依赖漏洞扫描
敏感信息扫描
```

`测试套件` 是 matrix job（`python-version: ["3.10", "3.11"]`），GitHub 会把它
展开成 `测试套件 (3.10)` 与 `测试套件 (3.11)` 两个独立 check，因此**两项都要
required**，漏掉任何一个都会让保护看起来生效、实际却在少一道门。

### 为什么 `RAGAS evaluator smoke（需显式启用）` 不是 required

它在 `ci.yml` 里被 `if:` 限定为

```yaml
if: >-
  github.event_name == 'push' &&
  github.ref == 'refs/heads/main' &&
  vars.RAGAS_EVAL_ENABLED == 'true'
```

`RAGAS_EVAL_ENABLED` 仓库变量当前**未设置**，且条件要求 `push` 事件，
所以它在 PR 上永远不会执行，只会报 `skipped`。若把它设为 required，GitHub
会一直等一个永远不会出现的 check，**所有 PR 将永久无法合并**。这是本配置
里最容易踩的坑，`derive` 子命令会主动断言它没有出现在 required 集合中。

## 为什么审批数是 0

`required_pull_request_reviews` 这个对象本身的作用是"**必须通过 PR 改
`main`**"。把 `required_approving_review_count` 设为 `1` 会产生两个后果：

1. 唯一作者无法 approve 自己的 PR（GitHub 不允许作者 approve 自己），
2. 仓库内没有第二个可审批的人。

结果是**任何 PR 都无法合并**，仓库被锁死。因此审批数固定为 `0`：
PR 强制，审批不强制。同时 `require_last_push_approval: false`，
不需要自我审批。

这一点是**实测**的，不是推断：PR #27（空提交，树与 `main` 完全一致）在
9/9 必需 check 全绿、**0 个 review** 的条件下，`mergeStateStatus` 为 `CLEAN`、
`mergeable` 为 `MERGEABLE`。

## 验证记录

以下为实际执行结果，非配置推断。

**直接 push 被拒**（`git push origin gov-probe-tmp:main`，探针分支为
`main` + 1 个空提交）：

```
remote: error: GH006: Protected branch update failed for refs/heads/main.
remote: - Changes must be made through a pull request.
remote: - 9 of 9 required status checks are expected.
```

该报错同时由 GitHub 确认"恰好是 9 项 required check"，与上表一致。
`main` 全程停留在 `f9e0aa9`，未被改动。

**PR 门禁生效**：PR #27 的 head 上恰好触发 9 项 required check，全部 `success`；
`RAGAS evaluator smoke` 如预期为 `skipped` 且不参与门禁。

**合并能力未被锁死**：见上方「为什么审批数是 0」。

**探针已清理**：PR #27 已 close，`gov-probe-tmp` 远端分支已删除，
本地临时分支已删除，`main` SHA 未变。

## 已知限制（重要）

**force push 与删除分支的拒绝行为，没有通过真实 push 验证。**

`git push --dry-run` **不评估 GitHub 分支保护**，它只在本地计算 ref 变化。
实测 `--dry-run --force` 会照常输出 `+ f9e0aa9...d303a37 (forced update)`、
`--dry-run --delete` 会输出 `- [deleted] main`，看起来都"成功"，但这些输出
**不构成任何保护生效的证据**。因此本文件对这两项的结论依据是：

- `GET /branches/main/protection` 返回 `allow_force_pushes.enabled = false`、
  `allow_deletions.enabled = false`（权威服务端状态）；
- 上面的 GH006 报错证明保护钩子确实在 `main` 上生效；
- 删除 `main` 另受 GitHub 默认分支保护限制（实测直接 push 探针时收到
  `refusing to delete the current branch: refs/heads/main`）。

没有为了取证而对正在使用的 `main` 做真实 force push：该操作一旦保护失效
就会改写 `main` 历史，代价与任务目标不成比例。**这是刻意的取舍，不是遗漏。**

### 另一个限制

`enforce_admins: true` 意味着管理员**也无法**绕过这些规则，包括
required status checks。若 CI 长期失败导致无法合并，唯一出口是**显式临时
关闭保护**——这属于有记录的运维操作，不是配置漏洞，但也意味着"没有人能
绕过"与"永远不会被卡住"之间存在取舍。本仓库选择了前者。

## 变更方式

改动治理配置请编辑 `scripts/branch_protection.sh` 的 `REQUIRED_CONTEXTS`
并执行 `apply`，然后把 `verify` 的输出附到 PR 里。不要在 UI 上直接勾选，
否则脚本的 `derive` 校验会与实际配置漂移。
# Worker Pool 题案拆分方案

> 起草日期: 2026-06-21（v4 修订：题案 1 已落地为 OpenSpec change，其余 5 个子题案保持规划状态）
>
> 进度索引：
> - 题案 1 `add-execution-workspace-and-profile-per-category` — ✅ **已折叠进 baseline spec，当前代码已有 execution workspace + narrow promotion bridge**
> - 旁路可靠性修复 `add-sequential-queue-fail-fast` — ✅ **独立于 worker pool 拆分；顺序队列现在可在连续 Hermes 认证/限流故障或取消时 fail-fast**
> - 题案 2 `add-staged-publication-allowlist` — ⏳ 规划中（正在题案整改）
> - 题案 3 `add-execution-lease-and-fencing` — ⏳ 规划中
> - 题案 4 `add-project-agent-layer-over-hermes-profiles` — ⏳ 规划中
> - 题案 5 `add-local-supervisor-and-slots` — ⏳ 规划中
> - 题案 6 `add-execution-audit-snapshots` — ⏳ 规划中
> - 收尾 `add-build-attempt-feedback-ui` — ⏳ 规划中（6 个子题案归档后再起）
>
> v3 修订原因（仍生效）: Git worktree 降级为开发调试可选项，运行时隔离改为项目侧 execution workspace
>
> 背景: 原题案 [add-agent-worker-pool-management](openspec/changes/add-agent-worker-pool-management/) scope 横跨 9 个 section（schema/lease/sandbox/supervisor/UI/审计/上线），一次性推进风险高、周期长。本文档把它拆成 6 个独立可交付的子题案，按事故复发风险与依赖关系排序。
>
> v3 修订原因: 重新评估产物形态后确认，本项目的核心产物是本地运行时文件（`work/challenges`、`work/shards`、`work/reports`、日志、Docker 构建上下文），不是 Git 分支实验。Hermes profile 仍适合做模型/状态/人格隔离，但 Git worktree 不应作为 worker pool 的核心运行时隔离手段；题案 1 改为项目侧自建 execution workspace。

---

## 一、原题案触发动机回顾

`assessment.md` 12 轮自审揭示，这个提案不是事前设计，而是事后复盘——曾经发生过一次 **"Web 题目执行串到 Pwn 题目"** 的真实事故。事故根因有 4 层叠加：

1. Hermes 的 Docker terminal 持久化 task 工作区（`task=default`）跨执行复用 → 留下了上一道题的残留
2. Prompt 里的 shard 路径是宿主机绝对路径，Docker 容器内挂载不上 → 当前 execution 读不到自己的 shard
3. 模型自主决策：读不到目标 shard → 在 sandbox 里搜到残留 → 当成自己的任务继续做
4. 写入路径无 allowlist → 错误执行的产物 commit 到了错误目录

拆分原则：**先修事故、再做并发增强、最后做运维与审计**；**Hermes profile 复用其状态隔离，运行时输入/输出隔离由项目显式控制**。

---

## 二、原题案内容地图

| 文件 | 行数 | 作用 |
|---|---|---|
| proposal.md | 74 | Why / What / Capabilities / Impact |
| design.md | 211 | 12 个核心架构决策（D1–D12） |
| assessment.md | 112 | 12 轮自审记录 |
| tasks.md | 126 | 9 个 section, 50+ checkbox |
| specs/worker-agent-management/spec.md | 230 | 6 项 ADDED requirements |
| specs/worker-pool-execution/spec.md | 174 | 8 项 ADDED requirements |
| specs/build-orchestration/spec.md | 40 | 2 项 MODIFIED requirements |

### tasks.md 9 个 section 一句话总结

| § | 主题 | 性质 |
|---|---|---|
| 1 | Schema / 迁移 / 索引 | 基础设施 |
| 2 | Agent registry + profile 安全封装 | 注册与运维 |
| 3 | 原子 dispatch + lease + fencing | 并发正确性 |
| 4 | Isolated execution preparation（sandbox + preflight） | **核心隔离** |
| 5 | Guarded artifact publication（staging + allowlist） | **核心隔离** |
| 6 | Single-host supervisor | 运维 |
| 7 | API / Dashboard | UI 表层 |
| 8 | 验证（fault injection / 串题回归 / soak） | 测试 |
| 9 | 迁移 / 回滚 | 上线 |

### 12 个决策按"撤掉就会让事故重现"排序

1. **D6 sandbox 隔离 + D7 preflight**：直接修复事故根因 1/2/3。
2. **D8 staged publication + allowlist**：修复事故根因 4。
3. **D3 lease + fencing**：防止 lease 过期后旧进程发布脏数据。
4. **D2 capabilities + 精确 dispatch**：让 dispatch 在数据库层拒绝错配，不依赖文件名顺序。
5. D5 supervisor + D10 audit + D4 控制/健康解耦：可运维性、可观测性、可审计性。
6. D9 profile 封装 + D11 legacy 隔离 + D12 fail-closed 上线：流程护栏。

---

## 三、Hermes 原生能力与边界（v3 修订）

### Profiles（[文档](https://hermes-agent.nousresearch.com/docs/zh-Hans/user-guide/profiles)）
- profile = 独立 Hermes home 目录，**完整状态隔离**：`config.yaml` / `.env` / `SOUL.md` / memory / sessions / skills / cron / state.db
- 每个 profile 自动生成命令别名（`coder chat`），可通过 `-p name` / `HERMES_HOME` / `hermes profile use` 选择
- profile 支持 **`description` 字段用于 kanban 任务路由**——原生的"按角色派任务"机制（建议性，非强制）
- profile 可在 `config.yaml` 里设 `terminal.cwd` 控制工作目录（独立于 profile 目录）
- **完整 CRUD 原生支持**：`profile create/list/show/rename/delete/export/import/install/update`
- **明确警告**: "profile 不对 agent 进行沙箱隔离... agent 仍拥有与你的用户账户相同的文件系统访问权限"

### Git Worktrees（降级为可选开发工具）
- `hermes -w` / Git worktree 适合隔离代码仓库 checkout、分支和 checkpoint 历史
- 本项目 worker pool 的核心产物是 `work/` 下的本地运行时文件，不是 Git 追踪代码；因此 worktree **不能**作为 execution 隔离的主方案
- worktree 不提供文件系统访问控制，也不天然隔离 `work/challenges`、`work/shards`、Docker volume、日志或外部工具链
- 后续可以把 worktree 作为开发者调试多个代码分支时的可选用法，但不写入 worker pool 的核心题案

### Profile Distributions（[文档](https://hermes-agent.nousresearch.com/docs/zh-Hans/user-guide/profile-distributions)）
- 纯粹是 profile 的 git 打包分发机制
- 对本拆分**不直接相关**——只在"想跨机器同步同一个 agent 配置"时有用
- 后续如果做团队版可以考虑把 `cf-web`/`cf-pwn`/`cf-re` profile 打包成 distribution

---

## 四、事故根因 × 责任边界映射（v3 修订）

| 事故根因 | Hermes 能帮助的部分 | 项目侧必须自建 |
|---|---|---|
| **task=default 残留** | profile 隔离 Hermes 状态；SOUL/config 可按类别分离 | 每次执行创建干净 `work/executions/<workspace_id>/`，只 materialize 本次输入 |
| **宿主机绝对路径** | profile 的 `terminal.cwd` 可作为启动 cwd 辅助 | Prompt 只暴露 execution workspace 内相对路径，preflight 校验 `input/` 可读、`output/` 可写 |
| **模型自主搜索串题** | 类别 profile 可降低行为偏移 | workspace 不放其他题目；preflight fail-closed；执行后 publisher 校验 category/id/scope |
| **写入路径无 allowlist** | 无（文档明示 profile ≠ sandbox） | **必须自建** output allowlist + staged publication + manifest hash |

**结论**: Hermes profile 是有价值的状态/人格/模型隔离层，但不能替代运行时 workspace。阻断串题事故的核心应是项目侧 `execution workspace + preflight + publisher allowlist`，而不是 Git worktree。

---

## 五、项目侧仍必须自建的事（v3 修订）

| 需求 | 为什么 Hermes 不提供 |
|---|---|
| **写入路径 allowlist** | profile 不是 sandbox（文档明示），Hermes 无文件系统访问控制 |
| **跨进程并发上限** | profile 模型假设各 profile 独立运行，不管全局并发 |
| **DB lease + fencing token** | Hermes session 没有外部 lease 概念 |
| **Build attempt → execution 的精确 dispatch** | Hermes kanban 路由是描述匹配，不是 ID 精确认领 |
| **Capability 硬约束** | profile.description 仅供 Hermes kanban 路由参考，不阻止越权 |
| **不可变审计快照** | Hermes 不知道项目的 attempt/execution 概念 |
| **Supervisor + slot 池** | Hermes 不管并发编排 |
| **Retention policy（execution 目录清理）** | execution workspace、quarantine、日志留存都属于项目运行时策略 |

---

## 六、拆分总览（v3 修订）

| 序 | 提案名 | 取自原题案 | 价值 | 依赖 | 量级 |
|---|---|---|---|---|---|
| 1 | `add-execution-workspace-and-profile-per-category` | D6 + D7 + tasks §4 + §8.3/8.4 | **直接修事故**，建立干净运行时输入/输出边界 | 无 | 1 PR |
| 2 | `add-staged-publication-allowlist` | D8 + tasks §5 + §8.6 | **直接修事故**（写入侧） | 1 | 1–2 PR |
| 3 | `add-execution-lease-and-fencing` | D3 + tasks §3 + §8.5 | 防 lease 过期脏写；建立同一 attempt 下多轮 execution 链 | 2（共享 execution 行） | 2–3 PR |
| 4 | `add-project-agent-layer-over-hermes-profiles` | D2 + worker-agent-management 整份 spec + tasks §1 (部分) + §2 + §7.1 | dispatch 授权基础 | 3 | 2 PR（薄抽象） |
| 5 | `add-local-supervisor-and-slots` | D4 + D5 + tasks §1（agent_slots）+ §6 + §7.2-7.4 + §8.7-8.9 | 运维与并发 | 4 | 3–4 PR |
| 6 | `add-execution-audit-snapshots` | D10 + D11 + tasks §1（execution snapshot 字段）+ build-orchestration delta + §7.6 | 审计、历史与人工反馈迭代证据 | 1–5 中任意一个落地后即可加 | 1–2 PR |

---

## 七、各子题案详情

### 1. `add-execution-workspace-and-profile-per-category` — 项目侧 execution workspace + 按方向建 profile

> **提案已起草并通过 `openspec validate --strict`**：[openspec/changes/add-execution-workspace-and-profile-per-category/](openspec/changes/add-execution-workspace-and-profile-per-category/)（proposal/design 含 41 项 patch 记录 + spec 含 14 项 SHALL Requirement 与 24 个 scenario + tasks 6 章节 + operator runbook）。

**一句话**: 每个方向一个 Hermes profile（`cf-web` / `cf-pwn` / `cf-re`），每次 build execution 创建项目侧 `work/executions/<workspace_id>/` 干净工作区；Hermes 的 prompt/runtime context 只暴露本次 materialize 的 `input/`、`references/` 和 `output/` 相对路径，调用前 preflight 验证 profile 存在、shard 可读、output 可写、category 一致，失败 fail-closed 不调 Hermes。

**关键设计选择（v4-v5 迭代后定型）**:
- **不使用 Hermes `-w` / Git worktree** 作为核心隔离——worktree 是 git 分支工具，不适合 runtime artifact 隔离
- **不新增数据库表**：`workspace_id` 优先取 `build_attempt_id`，legacy shard 用 `manual-<uuid>`；DB `execution_id` 从题案 3 引入
- **Hermes Docker backend 假设显式化**：preflight 在主机视角执行；Docker backend 需要 operator 在 `cf-<category>` profile config 把 `work/executions/` 挂进容器，否则主机 preflight 通过但模型读不到。in-sandbox visibility probe 推迟到后续题案。上线后强制单题 smoke test 兜底
- **写入隔离仍由题案 2 兜底**：本题案的 promotion 只是 narrow compatibility bridge，题案 2 publisher 落地时整段 REMOVED

**IN scope（结构化摘要）**:

1. **Profile 准备（operator 一次性手动）**：
   ```bash
   hermes profile create cf-web  --description "Generates web-category CTF challenges"
   hermes profile create cf-pwn  --description "Generates pwn-category CTF challenges"
   hermes profile create cf-re   --description "Generates reverse-engineering CTF challenges"
   ```
2. **共享 `_build_arguments` helper**：把 `hermes/research.py` 和 `hermes/design.py` 重复的 chat-index 注入函数提取到 `hermes/process.py`，research/design/build 三处统一调用（顺手消掉一处旧债）
3. **Workspace 布局**：
   ```
   work/executions/<workspace_id>/
     input/{shard.json,manifest.json}            # copy（claim 快照）
     references/                                  # symlink（静态资源不复制）
     output/                                      # Hermes 写入区
     bin/progress                                 # 进度蜘蛛（jq 或 python3）
     logs/{hermes.log,report.json,progress-events.jsonl}
     quarantine/<category>/<dirname>/             # promotion 时旧 canonical 的留存
   ```
4. **Materialize 策略**：

   | 内容 | 处理 | 理由 |
   |---|---|---|
   | `shard.json` / `manifest.json` / generation profile snapshot | **copy** | claim 时刻快照，避免运行中被改 |
   | `references/<category>/` / common guidance | **symlink** | 静态、可能上 MB，复制浪费 |
   | revision `base-artifact/`（题案 3 引入） | symlink read-only | 上一轮 output 引用 |
   | resume 已有 canonical challenge dir | **copy** 到 `output/challenges/<cat>/<id>-<slug>/` | 模型编辑 workspace 副本，避免污染 canonical |
5. **Prompt 路径全部相对** (`./input/shard.json` / `./output/` / `./logs/report.json` / `./bin/progress`)；禁止嵌入宿主机绝对路径
6. **Preflight 7 步**：profile 存在性（`profile_exists()` 复用，缺失消息含 `hermes profile create cf-<category>`）→ shard 可读 → category 一致 → output 可写 → 无其他 challenge 残留（regex `(web|pwn|re)-\d+`，含 symlink target）→ reference symlink 不越界
7. **Hermes argv**：`-p cf-<category>` 注入 + subprocess `cwd = workspace`；regression test 断言 cwd ≠ `paths.root`
8. **Output promotion（compatibility bridge，题案 2 落地后 REMOVED）**：
   - 仅 `input/shard.json` 中列出的 challenge_id 才 promote
   - 必须匹配 `./output/challenges/<category>/<id>-<slug>/` 布局；非合规布局拒绝 promote
   - 拒绝 symlink / `..` traversal / 缺 metadata.json / metadata id 或 category 不匹配 / 同一 id 多个 output dir
   - 原子 rename；若 canonical 已存在则先 quarantine 到 `work/executions/<workspace_id>/quarantine/<category>/<dirname>/`
   - 不删除非本次 claimed 的目录
   - **validation 失败不自动回滚**：quarantine 保留供 operator 手动 recover；新 canonical 留在原地带 `solve_status=failed`
9. **Report sync**：`./logs/report.json` import 到 `work/reports/<running-shard-stem>.report.json`，让 `domain.reports.merge_reports()` 和 DashboardService 不断链
10. **进度蜘蛛 = JSONL spool + 实时尾随（必须）**：
    - `./bin/progress` 必须用 `jq` 或 `python3` shebang（raw POSIX sh 因 JSON 转义脆弱被拒绝）；二者均不在 PATH 则 fail-closed
    - host runner 后台 thread 以 ≤2s poll 周期 tail `./logs/progress-events.jsonl`，写入现有 `ProgressStore`，保留 dashboard 实时刷新
    - Hermes 退出后强制 catch-up read 一次
11. **Workspace GC（最小版本）**：每次创建 workspace 前扫 `work/executions/manual-*`，删除 >7 天或孤儿；UUID-attributed workspace 不动（归题案 2 publisher）；dry-run 禁止 GC
12. **Hermes log redirect**：从 `paths.logs / f"{shard_name}.log"` 改到 `work/executions/<workspace_id>/logs/hermes.log`；research/design log 不动
13. **回归测试包**：
    - 残留题目不可见（核心串题事故复现测试）
    - profile 缺失 fail-closed + 错误消息含恢复命令
    - subprocess.Popen(cwd=...) 真等于 workspace
    - GC 行为正确（删 manual 老的、保留 UUID 的，dry-run 不跑 GC）
    - 进度蜘蛛处理特殊字符（`"` / `\` / `\n` / CJK / emoji）
    - 实时 tailing 在 fake-Hermes 跑完前就能看到 ProgressStore 有记录
    - validation 失败但 promotion 成功后 quarantine 留存、不自动回滚
    - 进度蜘蛛在 jq/python3 都不在 PATH 时 fail-closed
    - `domain.reports.merge_reports()` 能看到 workspace import 来的 report
14. **Operator runbook**:
    - 部署机一次性 `hermes profile create cf-{web,pwn,re}`
    - Docker backend 要在 `cf-<category>` config 加 mount `./work/executions:/work/executions:rw`，并确保镜像里有 `jq` 或 `python3`
    - 上线后强制单题 smoke test，看 Hermes log 里模型真能读到 `./input/shard.json` 再放量

**OUT scope**: agent 表、capability 强制、lease/fencing、写入 allowlist 完整版（仅 narrow promotion）、supervisor、Dashboard 视图改造、in-sandbox visibility probe、自动 rollback、Windows 平台。

**涉及代码**: [src/hermes/process.py](src/hermes/process.py)（共享 helper + 命令拼接）、[src/hermes/runner.py](src/hermes/runner.py)（workspace + cwd + log redirect + promotion + report sync + 进度 live tailer）、[src/hermes/prompt.py](src/hermes/prompt.py)（去主机绝对路径）、[src/services/research_agent_executor.py](src/services/research_agent_executor.py) + [src/services/design_agent_executor.py](src/services/design_agent_executor.py)（迁移到共享 helper）、[src/core/paths.py](src/core/paths.py)（新增 `executions` property + initialize 入口）。

**DB schema**: 不动。题案 1 的 `workspace_id` 只在文件路径和日志中，不作为数据库外键或审计主键。

**Spec deltas**: 改造 `hermes-execution-protocol`，加 5 个 Requirement + ~24 个 scenario；明确 Git worktree 不属于运行时隔离边界；narrow promotion bridge 标记为 "SHALL be REMOVED by add-staged-publication-allowlist"。

**量级**: 比原估"1 PR"明显增加。proposal/design/tasks/spec 总计 ~1200 行；实现量预估 2-3 个 PR（共享 helper 提取 → workspace/preflight 主体 → 进度蜘蛛/live tailing/report sync 补丁链）。

---

### 2. `add-staged-publication-allowlist` — 产物 Staging + 准入清单

**一句话**: Hermes 只能被要求写到 execution workspace 的 `./output/`；主机侧 publisher 跑 allowlist 校验通过后才原子 publish 到 `work/challenges`。

**为什么仍需独立题案**: execution workspace 解决"输入面干净"，没解决"模型写到不该写的地方"。Hermes 文档明示 profile ≠ sandbox（"agent 仍拥有与你的用户账户相同的文件系统访问权限"），写入隔离必须项目侧自建。

**IN scope**:
- staging 路径定义为 execution workspace 内的 `./output/`
- 主机 publisher 拒绝: symlink / special file / 绝对路径 / `..` traversal / 非预期 category 根 / 非预期 challenge id / metadata id-category 不匹配
- 新增 staging 专用路径安全扫描；再调用或扩展 [ChallengeValidator](src/domain/validation.py) 在 staging 根上做确定性校验，避免直接依赖现有 `work/challenges` 扫描语义
- 全部通过后才原子 rename 到 `work/challenges`，写入 output manifest hash
- 失败时 staging 留存供审计；`work/challenges` 不动
- **Retention policy**: 成功 publish 后清理 execution workspace 的临时输入和可重建缓存；失败保留 quarantine 受 bounded retention 限制（默认: 失败保留 last 20 个或 7 天，二者取严）
- **Change-policy 硬约束**（题案 3 引入 revision 后生效）: 当 execution 有 `base-artifact/` 和 `change-policy.json` 时，publisher 必须 diff `output/` vs `base-artifact/`，对 metadata 的 identity 字段（`challenge_id`、`flag`、`category`、`build_status`）和 `change_policy.preserve` 列出的路径执行硬约束——任何被改动一律 reject。`change_policy.forbid` 列出的路径出现新建文件也 reject。其余字段（题面文本、Dockerfile 内容）按软约束，仅作为 prompt 输入交给模型自我约束
- 回归测试: Web execution 输出包含 `pwn/pwn-0001-*` → 整次执行 fail scope validation → 没有任何文件被 publish
- 回归测试: revision execution 修改了 `metadata.challenge_id` → publisher diff 命中 identity 字段变动 → reject 并保留 staging

**OUT scope**: lease/fencing、agent registry、supervisor。token 重校验留给题案 3。

**依赖**: 题案 1（共享 execution workspace + staging 路径约定）。

**Spec deltas**: 新建 `worker-pool-execution` capability（首次引入），只放 staged publication 相关 Requirement 与对应 scenario。

---

### 迭代语义：retry / revision / revalidate

为后续"在上一轮题目基础上按人工反馈修改"预留三类动作，避免全部混成重新跑：

| 动作 | 是否调用 Hermes | 是否产生新 execution | 语义 |
|---|---|---|---|
| `retry` | 是 | 是 | 运行失败、环境失败或模型失败后，按原目标重新执行；通常不携带人工修改意见 |
| `revision` | 是 | 是 | 人工认为镜像、考点、题面或实现偏离预期，基于上一轮产物和反馈进行定向修改 |
| `revalidate` | 否 | 否或只写校验事件 | 不改产物，只重新跑主机侧 validation / publisher 检查 |

`revision` 的下一轮 workspace 应额外 materialize：

```text
input/
  base-artifact/                  # 上一轮 output 的快照或只读引用
  previous-output-manifest.json
  feedback.json                   # 人工反馈快照
  change-policy.json              # 本轮允许改/必须保留/禁止改的边界
```

典型人工反馈场景：
- "基础镜像从 Ubuntu 22.04 改为 Alpine，但保留 challenge_id 和目录结构"
- "考点从 SSRF 改为 JWT key confusion，不要重新生成无关题目"
- "题面表达可以重写，flag 格式、交付目录、validate.sh 合约不能变"

这类动作属于同一个 `build_attempt` 下的后续 `execution`，而不是创建一个无关联的新题。

---

### 3. `add-execution-lease-and-fencing` — Execution 行 + 租约 + Fencing Token

**一句话**: 每个 execution 在 PG 里有一行带租约、fencing token 和迭代关系的记录；过期 worker 即使 Hermes 还在跑，也不能 publish 或标记完成；同一 build attempt 可以串起多轮 retry/revision execution。

**为什么需要**: Hermes session 不知道外部 lease，多 worker 并发或 worker 崩溃恢复场景下需要项目侧 fence。

**IN scope**:
- `executions` 表（id, build_attempt_id, parent_execution_id, iteration_no, execution_kind, worker_id, claim_token, lease_expires_at, heartbeat_at, status, started_at, finished_at, exit_class）
- `execution_kind` 白名单：`initial` / `retry` / `revision`
- 同一 `build_attempt_id` 下 `iteration_no` 单调递增；`revision` 必须引用 `parent_execution_id`
- claim 是单事务: select 可领 attempt → 生成 token + lease → 建 execution 行 → 切 attempt 状态
- heartbeat / publish / complete / fail 都校验当前 token，旧 token 一律拒绝
- lease 过期回收时签发新 token；旧进程可以本地跑完，但不能 publish
- 把题案 2 的 publisher 增加"publish 前重校验 token"
- `revision` claim 会把 parent execution 的 output manifest、base artifact 引用和人工反馈快照写入新 workspace 的 `input/`
- **反馈入口**：人工通过 `POST /api/build-attempts/{id}/feedback` 提交结构化反馈（`summary` / `requested_changes` / `preserve` / `forbid` / `reviewer`），本题案只负责 schema、持久化和 materialize；管理端 UI 入口归题案 4 或独立反馈题案，不在本 scope
- **`revalidate` 不创建 execution 行**：只在原 execution 上追加一条 `revalidation_events` 记录（检查项 / 结果 / 时间戳 / 触发者），由 [BuildReconciler](src/services/build_reconciler.py) 复用现有逻辑执行
- **存量数据迁移**：新 execution 行只适用于迁移后 claim 的 build_attempt；迁移瞬间 in-flight 的 attempt 由 BuildReconciler 通过 legacy 路径完成本轮，不补建 execution 行。Migration 章节须显式列出这一边界
- 回归测试: execution E1 lease 过期被恢复签发新 token → E1 旧进程跑完后 publish 请求被拒 → 输出留 quarantine
- 回归测试: execution E1 产物镜像/考点偏离 → 人工提交 feedback → execution E2(kind=revision,parent=E1,iteration=2) 只 materialize E1 产物和反馈，不能重新领取无关 shard
- 回归测试: `revalidate` 触发后不出现新 execution 行，原 execution 上 revalidation_events 追加一条

**OUT scope**: agent 概念（worker_id 可先是字符串标识，不强约束到 agent 表）、capacity、supervisor、反馈管理 UI。

**依赖**: 题案 2（共享 execution 行与 publisher 流程）。

**Spec deltas**: 在 `worker-pool-execution` 加 lease/fencing Requirement，改 `build-orchestration` 加 current execution 引用。

---

### 4. `add-project-agent-layer-over-hermes-profiles` — 项目侧元数据薄层

**一句话**: 对已有 Hermes profile 加一层项目侧元数据（capability + concurrency + control_state），让 dispatch 在 DB 层硬约束授权；profile 名 ≠ 权限。

**v3 保留**: 仍采用"对 Hermes profile 加项目侧标签"的薄层方案。agent 表保持轻量，不把 profile CRUD 或运行时沙箱职责搬进项目侧。

**默认配置**: 题案 1 已建好 3 个 Hermes profile（cf-web/cf-pwn/cf-re），本题案的 agent 表初始就是 3 行 1:1 绑定：

```
agent_id  profile_name  capability   max_concurrency  control_state
--------  ------------  -----------  ---------------  -------------
web-01    cf-web        build:web    2                enabled
pwn-01    cf-pwn        build:pwn    2                enabled
re-01     cf-re         build:re     2                enabled
```

**IN scope**:
- `agents` 表 5 列（id/name, profile_name, capability, max_concurrency, control_state, heartbeat_at, last_error, soft_deleted_at）
- 对题案 3 已建 `executions` 表执行 `ALTER ADD agent_id` 可空外键；保留原 `worker_id` 字符串作为 legacy/cli 路径的兼容字段，agent 路径的 execution 同时填两列，保证 `worker_id` 查询和 `agent_id` 查询语义一致
- 校验 profile 在 Hermes 侧存在（包装 `hermes profile show`，复用 [src/hermes/process.py](src/hermes/process.py) 已有的 `profile_exists()`）
- capability 由项目硬约束（白名单：`research`/`design`/`build:web`/`build:pwn`/`build:re`），不依赖 profile.description
- claim 路径加授权: 解析 agent → 校验 enabled + 匹配 `build:<category>` → 复用题案 3 的 lease/fencing
- 控制状态机 (`enabled`/`disabled`/`draining`) 与健康 (`stopped`/`idle`/`running`/`offline`/`error`) 解耦
- HTTP API: `/api/agents` CRUD + enable/disable/drain/soft-delete
- Dashboard Agents 视图（仅 agent 元数据：profile/capability/concurrency/state，不含 supervisor 控制——那归题案 5）
- **不重新实现 Hermes profile CRUD**：创建/删除 profile 仍走 Hermes 原生命令；项目 UI 只提供 "查看现有 profile" 和 "绑定到 agent" 入口
- 不迁移现有 research/design profile bindings
- 回归测试: Web/Pwn attempt 共存 → agent web-01 仅 `build:web` → 只能领 Web，不能领 Pwn（即使 cf-web profile 的 description 写得不严谨）
- 回归测试: legacy CLI 路径起的 execution `worker_id` 非空 `agent_id` 为 NULL，agent 路径起的 execution 两列都非空且一致

**OUT scope**: supervisor / slots / 并发限制（题案 5）；审计快照（题案 6）。

**依赖**: 题案 3（execution 行已有 worker 概念，本题案让 worker = agent + slot 雏形）。

**Spec deltas**: 新建 `worker-agent-management` capability（首次引入），明确"agent 是 Hermes profile 的项目侧标签层"。

---

### 5. `add-local-supervisor-and-slots` — 单主机 Supervisor + 并发控制

**一句话**: 单主机 supervisor 用 PG advisory lock 选主，根据 enabled agents 协调 bounded slots，统一受 per-agent 和全局并发上限约束；支持 drain、重启 backoff、leader 失主切换。

**为什么需要**: Hermes 不管并发编排；多个 Dashboard 进程各自启 worker 会无限并发。

**IN scope**:
- `agent_slots` 表（agent_id × slot_index，状态 idle/busy）
- supervisor 用 PG advisory lock 选主，多 Dashboard 进程互斥
- 协调循环: enabled agents → bounded slots（per-agent + global）；capacity 预留与 claim 同事务，避免超额
- 启动 worker 子进程时显式传 agent_id / slot_id / execution_id / profile / token
- 进程组管理: SIGTERM → 5s → SIGKILL；leader 失主时停止派发新 claim 但不强杀在跑 execution
- drain 语义: 不再领新 work，已有 execution 跑完
- 启动失败 backoff
- Pool 启动 readiness 来自服务端派生，前端不能从"有 enabled agent"推断（fail-closed）
- API: `/api/pool/status|start|stop|drain`，`/api/executions/list|detail|log`
- Dashboard: Pool 控制 + Executions 视图
- 验证: 在每个状态转换点 kill supervisor / slot / Hermes / Dashboard，断言可恢复
- 单 slot soak 通过后才能开多 slot

**OUT scope**: 跨主机调度（原题案 non-goal）；audit snapshot（题案 6）。

**依赖**: 题案 4（agent 概念已存在）。

**Spec deltas**: 在 `worker-pool-execution` 加 supervisor + capacity + fail-closed readiness 相关 Requirement。

---

### 6. `add-execution-audit-snapshots` — 不可变审计快照 + Legacy 隔离

**一句话**: Execution 行保留 claim 时刻的 agent/profile/category/sandbox policy/manifest hash、父子迭代关系和人工反馈快照；后续 agent 改绑或软删不重写历史；legacy `challenge-factory run` 与 pool 路径互不相干。

**IN scope**:
- `executions` 表加字段: agent_name_used, profile_name_used, category_used, model_provider, sandbox_policy_version, input_manifest_hash, output_manifest_hash, base_artifact_manifest_hash, feedback_snapshot_hash, change_policy_hash, token_generation, exit_classification, log_paths
- claim 时填充，后续不可变
- `build_attempts` 加 nullable current/latest/successful execution 引用 + agent/profile/category 快照
- `execution_feedback` 或等价 JSON 快照记录人工反馈：source、reviewer、summary、requested_changes、preserve、forbid、created_at；快照写入后不可变，后续 revision 只引用 hash
- publisher 成功时记录 `successful_execution_id`，让最终 `work/challenges` 能追溯到具体哪一轮 revision
- 不重写历史 attempt
- Legacy `challenge-factory run --worker W` 仅保留作为 explicit 全队列 shard 管理用途
- Dashboard category-specific / pool 视图不调用 legacy 路径
- 不暴露 profile secrets / 原始 env / 子进程命令
- 回归测试: execution E1 用 profile cf-web@v1 → agent 改绑到 cf-web@v2 → E1.profile_name_used 仍为 cf-web@v1
- 回归测试: 人工反馈要求"镜像改 Alpine、考点改 JWT key confusion、保留 challenge_id" → E2 feedback_snapshot_hash 固定，E2 output_manifest_hash 与 E1 不同，E1 历史不被覆盖

**OUT scope**: 无。本题案是"加字段 + 接线"。

**依赖**: 题案 1–5 中任意一个落地后即可开始；不强制顺序。建议在题案 5 之后做完整收口，但也可以在每个前置题案落地时滚动加对应字段。

**Spec deltas**: 在 `worker-pool-execution` 加 audit Requirement，改 `build-orchestration` 加 attempt 上的 snapshot 字段，加 legacy 隔离 scenarios。

---

## 八、推进建议

1. **先 commit 已完成的 bug 清理 + 上次 4 个题案的归档移动**（两个独立 commit），让题案 1 有干净起点
2. **按 1→2→3→4→5 顺序推进**；题案 6 可在每个前置题案落地时滚动加字段
3. **每个新题案独立创建**: 复制原 `add-agent-worker-pool-management/` 的格式，scope 严格限定本文档对应行
4. **不要直接修改原题案**，将原 `add-agent-worker-pool-management` 标记为 "superset, deprecated by [题案 1-6 的名字]"，待 6 个子题案全部归档后一并归档
5. **题案 1 落地后做一次端到端实跑**: 检查 `work/executions/<workspace_id>/input/`、`output/`、`logs/` 是否只包含本次执行材料，验证 prompt/log 不再暴露宿主绝对 shard 路径；验证"串题不再发生"的最佳办法不是单测，是实跑
6. **题案 1 上线前**: 在部署机上一次性手动 `hermes profile create cf-web/cf-pwn/cf-re` 三条命令；后续 profile 管理走 Hermes 原生
7. **首次实现人工反馈迭代时**: 先只支持同一 `build_attempt` 内基于 latest failed/review_required execution 的 `revision`，不要同时引入跨 attempt 克隆或多分支候选，避免状态面过宽
8. **6 个子题案归档后再补一个轻量收尾题案** `add-build-attempt-feedback-ui`: 题案 3 落地后人工反馈已经能 `curl POST /api/build-attempts/{id}/feedback`，但长期可用性不足。收尾题案纯前端工作——在已有 build-attempts 详情页加反馈表单 + 展示历史反馈 + 触发 revision 按钮；schema/API/audit 全部已就绪

---

## 九、依赖关系图

```
题案1 add-execution-workspace-and-profile-per-category
   │   （项目侧 execution workspace + per-category profile）
   ↓
题案2 staged-publication-allowlist
   │   （execution output → 主机 publisher 把关 → publish 到 work/challenges）
   ↓
题案3 execution-lease-and-fencing
   │   （引入 executions 表 + token + parent/iteration，防过期 worker 脏写并支持同题多轮 revision）
   ↓
题案4 project-agent-layer-over-hermes-profiles
   │   （在 Hermes profile 上加薄层 capability + control_state）
   ↓
题案5 local-supervisor-and-slots
   │   （选主 + slot 池 + drain + readiness）
   │
   └─→ 题案6 execution-audit-snapshots
         （1-5 任意一步后均可开始滚动加字段）
```

**关键决策回顾**:
- 题案 1+2 阻断"串题"事故（execution workspace 解决输入面，publisher 解决写入面）
- 题案 3 加并发安全（lease + fencing）
- 题案 3+6 为"基于上一轮产物按人工反馈修改"提供 execution 链、反馈快照和 manifest 证据
- 题案 4+5 引入工厂规模化所需的运维抽象（agent 元数据 + supervisor）
- 题案 6 提供事故复盘所需的历史证据

**与 Hermes 原生的边界**:
- 复用：profile 状态/人格/模型隔离（题案 1+4）、profile CRUD（题案 4 不重新实现）
- 可选：Git worktree 仅作为开发者多分支调试工具，不作为 worker pool 运行时隔离依赖
- 自建：execution workspace（题案 1）、写入 allowlist（题案 2）、lease/fencing（题案 3）、capability 硬约束（题案 4）、supervisor（题案 5）、audit（题案 6）

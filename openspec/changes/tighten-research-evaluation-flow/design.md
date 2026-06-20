## Context

Research 子系统在生产环境暴露出来的契约漏洞，根因不是结构性的而是渐进性的：每一个失败 case 单独看都不大，叠加在一起就让操作员每次提交都要盯着 log 才能确信流程在跑。本提案把这批可证据漏洞一次收齐。

详细 finding 与现状证据见 `docs/research-logic-assessment-2026-06-20.md` 与会话评估记录。本 design 只覆盖工程实现的关键决策与取舍。

涉及模块（按 R 编号映射）：

- **R1**：`src/hermes/research.py`（env allowlist）+ `src/services/research_agent_executor.py:_resolve_profile_name`（fail-fast）。
- **R2**：`src/services/research_agent_executor.py:_normalize_source_payload` + `src/services/research_job_service.py`（promote/cleanup 钩子）+ `src/web/server.py:serve`（启动 sweep）+ `src/services/resource_deletion_service.py`（删除覆盖）。
- **R3**：`src/web/research_endpoints.py:_start_worker_from_request`（preflight）+ `src/web/research_worker_manager.py:start`（handshake）+ `src/services/research_worker.py`（worker touch ready）。
- **R4**：`src/web/research_endpoints.py:submit_request`、`src/services/research_job_service.py:submit_request`、`src/cli.py research submit`、`src/domain/research_validators.py:validate_runtime_constraints`（新）。
- **R5**：`src/services/research_job_service.py:submit_request`（lookup）、`src/web/research_endpoints.py:submit_request`（header 透传）、新增 alembic 迁移。
- **R6**：`src/web/research_endpoints.py`（响应字段、filter）、`src/web/static/js/views/research-*.js`（视图按 display_status 显示，sit on top of新字段）。
- **R7**：`src/domain/research_validators.py`（URL/sha256/min coverage）+ `src/services/research_agent_executor.py`（尾部 JSON 解析、apply gate）+ 新增 alembic 迁移把 `(run_id, content_hash)` 升 UNIQUE。

## Goals / Non-Goals

**Goals:**

- 把"今天能用但每步都得人盯着确认"的研究流程，变成 what-you-see-is-what-you-get：toast 状态、API 返回字段、UI 显示三处对齐持久化态。
- 让 Hermes subprocess 拿不到 DB 凭证；profile 配错立刻 fail-fast。
- 让 raw text 文件与 DB 行寿命一致：DB 没 commit 就别留落地文件，DB 删了文件也跟着删。
- 让 scoped worker 启动 toast 真实反映 worker 是否在干活。
- 让 web submit 能完整传 `runtime_constraints` 并阻挡未知 key，与 CLI 对齐。
- 让重复点提交不再重复消耗 Hermes 调用。
- 让 research 输出在结构合规之外，加一道最小质量门：URL/hash 形态、去重、最少 finding 数。

**Non-Goals:**

- 不评估研究**内容**质量（finding 的"主题切题度""技术新颖度"等需要 NLP/embedding 判定 —— 留给 `add-cross-request-research-novelty`）。
- 不调整 UI 视觉与中文 copy（留给 `revamp-research-design-task-ui`）。
- 不重写 lost-lease 终态契约（留给 `define-research-cancellation-terminal-states`）。
- 不动 Hermes 心跳 / 双 worker 资源浪费（非提案 hardening PR）。
- 不动 research_runs 状态机（仍是 `queued|running|completed|failed`）；R6 只在 generation_requests 这层加 display 字段。

## Decisions

### D1. R1 env：allowlist + 关键字 deny 双重防御

**选**：白名单作为正常通道；同时无论 key 是否在白名单，**只要包含**任一关键字片段（不区分大小写）就 drop：`DATABASE | POSTGRES | PASSWORD | TOKEN | SECRET | PRIVATE_KEY`。

**理由**：白名单只能挡掉已知 key；关键字 deny 是**误操作防御** —— 几个月后某个维护者把 allowlist 文字一改加进 `DATABASE_TIMEOUT`，凭证还是不会泄。"白名单+关键字"两道独立检查比单条规则可读且抗腐化。

**取舍**：少数名字里有 `TOKEN` 但不是密钥的（如 `GH_API_LIMIT_TOKEN_BUCKET_SIZE`）会被误杀。可接受 —— 真的需要时可显式加进 prompt 的 allowlist 文档外列单独豁免，且要在 PR 描述里证明该值不是凭证。

### D2. R1 profile fail-fast，不要 grace period

**选**：binding 缺失或 `disabled` 直接 `failed`，不留"先跑 default + WARNING"过渡。

**理由**：当前生产无 default-binding 的"事实依赖"存量（部署刚起没多久），切硬契约最便宜。grace period 实际只增加观测难度（操作员不会读 WARNING）。

**取舍**：如果后期要回 grace，可加一个 `RESEARCH_PROFILE_FALLBACK=warn-only` 环境变量临时打开，不破现有 spec。

### D3. R2 staging 用同 fs 同根的兄弟目录

**选**：`work/research/sources_staging/<run_id>/` 与 `work/research/sources/<run_id>/` 同父，用 `rename(src, dst)` 原子提升。

**理由**：rename 在同 fs 内是原子操作（POSIX 保证）。"先写最终路径 + 失败 rollback 删除"的反面方案在 server crash 半途时不可恢复。

**取舍**：操作员可能误把 sources_staging 当成正常数据；通过 README + log 注释化解。

### D4. R2 启动 sweep 阈值 300s

**选**：超过 300s 的 staging 目录视为孤儿。

**理由**：合法 staging 生命周期 = Hermes 调用 + DB commit，典型 30–120s，3 分钟封顶；server 重启窗口期不应该有 staging 同时处于"5 分钟内但实际已弃"的状态。

**取舍**：如果未来 Hermes 单次执行 > 5min，会把活的 staging 误清。届时把阈值改成 `max(300, HERMES_TIMEOUT + 60)` 即可。这里写死 300s 是为了简单 + 可读。

### D5. R3 handshake 用文件标记而不是 stdout marker

**选**：worker subprocess 在导入完 + DB pool 起好 + 即将 `claim_next_run` 之前 `touch work/research/worker_handshake/<pid>.ready`。manager 用 `select` 风格的轮询（≤100ms 间隔）等文件出现，5s 上限。

**理由**：stdout marker 需要 manager 持续读 subprocess 输出，复杂度高且容易跟现有日志管道冲突；文件 marker 简单可读、容易在测试里 stub。

**取舍**：依赖文件系统 latency；典型 < 5ms，不构成瓶颈。如果未来 worker 跑在容器/远程，再换 socket 或 healthcheck endpoint。

### D6. R3 preflight 跟 spawn 分两步开事务

**选**：preflight 是只读 SELECT，独立短事务；spawn 不在事务内（subprocess 启动不能持锁）。

**理由**：preflight 拿到的 status 在 spawn 时可能已经过时（毫秒级 TOCTOU），但 worker 自身在 claim 时还会用 `SELECT ... FOR UPDATE SKIP LOCKED` 重判，所以 preflight 是"看得见的可信反馈"，不是事务一致性边界。

**取舍**：极少数 race 下"preflight 通过 → claim 时无活可干 → worker 立刻 exit"仍可能发生。toast 仍然显示 started 但 worker 几秒内死。这是已知 corner case；可以在 worker 主循环里加一句"开 5s 内 claim 0 次时主动写 `worker_exited_no_work` 日志"作为后续 hardening。

### D7. R5 idempotency key TTL = 1800s 默认

**选**：默认 30 分钟；可由 `RESEARCH_SUBMIT_IDEMPOTENCY_TTL_SECONDS` 覆盖。

**理由**：30min 覆盖典型"前端连点 / 脚本重试"窗口；过短不防 retry，过长会让真心想重提的操作员被卡很久。

**取舍**：与 F13 的 cross-request novelty 不冲突 —— 本条只针对**带 key 的明确去重意图**，无 key 时维持现状；novelty 是无关 key 的语义相似性判定。

### D8. R6 一次性切完 `submit response` 字段

**选**：去掉硬编码 `"status": "queued"` 顶层字段；改返 `{request, latest_run}` 双对象。

**理由**：保留旧字段并行支持会让 spec 长期带"deprecated since vX"的负担；现在没有外部脚本化 caller，一次性 breaking 干净。

**取舍**：dashboard JS 需要同步更新；写在 tasks.md §6 里。

### D9. R7 stdout 末尾 JSON：括号匹配走带"字符串字面量感知"的逆向扫描

**选**：从 stdout 末尾找最后一个 `}`，逆向匹配大括号（计入字符串字面量里的 `\}` 转义和 `'..'`/`"..."` 边界），遇到匹配到 0 时取出子串 `json.loads`。失败则向左继续找下一个候选 `}`。

**理由**：Hermes 偶尔会在最终 JSON 之前打印"思考过程"，里面可能有半个看起来像 JSON 的片段。简单的"找最后一个 `{...}` block"会被这种噪声打断。带字面量感知的匹配能跳过 fake brace。

**取舍**：实现略复杂；用纯 Python 写在 `domain/research_validators.py` 一个 `extract_terminal_json_object(stdout) -> dict | None` 函数里，封装好就行。注意 unit-test 覆盖：转义、嵌套、注释 fake、未闭合等 case。

### D10. R7 dedup migration：先脚本后迁移

**选**：迁移 `0011` 先做 sanity check（重复仍存在则 raise，让 alembic 失败回滚），不在迁移里自动 dedup。

**理由**：dedup 涉及 finding 引用重写，逻辑应由独立可重跑脚本 `tools/scripts/dedup_research_sources.py` 持有，运维可先 dry-run 检查影响范围，确认后再实际跑。一旦把 dedup 嵌进迁移会让回滚极难（因为删过的 source 数据丢了）。

**取舍**：多一个手工步骤；通过 tasks.md §7 明确写出操作流程。

## Risks / Trade-offs

- **R1 + D2**：profile fail-fast 切硬契约。若团队未来增加新的 hermes agent role，必须先建 binding 才能用 → 操作复杂度小幅上升。**缓解**：CLI `profile bind` 已存在；写到 onboarding 文档。
- **R2 + D4**：staging sweep 阈值 300s 是经验值。若有用户配置 `HERMES_TIMEOUT > 240s`，理论上活 staging 可能被误清。**缓解**：实现层用 `max(300, HERMES_TIMEOUT + 60)` 而不是硬编码 300（spec 仍然写 300 作为默认）。
- **R3 + D5**：handshake 文件位置 `work/research/worker_handshake/<pid>.ready` 与多 worker 并行兼容。但**主机重启后 PID 可能复用**：扫 ready 文件时必须用 `/proc/<pid>` 或等价方式确认 PID 进程还活着，否则清理。
- **R4**：白名单太严 → 操作员遇到 unknown key 不知道怎么传。**缓解**：`experimental.*` 命名空间 + 错误响应里包含 allowed-keys 提示。
- **R5**：默认 TTL 1800s 对脚本场景可能太长（一个长跑脚本里多个 retry 间隔可能 > 30min）。**缓解**：env 可调。
- **R6 + D8**：submit 响应改字段 = breaking。**缓解**：dashboard JS 同 PR 改完；spec 明示。
- **R7 + D10**：dedup 操作要求运维参与。**缓解**：tasks.md §7 列清楚步骤。
- **D9 解析**：极端构造的 stdout 可能让解析器误选错误的 JSON。**缓解**：unit test 覆盖 + log 选中的 byte range 方便调试。
- **F13/F14 留白**：本提案修完后**仍然**会遇到重复 topic 产出重复题、UI 显示混乱两类问题。这是 by design，让操作员知道下一步要看哪两个提案。

## Migration Plan

1. PR 同时落 R1–R7 的代码 + spec 文档。先小 chunk per requirement，避免 review 爆炸。
2. 部署顺序（生产）：
   1. 运维 dry-run `tools/scripts/dedup_research_sources.py`，确认重复行数和影响范围；与操作员对一次"保留最早 id"是否可接受。
   2. 运维实际跑 dedup 脚本。
   3. `alembic upgrade head` 触发 `0010`（idempotency 字段）与 `0011`（content_hash UNIQUE）。`0011` 内的 sanity check 若仍报错则 alembic 自动回滚，运维回到 step 1 排查。
   4. server 走 `tools/scripts/serve.sh` 重启（清 pycache + 不写 bytecode）。
   5. 烟测：建一个测试 request → 启 worker → 看 toast 是"started"而不是"started 但 5s 后立刻 exit"；触发一次故意 fail（删掉 binding 然后启 worker）→ run 应该立刻 `failed: profile_not_bound`。
3. 回滚：
   - 代码层全部可 `git revert` 单 PR。
   - `0011` 可 downgrade（重建普通 index）；但已被 UNIQUE 拒掉的 source 不会自动恢复 —— 这是预期行为（它们本来就违规）。
   - `0010` 可 downgrade（drop 索引和列）。
   - `work/research/sources_staging/` 残留可由 server 启动 sweep 兜底，无运维干预需求。

## Open Questions

- **是否需要把 `R7.minimum_findings_ratio` 做成 env 可调？** 默认 0.5 是工程经验值；若操作员反馈 0.5 太严，临时切 0.4 应该够用。倾向先硬编码 0.5 + 在 spec 标注"未来视情况升级为可调"。
- **R3 handshake 失败时 PID 复用窗口的清理策略要不要 spec 明示？** 倾向在 tasks 里写实现细节但不进 spec —— 这是实现层的卫生。
- **R5 是否要把 `idempotency_key` 也加进列表过滤？** 暂不加；这个字段对操作员搜索价值低。

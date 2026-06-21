## 0. 现状 audit（实施前置）

- [x] 0.1 在生产 server 上 `env | sort` 截图存档；统计哪些 key 包含 `DATABASE/POSTGRES/PASSWORD/TOKEN/SECRET/PRIVATE_KEY`，作为 R1 allowlist 的现实校准。
- [x] 0.2 dry-run 一次 hermes research subprocess 看实际依赖的 env；审计方式必须平台感知：Linux 可用 `strace -e openat -f`，Windows 用 ProcMon/PowerShell 或测试替身捕获 `invoke_capture(..., env=...)`。如果有 R1 allowlist 之外的必需 key，加进 spec 附录。
- [x] 0.3 在生产 DB 跑 `SELECT research_run_id, content_hash, COUNT(*) FROM research_sources GROUP BY 1,2 HAVING COUNT(*) > 1` 看 R7 dedup 影响面；若 > 0 行，跟操作员对"保留最早 id 删晚的"是否可接受。

## 1. R1 — Hermes research subprocess 环境隔离 + binding fail-fast

- [x] 1.1 在 `src/hermes/research.py` 新增 `_build_research_env(paths: ProjectPaths) -> dict[str, str]`：实现 allowlist + 关键字 deny；替换 `os.environ.copy()`。allowlist 与 deny 关键字写成模块级常量便于 audit。
- [x] 1.2 把 `apply_legacy_custom_provider` / `remove_conflicting_custom_pool` 仍跑在新 env 之上（保持现行 provider 兼容）。
- [x] 1.3 在 `src/services/research_agent_executor.py:_resolve_profile_name` 把 `binding is None` 与 `binding.status != "enabled"` 两条 WARNING+fallback 改成 `raise ResearchValidationError("profile_not_bound")` / `("profile_disabled:<name>")`；executor 上层已有 `except ResearchValidationError: self._mark_failed_if_owned(...)` 捕获，直接复用。
- [x] 1.4 测试 `tests/app/test_hermes_research.py`：
   - `test_subprocess_env_excludes_database_url` —— mock `os.environ` 注入 `DATABASE_URL=foo` + `FOO_PASSWORD=bar`，断言 `invoke_capture` 收到的 environment 字典里都不在；`PATH` / `HERMES_HOME` 仍在。
   - `test_deny_keyword_survives_allowlist_addition` —— 临时把 `DATABASE_TIMEOUT` 强行写进 allowlist 常量，断言它仍然被 drop。
- [x] 1.5 测试 `tests/app/test_research_services_unit.py`：
   - `test_missing_binding_marks_run_failed_profile_not_bound`
   - `test_disabled_binding_marks_run_failed_profile_disabled`

## 2. R2 — Research artifact staging + promote + cleanup

- [x] 2.1 在 `src/core/paths.py` 加 `research_sources_staging: Path` property（指向 `work/research/sources_staging`）。
- [x] 2.2 把 `src/services/research_agent_executor.py:_normalize_source_payload` 里的 raw text 实际写入位置改为 staging 子目录；返回给 persistence 的 source payload 中 `raw_text_path` 字段记录**最终目标**路径（promote 后的位置），便于 DB 一次写对。
- [x] 2.3 在 `src/services/research_job_service.py` 新增 `_promote_staged_sources(run_id, paths)`、`_cleanup_staged_sources(run_id, paths)` 与 `_cleanup_final_sources(run_id, paths)`；promote 用同父目录原子 rename staging dir → final dir，目标已存在则 raise 一致错误。
- [x] 2.4 调整 `complete_run_with_results`：不能继续依赖自动 commit 的 transaction context manager；改用显式事务控制或给 transaction helper 增加 before-commit / commit-error hook，使同一终态事务里先 insert/flush sources/findings，再调用 `_promote_staged_sources`，最后 commit；promote 失败回滚 DB 并清 staging；commit 失败清 final；`StaleClaimError` / `ResearchValidationError` / parser error 路径清 staging。
- [x] 2.5 在 `src/web/server.py:serve` 启动序里加 `_sweep_stale_research_staging(paths, max_age_seconds=max(300, research_worker_manager.hermes_timeout_seconds + 60))` 与 `_reconcile_orphan_research_sources(paths)`：前者删除超龄 staging，阈值来自 research worker manager 的有效 timeout，不读取 shard 用的 `HERMES_TIMEOUT`；后者删除/隔离没有 completed run + persisted source rows 支撑的 final `sources/<run_id>/` 目录。
- [x] 2.6 在 `src/services/resource_deletion_service.py` 删除 generation request 的 collect 阶段把 `paths.work/"research"/"sources"/str(run_id)` 与 `paths.research_sources_staging/str(run_id)` 都加进 quarantine 列表。
- [x] 2.7 测试 `tests/app/test_research_services_unit.py`：
   - `test_failed_persist_leaves_no_staged_artifacts`
   - `test_successful_persist_promotes_atomically`
   - `test_promote_aborts_when_target_exists`
   - `test_startup_sweep_removes_stale_staging`
   - `test_startup_reconcile_removes_orphan_final_sources`
- [x] 2.8 测试 `tests/app/test_resource_deletion_service.py`：扩展现有 research-deletion 场景，断言 sources dir 也被回收。

## 3. R3 — scoped worker preflight + handshake

- [x] 3.1 在 `src/web/research_endpoints.py` 抽 `_preflight_scoped_research_worker(session, request_id) -> tuple[bool, int, dict]` 函数；按 design 三步检查；返回 `(ok, http_status, body_dict)`。
- [x] 3.2 在 `_start_worker_from_request` 里，scoped 分支先开短事务调 preflight；不通过直接返响应；通过才进 manager.
- [x] 3.3 在 `src/core/paths.py` 加 `worker_handshake: Path` property（指向 `work/research/worker_handshake`）。
- [x] 3.4 改 `src/services/research_worker.py` 主入口：导入完 + DB pool 起好后立刻 `(paths.worker_handshake / f"{os.getpid()}.ready").touch()`；正常退出 / 异常退出都 try-`unlink(missing_ok=True)` 删自己 marker。
- [x] 3.5 改 `src/web/research_worker_manager.py:start`：删 `time.sleep(0.2) + poll`；改为 `_wait_for_handshake(pid, timeout=5.0, interval=0.05) -> bool`；超时先用平台 API graceful terminate，1s 后仍存活则 force kill（POSIX 可映射为 `SIGTERM` → `SIGKILL`，Windows 用 `Popen.terminate()` / `Popen.kill()` 或 Win32 等价 API），读 stderr tail 进 response body。
- [x] 3.6 manager 启动时调一次 `_sweep_dead_handshake_files()`：遍历 `worker_handshake/*.ready`，PID 不存在的 unlink。探活实现必须跨平台：POSIX 可用 `os.kill(pid, 0)`，Windows 用 `psutil.pid_exists`（若依赖可用）或 Win32/OpenProcess 等等价实现。
- [x] 3.7 测试 `tests/app/test_research_api.py`：
   - `test_scoped_worker_404_when_request_missing`
   - `test_scoped_worker_409_when_already_researched`
   - `test_scoped_worker_409_when_final_failure_no_retry`
   - `test_scoped_worker_409_when_no_runnable_run`
- [x] 3.8 测试 `tests/app/test_research_worker_manager.py`：
   - `test_handshake_success_returns_started`
   - `test_handshake_timeout_kills_and_reports_stderr_tail`
   - `test_dead_handshake_files_swept_on_start`

## 4. R4 — Web submit runtime_constraints + 白名单

- [x] 4.1 在 `src/domain/research_validators.py` 新增 `validate_runtime_constraints(payload: Any) -> dict[str, Any]`：白名单 + 类型校验；未知 key（除 `experimental.*`）raise `ResearchValidationError`；返回规范化后的 dict。
- [x] 4.2 在 `src/web/research_endpoints.py:submit_request` 接收 `runtime_constraints`，调 validator，传给 `ResearchJobService.submit_request(..., runtime_constraints=...)`。
- [x] 4.3 在 `src/services/research_job_service.py:submit_request` 入口再调一次 validator（CLI 路径同样受保护）。
- [x] 4.4 在 `src/cli.py` 的 `research submit` 子命令加 `--runtime-constraint key=value`（`action='append'`），解析成 dict 喂给 service。
- [x] 4.5 在 prompt 渲染（`src/hermes/prompt.py` 或 `prompts/research_prompt.md` 的 placeholder）里确保 `runtime_constraints` 真的进 prompt 文本。
- [x] 4.6 测试 `tests/app/test_research_api.py`：
   - `test_submit_accepts_whitelisted_runtime_constraints`
   - `test_submit_rejects_unknown_runtime_keys_400`
   - `test_submit_accepts_experimental_namespace`
- [x] 4.7 测试 `tests/app/test_research_prompt.py`：runtime_constraints 出现在渲染结果里。

## 5. R5 — Submit Idempotency-Key

- [x] 5.1 新增 alembic 迁移 `alembic/versions/0008_generation_request_idempotency_key.py`：`add_column generation_requests.idempotency_key TEXT` + `add_column generation_requests.request_fingerprint TEXT` + `create_index ix_generation_requests_idempotency (idempotency_key, created_at DESC)`；downgrade 对称。
- [x] 5.2 在 `src/persistence/models/research.py:GenerationRequest` 加 `idempotency_key: Mapped[str | None]` 与 `request_fingerprint: Mapped[str | None]` 字段。
- [x] 5.3 在 `src/services/research_job_service.py:submit_request` 增加 `idempotency_key: str | None = None` 参数；非 None 时先校验 UTF-8 encoded length ≤ 256 bytes，再对 canonical intent（category/topic/target_count/difficulty_distribution/runtime_constraints/seed_urls/max_attempts，含默认值规范化）计算 lower-case SHA-256 `request_fingerprint`。进入 lookup 前必须在同一 submit 事务里获取同 key 串行化保护：优先用稳定 hash 拆成两个 int 调 `pg_advisory_xact_lock(...)`，或用单独 idempotency ledger 表的 UNIQUE key 达到等价效果。锁住后按 `idempotency_key AND created_at >= NOW() - INTERVAL :ttl` 查询最近命中：fingerprint 相同即返已存在 `{request, latest_run}` DTO；fingerprint 不同 raise conflict（HTTP 映射为 `409 idempotency_key_conflict`）；TTL 从 `RESEARCH_SUBMIT_IDEMPOTENCY_TTL_SECONDS` 读，缺省 1800，无效值 fall back + WARNING。
- [x] 5.4 在 `src/web/research_endpoints.py:submit_request` 读 `Idempotency-Key` 头透传；超长 key 返 `400`；命中改返 `200 OK` 而非 `201 Created`，且命中/新建都使用 `{request, latest_run}` 响应形状；同 key 不同 fingerprint 返 `409 idempotency_key_conflict`。
- [x] 5.5 测试 `tests/app/test_research_api.py`：
   - `test_idempotency_key_hit_within_ttl_returns_200_same_request`
   - `test_idempotency_hit_returns_submit_response_shape`
   - `test_idempotency_key_conflicting_body_returns_409`
   - `test_idempotency_key_concurrent_submits_create_one_request`
   - `test_idempotency_key_miss_after_ttl_returns_201_new_request`
   - `test_no_idempotency_key_always_creates_new`
   - `test_idempotency_ttl_env_override_respected`

## 6. R6 — 状态语义统一（status / display_status）

- [x] 6.1 在 `src/web/research_endpoints.py` 新增 `_derive_display_status(request, latest_run) -> str`：按 spec mapping 表实现。
- [x] 6.1a 在 `ResearchJobService` 终态转换和 lease-recovery 逻辑中维护 invariant：同一 generation request 最多一个 `queued|running` run，且 completed/final failed 后不得残留 runnable run；若读到历史脏数据（active run 与最新 terminal run 冲突），API 的 worker-start / design generation 路径返回 machine-readable conflict，不静默选择某条状态规则。
- [x] 6.2 修改请求行序列化函数把 `display_status` 加进所有返回 generation_request 的端点（list、detail、submit）。
- [x] 6.3 把列表 filter `?status=` 改为对持久化字段 `generation_requests.status` 过滤；不合法值（不在 `{draft, researching, researched, failed}`）返 `400`；新增 `?display_status=` 走派生（实现可以 fetch 后 in-memory 过滤，因为 derive 需要 latest_run）。
- [x] 6.4 删 `submit_request` 响应里硬编码 `"status": "queued"` 顶层字段；改返 `{request: {...}, latest_run: {...}}`。
- [x] 6.5 同步前端 `src/web/static/js/views/research-requests.js`：把状态徽章从读 `status` 字段切到读 `display_status`；submit 响应改读 `request.id` / `latest_run.status`。
- [x] 6.6 测试 `tests/app/test_research_api.py`：
   - `test_submit_response_uses_request_and_latest_run_objects`
   - `test_list_filter_status_uses_persisted_field`
   - `test_list_filter_display_status_uses_derived_field`
   - `test_list_filter_unknown_status_400`
   - `test_inconsistent_active_and_terminal_runs_rejected`
- [x] 6.7 测试 `tests/app/test_research_ui_smoke.py`（前端集成有就扩展，没有可跳）：断言徽章读 display_status。

## 7. R7 — 输出最小质量门 + content_hash UNIQUE

- [x] 7.1 在 `src/domain/research_validators.py` 新增：
   - `URL_RE = re.compile(r"^https?://[^\s]+$")`
   - `CONTENT_HASH_RE = re.compile(r"^[0-9a-f]{64}$")`
   - `extract_terminal_json_object(stdout: str) -> dict | None` —— 见 design D9
   - `apply_research_quality_gate(parsed, target_count) -> tuple[bool, str | None]` —— 按 spec 五条 code 依次检查；返回 `(ok, last_error_or_None)`
- [x] 7.2 在 `src/services/research_agent_executor.py` 把现有 `_parse_research_output` 拆成：先调 `extract_terminal_json_object` 拿 dict（拿不到 → `unparseable_output:no_terminal_json_object`）；然后调 `apply_research_quality_gate`；不通过则 `self._mark_failed_if_owned(run, agent_id, last_error, log_path)` 直接返回。
- [x] 7.3 新增 `tools/scripts/dedup_research_sources.py`：
   - 默认 `--dry-run`，列出每个 `(run_id, content_hash)` 组的所有 row，标出"保留 id=最早 created_at"和"删除 ids=其余"。
   - `--apply` 实际执行：(a) UPDATE `research_finding_sources.source_id`，把所有引用待删 source 的行改到保留 source，(b) DELETE 多余 `research_sources` 行；包在单事务里。
   - 输出统计与 affected request id 列表方便操作员复核。
- [x] 7.4 新增 alembic 迁移 `alembic/versions/0009_research_sources_content_hash_unique.py`：
   - upgrade：先 `SELECT COUNT(*) FROM (SELECT research_run_id, content_hash, COUNT(*) c FROM research_sources GROUP BY 1,2 HAVING c > 1) t`，> 0 则 `raise RuntimeError("run tools/scripts/dedup_research_sources.py --apply first")` 让 alembic 失败回滚。`drop_index('ix_research_sources_run_hash')`，`create_unique_constraint('uq_research_sources_run_hash', 'research_sources', ['research_run_id', 'content_hash'])`。
   - downgrade：反向。
- [x] 7.5 测试 `tests/app/test_research_validators.py`：
   - `test_url_shape_invalid_path`
   - `test_content_hash_shape_invalid_path`
   - `test_content_hash_dup_within_run_path`
   - `test_insufficient_findings_path`
   - `test_extract_terminal_json_object_ignores_leading_markdown`
   - `test_extract_terminal_json_object_handles_brace_in_string_literal`
   - `test_extract_terminal_json_object_returns_none_when_no_object`
- [x] 7.6 测试 `tests/app/test_research_repository.py`（Postgres-marked）：插重复 content_hash 应在 commit 时 IntegrityError。

## 8. design-task-planning 防御性 reject

- [x] 8.1 在 design task 生成入口 `DesignTaskPlanningService.generate_for_request` 与对应 API 映射加前置：必须读取真正 latest run（不是 latest completed run）；latest_run 缺失/queued/running/failed 返 `409 latest_run_not_completed`；latest_run completed 但 `findings_count < ceil(target_count * 0.5)` 返 `409 insufficient_findings`。
- [x] 8.2 测试 `tests/app/test_design_task_planning_api.py`：上述两条 reject 路径。

## 9. 文档与产物

- [x] 9.1 在项目 README / 操作员手册（如有）补一节"Hermes profile binding 已是 hard requirement"。
- [x] 9.2 在 OpenSpec 主索引 `openspec/changes/README.md`（若存在）加一行指向本提案。
- [x] 9.3 把 `docs/research-logic-assessment-2026-06-20.md` 顶部加一行 "Tracked by openspec change `tighten-research-evaluation-flow`"。

## 10. 上线 & 验证

- [x] 10.1 本地 `pytest tests/app -q` 全过。（651 passed，5 subtests passed；补齐环境隔离、质量门、handshake 与 idempotency 缺失回归。）
- [x] 10.2 dry-run 部署：运维跑 `tools/scripts/dedup_research_sources.py` 确认影响范围。
- [x] 10.3 运维实际跑 `--apply`，提交一次 audit log 截图。
- [x] 10.4 `alembic upgrade head` 在 lab DB 跑通；`0009` 的 sanity check 不报错。
- [x] 10.5 server `tools/scripts/serve.sh` 重启。
- [x] 10.6 烟测：
   - 提交一个新 request，带 `Idempotency-Key: smoke-1`，连提两次 → 第二次返 200 + 相同 id；同 key 改 body → 409 `idempotency_key_conflict`。
   - 删 binding，启 scoped worker → toast 报 `worker_startup_failed` 或下一个 run 直接 `failed: profile_not_bound`（根据 binding 是否在 worker 前后被删）。
   - 故意构造一个 stdout 多 `flag{...}` + 末尾 markdown 的 fixture（通过 mock）→ 验证不会再把 cleanup 行当 flag。
   - 删 request 后 `ls work/research/sources/<run_id>/` 应不存在。
   - 人工制造一个无 completed run 支撑的 `work/research/sources/<run_id>/` 目录，重启 server 后应被 quarantine/delete。
- [x] 10.7 把验证截图/日志贴进本提案 PR review 里。

## 11. 后续提案占位（仅 proposal.md）

- [x] 11.1 `openspec new change define-research-cancellation-terminal-states` + 写 proposal.md（占位，不实现）。
- [x] 11.2 `openspec new change add-cross-request-research-novelty` + 写 proposal.md（占位）。
- [x] 11.3 `openspec new change revamp-research-design-task-ui` + 写 proposal.md（占位）。
- [x] 11.4 给 Hermes 心跳 / 双 worker 资源浪费写一个 GitHub issue（或本项目用的等价 tracker），关联本提案。

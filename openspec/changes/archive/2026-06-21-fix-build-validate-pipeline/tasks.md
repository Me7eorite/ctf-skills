## 1. Bug 2: host validator flag extraction

- [x] 1.1 在 `src/domain/validation.py` 引入正则常量；删除已无调用的 `last_nonempty_line`。
- [x] 1.2 修改 `ChallengeValidator.validate_one`：用带 token 边界且不跨行/花括号的正则取最后一个 flag；其余 mismatch / passed 判断逻辑不动。
- [x] 1.3 在 `tests/app/test_validate_challenge.py` 覆盖 cleanup、无匹配和多个 flag。
- [x] 1.4 跑 validator + Hermes 定向测试，确认无回归。

## 2. Bug 2: Hermes prompt 模板加 stderr cleanup + pre-cleanup

- [x] 2.1 修改真实模板 `prompts/shard_prompt.md` 的 `Exploit Validation` 段落。
- [x] 2.2 追加 stderr cleanup 和 stale-container pre-cleanup 硬要求。
- [x] 2.3 扩展 `tests/app/test_hermes.py` 覆盖两个字面契约。
- [x] 2.4 把"image 缺失 → fail-fast，禁止 `docker build` / `pip install` / `apt-get`"写进 `prompts/shard_prompt.md` 的 validate.sh 段落（offline-capable 验证契约）。
- [x] 2.5 把 `tests/app/test_prompt_rendering.py::test_prompt_contains_image_inspect_pattern` 改名为 `test_prompt_forbids_in_script_image_build`，断言 prompt 包含 `validate.sh: required image '$IMAGE' is missing` 与 `MUST NOT contain \`docker build\``，且不再包含 `|| docker build -t "$IMAGE" .`。

## 3. Bug 3: 加固现有 per-attempt revalidate

- [x] 3.1 保留现有 failed-only/latest/failed-shard API 语义，不新增第二套 service。
- [x] 3.2 用 PostgreSQL session advisory lock 覆盖整个验证过程；重复请求在写事件前 409。
- [x] 3.3 将 validator 绑定到精确 artifact 目录，校验路径位于 challenges 根且 metadata.id 匹配。
- [x] 3.4 捕获 validator 异常并写 validate/failed + complete/failed；锁在 finally 释放。
- [x] 3.5 complete/passed 延后到 shard move 和 DB commit 成功之后；commit 失败时反向恢复 shard/claim。
- [x] 3.6 覆盖 advisory 冲突、异常、目录绑定和提交失败补偿。

## 4. Bug 3: 保持现有 UI 语义

- [x] 4.1 保留 failed-only“重新校验”、重试构建和中文失败摘要。
- [x] 4.2 不恢复列表级 `#ba-validate` / Start Worker 全局动作。

## 5. Bug 1: list_attempts 子查询收敛 + 索引

- [x] 5.1 在 `list_attempts` 中先构造应用全部 filter/order/limit 的 selected CTE，再聚合 selected shard 的 snapshots。
- [x] 5.2 保持 latest-per-task 后再 filter 的既有语义，并确保稳定排序。
- [x] 5.3 不增加索引迁移；现有 `(shard, challenge_id)` 主键索引已覆盖 shard 前缀。
- [x] 5.4 测试 limit/filter 外 shard 不进入聚合集合且返回 percent 正确；PostgreSQL `EXPLAIN ANALYZE` 断言使用 `progress_snapshots_pkey`。

## 6. Bug 1: 详情页事件时间线增量渲染

- [x] 6.1 在模块作用域维护 event node Map，不污染 API detail DTO。
- [x] 6.2 poll 比较非事件字段；不变且事件仅尾部追加时 patch DOM 和计数，完全不变时零 DOM 写。
- [x] 6.3 删除、乱序、重复、非事件字段变化时回退全量 render。
- [x] 6.4 离开详情页 / 切换 attempt 时清空 Map。
- [x] 6.5 用可执行 DOM harness 模拟 ca789ee5 的 22 events，连续跑 5 个无变化轮询周期，确认 DOM query / diff 操作均为 0 次。

## 7. 上线 & 验证

- [x] 7.1 本地全部 `pytest tests/app -q` 通过（641 passed，5 subtests passed）。
- [x] 7.2 本地 dashboard 用当前代码成功启动并完成 application startup（4173 已被既有进程占用，验收实例使用 4183）。
- [x] 7.3 API/service 回归覆盖 failed attempt revalidate 后变成 `succeeded`，无需依赖生产 UUID。
- [x] 7.4 prompt rendering / Hermes 契约回归确认新生成的 validate.sh cleanup 全部走 stderr、包含 stale-container pre-cleanup 且禁止 validate 阶段构建 image。
- [x] 7.5 可执行 DOM harness 对 22 events 连续轮询 5 次，确认无变化时零 DOM 写。
- [x] 7.6 PostgreSQL fixture 上对 `/api/build-attempts` 仓储实际 SQL 执行 `EXPLAIN ANALYZE`，确认受限 shard 扫描使用 `progress_snapshots_pkey`。

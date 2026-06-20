## 1. Bug 2: host validator flag extraction

- [x] 1.1 在 `src/domain/validation.py` 引入正则常量；删除已无调用的 `last_nonempty_line`。
- [x] 1.2 修改 `ChallengeValidator.validate_one`：用带 token 边界且不跨行/花括号的正则取最后一个 flag；其余 mismatch / passed 判断逻辑不动。
- [x] 1.3 在 `tests/app/test_validate_challenge.py` 覆盖 cleanup、无匹配和多个 flag。
- [x] 1.4 跑 validator + Hermes 定向测试，确认无回归。

## 2. Bug 2: Hermes prompt 模板加 stderr cleanup + pre-cleanup

- [x] 2.1 修改真实模板 `prompts/shard_prompt.md` 的 `Exploit Validation` 段落。
- [x] 2.2 追加 stderr cleanup 和 stale-container pre-cleanup 硬要求。
- [x] 2.3 扩展 `tests/app/test_hermes.py` 覆盖两个字面契约。

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
- [ ] 5.4 测试 limit/filter 外 shard 不进入聚合集合且返回 percent 正确；PostgreSQL 环境下记录 EXPLAIN。

## 6. Bug 1: 详情页事件时间线增量渲染

- [x] 6.1 在模块作用域维护 event node Map，不污染 API detail DTO。
- [x] 6.2 poll 比较非事件字段；不变且事件仅尾部追加时 patch DOM 和计数，完全不变时零 DOM 写。
- [x] 6.3 删除、乱序、重复、非事件字段变化时回退全量 render。
- [x] 6.4 离开详情页 / 切换 attempt 时清空 Map。
- [ ] 6.5 单测 / 手工：用 ca789ee5（22 events）打开详情页 + Chrome Performance 录 5 个轮询周期，确认 DOM diff 操作只发生 0 次（无新 event）。

## 7. 上线 & 验证

- [x] 7.1 本地全部 `pytest tests/app -q` 通过（564 passed）。
- [ ] 7.2 server 端 `git pull && tools/scripts/serve.sh --host 0.0.0.0 --port 4173` 重启（沿用既有部署流程）。
- [ ] 7.3 触发现有失败 attempt 的 revalidate（例如 `ca789ee5-420f-4ba5-81e7-2ac696d241da`），确认在 D2 兜底下变成 `succeeded`。
- [ ] 7.4 复跑一次 build（让 Hermes 用新 prompt 生成 validate.sh），手工 cat 新 `validate.sh`，确认 cleanup 函数全部 `>&2` + 有 pre-cleanup `docker rm -f`。
- [ ] 7.5 详情页打开 ca789ee5，DevTools Performance 录制确认事件时间线不再每 2.5s 全量 DOM 重建。
- [ ] 7.6 `EXPLAIN ANALYZE` `/api/build-attempts` 实际查询，确认受限 shard 扫描可走 `progress_snapshots_pkey`。

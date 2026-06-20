## 评估结论

原提案识别的三个生产问题成立，但初稿不能直接实施。主要矛盾是：revalidate
没有绑定 attempt 的实际产物、允许旧 attempt 改写父任务、缺少并发保护；性能方案的
SQL 子集实际不受过滤和 limit 约束；前端方案调用了不存在的局部 `initIcons(root)` API。

以下十轮均以当前代码、数据库约束和可测试行为为依据。每轮整改后重新检查其对前后
轮结论的影响；第十轮通过后才进入实施。

## 十轮评估与整改

### 第 1 轮：flag 提取边界

- **问题**：`last_nonempty_line(stdout)` 会把 EXIT trap 的 cleanup 文本当作 flag。
- **矛盾**：初稿正则只接受小写字母、数字和下划线，收窄了既有 metadata 中可能合法的
  flag 内容；在任意日志子串中搜索也会接受 `prefixflag{...}suffix`。
- **整改**：使用独立 token 正则 `(?<![A-Za-z0-9_])flag\{[^\r\n{}]+\}(?![A-Za-z0-9_])`，
  取最后一个匹配并与 metadata 精确比较；保留“无匹配即 mismatch”。
- **复评**：兼容 cleanup/debug 输出，同时不把嵌入单词的片段当作 flag。

### 第 2 轮：验证对象身份

- **问题**：初稿调用 `validate_challenge(parent.challenge_id)`，会重新全局前缀匹配目录。
- **矛盾**：attempt 已持久化 `resulting_challenge_dir`；忽略它会验证到其他 attempt 的
  产物，或因同 ID 多目录而误报 ambiguous。
- **整改**：revalidate 解析并校验 `resulting_challenge_dir` 必须位于
  `work/challenges` 内且存在，然后直接调用 `validate_one(path)`；metadata.id 必须等于
  父 design task 的 challenge_id。
- **复评**：验证结果与被操作的 attempt 一一对应，路径越界和身份错配返回 409。

### 第 3 轮：旧 attempt 与父任务状态

- **问题**：初稿允许任意 failed/lost/succeeded attempt 回写 `design_task.status`。
- **矛盾**：旧 sibling 的结果可覆盖更新 attempt 的真实状态。
- **整改**：仅允许该 design task 的最新 attempt revalidate；旧 attempt 返回 409。
- **复评**：父任务状态始终由最新 attempt 决定，和列表的 latest-per-task 语义一致。

### 第 4 轮：并发重复触发

- **问题**：两个请求可同时写两对 progress 事件并竞争最终状态。
- **矛盾**：初稿测试提到“并发”，规格却没有互斥契约。
- **整改**：服务进程维护 per-attempt 非阻塞互斥锁；同一 attempt 已在 revalidate 时
  返回 409。部署当前为单进程 uvicorn，此约束与现状一致；多进程部署前必须升级为
  PostgreSQL advisory lock 或持久化 lease。
- **复评**：当前支持的部署拓扑中同 attempt 至多一个验证进程，不占 TaskManager 槽位。

### 第 5 轮：异常与终态事件

- **问题**：validator 调用若抛出未预期异常，可能只留下 `validate/running`。
- **矛盾**：“恰好两个事件”无法和两个独立事务绝对原子化。
- **整改**：捕获 validator 异常并归一为 `validator_error`，在 `finally` 路径完成状态和
  terminal event 回写；running 事件提交失败则不启动 subprocess。规格明确数据库在
  running 提交后永久失效属于不可原子化的运维故障，由事件审计发现。
- **复评**：应用级异常均产生 terminal event；不会把 Python 异常变成 500 且遗留锁。

### 第 6 轮：查询批次边界

- **问题**：初稿 `WHERE shard IN (SELECT ... FROM ranked WHERE rank=1)` 包含全库所有
  task 的最新 attempt。
- **矛盾**：过滤条件和 `limit` 在外层，所谓“5 条返回行”并未约束 progress 子查询。
- **整改**：先构造 `selected_attempts` CTE：latest-per-task 后应用全部过滤、排序和 limit；
  再只对该 CTE 的 shard 集合聚合 snapshots，最后 join 回 selected rows。
- **复评**：snapshot 聚合的 shard 集严格等于 API 折叠批次，性能契约可由编译 SQL 和
  PostgreSQL EXPLAIN 验证。

### 第 7 轮：索引必要性

- **问题**：初稿新增 `progress_snapshots(shard)` 单列 BTree。
- **矛盾**：表的复合主键已生成 `(shard, challenge_id)` BTree，满足 shard 前缀扫描；
  重复索引增加写放大，不能仅凭假设添加。
- **整改**：不新增索引迁移；验收要求 EXPLAIN 使用现有主键索引。只有生产计划证明
  单列索引更优时才另提迁移。
- **复评**：查询获得索引支持且没有冗余 schema 变更，部署无需 Alembic。

### 第 8 轮：HTTP 完成语义

- **问题**：初稿同步执行并在结束后返回，却声明 `202 Accepted`。
- **矛盾**：202 表示已接受但处理未完成，响应体却是最终状态。
- **整改**：成功返回 200；TaskManager 全量异步接口继续使用 202。
- **复评**：客户端不需要额外 task/poll 状态，协议与实际一致。

### 第 9 轮：前端增量机制

- **问题**：初稿把 `_eventNodes: Map` 放进 API detail DTO，并调用 `initIcons(newRoot)`。
- **矛盾**：每次 poll 仍执行 `root.innerHTML = ...`，Map 节点随之失效；现有
  `initIcons()` 不接收 root 参数，而且事件行本身没有图标。
- **整改**：Map 保持为模块私有状态。poll 比较去除 `progress_events` 后的 detail：
  其他字段变化才全量渲染；只有事件追加时直接 append 并更新计数，不调用 icons。
  检测删除、乱序、重复或 ID 非单调时安全回退全量渲染。
- **复评**：无变化轮询为零 DOM 写；append-only 正常路径只添加新节点。

### 第 10 轮：提示词、测试与上线可执行性

- **问题**：初稿指向 `src/hermes/prompt.py`，实际契约正文在
  `prompts/shard_prompt.md`；任务使用固定生产 UUID 和人工 DevTools 作为必需验收。
- **矛盾**：路径错误会导致实现遗漏；环境专属 ID 不可复现，纯人工检查不能作为回归门。
- **整改**：修改真实模板；自动化覆盖 prompt 字面契约、输出解析、路径/身份/latest/
  并发/异常、SQL 批次范围和前端增量 helper。生产 EXPLAIN 与浏览器录制保留为部署后
  观察项，不作为本地实现完成的前置条件。
- **复评**：提案范围、实现位置、自动化门禁和运维验证一致，可以进入实施。

## 最终推荐方案

1. host validator 使用边界明确的 flag token 提取，prompt 同时约束 trap 输出到 stderr
   并在启动前清理同名容器。
2. 增加同步 200 的 per-attempt revalidate；直接验证已记录产物，只允许最新 terminal
   attempt，单进程内互斥，并保证应用异常也写 terminal 结果。
3. `list_attempts` 先选定受 filter/order/limit 约束的批次，再聚合其 snapshots；复用
   复合主键索引。
4. 前端仅在 detail 主体变化时重渲染；append-only event 更新走局部 DOM patch。

## 剩余风险

- 当前互斥锁范围是单个 server 进程。若部署改为多 uvicorn worker，必须先实现数据库
  advisory lock/lease。
- progress running 与 terminal 是两个事务，数据库在其间永久不可用时不能保证事件成对；
  这是审计可见的失败，不会伪造 succeeded。
- validate.sh 仍是受信任的本地产物执行入口；本变更不增加沙箱。权限边界沿用现有 CLI
  validate 的运维模型。

## 第 11 轮：基线变化后的重新评估（bcdd3fba）

- **变化**：主分支已加入 `BuildAttemptRevalidationService`、revalidate API、中文 UI 和
  `validate/complete` 事件，原“没有单 attempt 闭环”不再成立。
- **仍存在**：最后一行 flag 提取、prompt cleanup 污染、全表 snapshot 聚合和详情页
  全量 DOM 重建均未修复。
- **新增矛盾**：短事务行锁在 subprocess 前释放，两个请求可并发验证；验证器异常会
  遗留 running；`complete/passed` 早于 failed→done 和 DB 提交；文件移动后 DB 提交失败
  没有补偿；全局 challenge-id 查找没有优先绑定 attempt 产物目录。
- **整改**：不再新增 API 或扩展 lost/succeeded；保留当前 failed-only 和 complete 事件
  语义，改为 advisory lock、bound validator、异常收敛、complete 延后及文件反向补偿。
- **复评准入**：并发第二请求不得启动验证；异常必须产生 validate/failed 和
  complete/failed；模拟 DB 提交失败后 shard 必须回到 failed；精确目录不得被同 ID
  sibling 替换。

## 第 12 轮：整改完成后的重新评估

- **已消除**：host validator 不再依赖最后一行；prompt 同时约束 trap stderr 和 stale
  container cleanup；snapshot 聚合受 selected CTE 的 filter/order/limit 约束；详情页
  无变化轮询不再写 DOM。
- **revalidate 复评**：PostgreSQL advisory lock 在 progress 之前获取并覆盖 subprocess；
  validator 使用 attempt 记录目录（无记录时才做严格单目录查找）；异常收敛到
  validate/failed + complete/failed；complete/passed 延后到 queue/DB 成功之后。
- **本轮发现并修复**：初次补偿先移动 shard 再移动 claim，claim 移动失败时可能再次
  产生分裂；已改为先移动 claim、再移动 shard，后者失败则回滚 claim。不存在 attempt
  的 endpoint 也从笼统 409 修正为 404。
- **验证**：新增 advisory 冲突、validator 异常、精确目录、DB 提交失败补偿和 404
  回归；全量 app suite 通过 564 项，整改后的 OpenSpec strict validate 通过。
- **剩余运维项**：生产数据上的 `EXPLAIN ANALYZE` 与浏览器 Performance 录制仍需部署后
  执行；是否增加单列 shard 索引只由该计划决定。

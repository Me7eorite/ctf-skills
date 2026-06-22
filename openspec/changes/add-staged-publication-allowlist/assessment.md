# 题案循环评估与整改记录

评估范围：`add-staged-publication-allowlist` 的 proposal、design、spec
delta 与 tasks。每轮均按“分析问题 → 推荐方案 → 题案整改 → 重新分析”执行。
本记录只整改题案，不代表业务代码已经实现。

## 第 1 轮：重试产生同 ID 双目录

- **问题**：重试会先 materialize 旧 `<id>-<slug>`，通用 prompt 又允许
  Hermes 创建另一个 slug，publisher 只能在末端报 duplicate-id。
- **推荐方案**：记录每个 claimed id 的精确 resume target，并要求原地修改；
  publisher 继续 fail-closed，禁止猜测或合并。
- **整改**：新增 `resume_output_targets`、精确路径 prompt 契约、重复目录回归
  场景和任务。
- **重新分析**：安全边界保留，常见误生成路径被前置约束；agent 违反契约时
  仍能安全拒绝。该问题已在题案层闭合。

## 第 2 轮：重试与“重新构建”语义混淆

- **问题**：现有 retry 实际是断点续建，不是空目录重建；操作员无法明确丢弃
  旧产物和旧进度。
- **推荐方案**：拆分 `resume` 与 `clean` 两种 execution mode。
- **整改**：新增独立 clean rebuild API/UI/runner 语义；clean 不 materialize、
  不 carry-forward，旧 canonical 仅在成功发布时被 quarantine/替换。
- **重新分析**：两类行为可预测，避免通过删除目录等手工方式模拟 clean。

## 第 3 轮：publisher 成功后立即清理破坏验证修复

- **问题**：runner 在首次 publish 后还会执行 host validation，并可能在同一
  workspace 进行多轮 validation repair；立即删除 `output/`、`logs/` 会破坏该
  流程。
- **推荐方案**：publisher 不负责成功清理；仅 terminal validation success
  后由 runner 清理。
- **整改**：proposal、design、retention requirement 和 tasks 均改为 terminal
  cleanup；publisher/validation failure 保留诊断材料。
- **重新分析**：初次发布、验证、repair、再次发布使用同一 workspace 的生命周期
  已一致。

## 第 4 轮：多题“原子发布”描述不成立

- **问题**：多个目录的顺序 rename 不是单一文件系统事务；原设计也没有列出
  已提交项的完整反向回滚状态。
- **推荐方案**：不再宣称不可实现的瞬时全局原子性，改为全批预验证/预 staging、
  同步异常回滚、持久 journal 和崩溃恢复。
- **整改**：将目标改为 serialized, failure-atomic, crash-recoverable batch；
  明确 journal 内容、phase fsync、反向 rollback 和 bootstrap reconcile。
- **重新分析**：普通异常可恢复到发布前状态；进程死亡窗口被显式记录并可恢复，
  同时诚实保留“非锁协议读者可能短暂看到中间态”的限制。

## 第 5 轮：并发 publisher 存在丢失更新

- **问题**：temp + rename 只保证单次 rename 原子，不能阻止两个进程同时替换同一
  claimed id；fencing 又被推迟到后续题案。
- **推荐方案**：本题案先提供本地跨进程互斥，fencing 以后叠加。
- **整改**：新增按 `(category, claimed_id)` 排序获取的跨进程锁，覆盖 canonical
  状态重读、commit、manifest 和 rollback；recovery 使用同一锁。
- **重新分析**：同机并发写入被串行化，避免 lock-order deadlock；跨主机共享存储
  仍属于后续 fencing 范围。

## 第 6 轮：change-policy 信任时点错误

- **问题**：若 Hermes 返回后才读取 `change-policy.json` 或 base artifact，agent
  可以同时修改策略和比较基线，使 hard diff 失去意义。
- **推荐方案**：Hermes 调用前捕获 host-owned immutable publication contract，
  调用后复核 input digests。
- **整改**：publisher API 拆为 `prepare_publication_contract` 和带 contract 的
  publish；contract 包含 identity、mode、policy、base hashes、resume targets、
  input hashes。
- **重新分析**：publisher 不再以后置 workspace 内容作为唯一信任源；策略 TOCTOU
  已在题案层封闭。

## 第 7 轮：change-policy 路径解析可逃逸

- **问题**：`base_artifact_relpath`、preserve、forbid 未规定 `..`、绝对路径、
  反斜杠、NUL、symlink、未知字段和 JSON field 缺失行为。
- **推荐方案**：严格 schema + normalized relative POSIX path + root containment。
- **整改**：逐项定义拒绝条件，并要求 contract preparation 在 Hermes 前失败。
- **重新分析**：策略路径不能逃逸 base/candidate root，错误不再因平台路径语义不同
  而产生歧义。

## 第 8 轮：forbid 目录已存在时可新增子文件

- **问题**：原文只判断 forbid path 是否“新出现”；若 base 已有 `secrets/`，新增
  `secrets/new-key.pem` 可能被放行。
- **推荐方案**：forbid 作为递归 prefix，按相对路径比较 base/staging inventory。
- **整改**：规范和测试任务新增“已有 prefix 下新增 descendant 仍拒绝”。
- **重新分析**：forbid 的语义与 proposal 中“任何新文件”一致。

## 第 9 轮：manifest hash 不代表完整可执行树

- **问题**：只哈希文件路径与内容会忽略空目录和 executable mode；分隔符拼接对
  合法特殊文件名也可能有歧义。
- **推荐方案**：使用 length-prefixed canonical records，纳入 claimed id、path、
  type、normalized mode、content hash。
- **整改**：重写 hash 规范，补充 executable mode、empty directory、特殊文件名
  回归任务。
- **重新分析**：相同 hash 对应的语义更接近下游实际执行树。

## 第 10 轮：canonical 已变更但 manifest 写失败

- **问题**：原顺序在 publish 后写 manifest；写失败会产生 canonical 已更新但审计
  hash 缺失的半提交状态，且与“publish failure 不改 canonical”冲突。
- **推荐方案**：staging 先算 hash，rename 后复核，持锁原子更新 manifest；失败
  纳入 journal rollback。
- **整改**：hash verification、manifest replace 和 crash window 均纳入 batch
  transaction/journal 要求。
- **重新分析**：canonical 与 manifest 的普通失败一致性闭合，进程死亡由 recovery
  收敛。

## 第 11 轮：跨文件系统 rename 假设未验证

- **问题**：canonical、temp、quarantine 若不在同一 filesystem，`replace/rename`
  不能提供预期语义，可能在 commit 中途才失败。
- **推荐方案**：commit 前验证三类路径同 filesystem。
- **整改**：design、requirement、tasks 和 recovery tests 增加 same-filesystem
  precondition 与 cross-device rejection。
- **重新分析**：部署路径不满足原子 rename 前提时会在 canonical mutation 前失败。

## 第 12 轮：retention 会与活跃发布/恢复竞争

- **问题**：仅按 mtime 和数量清 quarantine，可能删除正在 rollback/recovery 使用
  的前任目录；子文件 mtime 也不是可靠的失败时间。
- **推荐方案**：使用 host-owned terminal timestamp；对 incomplete journal 和无法
  非阻塞取锁的 workspace 跳过清理。
- **整改**：retention requirement 与 tasks 增加 terminal marker、active journal/
  lock exclusion，并明确同时约束 failed output/log staging。
- **重新分析**：清理不会再破坏活跃事务，7 天/20 个的计数对象和时间来源明确。

## 第 13 轮：clean rebuild 的并发与重放未定义

- **问题**：新增 clean API 后，双击、网络重放或并发请求可能为同一个失败 attempt
  创建多个新 attempt；只靠浏览器确认也可绕过。
- **推荐方案**：沿用 retry eligibility，并在同一事务重查；API 要求 confirmation
  与 idempotency key。
- **整改**：新增 transactional/idempotent clean submission requirement、并发
  scenario、service/API/UI tasks 和测试。
- **重新分析**：clean 不会绕开 latest-attempt/build_failed 约束，重放最多生成一个
  attempt。

## 第 14 轮：execution_mode 可矛盾或漂移

- **问题**：显式 `clean` 与 `resume_from_shard_basename` 可同时出现；不同阶段若各自
  推断 mode，可能 materialize 与 plan 行为不一致。
- **推荐方案**：只接受 `clean|resume`，建立兼容推断规则，并在 preflight 规范化为
  单一内存值。
- **整改**：显式 resume 必须有安全 basename，显式 clean 禁止 resume source；
  normalized mode 贯穿 plan/materialize/prompt/contract。
- **重新分析**：矛盾 payload 在 Hermes 前失败，旧 payload 仍按是否含 resume source
  保持兼容。

## 第 15 轮：allowlist 缺乏资源上限

- **问题**：即使所有条目都是 regular file，超大输出、超多文件或极深目录仍可耗尽
  磁盘、inode、时间或递归栈。
- **推荐方案**：staging 前以 `lstat` 扫描并限制总 bytes、file count、depth 和
  component length，temp copy 后再扫描。
- **整改**：新增 resource-bound requirement、limits phase、配置项实现任务和测试。
- **重新分析**：资源拒绝发生在 canonical mutation 前，且复制过程不跟随 symlink。

## 第 16 轮：失败信息只能解析自由文本

- **问题**：duplicate-id、policy mismatch、磁盘错误和 rollback failure 全部落入
  `failure_type=infrastructure`，操作员难以稳定判断故障阶段。
- **推荐方案**：本题案不扩大全局 taxonomy，但 publisher 输出稳定 phase 和安全的
  claimed-id/relative-path 诊断。
- **整改**：新增九个稳定 phase、runner/terminal marker 传递要求及 duplicate-id
  诊断场景。
- **重新分析**：保持现有 reconciler 兼容，同时无需解析异常字符串即可区分阶段。

## 第 17 轮：成功发布的 quarantine 未被数量策略覆盖

- **问题**：retention 总述声称同时清理 quarantine 和 failed staging，但原细则的
  7 天/20 个计数对象仅写成 failed workspace；成功发布保留的旧 canonical
  quarantine 可能无限积累。
- **推荐方案**：以“包含 quarantine 或 failed staging 的 terminal workspace”为
  统一 retention root，对成功和失败都写 terminal marker，并共同执行年龄与数量
  上限。
- **整改**：design、requirement、scenario 和 tasks 均改为统一 retention root；
  删除 root 内 retained artifacts，而不是只删除失败 output/log。
- **重新分析**：成功 quarantine 和失败诊断 staging 均受 7 天且最多 20 roots 的
  双重约束，计数对象不再遗漏。

## 第 18 轮：validation repair 的二次发布与 contract 校验冲突

- **问题**：首次 publish 会合法修改 `manifest.output_manifest_hash`；若 repair 再次
  使用 Hermes 前捕获的整文件 hash，publisher 会把自己的修改判为 agent 篡改。
  同一个固定 journal 也无法清晰区分多次发布。
- **推荐方案**：校验 manifest 的 immutable projection，仅排除 publisher-owned
  字段；每次发布递增 generation 并使用新 journal。
- **整改**：新增 `publish_generation`、immutable projection、repair republish scenario、
  实现任务和回归测试。
- **重新分析**：原 contract 可安全覆盖同 workspace 多轮 repair；identity、input
  hashes、timeout、mode、resume target 仍不可被 agent 修改。

## 总体结论

经 18 轮循环，题案已从“移动现有 promotion 代码并叠加 hash/retention”提升为：

1. Hermes 前建立可信 publication contract；
2. Hermes 后执行完整 allowlist、policy、resource 和 input-integrity 检查；
3. 对重叠写者加锁，对整批先 staging，再用 journal 提交/回滚/恢复；
4. 保证 validation repair 生命周期不被提前清理；
5. 明确 retry/resume 与 clean rebuild 的行为、并发和幂等边界；
6. **接受多目录 rename 不提供瞬时全局原子视图作为本题案的边界**：本题案不再
   宣称跨目录的瞬时原子性；该限制由后续 execution fencing + 所有消费者统一
   锁协议（题案 3 起）继续收紧，而不是在 publisher 层假装解决。

## 第 19 轮：外部独立审视的整改

在 18 轮自审之外，另一名独立审视者就题案 1 落地后的现状提出 9 项问题（编号
B–J），其中没有一项被前 18 轮覆盖。本轮按推荐方案整改：

- **B（shim 兼容路径）**：删除 tasks 10.1 的“或保留一发布”软出口；shim 转为
  显式 raise 的 deprecation stub，tasks 1.2/10.1/10.2 同步收紧，spec migration
  追加“归档前必须删除”约束。
- **C（publisher_generation / output_manifest_hash 单调性）**：spec 加入“以
  committed journal 为权威而非 manifest”、“新 generation 必须严格大于已提交
  上限”两条约束 + 两个 scenario；tasks 1.7 新增对应实现项。
- **D（locks 路径悬空）**：design.md decision 5 改写为 `paths.build_publisher_locks`
  + `fcntl.flock(LOCK_EX)` + 非 POSIX 平台 preflight 拒绝；spec 同步；tasks 5.0
  新增对 ProjectPaths 与 initialize() 的扩展。
- **E（保留输入文件唯一性）**：spec 显式约定 `(workspace_id, publish_generation)`
  唯一标识 journal 记录、未 committed 的 journal 强制走 bootstrap recovery。
- **F（repair 无变更协同 F5）**：spec 新增 `noop` 结果 scenario，publisher 在
  staging hash 等于 committed hash 时短路；tasks 4.4 新增实现项；runner 据此
  跳过冗余 validation rerun。
- **G（sweep 频率退避）**：spec 加入每进程 60 秒 throttle、被压制调用不丢失
  sweep 待办；tasks 6.6 新增实现项。
- **H（clean rebuild 事务边界落地）**：tasks 7A.2–7A.4 指明在
  `BuildOrchestrationService.clean_rebuild()` 复用 retry 的事务结构；新增
  `build_attempts.idempotency_key` 迁移；UI 客户端生成 UUIDv4 作为 key。
- **I（staging 是唯一事实源）**：spec 新增"resume_output_targets 仅作 prompt /
  诊断、与 staging 不一致时 publisher 在 contract 阶段失败"的 Requirement 文本
  和场景。
- **J（atomicity 退让升级到总体结论）**：本节第 6 条。

## 第 20 轮：整改后再审

在 19 轮整改完成后做的二次独立审视，发现 4 项新张力：

- **K（noop vs increment 字面冲突）**：spec 同时写"每次成功 publish 都
  increment"与"noop 不变"。整改：把 increment 约束的主语改为
  `succeeded`，noop 单独成立；不再依赖读者拼接两段文本。
- **M（"remove or archive committed journal"二选一让信任源消失）**：若选
  remove，下一次 publish 找不到任何 committed journal，C 的"严格大于已提
  交上限"退化为"从 1 开始"。整改：spec 强制 archive 到
  `input/publish-journal-archive/<generation>.json`，禁止 delete；archive
  目录纳入 reserved 文件集合；tasks 1.7 同步约束。
- **P（clean rebuild 与 split-plan 题案 3 的 execution_kind 未对齐）**：
  split-plan 写 execution kind 为 `initial / retry / revision`，本题案的
  clean 是 build_attempt 级别。整改：proposal 加 forward-compat note，明
  确 `execution_mode: "clean"`（shard 字段）与未来的 `execution_kind`
  （DB 列）正交，由题案 3 决定是否引入新 kind。
- **R（tasks 7A.2 与现状不一致）**：复读 [BuildOrchestrationService.retry()](src/services/build_orchestration_service.py:123)
  实际是 3 个独立 session，与原"single transaction"措辞矛盾；继续要求
  "single transaction"既会偏离 retry 现状，也无实际并发收益。整改：spec
  改为"eligibility re-check 在 `_prepare` 的 session 内重读源 attempt 与
  design task；并发安全由 `build_attempts.idempotency_key` UNIQUE 约束兜
  底；UNIQUE race 由 API 转换为 existing-row 响应"；tasks 7A.2/7A.3 同步。

## 第 21–22 轮：第二次独立审视

19 轮整改后再做两轮独立审视，识别 10 项遗漏（编号 S/T/V/W/Y/Z/AA/BB/CC/EE），
其中 2 项为按字面实现必撞的真矛盾：

- **S（高）**：reserved publisher-owned 文件（journal/status/archive）若放在
  `input/` 下，被 contract input-hash 当作 host-owned 输入 → 第一次成功
  publish 后 input-hash 改变 → 下一次 repair 必报 `contract` 阶段失败。
- **T（高）**：上一轮要求 archive 不可删除，但单 workspace 长期 repair 会让
  archive 文件不断累积；与"不删除"的硬约束直接相撞。

整改采用**统一更简的设计**：把所有 publisher-owned 运行时状态搬到 workspace
内独立目录 `state/`，archive 改成单个 high-water 文件
`state/highest-committed-generation.json`：

- **S 闭合**：spec 与 design 都明确"contract input-hash SHALL exclude
  every path under `state/` and the publisher-owned manifest projection
  fields"；实现以代码枚举排除集，禁止 regex 兜底。
- **T 闭合**：archive 收敛为单文件 high-water；删除"全历史 archive"承诺
  并把完整审计交给 proposal #6；任何 generation 重新可读但不维护历史。

剩下 8 项均已落地：

- **CC（文本残留）**：上一轮把 reserved 集合扩到 3 项后仍写 "Both reserved
  files"。已改为 "These reserved paths" 并按新结构重写整段。
- **V（runner 状态机覆盖）**：新增 scenario "Runner observes noop and exits
  the repair loop" 明确 noop 时跳出 repair loop、不刷新进度百分比、不写
  terminal marker。
- **W（flock 选型理由）**：spec 显式说明选 `fcntl.flock` 而非 `lockf`
  是因为 supervisor fork worker 时需要 fd 继承语义；非 POSIX 仍走
  preflight fail。
- **Y（clean-retry 跨入口并发）**：本题案仅承诺 clean-vs-clean 并发；
  clean-vs-retry 与 retry-vs-retry 同源，等 proposal #3 用 lease 收紧。
- **Z（recovery → publish 时序）**：spec 在 reserved-files 段补一段
  "After bootstrap recovery completes successfully ... publisher SHALL
  treat the workspace as in its post-recovery canonical state"；recovery
  本身不消耗 generation；finalize 更新 high-water，rollback 不更新。
- **AA（"safe permissions" 措辞模糊）**：tasks 5.0 改为 "default umask is
  sufficient — the lock files carry no secrets"。
- **BB（task 编号倒置）**：交换原 7A.2 与 7A.3 顺序——idempotency_key 迁移
  作为 7A.2 先做，clean_rebuild 实现作为 7A.3 在其后。
- **EE（idempotency key 复用语义）**：tasks 7A.4 改为 "one UUIDv4 per
  click"，明确"新点击 = 新 key、HTTP-layer 重试复用 key"，避免 UI 跨用户
  动作复用 key 导致后端误返 existing row。

## 第 23 轮：第三次独立审视

第 21–22 轮整改后再做一次独立审视，发现 6 项：

- **F3（中-高）**：commit 流程是"canonical rename → manifest write → high-water
  update"三步，第 3 步前 crash 会留下 `committed` journal + 落后的
  high-water。原 spec 只规定 "refuse to recover a journal whose generation
  is less than high-water"——只防退化、不防推进，留下半提交卡死空白。
  整改：spec 显式补 "committed journal generation > high-water 时，
  recovery SHALL atomic 推进 high-water 到 journal generation，幂等；
  canonical 与 manifest 不回滚"；加 scenario "Recovery pushes high-water
  forward after manifest-then-crash"；tasks 5.7 跟随。
- **F4（中）**：原措辞 "SHALL NOT rely only on a browser confirmation dialog"
  暗示 API 层有独立防御能力，但实际只是收一个 `confirmed=true` boolean，
  脚本塞个字段就能绕过。整改：把措辞弱化到匹配实际防御能力—— "`confirmed=true`
  仅防止 client 默认值漏写导致的误触发，不是反 abuse；更强的 RBAC/audit
  out of scope"。
- **F6（小）**：`paths.locks_root` 与 `paths.build_publisher_locks` 的关系隐式，
  题案 3 后续加 lease lock 时易分叉。整改：spec 与 tasks 显式声明
  `build_publisher_locks = locks_root / "build-publisher"`，未来子题案
  必须加在 `locks_root` 下的 sibling，不准嵌套。
- **F7（小）**：lock 文件生命周期未约束；题目被 resource_deletion 删除后
  lock 文件变孤儿。整改：spec 与 tasks 显式说明 lock 文件与题目生命周期
  解耦、孤儿无害；publisher 不主动清理。
- **F8（中）**：[resource_deletion.py](src/web/resource_deletion.py)
  删题目时不取 publisher 的 `(category, id)` 锁；in-flight publish 与
  delete 并发会观察到半 rename 中间态或删掉 publisher 刚就位的目录。
  本题案不收紧 scope，但 spec 加 forward note："cross-feature concurrency
  with resource_deletion 是预存 race；与 publisher lock 对齐留给 proposal #3
  的 lease/fencing 一起做"。
- **F9（小）**：tasks 7A.3 原措辞 "reuses the existing _prepare / _submit
  plumbing" 过于乐观——`_submit`/`_prepare` 的 `retry_sources` 语义专为
  resume 写 `resume_from_shard_basename`，clean 不需要这个字段。整改：
  tasks 7A.3 补一句 "extend `_prepare`/`_validate_task_for_submit` with
  an explicit `execution_mode` branch"，明确 resume 写 basename、clean
  不写但仍消费 source attempt 做 eligibility；测试加一条 "clean payload
  contains execution_mode='clean' and omits resume_from_shard_basename"。

## 总体结论（v5）

19 轮自审 + 3 轮独立审视 + 3 轮整改后再审，本题案现在的关键不变量：

- publisher-owned 运行时状态完全隔离在 `state/`，agent 不可写；contract
  input-hash 按枚举排除该目录；
- monotonicity 信任源是 single high-water file，**且 crash 恢复对
  "committed-journal-ahead-of-high-water" 这种半提交状态有显式 finalize
  路径**；
- `succeeded` / `noop` 是两条分立结果，runner 在 noop 时退出 repair loop
  且不更新 attempt 状态；
- clean rebuild 的事务边界对齐到现存 retry 实现 + idempotency_key UNIQUE
  约束；clean-vs-retry 跨入口并发以及 resource_deletion 跨入口并发显式
  留给 proposal #3 的 lease；
- `confirmed=true` 仅承诺防 client 默认值漏写，不承诺反 abuse；
- lock primitive `fcntl.flock` 的 fd 继承语义被显式选定为 supervisor /
  worker fork 场景的正确选择；lock 文件与题目生命周期解耦；
- 与 split-plan 题案 3 的 `execution_kind` 关系正交、推迟决策；
- 锁路径命名约定（`locks_root` / `build_publisher_locks` 关系、未来 sibling
  扩展规则）显式写明，避免后续子题案分叉。

## 第 24 轮：第四次独立审视后的整改

本轮按当前 live repo、baseline spec 和题案文本重新核对，发现 6 项残留矛盾并已
整改：

- **G1（高）manifest hash 权威源残留矛盾**：spec 一边说 committed journal
  终态会 archived/removed，一边又说 `manifest.output_manifest_hash` 只有匹配
  committed journal entry 才权威。整改：权威性改为
  `manifest.publish_generation/output_manifest_hash` 与
  `state/highest-committed-generation.json` 同 generation/hash 匹配；journal
  只负责 in-flight/recovery phase，不再作为长期审计信任源。
- **G2（高）noop repair 终态不清**：原 spec 容易把 publisher succeeded 当成
  attempt terminal state，导致 repair no-op 后跳出但不写最终失败。整改：
  明确 noop 只是不再重复 publish/validation；runner 必须使用最近一次 validation
  结果完成 attempt，若仍失败则走正常 terminal failure marker。
- **G3（高）proposal 的 no schema change 与 tasks migration 矛盾**：proposal
  写数据库无变化，但 tasks 要新增 `build_attempts.idempotency_key`。整改：
  proposal Impact 改为承认这一项 narrow schema change，同时继续说明 execution
  rows 推迟到 proposal #3。
- **G4（中-高）clean rebuild 同源并发承诺过强**：design 曾写同一 source
  attempt 并发最多生成一个 attempt，但机制只对 same idempotency key 成立，UI
  又规定每次点击新 key。整改：spec/design/tasks 统一降级为 same-key idempotency；
  different-key 请求在本题案中是独立提交，source-attempt-scoped collapse 留给
  proposal #3 lease/fencing。
- **G5（中）POSIX-only publisher 与 Windows 开发环境边界不明**：spec 选
  `fcntl.flock` 且非 POSIX fail，但当前开发机是 Windows。整改：design/tasks
  明确 publisher runtime path 是 POSIX-only；Windows 可跑非 publisher 测试，
  POSIX-only publisher lock/recovery 测试必须 platform-gate 或放到 POSIX CI。
- **G6（中）题案 1 状态说明不一致**：proposal 写 just-archived，split plan
  仍写等待实现。整改：proposal 改为"已折叠进 baseline spec/current runner code
  的 narrow bridge"，split plan 进度索引同步为 baseline + 当前代码已有 bridge。

## 总体结论（v6）

第 24 轮整改后，本题案当前可实现边界更新为：

- `output_manifest_hash` 的长期权威源是 `state/highest-committed-generation.json`
  与 manifest 的 generation/hash 配对；journal 只承担 in-flight 与 recovery
  决策职责，不再作为长期审计信任源；
- `noop` 只表示本轮 repair 无需重复 publish/validation，不表示 attempt 成功；
  runner 仍必须用最近一次 validation 结果完成终态；
- 本题案包含一项窄 DB 迁移（`build_attempts.idempotency_key`），但不引入
  execution rows；
- clean rebuild 只承诺 same-key idempotency，different-key/source-attempt-scoped
  collapse 留给 proposal #3 的 lease/fencing；
- publisher runtime path 是 POSIX-only；Windows 开发只承诺非 publisher 测试，
  完整 lock/recovery 验证必须在 POSIX CI/deployment 环境完成；
- 题案 1 的状态按"baseline spec + current runner bridge"描述，不再混用
  archived/implemented/validated 三种状态。

## 第 25 轮：实施前歧义清理

按"没有明显歧义和问题才进入实施"的标准，再清理 3 项残留：

- generation 场景标题和 GIVEN 从 "last committed journal" 改为
  `state/highest-committed-generation.json`，与 high-water 权威源一致；
- design 中 "workspace manifest is the source of truth" 改为 manifest 只是
  visible workspace record，只有与 high-water 同 generation/hash 配对才权威；
- tasks 1.6 补明只有 `succeeded` validation-repair publication 递增 generation，
  `noop` 不递增、不写 journal。

这轮不引入新范围，只把 v6 的关键不变量同步到 proposal/design/tasks/spec 的可实施
文字上。

## 第 26 轮：Task 1 实施前/实施中再评估

按 live repo 实施 Task 1 时发现一个高优先级架构矛盾：题案原写
`src/services/build_publisher.py`，但仓库的 `test_dependency_direction.py`
明确禁止 `hermes -> services`，而 runner 又必须直接调用 publisher。若按原文
实施，会破坏现有依赖方向。整改：publisher 模块改为
`src/hermes/build_publisher.py`，proposal/design/tasks/spec 同步改写；旧
`promote_claimed_outputs` stub 的指引也改为 `hermes.build_publisher`。

Task 1 实施中还发现 contract capture 顺序问题：`record_effective_timeout()`
会写 `input/manifest.json`，因此 contract 必须在所有 host pre-invocation
materialization 完成之后、Hermes 调用之前捕获。runner 已按该顺序接入。

Task 1 当前验证结果：`uv run pytest tests/app/test_dependency_direction.py
tests/app/test_build_publisher.py tests/app/test_execution_workspace.py
tests/app/test_runner_resume.py -q --basetemp .pytest-basetemp/build-publisher-stage1-all3`
为 `35 passed, 29 skipped`；`uv run ruff check src/hermes/build_publisher.py
src/hermes/workspace.py src/hermes/runner.py tests/app/test_build_publisher.py
tests/app/test_execution_workspace.py tests/app/test_runner_resume.py` 通过。

## 第 27 轮：Task 2 实施前评估

Task 2 进入实施前重新核对，发现 2.4 只写了 "environment overrides"，但未命名
具体变量，实施者无法稳定配置或测试。整改：design/spec/tasks 同步指定
`BUILD_PUBLISH_MAX_BYTES`、`BUILD_PUBLISH_MAX_FILES`、
`BUILD_PUBLISH_MAX_DEPTH`、`BUILD_PUBLISH_MAX_COMPONENT_BYTES`，均按正整数
解析，非法配置在 publisher preflight/发布前失败。

Task 2 实施结果：publisher 在 canonical mutation 前扫描 source output，temp copy
在 commit 前复扫；limits 使用 `lstat`，配置非法或超限均以 `limits` phase 失败。
新增跨平台 publisher 回归覆盖 metadata 缺失、metadata id/category 不匹配、重复
claimed id、错误 category layout、文件数限制、component byte 限制和非法 override。
验证：`uv run pytest tests/app/test_dependency_direction.py tests/app/test_build_publisher.py
tests/app/test_execution_workspace.py tests/app/test_runner_resume.py -q --basetemp
.pytest-basetemp/build-publisher-task2b` 为 `42 passed, 29 skipped`；对应 ruff
通过。

## 第 28 轮：Task 3 实施前评估与结果

Task 3 实施前核对后未发现新的题案矛盾；缺口集中在代码：此前只读取
`input/change-policy.json` 并检查 `base-artifact/` 存在，没有 schema 校验、
preserve/forbid diff、JSON field selector、路径归一化和新 descendant forbid
检查。

Task 3 实施结果：contract 捕获解析后的 immutable `ChangePolicy`；发布前按原
contract 校验 policy 未被 Hermes 修改，并在 canonical mutation 前执行
preserve/forbid diff。新增测试覆盖无 base-artifact、unknown key、traversal path、
preserve byte mismatch、preserve JSON-field mismatch、forbid 新 descendant，以及
policy all-clear 成功路径。验证：
`uv run pytest tests/app/test_dependency_direction.py tests/app/test_build_publisher.py
tests/app/test_execution_workspace.py tests/app/test_runner_resume.py -q --basetemp
.pytest-basetemp/build-publisher-task3` 为 `49 passed, 29 skipped`；对应 ruff 通过。

## 第 29 轮：Task 4 实施前评估与部分结果

Task 4 实施前发现它与 Task 5 存在顺序耦合：4.2 的 "while locks remain held"
依赖 Task 5 的 publisher locks；4.3 的 durable crash window recovery 依赖 Task 5
的 fsynced journal/reconciliation。当前阶段可以完成 deterministic hash、canonical
rehash、manifest replacement failure rollback 和 noop 语义，但不能把 lock-held
atomicity / process-death recovery 伪装成已完成。

Task 4 当前完成项：4.1 已改为 length-prefixed canonical records；4.4 已实现
`noop` 不写 journal、不递增 generation、不 quarantine。另已补 canonical rehash
和 manifest 写失败的反向 rollback，但 4.2/4.3 仍需 Task 5 锁与 durable journal
完成后再勾选。验证：
`uv run pytest tests/app/test_dependency_direction.py tests/app/test_build_publisher.py
tests/app/test_execution_workspace.py tests/app/test_runner_resume.py -q --basetemp
.pytest-basetemp/build-publisher-task4` 为 `51 passed, 29 skipped`；对应 ruff 通过。

## 总体结论（v4，已被 v5 覆盖）

19 轮自审 + 2 轮独立审视 + 2 轮整改后再审，本题案现在的关键不变量：

- **publisher-owned 运行时状态完全隔离在 `state/`**，agent 不可写；contract
  input-hash 按枚举排除该目录，避免 publisher 自身写入触发误报；
- **monotonicity 的真实信任源是 single high-water file**，不是 manifest 也
  不是无界 archive；
- **`succeeded` / `noop`** 是两条分立结果，runner 在 noop 时退出 repair loop
  且不更新 attempt 状态；
- **clean rebuild 的事务边界对齐到现存 retry 实现 + idempotency_key UNIQUE
  约束**；clean-vs-retry 跨入口并发显式留给 proposal #3 的 lease；
- **lock primitive `fcntl.flock` 的 fd 继承语义**被显式选定为 supervisor /
  worker fork 场景的正确选择；非 POSIX preflight fail；
- 与 split-plan 题案 3 的 `execution_kind` 关系正交、推迟决策。

## 总体结论（更新）

经 18 轮自审 + 1 轮外部独立审视，题案现已：

- 与拆分计划 `worker-pool-split-plan.md` 显式对齐（proposal #2 of 6）；
- 在 hermes-execution-protocol 中托管 bridge 的完整移除路径（无 silent shim）；
- 把 publisher 的私有字段（`publish_generation` / `output_manifest_hash`）的
  权威性绑定到 journal commit；
- 把 lock 根、lock primitive、平台限制、sweep throttle、no-op 短路全部落到
  spec 和 tasks，不留给实现者临时拼装；
- 把 clean rebuild 的并发/幂等边界对到现有事务实现，避免与上一轮 retry 流程
  产生分叉。

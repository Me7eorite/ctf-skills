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
5. 明确 retry/resume 与 clean rebuild 的行为、并发和幂等边界。

仍有一个明确残余限制：在不整体替换 category root 的前提下，多目录 rename 对不遵守
publisher lock 的外部读者无法提供瞬时全局原子视图。本题案已不再作该过度承诺；后续
execution fencing 和所有消费者统一锁协议可继续收紧该边界。

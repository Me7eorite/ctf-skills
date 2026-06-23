# AI 修复流程整改方案

## 背景

当前项目里的“AI 修复”语义，和“构建 / 断点续跑 / 重新生成”混在了一起。
这会导致两个问题：

- 修复任务会沿用构建流水线的日志和阶段，日志看起来像“又从 `queued/design/implement/build` 重跑了一遍”。
- 对于已经明确失败的题目，例如 `contract_failed` 或 `solver references metadata.json`，AI 实际上只需要基于**当前项目状态 + 当前错误**做修复，再重新走验收逻辑，而不是重新构建 challenge。

你现在要的是一个更清晰的闭环：

1. **分析**当前失败原因
2. **解决**当前 workspace / solver / metadata / validate 文件中的问题
3. **验收**修复结果

也就是说：

- **修复不是构建**
- **修复不是 resume**
- **修复不是 carry-forward 历史进度**
- **修复之后要重新验收**，而不是重新生成 challenge

---

## 目标

本次整改的目标是把系统拆成三条语义清晰的流程：

### 1. 构建流

用于首次生成 / 首次构建 challenge。

- 保留现有 `HermesRunner` 构建管线
- 保留当前的 `design / implement / build / document / complete` 进度语义
- 保留断点续跑能力

### 2. 修复流

用于对**已经失败的当前产物**做定点修复。

- 输入是当前失败 attempt、当前错误摘要、当前 workspace / challenge 文件
- 输出是对现有文件的最小修补
- 不允许重走构建流程
- 不允许 carry-forward 历史进度
- 不允许把修复伪装成新一轮生成

### 3. 验收流

用于对修复后的结果做权威验收。

- 仍然由 host-side validation / revalidation 负责
- 验收通过后再更新任务状态为成功
- 验收失败则保留失败态并记录最新错误

---

## 当前问题归纳

### 1. repair 语义仍然偏向 resume

当前 `repair()` 入口虽然已经带了 `repair_requested` / `repair_context`，但整体语义仍容易和 `resume` 混在一起。

表现为：

- 仍会出现历史窗口、carry-forward、skip_stages 这类 resume 术语
- 日志会从构建流水线的角度展开，而不是“分析 / 解决 / 验收”

### 2. 验收与修复没有明确分层

`BuildAttemptRevalidationService` 已经很接近“验收闭环”，但当前它的职责还是“重新跑 host validation”。

你要的不是“再构建一次再验收”，而是：

- 先修复当前失败点
- 再把修复后的结果交给 revalidation

### 3. 日志语义错误

修复场景下不该再出现：

- `Worker claimed 1 challenge(s)`
- `design/pending`
- `design/running`
- `implement/running`
- `build/passed`

这些是构建流水线的日志，而不是修复流水线的日志。

### 4. 状态展示混乱

修复任务如果仍展示为构建阶段，会让人误以为系统又重新跑了一遍 challenge。

---

## 推荐流程设计

### 构建流

适用于首次生成 / 首次构建。

1. 设计
2. 实现
3. 构建
4. 验证
5. 文档化
6. 完成

这条流保持不变。

### 修复流

适用于已经失败的 attempt，尤其是验收失败场景，例如：

- `contract_failed`
- `metadata.json` / `challenge.yml` 误用
- solver 参考了 organizer 文件
- validate.sh / exp.py 违反题目契约

修复流建议分成三步：

1. **analysis**
   - 读取失败摘要
   - 读取当前 workspace / challenge 文件
   - 判断失败属于哪类：契约违规、solver 违规、artifact 不一致、验收失败等

2. **solve**
   - 只修改当前需要修的文件
   - 优先修 solver / validate / metadata / 文档 / 题目产物
   - 目标是最小化变更

3. **verify**
   - 重新执行权威验收
   - 若通过则成功
   - 若失败则保留失败原因并结束

### 验收流

验收流就是权威判定：

- 由 host-side validation 负责
- 不由 AI 自己宣布通过
- 不允许绕过

---

## 文件级整改方案

### `src/services/build_orchestration_service.py`

#### 目标

把 `repair()` 改成真正的“修复入口”，而不是构建重放入口。

#### 要点

- `repair()` 只针对最新失败 attempt
- 保留 `repair_context`，用于描述失败摘要和修复目标
- 不再暗示 resume/carry-forward
- 不要把 repair 变成重新提交 challenge 的逻辑

#### 建议行为

- repair 任务复用当前 attempt / 当前失败 attempt 的上下文
- 产物中明确标记：这是修复任务，不是新构建任务

---

### `src/hermes/prompt.py`

#### 目标

给修复流单独一套 prompt 语义。

#### 要点

- 保留构建流 prompt
- 新增修复流 prompt 片段
- 修复流 prompt 只强调：
  - 当前失败原因
  - 当前项目状态
  - 当前允许修改的文件
  - 修复后必须重新验收

#### 文案建议

修复 prompt 中应出现类似语义：

- `analysis`
- `solve`
- `verify`

不要再出现：

- `skip_stages`
- `next_stage`
- `carry-forward`

---

### `prompts/shard_prompt.md`

#### 目标

把修复任务的语言从“构建/续跑”改成“定点修复 + 验收”。

#### 要点

- 构建流保留原有阶段说明
- 修复流新增专门段落
- 修复流明确禁止把任务解释成“重新构建 challenge”
- 修复流强调“基于当前 failure context 做最小修补，然后重新验收”

#### 建议新增的修复约束

- 只看当前失败 attempt 的错误上下文
- 只修改当前修复所需文件
- 不恢复历史构建阶段
- 不重建 challenge

---

### `src/services/build_attempt_revalidation_service.py`

#### 目标

把这里定位成**修复后的验收闭环**。

#### 现状

它已经负责：

- 读取失败 attempt
- 找出 challenge
- 跑 `run_validation()`
- 成功后更新 attempt / task 状态

#### 建议

- 继续把它作为验收引擎
- 但修复任务进入这里时，语义应是：
  - “修复后重新验收”
  - 不是“构建后再验收”

#### 最好补的行为

- 如果修复后验收通过，更新 attempt 状态为成功
- 如果验收仍失败，记录最新失败摘要并结束
- 不要在这里偷偷触发 build 流程

---

### `src/web/build_attempts_endpoints.py`

#### 目标

让 API 命名和 UI 语义都更贴近真实行为。

#### 建议

- `/repair`：表示发起修复任务
- `/revalidate`：表示重新验收
- 如果 UI 有按钮文案，建议改成：
  - `分析并修复`
  - `重新验收`

#### 不建议

- 不要把 repair 按钮显示成“重建”
- 不要把 repair 结果描述成“重新构建成功”

---

### `src/hermes/runner.py`

#### 目标

保留构建流，避免修复流继续走构建语义。

#### 建议

- 构建流继续使用当前 runner
- repair 流不再依赖 resume plan / carry-forward
- 若 repair 需要 prompt 支持，应只注入 `repair_context`
- repair 流的日志不应表现为“从头 build”

#### 重点

当前 runner 里那些 `design/implement/build/document` 的事件，应只属于构建流，不应再借给修复流复用。

---

### `src/core/state.py` / `src/hermes/workspace_progress.py`

#### 目标

避免修复流继续污染构建进度模型。

#### 建议

- 构建进度仍使用现有阶段：`design / implement / build / document / complete`
- 修复流单独使用修复日志或修复状态
- 如果需要在 UI 里展示修复进度，可以单独引入：
  - `analysis`
  - `solve`
  - `verify`

#### 不建议

- 不要直接把修复阶段硬塞进现有构建阶段里
- 不要让修复任务继续复用 `carry-forward` 事件

---

## 推荐的日志语义

修复任务建议输出如下语义：

1. `analysis started`
2. `root cause identified`
3. `patch applied`
4. `verification running`
5. `verification passed` / `verification failed`

这样日志就会和你的预期一致：

- 不是重新构建
- 而是分析当前错误、修改当前项目、再验收

---

## 推荐的状态机

### 构建任务

- `queued`
- `designing`
- `building`
- `built`
- `build_failed`

### 修复任务

如果要严格区分，建议增加修复语义：

- `analysis`
- `repairing`
- `revalidating`
- `succeeded`
- `failed`

如果暂时不想改数据库状态，也至少在 UI 和日志层把它们解释成这套语义。

---

## 验收标准

整改完成后，应满足以下条件：

1. repair 任务不再看起来像重新构建 challenge
2. repair 日志不再从 `queued/design/implement/build` 开始
3. repair 只围绕当前失败 attempt 和当前 failure context 工作
4. 修复后会重新跑权威验收
5. 验收通过后，任务正确进入成功终态
6. 验收失败时，任务保留失败终态和最新失败摘要

---

## 实施顺序建议

### 第一阶段：语义拆分

- 把 repair 和 build 的语义分开
- 修复 prompt 中移除 resume / carry-forward 语义

### 第二阶段：日志重写

- 将 repair 流日志改成 analysis / solve / verify
- UI 文案同步更新

### 第三阶段：验收闭环

- 让 repair 结束后强制进入 revalidate
- 通过 revalidate 决定成功 / 失败

### 第四阶段：测试补齐

- repair 不能再走构建阶段日志
- repair 不能再 carry-forward
- 失败后修复应进入重新验收，而不是重建

---

## 需要重点回归的例子

你给的这个例子应该这样处理：

> `status=contract_failed error=re solver references 'metadata.json'; it must derive the flag from the artifact, not organizer files`

正确的 repair 行为应是：

1. 识别出 solver 违规引用 organizer 文件
2. 修改 solver，使其从 artifact 中推导 flag
3. 不重新构建 challenge
4. 重新执行验收逻辑
5. 验收通过则成功

而不是：

- 再来一遍 `design/build`
- 再让日志从 `Worker claimed 1 challenge(s)` 开始
- 再把修复伪装成构建恢复

---

## 结论

这次整改的核心，不是“修一个失败”，而是**把修复流程的语义从构建链路里剥离出来**。

最终应形成三个清晰角色：

- **build**：首次生成 / 构建 challenge
- **repair**：分析当前失败并修复当前项目
- **revalidate**：对修复结果做权威验收

这样你看到的日志、进度和状态才会和真实动作一致。

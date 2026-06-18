# Challenge Factory 代码审查报告 — Bug 与实现不合理分析

> 审查日期: 2026-06-18 | 审查范围: src/ 全部 Python 源文件（66 个 .py 文件），以及 alembic/、tools/ 目录

---

## 一、严重 Bug（影响正确性）

### BUG-1: `domain/metrics.py` — `_parse_timestamp` 时区处理错误 ★★☆

**文件**: `src/domain/metrics.py:29-33`
**严重程度**: 中高

```python
def _parse_timestamp(value: str) -> float | None:
    try:
        return time.mktime(time.strptime(value, _TIMESTAMP_FORMAT))
    except (TypeError, ValueError):
        return None
```

**问题**: 项目中所有时间戳均为 UTC 格式 (`%Y-%m-%dT%H:%M:%SZ`)，但 `time.mktime` 会将输入**当作本地时间**解析，而非 UTC 时间。在 UTC+8 (中国时区) 的环境下，计算出的 epoch 值会比实际 UTC epoch 值多 8*3600=28800 秒。

**实际影响**: 由于 `duration_breakdown()` 中对同一阶段的 start/end 时间戳都使用了相同的错误转换，相对差值(duration)在非 DST 切换期间碰巧是正确的。但：
1. 在夏令时切换日期，start 和 end 可能被映射到不同的 UTC offset，导致 duration 计算错误
2. 代码的语义完全不正确，任何依赖绝对时间戳的操作都会出问题

**修复建议**: 使用 `datetime.strptime` + `calendar.timegm` 或 `datetime.fromisoformat` 进行正确的 UTC 解析。

---

### BUG-2: `hermes/process.py` — `invoke_capture()` 中 `process.wait()` 无限阻塞风险 ★★☆

**文件**: `src/hermes/process.py:249-270`
**严重程度**: 中

```python
except BaseException:
    _terminate(process)
    stdout_thread.join(timeout=2)
    stderr_thread.join(timeout=2)
    process.wait()          # <-- 没有 timeout
    ...
    raise
```

**问题**: `BaseException` 异常分支中（如 KeyboardInterrupt），虽然调用了 `_terminate(process)`（SIGTERM → 5s → SIGKILL），但 `process.wait()` **没有设置超时**。如果 `_terminate()` 中的 SIGKILL 因某种原因未能杀死进程（例如进程处于 D 状态），这里会永久阻塞。

**触发条件**: 子进程进入不可中断睡眠(如 I/O 挂起)，且 SIGKILL 未生效。

**修复建议**: `process.wait(timeout=10)` 并额外处理 `TimeoutExpired`。

---

### BUG-3: `packing/packer.py` — 端口 0 未被校验 ★☆☆

**文件**: `src/packing/packer.py:168`

```python
if metadata.get("port") in (None, ""):
    raise PackingError(f"{challenge_id}: containerized challenge has no port")
```

**问题**: 判空逻辑 `in (None, "")` 不会捕获端口值为 `0` 的情况。虽然 `domain/seeds.py` 的种子校验已强制端口为 1-65535，但 `metadata.json` 可能由其他路径生成（如手动编辑、Hermes 输出），packer 不应信任上游数据始终合法。

**实际影响**: 端口 0 的容器化题目不会被拒绝，但 Docker 无法绑定端口 0。

**修复建议**: 使用 `not metadata.get("port")` (端口 0 也是 falsy) 或显式检查 `<= 0`。

---

### BUG-4: `domain/validation.py` — `contract_errors` 中缺失值判断使用 `not` 过于宽泛 ★☆☆

**文件**: `src/domain/validation.py:175-179`

```python
errors = [
    f"metadata.{field} is missing"
    for field in ("id", "title", "difficulty", "build_status", "flag")
    if not metadata.get(field)
]
```

**问题**: `not metadata.get(field)` 会把 `False`、`0`、`""` 都当作"缺失"。对于 `id` 字段值为 `0` (虽然不太可能) 的边界情况会误判，且 `build_status` 的值 `"passed"` 恰好是 truthy 所以不受影响。

**实际影响**: 极低，但在理论上不够严谨。

**修复建议**: 使用 `not isinstance(metadata.get(field), str) or not metadata.get(field)`，显式要求非空字符串。

---

## 二、中等 Bug（代码质量问题，特定条件下触发）

### BUG-5: `domain/seeds.py` — `enqueue()` 中校验结果被丢弃 ★★☆

**文件**: `src/domain/seeds.py:59-70`

```python
def enqueue(self, size: int = 5) -> list[Path]:
    seeds = self.list()
    if not seeds:
        raise ValueError("请先保存至少一个题目种子")
    for seed in seeds:
        validate_seed(seed)      # <-- 返回值被丢弃
    return split_challenges(seeds, ...)
```

**问题**: `validate_seed()` 会对 seed 做 `.strip()`、`.lower()` 等规范化处理并返回规范化后的 dict。但这里直接丢弃了返回值，后续 `split_challenges` 使用的是未规范化的原始数据。虽然 `save()` 时已做过一次规范化，但如果 seed 文件被外部程序直接修改后包含不规范数据（如带尾随空格的 id），这里的行为会不一致。

**实际影响**: 如果种子文件被外部修改，可能生成不符合预期的 shard 文件名。

**修复建议**: 使用 `seeds = [validate_seed(s) for s in seeds]` 并在必要时写回规范化后的数据。

---

### BUG-6: `hermes/process.py` — `invoke()` 简单版未捕获 `PermissionError` ★☆☆

**文件**: `src/hermes/process.py:148-152`

```python
except FileNotFoundError:
    output.write("Hermes command not found. ...")
    return 127
```

**问题**: 只捕获了 `FileNotFoundError`。在极少数场景下，如果 Hermes 可执行文件存在但无执行权限，Python 子进程调用可能抛出其他 `OSError` 子类。虽然 `subprocess.run(shell=False)` 通常只抛出 `FileNotFoundError`，但在某些操作系统/文件系统下有不同行为。

**实际影响**: 极低。`subprocess.run` 在 `shell=False` 时对无执行权限的文件行为依赖 Python 版本和 OS。

**修复建议**: 捕获更广泛的 `OSError`，或者保持现状并在文档中说明。

---

## 三、代码设计与实现不合理

### DESIGN-1: `_normalize_shard()` 函数三处重复定义 ★★★

**文件**:
- `src/core/state.py:248-249`: `def _normalize_shard(shard: str) -> str: return Path(shard).name`
- `src/persistence/repositories/progress.py:233-234`: 完全相同的实现
- `src/hermes/validation.py`: 通过 `core.state._normalize_shard` 导入，但 `progress.py` 有自己独立的副本

**问题**: 这是典型的代码重复（DRY 违反）。如果未来需要修改 shard 名称归一化逻辑，必须同时修改两个地方，否则会产生数据不一致。

**修复建议**: 将 `_normalize_shard` 提取到 `core/` 或 `domain/` 作为公共工具函数。

---

### DESIGN-2: `_category_of()` 函数三处重复定义 ★★★

**文件**:
- `src/hermes/runner.py:586-591`
- `src/hermes/validation.py:161-166`
- `src/domain/resume.py:324-329`

三个文件中都有完全相同的实现：

```python
def _category_of(challenge_dir, paths: ProjectPaths) -> str:
    try:
        relative = challenge_dir.resolve().relative_to(paths.challenges.resolve())
    except ValueError:
        return ""
    return relative.parts[0] if relative.parts else ""
```

**问题**: 高度重复的业务逻辑，任何一个文件的修改都需要同步到另外两处。

**修复建议**: 提取到 `core/paths.py` 或 `domain/` 中的公共模块。

---

### DESIGN-3: `_prepare_event()` 函数两处独立实现 ★★★

**文件**:
- `src/core/state.py:233-245`: `_prepare_event(event: ProgressEventInput) -> ProgressEventInput`
- `src/persistence/repositories/progress.py:218-230`: 完全相同的逻辑

**问题**: 同 DESIGN-1，这是典型的 DRY 违反。虽然有相同的参数校验逻辑，但维护负担加倍。

**修复建议**: 在 `core/state.py` 中保留一份实现，`progress.py` 中直接导入使用。

---

### DESIGN-4: 到处散落的 `_utcnow()` 重复定义 ★★☆

**文件**:
- `src/persistence/repositories/research.py:442-444`
- `src/services/research_job_service.py:371-373`

两者都是 `return datetime.now(timezone.utc)` 的包装。

**修复建议**: 提取到公共工具模块。

---

### DESIGN-5: `STAGE_ORDER` 定义不一致 ★★☆

**文件**:
- `src/domain/resume.py:29-35`: 5 个阶段 (design → document)
- `src/domain/metrics.py:18-24`: 5 个阶段 (相同顺序)
- `src/core/state.py:11-19`: `STAGES` 包含 7 个阶段 (含 queued 和 complete)

**问题**: `metrics.py` 和 `resume.py` 都定义了 `STAGE_ORDER`，虽然值相同，但属于重复定义。同时 `state.py` 有 `STAGES`，两者语义略有不同但高度相关。如果将来要新增或删减阶段，需要修改所有三处。

**修复建议**: 在 `core/state.py` 中定义一个权威阶段列表，其他模块从中派生需要的部分。

---

### DESIGN-6: `transaction()` 上下文管理器的异常捕获过于宽泛 ★★☆

**文件**: `src/persistence/session.py:54`

```python
except BaseException:
    session.rollback()
    raise
```

**问题**: 捕获 `BaseException` 会拦截 `SystemExit` 和 `KeyboardInterrupt`，触发 rollback 后重新抛出（这是合理的行为）。但同时也会拦截 `GeneratorExit`，而 GeneratorExit 被无意中捕获可能会掩盖生成器生命周期问题。

**实际影响**: 低。在 `contextmanager` 装饰的生成器中，`GeneratorExit` 通常不会发生在 `yield` 处（因为 `yield` 在 `try` 块的中间），但捕获 `BaseException` 始终是一个值得注意的设计决策。更标准的做法是捕获 `Exception` 并单独处理 `KeyboardInterrupt`。

---

### DESIGN-7: `Packer._pack_challenge` 中过多的硬编码魔法字符串 ★★☆

**文件**: `src/packing/packer.py:158`

```python
stem = f"js-{prefix}-{delivery_name}"
```

**问题**: `"js-"` 前缀是硬编码的，`CATEGORY_PREFIXES` 中有 `"re": "reverse"` 的映射（即 Reverse Engineering 被显示为 "reverse" 而不是 "re"），但 `SUPPORTED_CATEGORIES = {"web", "pwn", "re"}` 使用的是 `"re"`。这些命名约定散布在不同模块中，没有一个统一的 crosswalk。

---

### DESIGN-8: `SeedStore._write()` 中 `item["id"]` 可能 KeyError ★☆☆

**文件**: `src/domain/seeds.py:75`

```python
write_json(temporary, {"seeds": sorted(seeds, key=lambda item: item["id"])})
```

**问题**: `sorted()` 的 key 函数假设所有 seed dict 都有 `"id"` 键。虽然 `save()` 和 `validate_seed()` 都保证了这一点，但如果 `list()` 返回的 seeds 来自损坏的 JSON 文件，这里会直接崩溃而不是优雅处理。

---

## 四、安全相关

### SEC-1: `domain/seeds.py` — 用户输入的 `id` 通过 `Path.name` 做了基础沙箱化，但路径遍历保护有限 ★☆☆

**文件**: `src/domain/seeds.py:52`

```python
safe_id = Path(challenge_id).name
```

**问题**: `Path("/etc/passwd").name` 返回 `"passwd"`，这确实防止了路径遍历。但 `Path("../../../etc/passwd").name` 也返回 `"passwd"`。这个 `name` 属性在这里的语义是正确的 — 它只需要文件名。但如果在其他地方使用了 `challenge_id` 而没有类似的保护，则存在风险。

### SEC-2: `hermes/process.py` — API Key 保护措施完善 ✓

`_LOGGED_ENV_KEYS` 只包含 `HERMES_HOME`, `HERMES_CMD`, `HERMES_PROFILE`, `CUSTOM_BASE_URL`，刻意排除了 `CUSTOM_API_KEY`。日志输出中不会泄露敏感凭据。**这是一个好的安全实践。**

---

## 五、总结

| 类别 | 严重 | 中等 | 轻微 | 合计 |
|------|------|------|------|------|
| Bug (影响正确性) | 0 | 3 | 1 | 4 |
| 代码重复/设计问题 | 3 | 1 | 0 | 4 |
| 安全相关 | 0 | 0 | 2 | 2 |
| **总计** | **3** | **4** | **3** | **10** |

### 建议优先修复

1. **BUG-1** (`_parse_timestamp` 时区错误) — 虽然不是立即导致可见错误，但一旦进入夏令时切换期或依赖绝对时间戳的功能上线，就会出问题。
2. **DESIGN-1/2/3** (重复代码) — 代码重复已经达到影响可维护性的程度，三个模块中有多处 3 份以上的完全重复实现。

### 项目整体评价

项目代码整体**质量较高**，遵循了清晰的分层架构（core → domain → persistence → hermes → services → web），事务边界处理正确，并发控制（lease + claim_token fencing）设计合理。上述发现的问题主要属于：
- 个别函数的时间处理疏忽
- 代码复制导致的维护负担
- 边界条件防御不足

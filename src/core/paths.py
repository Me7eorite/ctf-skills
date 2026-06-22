"""项目路径配置。

定义了 ProjectPaths 数据类，统一管理项目所有目录和文件的路径。
所有路径都相对于项目根目录（即包含 pyproject.toml 的目录）。
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path


@dataclass(frozen=True)
class ProjectPaths:
    """项目中所有文件和目录路径的统一入口。

    这是一个不可变（frozen）数据类，所有路径由 root 和 repository 两个基础路径派生。
    使用 property 而非字段使得路径总是动态计算，和基础路径保持同步。

    属性:
        root: 项目根目录（包含 pyproject.toml）
        repository: skills 和 prompts 等资源文件的仓库根目录
    """

    # 项目根目录
    root: Path
    # 仓库根目录（可能和 root 不同，用于引用 skills 等共享资源）
    repository: Path

    @classmethod
    def discover(cls) -> "ProjectPaths":
        """自动发现项目根目录。

        从当前文件的路径向上走两级（src/core/paths.py → src → 项目根），
        推导出项目根目录位置。
        """
        root = Path(__file__).resolve().parents[2]
        return cls(root=root, repository=root)

    # ========== 工作目录（work/）==========

    @property
    def work(self) -> Path:
        """工作目录根路径，所有运行时产物都放在 work/ 下。"""
        return self.root / "work"

    @property
    def shards(self) -> Path:
        """分片目录，存放 pending/running/done/failed 四种状态的分片文件。"""
        return self.work / "shards"

    @property
    def build_attempt_staging(self) -> Path:
        """构建分片在数据库提交前的私有 staging 目录。"""
        return self.shards / "staging" / "build-attempts"

    @property
    def challenges(self) -> Path:
        """生成的题目目录，按类别（web/pwn/re）组织。"""
        return self.work / "challenges"

    @property
    def reports(self) -> Path:
        """报告输出目录，存放校验结果、各阶段报告等 JSON 文件。"""
        return self.work / "reports"

    @property
    def delivery_bundle(self) -> Path:
        """交付包的输出目录（中文名"资源包"）。"""
        return self.work / "资源包"

    @property
    def challenge_seeds(self) -> Path:
        """题目种子文件（challenge-seeds.json），存储人工预设的题目原型数据。"""
        return self.work / "challenge-seeds.json"

    @property
    def logs(self) -> Path:
        """通用日志目录。"""
        return self.work / "logs"

    @property
    def executions(self) -> Path:
        """Build execution workspaces, isolated by workspace id."""
        return self.work / "executions"

    @property
    def locks_root(self) -> Path:
        """Cross-process lock root. Future proposals add sibling subdirectories
        here (e.g. lease locks in proposal #3); never nest under
        build-publisher/."""
        return self.work / "locks"

    @property
    def build_publisher_locks(self) -> Path:
        """Cross-process lock directory owned by hermes.build_publisher.

        Lock filenames are a hex digest of `(category, claimed_id)` and
        carry no secrets, so default umask permissions are sufficient.
        """
        return self.locks_root / "build-publisher"

    # ========== Research 研究流程路径 ==========

    @property
    def research_sources(self) -> Path:
        """Research 流程的资料来源目录。"""
        return self.work / "research" / "sources"

    @property
    def research_sources_staging(self) -> Path:
        """Research source raw-text staging directory."""
        return self.work / "research" / "sources_staging"

    @property
    def research_logs(self) -> Path:
        """Research 流程的日志目录。"""
        return self.work / "research" / "logs"

    @property
    def worker_handshake(self) -> Path:
        """Research worker startup handshake directory."""
        return self.work / "research" / "worker_handshake"

    # ========== Design 设计流程路径 ==========

    @property
    def design_prompts(self) -> Path:
        """Design 流程的提示词（prompt）模板目录。"""
        return self.work / "design" / "prompts"

    @property
    def design_logs(self) -> Path:
        """Design 流程的日志目录。"""
        return self.work / "design" / "logs"

    # ========== 资源文件路径 ==========

    @property
    def static(self) -> Path:
        """Web Dashboard 的静态资源目录（CSS/JS）。"""
        return Path(str(files("web") / "static"))

    @property
    def prompt_template(self) -> Path:
        """Shard 执行的 AI prompt 模板文件（Markdown 格式）。"""
        return self.root / "prompts" / "shard_prompt.md"

    @property
    def prompts(self) -> Path:
        """Prompts 模板目录（不存在的子文件由调用方解析）。"""
        return self.root / "prompts"

    @property
    def generation_profile(self) -> Path:
        """生成配置文件（generation-profiles.json），定义题目生成的参数预设。"""
        return self.root / "generation-profiles.json"

    @property
    def design_skill(self) -> Path:
        """设计挑战的 skill 定义文件（SKILL.md）。"""
        return self.repository / "skills" / "design-challenges" / "SKILL.md"

    @property
    def design_references(self) -> Path:
        """设计挑战的参考文件目录。"""
        return self.repository / "skills" / "design-challenges" / "references"

    @property
    def hermes_home(self) -> Path:
        """Hermes AI 代理的家目录，存放配置、profile 等。"""
        return self.root / ".hermes"

    def initialize(self) -> list[Path]:
        """创建所有必需的目录结构。

        在项目首次使用时调用，确保所有工作目录就位。
        返回创建的目录列表。

        创建的目录包括：
          - shards/ 下的 pending、running、done、failed 四个状态子目录
          - challenges/ 下的 web、pwn、re 三个类别子目录
          - reports、logs、research、design 等目录
        """
        directories = [
            # 分片的四种状态目录
            *(
                self.shards / state
                for state in ("pending", "running", "done", "failed")
            ),
            self.build_attempt_staging,
            # 三种题目类别目录
            *(self.challenges / category for category in ("web", "pwn", "re")),
            self.reports,
            self.logs,
            self.executions,
            self.locks_root,
            self.build_publisher_locks,
            self.research_sources,
            self.research_sources_staging,
            self.research_logs,
            self.worker_handshake,
            self.design_prompts,
            self.design_logs,
        ]
        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)
        return directories


def category_of(challenge_dir: Path, paths: "ProjectPaths") -> str:
    """从题目目录路径中推断类别。

    逻辑: 相对于 `paths.challenges` 根目录的路径的第一段即为类别名。
    例如: `work/challenges/web/web-0001-sqli/` → `"web"`。
    当目录不在 `challenges` 树下或处于根本身时返回空字符串。

    实现为模块级函数（而非 ProjectPaths 方法），让测试中的 paths 替身可以
    用结构化鸭子类型（只需 `.challenges` 属性）参与，而不必实现新方法。
    """
    try:
        relative = challenge_dir.resolve().relative_to(paths.challenges.resolve())
    except ValueError:
        return ""
    return relative.parts[0] if relative.parts else ""

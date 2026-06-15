// Skeleton view for the upcoming "Research" workflow (Section 10 of
// `add-research-planning-core`). Endpoints listed below are NOT implemented
// yet — once they land, replace the placeholder cards with live data.
//
//   GET /api/research/requests?category=&status=     (10.1)
//   GET /api/research/requests/{id}                  (10.2)
//   GET /api/research/categories                     (10.4)
//   GET /api/profile/bindings                        (10.5)
//   GET /api/profile/bindings/{role}                 (10.6)
//   GET /api/research/runs?...                       (10.7)
//   GET /api/research/queue/stats                    (10.8)

import { escapeHtml } from "../ui/format.js";

function placeholder(title, subtitle, hint) {
  return `
    <section class="card">
      <div class="card-header">
        <div>
          <div class="card-title">${escapeHtml(title)}</div>
          <div class="card-subtitle">${escapeHtml(subtitle)}</div>
        </div>
        <span class="pill">即将上线</span>
      </div>
      <div class="empty" style="border: none; padding: 32px 18px;">
        ${escapeHtml(hint)}
      </div>
    </section>
  `;
}

export function render() {
  const root = document.querySelector('[data-view="research"]');
  if (!root) return;
  root.innerHTML = `
    <div class="mb-5 rounded-md border border-brand-500/30 bg-brand-50 px-4 py-3 text-[13px] text-brand-700">
      <div class="font-medium">研究模块预览</div>
      <p class="mt-0.5 text-[12px] text-brand-700/80">
        本页为 <code class="font-mono">add-research-planning-core</code> 第 10 节预留的视图骨架；
        后端 read 端点上线后会自动填充数据。
      </p>
    </div>
    <div class="grid gap-4 xl:grid-cols-2">
      ${placeholder(
        "生成请求",
        "从话题与种子 URL 触发的调研任务",
        "GET /api/research/requests 落地后这里展示请求列表与状态分布。",
      )}
      ${placeholder(
        "队列统计",
        "running / queued / failed 概览，租约即将过期项",
        "GET /api/research/queue/stats 落地后填充。",
      )}
      ${placeholder(
        "运行记录",
        "research_runs，可按状态、claimed_by 过滤",
        "GET /api/research/runs 落地后填充分页表格。",
      )}
      ${placeholder(
        "Profile 绑定",
        "research / planning / shard_execution → Hermes profile",
        "GET /api/profile/bindings 落地后填充只读绑定表。",
      )}
    </div>
  `;
}

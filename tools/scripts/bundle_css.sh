#!/bin/sh
# 把 src/web/static/css/ 下所有 CSS 合并成单个 app.css，保留 index.html 历史 cascade 顺序。
# 用法：tools/scripts/bundle_css.sh
# 不要直接改 app.css；改源 CSS 后跑这个脚本重新生成。
set -eu

cd "$(dirname "$0")/../.."
ROOT="$(pwd)"
OUT="$ROOT/src/web/static/css/app.css"

{
  echo "/* Auto-generated bundle. DO NOT EDIT — source files under css/ are authoritative."
  echo " * Regenerate via: tools/scripts/bundle_css.sh"
  echo " * Order MUST match the historical <link> order in index.html for cascade safety."
  echo " */"
  for f in \
    src/web/static/css/tokens.css \
    src/web/static/css/base.css \
    src/web/static/css/layout.css \
    src/web/static/css/sidebar.css \
    src/web/static/css/header.css \
    src/web/static/css/components/card.css \
    src/web/static/css/components/table.css \
    src/web/static/css/components/pill.css \
    src/web/static/css/components/dot.css \
    src/web/static/css/components/form.css \
    src/web/static/css/components/button.css \
    src/web/static/css/components/toast.css \
    src/web/static/css/components/tabs.css \
    src/web/static/css/components/empty.css \
    src/web/static/css/components/filter.css \
    src/web/static/css/components/metrics.css \
    src/web/static/css/components/breadcrumb.css \
    src/web/static/css/views/progress.css \
    src/web/static/css/views/logs.css \
    src/web/static/css/views/research-submit.css \
    src/web/static/css/views/research-requests.css \
    src/web/static/css/views/design-tasks.css \
    src/web/static/css/views/build-attempts.css \
  ; do
    rel=${f#src/web/static/}
    printf '\n/* ==== %s ==== */\n' "$rel"
    cat "$f"
  done
} > "$OUT"

echo "wrote $OUT ($(wc -c < "$OUT") bytes)"

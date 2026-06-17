/**
 * 图标初始化模块 —— 解决 CDN 加载失败后图标静默丢失的问题
 *
 * 功能:
 *  - 安全初始化 Lucide 图标，含加载失败时的自动重试
 *  - CDN 超时后提供文字 fallback（首字母占位）
 *  - 所有调用方统一入口，避免散落的 window.lucide?.createIcons()
 */

const ICON_RETRY_DELAY = 500;
const ICON_MAX_RETRIES = 6;

let iconReady = false;
let iconRetries = 0;
let iconRetryTimer = null;

/**
 * 安全地初始化所有 Lucide 图标
 * 如果 Lucide 未加载，自动延迟重试
 * 达到最大重试次数后显示文字 fallback
 */
export function initIcons() {
    if (window.lucide && typeof window.lucide.createIcons === 'function') {
        try {
            window.lucide.createIcons();
            iconReady = true;
            iconRetries = 0;
            return;
        } catch (err) {
            console.warn('[图标] lucide.createIcons 执行异常:', err);
        }
    }

    // 尚未就绪，安排重试
    iconRetries += 1;
    if (iconRetries <= ICON_MAX_RETRIES) {
        if (iconRetryTimer) clearTimeout(iconRetryTimer);
        iconRetryTimer = setTimeout(() => initIcons(), ICON_RETRY_DELAY);
        return;
    }

    // 最终 fallback: 显示文字占位符替代空图标
    if (!iconReady) {
        console.error(
            '[图标] Lucide 图标库加载失败（已重试 %d 次），已启用文字 fallback。请检查网络或 assets/lucide.min.js 是否存在。',
            ICON_MAX_RETRIES,
        );
        showFallback();
    }
}

/**
 * 重置图标就绪状态（新导航时调用）
 */
export function resetIcons() {
    iconReady = false;
    iconRetries = 0;
    if (iconRetryTimer) {
        clearTimeout(iconRetryTimer);
        iconRetryTimer = null;
    }
}

/**
 * Fallback: 将未渲染的 data-lucide 图标替换为首字母文字占位符
 */
function showFallback() {
    const icons = document.querySelectorAll('i[data-lucide]');
    icons.forEach((el) => {
        if (!el.innerHTML.trim()) {
            const iconName = el.getAttribute('data-lucide') || '';
            const initial = iconName.charAt(0).toUpperCase();
            el.style.display = 'inline-flex';
            el.style.alignItems = 'center';
            el.style.justifyContent = 'center';
            el.style.fontSize = '9px';
            el.style.fontWeight = '700';
            el.style.color = 'var(--ink-400)';
            el.style.width = '16px';
            el.style.height = '16px';
            el.textContent = initial;
        }
    });
    iconReady = true; // 标记已处理，避免重复 fallback
}

/**
 * 检查图标系统是否就绪
 */
export function isIconReady() {
    return iconReady;
}

<script setup lang="ts">
import { useRoute } from 'vue-router'

const route = useRoute()

interface NavItem {
  to: string
  label: string
  icon: string
}

interface NavSection {
  label: string
  items: NavItem[]
}

const navSections: NavSection[] = [
  {
    label: '',
    items: [
      { to: '/', label: '控制台', icon: '◫' }
    ]
  },
  {
    label: '知识管理',
    items: [
      { to: '/knowledge-bases', label: '知识库', icon: '▤' },
      { to: '/documents', label: '文档', icon: '▢' },
      { to: '/documents/upload', label: '文档上传', icon: '⬆' },
    ]
  },
  {
    label: '质量与调试',
    items: [
      { to: '/parse-eval', label: '解析评测', icon: '◈' },
      { to: '/debug/search', label: '检索调试', icon: '◎' },
    ]
  },
  {
    label: '系统',
    items: [
      { to: '/settings/model', label: '模型配置', icon: '⚙' },
      { to: '/settings/chunker', label: '切分配置', icon: '⚙' },
      { to: '/settings/vector-store', label: '向量库', icon: '⚙' },
      { to: '/settings/retrieval', label: '检索配置', icon: '⚙' },
      { to: '/settings/mcp-access', label: 'MCP 接入', icon: '⚙' },
    ]
  }
]

function isActive(path: string): boolean {
  if (path === '/') return route.path === '/'
  return route.path === path || route.path.startsWith(path + '/')
}

function handleLogout() {
  localStorage.removeItem('know_rag_token')
  window.location.href = '/login'
}
</script>

<template>
  <div class="app-layout">
    <aside class="sidebar">
      <div class="sidebar-top">
        <RouterLink to="/" class="brand">
          <span class="brand-icon">◆</span>
          <span class="brand-text">know_rag</span>
        </RouterLink>
      </div>

      <nav class="sidebar-nav">
        <template v-for="(section, si) in navSections" :key="si">
          <div v-if="section.label" class="nav-section-label">{{ section.label }}</div>
          <RouterLink
            v-for="item in section.items"
            :key="item.to"
            :to="item.to"
            class="nav-link"
            :class="{ active: isActive(item.to) }"
          >
            <span class="nav-icon">{{ item.icon }}</span>
            {{ item.label }}
          </RouterLink>
        </template>
      </nav>

      <div class="sidebar-footer">
        <button class="nav-link logout-btn" @click="handleLogout">
          <span class="nav-icon">↩</span>
          退出登录
        </button>
      </div>
    </aside>

    <div class="main-column">
      <header class="top-header">
        <div class="header-left">
          <h1 class="page-title">{{ route.meta.title || '' }}</h1>
        </div>
      </header>
      <main class="main-content">
        <RouterView />
      </main>
    </div>
  </div>
</template>

<style scoped>
.app-layout {
  display: grid;
  grid-template-columns: var(--sidebar-width) 1fr;
  min-height: 100vh;
}

/* ─── Sidebar ─── */
.sidebar {
  position: sticky;
  top: 0;
  height: 100vh;
  display: flex;
  flex-direction: column;
  background: linear-gradient(180deg, #102033 0%, #0e1b2c 100%);
  color: var(--sidebar-text);
  padding: 0;
  overflow-y: auto;
  box-shadow: 4px 0 24px rgba(0, 0, 0, 0.08);
  z-index: 100;
}

.sidebar-top {
  padding: 20px 20px 16px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
}

.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  color: #fff;
  text-decoration: none;
}

.brand-icon {
  font-size: 22px;
  color: var(--c-primary);
}

.brand-text {
  font-size: 20px;
  font-weight: 700;
  letter-spacing: 0.3px;
}

/* ─── Nav ─── */
.sidebar-nav {
  flex: 1;
  padding: 12px 12px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.nav-section-label {
  margin: 14px 8px 6px;
  font-size: 11px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: rgba(200, 212, 227, 0.4);
}

.nav-link {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 12px;
  border-radius: var(--r-sm);
  color: var(--sidebar-text);
  font-size: 14px;
  font-weight: 500;
  transition: all 0.15s ease;
  text-decoration: none;
  border: none;
  background: none;
  width: 100%;
  cursor: pointer;
  text-align: left;
}

.nav-link:hover {
  background: rgba(255, 255, 255, 0.07);
  color: #fff;
}

.nav-link.active {
  background: var(--sidebar-active-bg);
  color: var(--sidebar-active-text);
  font-weight: 600;
}

.nav-icon {
  width: 20px;
  text-align: center;
  font-size: 15px;
  opacity: 0.7;
  flex-shrink: 0;
}

.nav-link.active .nav-icon {
  opacity: 1;
}

/* ─── Footer ─── */
.sidebar-footer {
  padding: 12px;
  border-top: 1px solid rgba(255, 255, 255, 0.06);
}

.logout-btn {
  opacity: 0.6;
}

.logout-btn:hover {
  opacity: 1;
  background: rgba(239, 68, 68, 0.15) !important;
  color: #fca5a5 !important;
}

/* ─── Main column ─── */
.main-column {
  display: flex;
  flex-direction: column;
  min-width: 0;
  background: var(--c-bg);
}

.top-header {
  position: sticky;
  top: 0;
  z-index: 50;
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: var(--header-height);
  padding: 0 var(--content-padding);
  background: var(--header-bg);
  border-bottom: 1px solid var(--header-border);
  backdrop-filter: blur(8px);
}

.page-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--t-primary);
  margin: 0;
}

.main-content {
  flex: 1;
  padding: var(--content-padding);
  display: flex;
  justify-content: center;
}

.main-content > :deep(*) {
  width: 100%;
  max-width: var(--content-max-width);
}

/* ─── Responsive ─── */
@media (max-width: 1024px) {
  .app-layout {
    grid-template-columns: 220px 1fr;
  }
}

@media (max-width: 820px) {
  .app-layout {
    grid-template-columns: 1fr;
  }
  .sidebar {
    position: fixed;
    left: -280px;
    width: 280px;
    transition: left 0.25s ease;
  }
  .sidebar.open {
    left: 0;
  }
  .main-content {
    padding: 20px 16px;
  }
}
</style>

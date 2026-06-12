import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'

const routes: RouteRecordRaw[] = [
  {
    path: '/',
    name: 'overview',
    component: () => import('@/pages/OverviewPage.vue'),
    meta: { breadcrumb: 'Overview' },
  },
  {
    path: '/generate/new',
    name: 'generate-new',
    component: () => import('@/pages/NewRunPage.vue'),
    meta: { breadcrumb: 'New Run' },
  },
  {
    path: '/generate/runs',
    name: 'runs-list',
    component: () => import('@/pages/RunsListPage.vue'),
    meta: { breadcrumb: 'Runs' },
  },
  {
    path: '/generate/runs/:shard',
    name: 'run-detail',
    component: () => import('@/pages/RunDetailPage.vue'),
    meta: { breadcrumb: 'Run detail' },
    props: true,
  },
  {
    path: '/generate/runs/:shard/challenges/:id',
    name: 'challenge-detail',
    component: () => import('@/pages/ChallengeDetailPage.vue'),
    meta: { breadcrumb: 'Challenge detail' },
    props: true,
  },
  {
    path: '/scenario',
    name: 'scenario',
    component: () => import('@/pages/PlaceholderPage.vue'),
    props: { capability: 'scenario-builder' },
    meta: { breadcrumb: 'Scenario' },
  },
  {
    path: '/learning/materials',
    name: 'learning-materials',
    component: () => import('@/pages/PlaceholderPage.vue'),
    props: { capability: 'learning-materials' },
    meta: { breadcrumb: 'Learning · Materials' },
  },
  {
    path: '/learning/paths',
    name: 'learning-paths',
    component: () => import('@/pages/PlaceholderPage.vue'),
    props: { capability: 'learning-paths' },
    meta: { breadcrumb: 'Learning · Paths' },
  },
  {
    path: '/operate/queue',
    name: 'operate-queue',
    component: () => import('@/pages/OperateQueuePage.vue'),
    meta: { breadcrumb: 'Operate · Queue' },
  },
  {
    path: '/operate/workers',
    name: 'operate-workers',
    component: () => import('@/pages/OperateWorkersPage.vue'),
    meta: { breadcrumb: 'Operate · Workers' },
  },
  {
    path: '/operate/logs',
    name: 'operate-logs',
    component: () => import('@/pages/OperateLogsPage.vue'),
    meta: { breadcrumb: 'Operate · Logs' },
  },
  {
    path: '/quality/lint',
    name: 'quality-lint',
    component: () => import('@/pages/PlaceholderPage.vue'),
    props: { capability: 'quality-lint' },
    meta: { breadcrumb: 'Quality · Lint' },
  },
  {
    path: '/quality/diversity',
    name: 'quality-diversity',
    component: () => import('@/pages/PlaceholderPage.vue'),
    props: { capability: 'quality-diversity' },
    meta: { breadcrumb: 'Quality · Diversity' },
  },
  {
    path: '/settings/llm',
    name: 'settings-llm',
    component: () => import('@/pages/SettingsLLMPage.vue'),
    meta: { breadcrumb: 'Settings · LLM Provider' },
  },
  {
    path: '/settings/profile',
    name: 'settings-profile',
    component: () => import('@/pages/SettingsProfilePage.vue'),
    meta: { breadcrumb: 'Settings · Generation Profile' },
  },
  {
    path: '/:pathMatch(.*)*',
    redirect: '/',
  },
]

export const router = createRouter({
  history: createWebHistory(),
  routes,
})

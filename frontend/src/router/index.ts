import { createRouter, createWebHistory } from 'vue-router'
import MainLayout from '../layouts/MainLayout.vue'
import DashboardView from '../views/DashboardView.vue'
import LoginView from '../views/LoginView.vue'
import KnowledgeBaseListView from '../views/KnowledgeBaseListView.vue'
import KnowledgeBaseFormView from '../views/KnowledgeBaseFormView.vue'
import KnowledgeBaseDetailView from '../views/KnowledgeBaseDetailView.vue'
import DocumentUploadView from '../views/DocumentUploadView.vue'
import DocumentListView from '../views/DocumentListView.vue'
import DocumentDetailView from '../views/DocumentDetailView.vue'
import DocumentContentView from '../views/DocumentContentView.vue'
import ChunkListView from '../views/ChunkListView.vue'
import ChunkDetailView from '../views/ChunkDetailView.vue'
import RetrievalConfigView from '../views/RetrievalConfigView.vue'
import ModelConfigView from '../views/ModelConfigView.vue'
import ChunkerConfigView from '../views/ChunkerConfigView.vue'
import VectorStoreConfigView from '../views/VectorStoreConfigView.vue'
import SearchDebugView from '../views/SearchDebugView.vue'
import PlaceholderView from '../views/PlaceholderView.vue'
import ParseWorkbenchView from '../views/ParseWorkbenchView.vue'
import parseEvalRoutes from '../plugins/parse-eval/routes'

const page = (title: string, description: string) => ({
  component: PlaceholderView,
  props: { title, description }
})

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', component: LoginView },
    {
      path: '/',
      component: MainLayout,
      children: [
        { path: '', component: DashboardView, meta: { title: '控制台' } },
        { path: 'knowledge-bases', component: KnowledgeBaseListView, meta: { title: '知识库' } },
        { path: 'knowledge-bases/new', component: KnowledgeBaseFormView, meta: { title: '新建知识库' } },
        { path: 'knowledge-bases/:id/edit', component: KnowledgeBaseFormView, meta: { title: '编辑知识库' } },
        { path: 'knowledge-bases/:id', component: KnowledgeBaseDetailView, meta: { title: '知识库详情' } },
        { path: 'documents/upload', component: DocumentUploadView, meta: { title: '文档上传' } },
        { path: 'documents', component: DocumentListView, meta: { title: '文档管理' } },
        { path: 'documents/:docId/chunks', component: ChunkListView, meta: { title: '文档切片' } },
        { path: 'documents/:id/content', component: DocumentContentView, meta: { title: '文档查看' } },
        { path: 'documents/:id', component: DocumentDetailView, meta: { title: '文档详情' } },
        { path: 'chunks', component: ChunkListView, meta: { title: '切片浏览' } },
        { path: 'chunks/:id', component: ChunkDetailView, meta: { title: '切片详情' } },
        { path: 'parse-tasks/:taskId', component: ParseWorkbenchView, meta: { title: '解析任务' } },
        ...parseEvalRoutes.map(r => ({
          ...r,
          path: r.path.replace(/^\//, ''),
          meta: { title: r.path.includes('/runs/') ? '评测详情' : r.path.includes('/documents/') ? '文档评测' : '解析评测' }
        })),
        { path: 'faqs', ...page('问答库', 'FAQ 持久化将在后续阶段接入。'), meta: { title: '问答库' } },
        { path: 'settings/model', component: ModelConfigView, meta: { title: '模型配置' } },
        { path: 'settings/chunker', component: ChunkerConfigView, meta: { title: '切分配置' } },
        { path: 'settings/vector-store', component: VectorStoreConfigView, meta: { title: '向量库配置' } },
        { path: 'settings/retrieval', component: RetrievalConfigView, meta: { title: '检索配置' } },
        { path: 'debug/search', component: SearchDebugView, meta: { title: '检索调试' } },
        { path: 'debug/chat', ...page('本地问答调试', '本地 RAG 问答将在 Phase 5 实现。'), meta: { title: '问答调试' } },
        { path: 'mcp-reserved', ...page('MCP 预留', '完整 MCP Gateway 当前不实现，只保留目录、接口占位和文档说明。'), meta: { title: 'MCP 预留' } }
      ]
    }
  ]
})

router.beforeEach((to) => {
  if (to.path === '/login') return true
  if (!localStorage.getItem('know_rag_token')) return '/login'
  return true
})

export default router

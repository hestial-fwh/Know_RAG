<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import PageHeader from '../components/common/PageHeader.vue'
import {
  createMcpClient,
  deleteMcpClient,
  disableMcpClient,
  enableMcpClient,
  listMcpClients,
  resetMcpClientSecret,
  updateMcpClient,
  MCP_SCOPE_OPTIONS,
  type McpClient,
  type McpClientWithSecret
} from '../api/mcpClients'

const items = ref<McpClient[]>([])
const total = ref(0)
const loading = ref(false)
const error = ref('')

// ── create / edit form ───────────────────────────────────────────────────
const showCreate = ref(false)
const showEdit = ref(false)
const editingId = ref('')
const form = reactive({
  name: '',
  description: '',
  expiry: '30' as '30' | '90' | '365' | 'custom' | 'forever',
  customDays: 30,
  scopes: [] as string[]
})
const formError = ref('')
const saving = ref(false)

// ── secret reveal (one-time) ─────────────────────────────────────────────
const showSecret = ref(false)
const secretData = reactive<{ client_id: string; client_secret: string }>({
  client_id: '',
  client_secret: ''
})
const copied = ref('')

const EXPIRY_PRESETS = [
  { value: '30', label: '30 天' },
  { value: '90', label: '90 天' },
  { value: '365', label: '1 年' },
  { value: 'custom', label: '自定义' },
  { value: 'forever', label: '永久' }
] as const

function scopeLabel(value: string): string {
  return MCP_SCOPE_OPTIONS.find((o) => o.value === value)?.label ?? value
}

function expiryText(item: McpClient): string {
  if (!item.expires_at) return '永久'
  return item.expires_at.slice(0, 10)
}

function statusBadge(item: McpClient): { text: string; variant: string } {
  if (item.status === 'disabled') return { text: '已禁用', variant: 'error' }
  if (item.status === 'expired') return { text: '已过期', variant: 'warning' }
  return { text: '启用中', variant: 'success' }
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const result = await listMcpClients()
    items.value = result.items
    total.value = result.total
  } catch (err) {
    error.value = err instanceof Error ? err.message : '请求失败'
  } finally {
    loading.value = false
  }
}

function resetForm() {
  form.name = ''
  form.description = ''
  form.expiry = '30'
  form.customDays = 30
  form.scopes = [...MCP_SCOPE_OPTIONS.map((o) => o.value)]
  formError.value = ''
}

function openCreate() {
  resetForm()
  showCreate.value = true
}

function openEdit(item: McpClient) {
  editingId.value = item.id
  form.name = item.name
  form.description = item.description
  form.scopes = [...item.scopes]
  if (!item.expires_at) {
    form.expiry = 'forever'
  } else {
    form.expiry = '30'
  }
  form.customDays = 30
  formError.value = ''
  showEdit.value = true
}

function expiresInDays(): number | null {
  if (form.expiry === 'forever') return null
  if (form.expiry === 'custom') return Number(form.customDays) || 1
  return Number(form.expiry)
}

async function submitCreate() {
  formError.value = ''
  if (!form.name.trim()) {
    formError.value = '请填写应用名称'
    return
  }
  if (!form.scopes.length) {
    formError.value = '请至少选择一个 MCP Tool 权限'
    return
  }
  saving.value = true
  try {
    const created = await createMcpClient({
      name: form.name.trim(),
      description: form.description.trim(),
      scopes: form.scopes,
      expires_in_days: expiresInDays()
    })
    showCreate.value = false
    revealSecret(created)
    await load()
  } catch (err) {
    formError.value = err instanceof Error ? err.message : '创建失败'
  } finally {
    saving.value = false
  }
}

async function submitEdit() {
  formError.value = ''
  if (!form.name.trim()) {
    formError.value = '请填写应用名称'
    return
  }
  if (!form.scopes.length) {
    formError.value = '请至少选择一个 MCP Tool 权限'
    return
  }
  saving.value = true
  try {
    await updateMcpClient(editingId.value, {
      name: form.name.trim(),
      description: form.description.trim(),
      scopes: form.scopes,
      expires_in_days: expiresInDays()
    })
    showEdit.value = false
    await load()
  } catch (err) {
    formError.value = err instanceof Error ? err.message : '保存失败'
  } finally {
    saving.value = false
  }
}

function revealSecret(data: McpClientWithSecret) {
  secretData.client_id = data.client_id
  secretData.client_secret = data.client_secret
  copied.value = ''
  showSecret.value = true
}

function closeSecret() {
  showSecret.value = false
  // 清空，确保 Secret 不长期保留在前端状态
  secretData.client_id = ''
  secretData.client_secret = ''
}

async function copyText(text: string, key: string) {
  try {
    await navigator.clipboard.writeText(text)
    copied.value = key
    setTimeout(() => {
      if (copied.value === key) copied.value = ''
    }, 1500)
  } catch {
    copied.value = ''
  }
}

async function toggleEnabled(item: McpClient) {
  error.value = ''
  try {
    if (item.is_enabled) {
      await disableMcpClient(item.id)
    } else {
      await enableMcpClient(item.id)
    }
    await load()
  } catch (err) {
    error.value = err instanceof Error ? err.message : '操作失败'
  }
}

async function resetSecret(item: McpClient) {
  if (!confirm(`确定重置「${item.name}」的 Client Secret 吗？\n旧 Secret 将立即失效，且新 Secret 只展示一次。`)) {
    return
  }
  error.value = ''
  try {
    const data = await resetMcpClientSecret(item.id)
    revealSecret(data)
    await load()
  } catch (err) {
    error.value = err instanceof Error ? err.message : '重置失败'
  }
}

async function remove(item: McpClient) {
  if (!confirm(`确认删除「${item.name}」吗？\n删除后该 Client 的凭证立即失效，无法恢复。`)) {
    return
  }
  error.value = ''
  try {
    await deleteMcpClient(item.id)
    await load()
  } catch (err) {
    error.value = err instanceof Error ? err.message : '删除失败'
  }
}

onMounted(load)
</script>

<template>
  <section class="page wide-page">
    <PageHeader title="MCP 接入管理" description="为外部 MCP Client 签发 Client ID / Client Secret，通过 HMAC-SHA256 签名接入">
      <template #actions>
        <button @click="openCreate">+ 创建接入</button>
      </template>
    </PageHeader>

    <p v-if="error" class="text-error">{{ error }}</p>

    <div v-if="loading" class="card">
      <p class="text-muted">加载中...</p>
    </div>

    <div v-else-if="!items.length" class="empty-state">
      <div class="empty-icon">🔐</div>
      <div>暂无 MCP 接入</div>
      <small>点击「创建接入」签发第一个 Client Credential</small>
    </div>

    <div v-else class="card" style="padding: 0; overflow: hidden;">
      <table class="data-table">
        <thead>
          <tr>
            <th>应用名称</th>
            <th>Client ID</th>
            <th>权限</th>
            <th>状态</th>
            <th>有效期</th>
            <th>最后调用时间</th>
            <th>创建时间</th>
            <th style="text-align: right;">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in items" :key="item.id">
            <td>
              <strong>{{ item.name }}</strong>
              <small v-if="item.description" class="text-muted" style="display: block;">{{ item.description }}</small>
            </td>
            <td>
              <code style="font-size: 12px;">{{ item.client_id }}</code>
              <button class="ghost sm" style="margin-left: 6px;" @click="copyText(item.client_id, item.client_id)">复制</button>
            </td>
            <td>
              <span v-for="scope in item.scopes" :key="scope" class="badge info" style="margin-right: 4px;">{{ scopeLabel(scope) }}</span>
            </td>
            <td>
              <span class="badge" :class="statusBadge(item).variant">{{ statusBadge(item).text }}</span>
            </td>
            <td>{{ expiryText(item) }}</td>
            <td>{{ item.last_used_at ? item.last_used_at.slice(0, 19).replace('T', ' ') : '—' }}</td>
            <td>{{ item.created_at ? item.created_at.slice(0, 10) : '—' }}</td>
            <td>
              <div class="actions">
                <button class="sm" @click="openEdit(item)">编辑</button>
                <button class="sm secondary" @click="toggleEnabled(item)">
                  {{ item.is_enabled ? '禁用' : '启用' }}
                </button>
                <button class="sm secondary" @click="resetSecret(item)">重置 Secret</button>
                <button class="sm danger" @click="remove(item)">删除</button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- ── 创建 / 编辑弹窗 ─────────────────────────────────────────────── -->
    <div v-if="showCreate || showEdit" class="modal-backdrop" @click.self="showCreate ? (showCreate = false) : (showEdit = false)">
      <div class="modal" style="width: min(560px, 96vw);">
        <h3>{{ showCreate ? '创建接入' : '编辑接入' }}</h3>
        <form class="form" @submit.prevent="showCreate ? submitCreate() : submitEdit()">
          <label>
            应用名称 *
            <input v-model="form.name" placeholder="例如：客服助手 MCP" />
          </label>
          <label>
            描述
            <textarea v-model="form.description" rows="2" placeholder="可选" />
          </label>
          <label>
            有效期
            <div class="actions" style="margin-top: 4px;">
              <label v-for="p in EXPIRY_PRESETS" :key="p.value" class="check">
                <input type="radio" :value="p.value" v-model="form.expiry" />
                {{ p.label }}
              </label>
            </div>
          </label>
          <label v-if="form.expiry === 'custom'">
            自定义天数
            <input v-model.number="form.customDays" type="number" min="1" />
          </label>
          <div>
            <div class="text-muted" style="margin-bottom: 6px;">MCP Tool 权限 *</div>
            <label v-for="o in MCP_SCOPE_OPTIONS" :key="o.value" class="check" style="margin-bottom: 4px;">
              <input type="checkbox" :value="o.value" v-model="form.scopes" />
              <span><code>{{ o.label }}</code> — {{ o.hint }}</span>
            </label>
          </div>
          <p v-if="formError" class="text-error">{{ formError }}</p>
          <div class="actions">
            <button type="submit" :disabled="saving">{{ saving ? '保存中...' : '保存' }}</button>
            <button type="button" class="secondary" @click="showCreate ? (showCreate = false) : (showEdit = false)">取消</button>
          </div>
        </form>
      </div>
    </div>

    <!-- ── Secret 一次性展示弹窗 ───────────────────────────────────────── -->
    <div v-if="showSecret" class="modal-backdrop" @click.self="closeSecret">
      <div class="modal" style="width: min(600px, 96vw);">
        <h3>Client Credential 已生成</h3>
        <p class="text-error" style="margin: 8px 0 16px;">
          ⚠️ Client Secret 仅展示一次，请立即保存。关闭后无法再次查看，遗忘需重新「重置 Secret」。
        </p>
        <div class="form">
          <label>
            Client ID
            <div class="actions" style="margin-top: 4px;">
              <code style="flex: 1; word-break: break-all;">{{ secretData.client_id }}</code>
              <button type="button" class="sm" @click="copyText(secretData.client_id, 'cid')">{{ copied === 'cid' ? '已复制' : '复制' }}</button>
            </div>
          </label>
          <label>
            Client Secret
            <div class="actions" style="margin-top: 4px;">
              <code style="flex: 1; word-break: break-all;">{{ secretData.client_secret }}</code>
              <button type="button" class="sm" @click="copyText(secretData.client_secret, 'secret')">{{ copied === 'secret' ? '已复制' : '复制' }}</button>
            </div>
          </label>
        </div>
        <div class="actions" style="margin-top: 20px;">
          <button class="secondary" @click="closeSecret">我已保存，关闭</button>
        </div>
      </div>
    </div>
  </section>
</template>

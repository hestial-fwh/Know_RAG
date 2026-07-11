<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { getVectorStoreConfig, getVectorStoreProviders, testVectorStore, updateVectorStoreConfig, type VectorStoreConfig } from '../api/config'

const provider = ref('postgres')
const isActive = ref(true)
const configText = ref('{}')
const providers = ref<Array<{ value: string; label: string; default_config: Record<string, unknown> }>>([])
const message = ref('')
const error = ref('')
const testing = ref(false)

const currentPreset = computed(() => providers.value.find(item => item.value === provider.value))

async function load() {
  const [data, presets] = await Promise.all([getVectorStoreConfig(), getVectorStoreProviders().catch(() => ({ providers: [] }))])
  providers.value = presets.providers || data.providers || []
  provider.value = data.provider
  isActive.value = data.is_active
  configText.value = JSON.stringify(data.config || currentPreset.value?.default_config || {}, null, 2)
}

function applyDefault() {
  configText.value = JSON.stringify(currentPreset.value?.default_config || {}, null, 2)
}

async function save() {
  message.value = ''
  error.value = ''
  try {
    const payload: VectorStoreConfig = {
      provider: provider.value,
      is_active: isActive.value,
      config: JSON.parse(configText.value || '{}')
    }
    const saved = await updateVectorStoreConfig(payload)
    configText.value = JSON.stringify(saved.config || {}, null, 2)
    message.value = '保存成功。已生成的旧向量需要重新向量化，才会同步到新的向量库。'
  } catch (err) {
    error.value = err instanceof Error ? err.message : '保存失败'
  }
}

async function runTest() {
  message.value = ''
  error.value = ''
  testing.value = true
  try {
    const payload: VectorStoreConfig = { provider: provider.value, is_active: isActive.value, config: JSON.parse(configText.value || '{}') }
    const result = await testVectorStore(payload)
    message.value = `${result.provider} 连接测试成功`
  } catch (err) {
    error.value = err instanceof Error ? err.message : '连接测试失败'
  } finally {
    testing.value = false
  }
}

onMounted(load)
</script>

<template>
  <section class="page narrow">
    <h1>向量库配置</h1>
    <form class="card form" @submit.prevent="save">
      <p class="text-muted">PostgreSQL/pgvector 仍作为本地元数据与向量备份；选择 Qdrant/Milvus 后，重新向量化会同步外部向量库，检索时优先走当前激活供应商。</p>
      <label>供应商
        <select v-model="provider" @change="applyDefault">
          <option v-for="item in providers" :key="item.value" :value="item.value">{{ item.label }}</option>
        </select>
      </label>
      <label class="check"><input v-model="isActive" type="checkbox" /> 启用</label>
      <label>配置 JSON <textarea v-model="configText" rows="12" /></label>
      <p class="text-muted" v-if="provider === 'qdrant'">Qdrant 可用配置：url、api_key、collection、distance、fallback_to_postgres、strict_external_index。</p>
      <p class="text-muted" v-if="provider === 'milvus'">Milvus 可用配置：uri、token、collection、metric_type、fallback_to_postgres、strict_external_index。</p>
      <p v-if="message" class="text-success">{{ message }}</p>
      <p v-if="error" class="text-error">{{ error }}</p>
      <div class="actions">
        <button>保存</button>
        <button type="button" class="secondary" :disabled="testing" @click="runTest">{{ testing ? '测试中...' : '测试连接' }}</button>
        <button type="button" class="secondary" @click="applyDefault">填入默认配置</button>
      </div>
    </form>
  </section>
</template>

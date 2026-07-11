<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { login, getCaptcha } from '../api/auth'

const router = useRouter()
const username = ref('admin')
const password = ref('admin123')
const captchaId = ref('')
const captchaCode = ref('')
const captchaImage = ref('')
const error = ref('')
const loading = ref(false)

async function refreshCaptcha() {
  try {
    const data = await getCaptcha()
    captchaId.value = data.captcha_id
    captchaImage.value = data.image
  } catch {
    error.value = '无法获取验证码，请刷新重试'
  }
}

onMounted(() => {
  refreshCaptcha()
})

async function submit() {
  loading.value = true
  error.value = ''
  try {
    const result = await login(username.value, password.value, captchaId.value, captchaCode.value)
    localStorage.setItem('know_rag_token', result.access_token)
    localStorage.setItem('know_rag_user', JSON.stringify(result.user))
    router.push('/knowledge-bases')
  } catch (err) {
    error.value = err instanceof Error ? err.message : '登录失败'
    refreshCaptcha()
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="login-page">
    <div class="login-card">
      <div class="login-brand">
        <span class="login-logo">◆</span>
        <h1 class="login-title">know_rag</h1>
        <p class="login-subtitle">智能知识库管理系统</p>
      </div>

      <form class="login-form" @submit.prevent="submit">
        <label class="login-field">
          <span>用户名</span>
          <input v-model="username" autocomplete="username" placeholder="请输入用户名" />
        </label>
        <label class="login-field">
          <span>密码</span>
          <input v-model="password" type="password" autocomplete="current-password" placeholder="请输入密码" />
        </label>
        <label class="login-field">
          <span>验证码</span>
          <div class="captcha-row">
            <input v-model="captchaCode" class="captcha-input" placeholder="请输入验证码" maxlength="4" autocomplete="off" />
            <img :src="captchaImage" alt="验证码" class="captcha-img" @click="refreshCaptcha" title="点击刷新验证码" />
          </div>
        </label>
        <p v-if="error" class="login-error">{{ error }}</p>
        <button class="login-btn" :disabled="loading">
          {{ loading ? '登录中...' : '登 录' }}
        </button>
      </form>

      <p class="login-hint">默认账号来自 ADMIN_USERNAME / ADMIN_PASSWORD</p>
    </div>
  </div>
</template>

<style scoped>
.login-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  background: linear-gradient(135deg, #0f2027 0%, #203a43 50%, #1f7a5c 100%);
}

.login-card {
  width: 100%;
  max-width: 420px;
  background: #ffffff;
  border-radius: var(--r-xl);
  padding: 40px 36px 32px;
  box-shadow: 0 24px 64px rgba(0, 0, 0, 0.2);
}

.login-brand {
  text-align: center;
  margin-bottom: 32px;
}

.login-logo {
  display: inline-block;
  font-size: 36px;
  color: var(--c-primary);
  margin-bottom: 8px;
}

.login-title {
  font-size: 28px;
  font-weight: 800;
  color: var(--t-primary);
  margin: 0 0 4px;
  letter-spacing: 0.5px;
}

.login-subtitle {
  font-size: 14px;
  color: var(--t-muted);
  margin: 0;
}

.login-form {
  display: grid;
  gap: 18px;
}

.login-field {
  display: grid;
  gap: 6px;
}

.login-field span {
  font-weight: 600;
  font-size: 14px;
  color: var(--t-secondary);
}

.login-field input {
  padding: 12px 16px;
  border: 1px solid var(--c-border);
  border-radius: var(--r-sm);
  font-size: 15px;
  background: var(--c-surface-alt);
  transition: border-color 0.15s, box-shadow 0.15s;
}

.login-field input:focus {
  outline: none;
  border-color: var(--c-primary);
  box-shadow: 0 0 0 3px rgba(31, 122, 92, 0.12);
  background: #fff;
}

.captcha-row {
  display: flex;
  gap: 10px;
  align-items: center;
}

.captcha-input {
  flex: 1;
  min-width: 0;
}

.captcha-img {
  height: 44px;
  border-radius: var(--r-sm);
  cursor: pointer;
  border: 1px solid var(--c-border);
  background: #fafafa;
  flex-shrink: 0;
}

.captcha-img:hover {
  border-color: var(--c-primary);
  box-shadow: 0 0 0 2px rgba(31, 122, 92, 0.1);
}

.login-error {
  color: var(--s-error);
  font-size: 14px;
  font-weight: 600;
  margin: 0;
  padding: 10px 14px;
  background: var(--s-error-bg);
  border-radius: var(--r-sm);
  border: 1px solid var(--s-error-border);
}

.login-btn {
  width: 100%;
  padding: 13px;
  font-size: 16px;
  font-weight: 700;
  border: none;
  border-radius: var(--r-sm);
  background: var(--c-primary);
  color: #fff;
  cursor: pointer;
  transition: background 0.15s, transform 0.1s;
  box-shadow: 0 2px 8px rgba(31, 122, 92, 0.24);
  margin-top: 4px;
}

.login-btn:hover:not(:disabled) {
  background: var(--c-primary-hover);
  transform: translateY(-1px);
}

.login-btn:active:not(:disabled) {
  transform: translateY(0);
}

.login-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.login-hint {
  text-align: center;
  margin: 24px 0 0;
  font-size: 13px;
  color: var(--t-muted);
}
</style>

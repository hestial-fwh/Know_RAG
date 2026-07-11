<script setup lang="ts">
defineProps<{
  icon?: string
  message?: string
  description?: string
}>()
</script>

<template>
  <div class="empty-state">
    <div v-if="icon" class="empty-icon">{{ icon }}</div>
    <div>{{ message || '暂无数据' }}</div>
    <small v-if="description">{{ description }}</small>
    <slot />
  </div>
</template>

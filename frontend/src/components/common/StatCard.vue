<script setup lang="ts">
defineProps<{
  label: string
  value: string | number
  description?: string
  icon?: string
}>()
</script>

<template>
  <div class="stat-card">
    <span class="stat-label">
      <template v-if="icon">{{ icon }} </template>
      {{ label }}
    </span>
    <span class="stat-value">{{ value }}</span>
    <span v-if="description" class="stat-desc">{{ description }}</span>
  </div>
</template>

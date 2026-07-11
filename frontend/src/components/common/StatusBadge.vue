<script setup lang="ts">
defineProps<{
  text: string
  variant?: 'success' | 'warning' | 'error' | 'info' | 'muted' | 'primary' | ''
}>()
</script>

<template>
  <span class="badge" :class="variant || ''">{{ text }}</span>
</template>

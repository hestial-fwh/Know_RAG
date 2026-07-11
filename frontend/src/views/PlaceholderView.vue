<script setup lang="ts">
defineProps<{
  title: string
  description: string
}>()
</script>

<template>
  <section class="page">
    <h1>{{ title }}</h1>
    <div class="card">
      <p>{{ description }}</p>
      <p class="text-muted" style="margin-top: 8px;">状态：当前仍为占位页面。</p>
    </div>
  </section>
</template>

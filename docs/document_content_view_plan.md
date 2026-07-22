# DocumentContentView 规划文档

> 状态：Planning
> 创建日期：2026-07-21
> 目标分支：master

## 背景

当前 `DocumentDetailView.vue` 只展示文档元数据和解析统计，没有原始文档的查看功能。用户需要一个类似论文下载网站的页面——可以**在线查看原始文档**（PDF、图片等），也可以**下载**。

## 目标

新建 `DocumentContentView.vue`，根据文档原始文件的类型，在浏览器中内联展示原始文件内容，并提供下载入口。

## 技术设计

### 1. 数据来源

| 数据 | 来源 | 说明 |
|------|------|------|
| 文档元数据 | `GET /api/v1/documents/{id}` | 获取 `storage_path`、`file_type`、`file_name`、`title` |
| 原始文件 | `GET /files/{encoded_path}` | 已有 MinIO 文件服务端点，自动设置 Content-Type |

**不需要**切片数据、不需要修改后端、不需要 Markdown 渲染器。

### 2. 渲染策略（按 `file_type`）

| 文件类型 | 渲染方式 | 说明 |
|----------|---------|------|
| PDF (`pdf`) | `<iframe>` 内联 | 现代浏览器内置 PDF 查看器，支持翻页、缩放、搜索 |
| 图片 (`png`, `jpg`, `jpeg`, `gif`, `webp`, `svg`, `bmp`) | `<img>` | 自适应容器宽度，点击可全屏查看 |
| 纯文本 (`txt`, `md`, `csv`, `json`, `xml`, `yaml`, `log`) | `<pre>` | 从 `/files/` 获取文本内容，保持换行和缩进 |
| 其他 (`docx`, `xlsx`, `pptx`, 等) | 下载提示 | 浏览器无法内联渲染，显示说明并引导下载 |

### 3. 路由

```
path: 'documents/:id/content'  →  DocumentContentView.vue
```

`/documents/:id/content` 比 `/documents/:id` 更具体，Vue Router 4 评分系统会优先匹配。

### 4. 组件结构

```
<script setup>
- route.params.id → documentId
- onMounted → getDocument(id) → doc
- computed fileUrl → `/files/${segmentEncode(doc.storage_path)}`
- computed renderMode → 'pdf' | 'image' | 'text' | 'unsupported'
- 纯文本模式：fetch fileUrl → textContent
- loading / error 状态
</script>

<template>
  页面标题栏：
    - 文档标题 + 文件名
    - "下载"按钮（download 属性）
    - "元数据详情"链接 → DocumentDetailView
    - "返回"按钮

  内容区（全宽）：
    - pdf 模式：<iframe :src="fileUrl" class="doc-iframe">
    - image 模式：<img :src="fileUrl" class="doc-image" @click="fullscreen">
    - text 模式：<pre class="doc-text">{{ textContent }}</pre>
    - unsupported 模式：提示消息 + 下载按钮
</template>
```

### 5. 下载逻辑

```
<a :href="fileUrl" :download="doc.file_name">下载文件</a>
```

与 DocumentDetailView 中已有的下载链接逻辑一致（segment-level `encodeURIComponent`）。

### 6. 导航链接更新

| 文件 | 现有链接 | 更新为 |
|------|---------|--------|
| `SearchDebugView.vue` | `/documents/${id}` (×2) | `/documents/${id}/content` |
| `ChunkDetailView.vue` | `/documents/${id}` | `/documents/${id}/content` |
| `ChunkListView.vue` | `/documents/${documentId}` | `/documents/${documentId}/content` |
| `DocumentDetailView.vue` | — | 新增"查看原文档"按钮 → `/documents/${doc.id}/content` |

### 7. 边界情况

| 场景 | 处理 |
|------|------|
| 文档未上传文件（storage_path 为空） | 提示"未找到原始文件" |
| 文件加载失败（404 / 网络错误） | 显示错误提示 + 重试按钮 |
| PDF 文件 → 移动端 | iframe 的 PDF 查看器在移动端可能体验不佳，降级为下载 |
| 超大图片 | `max-width: 100%` CSS 限制，点击全屏 |
| SVG 内嵌脚本 | 用 `<img>` 加载（非内联 SVG）自然隔离脚本 |

## 实施步骤

1. **新建 `DocumentContentView.vue`** —— 实现文件类型检测与内联渲染
2. **注册路由** —— `router/index.ts` 新增路由
3. **更新导航链接** —— 4 个文件的"查看文档"链接指向内容页
4. **DocumentDetailView 增加入口** —— 新增"查看原文档"按钮
5. **类型检查** —— `vue-tsc --noEmit`

## 不涉及

- 不修改后端 API（复用 `getDocument` + 已有 `/files/` 端点）
- 不引入第三方 PDF 库（依赖浏览器内置能力）
- 不处理文档内编辑/批注

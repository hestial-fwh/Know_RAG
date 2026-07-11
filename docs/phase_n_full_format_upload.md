# Phase N：全格式上传与 DeepDoc XGBoost 兼容修复

## 完成内容

1. 上传入口从原来的 txt/md/pdf/docx 扩展为常见全格式：
   - 文档：pdf、docx、doc、rtf、txt、md、html、json、xml、epub
   - 表格：csv、tsv、xlsx、xls
   - 演示文稿：pptx、ppt
   - 图片：jpg、jpeg、png、gif、bmp、webp、tif、tiff、svg、heic、heif
   - 其他未知格式：安全保存为附件并生成占位 text chunk。

2. 新增 standalone image parser：上传单张图片会保存到本地 storage，并生成 image chunk。

3. 新增轻量解析器：CSV/Excel/HTML/JSON/XML/PPTX/EPUB/RTF/ZIP/未知附件。

4. OCR 适配：上传图片、PPT/EPUB 内图片可作为 OCR candidate，由 DeepDoc 内置 OCR 或外部 OCR/VLM 继续增强。

5. 修复 DeepDoc XGBoost 拼接模型 32/33 特征不一致导致的报错：运行时根据 booster.num_features() 自动裁剪或补齐特征，并压缩错误信息，避免页面被原生 XGBoost 堆栈刷屏。

## 注意

- `.doc`、`.ppt` 这类旧二进制 Office 格式如果容器中没有 antiword/catdoc/LibreOffice，会作为附件 fallback 保存。
- 完整 Office 高保真转换可以后续接入 LibreOffice 或 MarkItDown 服务。

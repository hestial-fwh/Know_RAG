# Parse Quality Optimization Notes (Fix6)

This document describes the targeted improvements made on top of the Fix5 baseline.

## Optimization Goals

This round of changes focuses on:

1. Improving table vs. prose classification accuracy for Chinese-English mixed PDFs
2. Fixing parent_text deduplication in diagnostics to avoid double-counting
3. Reducing the weight of space_gluing as an auxiliary issue
4. Adding OCR layer display in the frontend for debugging
5. Adding comprehensive test coverage

## File Changes

### 1. `backend/app/services/document_parser/ragflow_format_adapter.py`

**Problem**: `_looks_like_long_body_text()` used hardcoded paragraph markers that failed for arbitrary CJK-English mixed prose.

**Fix**: Replaced with a generalized prose score accumulator:
- CJK punctuation density (>2% of chars)
- CJK-heavy line ratio (>50% of lines with >30% CJK chars)
- Average line length (>60 chars per line)
- Sentence marks (>=5)
- Long lines (>=2 lines >120 chars)
- Tab/pipe delimiter penalty (suggests real table structure)
- Legacy markers (backward compat)

**Table classification guard**: Added early guard in `_table_structure_assessment()`: if max_cell_len > 100 AND cjk_cell_ratio > 0.3 AND no header cue, classify as `image/pseudo_table` early. Key-value tables (Field/Value/Note) are exempt via `has_header_cue`.

### 2. `backend/app/plugins/parse_eval/diagnostics.py`

**Problem 1**: `diagnose_no_gt()` iterated all chunks including `parent_text`, causing double-counting of issues like space_gluing.

**Fix**: Added `if chunk_type == "parent_text": continue` at the top of the chunk loop. Added `parent_text_skipped` counter to summary.

**Problem 2**: Page count was guessed from filename (e.g. `25p`).

**Fix**: Added logging warning when filename hint is used as fallback. Added `_page_count_source_label()` helper and `page_count_source` field to scanned contract summary for transparency.

**Problem 3**: `whitespace_quality` weight (16) was too prominent; space_gluing should be auxiliary.

**Fix**: Reduced whitespace_quality from 16 to 6. Redistributed: line_rate 18→22 (+4), invoice_rate 27→33 (+6).

**Problem 4**: `table_text_weak()` only used specific prose markers.

**Fix**: Added generalized CJK punctuation density check (>2% of chars → weak table).

### 3. `frontend/src/plugins/parse-eval/ParseEvalRunView.vue`

**Problem**: Issue detail panel could not show OCR text layers for debugging.

**Fix**: Added helper functions `chunkOcrMeta()`, `ocrLayerValue()`, `hasOcrLayers()` and a template section between "content preview" and "evidence" showing:
- chunk.content (final indexed text)
- ocr_raw_text (raw OCR text)
- ocr_cleaned_text (lightly cleaned OCR text)
- ocr_normalized_text (field-level normalized text)
- vlm_description (VLM image description)

### 4. `frontend/src/plugins/parse-eval/eval.css`

Added `.pe-ocr-layers`, `.pe-ocr-layer`, `.pe-ocr-label` styles for the new OCR layer display section.

### 5. `backend/tests/test_parse_quality_fix.py`

Added 6 new tests:
- `test_ocr_layering_content_not_overwritten` - raw preserved, normalized recovers, content != normalized
- `test_page_inference_various_patterns` - 7 page inference patterns
- `test_cjk_english_mixed_prose_not_table` - CJK prose rows → image/pseudo_table
- `test_real_table_still_classified_correctly` - Field/Value/Note → table
- `test_strict_excludes_normalized_text` - strict has content+raw, recoverable has normalized
- `test_parent_text_skipped_in_quality_checks` - parent_text skipped in diagnose_no_gt

## Files NOT Modified

- `backend/app/services/chunk_service.py` - OCR layering, content handling, page inference all correct
- `backend/app/services/vision_service.py` - VLM/OCR separation already correct
- `backend/app/db/models.py` - No schema changes needed
- `docker-compose.yml` - No infrastructure changes

## Verification

After implementation, run tests:

```bat
cd d:\know_rag

docker compose build backend
docker compose up -d --force-recreate backend
docker compose exec backend python -m pytest tests/test_parse_quality_fix.py -v
```

Expected: all existing + new tests pass.

## Risk Notes

- Table classification changes may affect documents with CJK-heavy rows. If real tables are misclassified, check that they have `Field/Value/Note` headers or short cell lengths (<100 chars).
- Score weight redistribution changes the scanned contract template scoring. This should improve scores when line/invoice/callout hit rates are good but space_gluing is present.
- Parent text deduplication should not affect any other chunk types.

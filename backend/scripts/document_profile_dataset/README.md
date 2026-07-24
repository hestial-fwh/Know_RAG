# DCP-0 document profile dataset

This package generates the deterministic engineering corpus for the document-profile,
page-visual and adaptive-chunking plan.

It contains eight document types and eleven page scenarios covering visual collections,
calligraphy OCR, scanned contracts, posters, academic PDFs, tables, presentations, code and a
mixed three-page document. All PDFs and PNG assets are generated locally without downloads or
model calls.

The replay closes six layers:

```text
source PDF facts
→ MinerU-shaped raw payload
→ ParsedBlock
→ page visual enrichment
→ Canonical artifact
→ production Chunk drafts
```

Gold annotations record expected page kind, page-visual preservation, semantic role, index policy
and recommended chunk policy. The report retains the raw DCP-0 parser baseline and separately
checks the DCP-1 enrichment result. Adaptive chunk policy IDs and OCR index policy remain future
stages.

Run from `backend/`:

```bash
python -m scripts.generate_document_profile_dataset
```

Generated output defaults to `.generated/document_profile_dcp0/` and is not committed. The tracked
contract is `tests/fixtures/document_profile_dcp0/generated_corpus_spec.json`.

Synthetic metrics are engineering coverage only and must not be presented as real MinerU
precision, recall or production accuracy.

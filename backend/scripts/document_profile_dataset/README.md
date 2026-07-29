# DCP-0 document profile dataset

This package generates the deterministic DCP-0/DCP-0.1 engineering corpus for the
document-profile, page-visual and adaptive-chunking plan.

It retains eight baseline document types and eleven production-regression pages covering visual collections,
calligraphy OCR, scanned contracts, posters, academic PDFs, tables, presentations, code and a
mixed three-page document. DCP-0.1 adds thirty adversarial pages: threshold pairs, OCR-quality
contrasts, negative document types, VLM failures, cross-page evidence and `VI-01` through `VI-10`
multi-work layouts. All PDFs and PNG assets are generated locally without downloads or model calls.

The replay closes six layers:

```text
source PDF facts
→ MinerU-shaped raw payload
→ ParsedBlock
→ page visual enrichment
→ Canonical artifact
→ production Chunk drafts
```

Gold annotations record expected page kind, page-visual preservation, semantic role, index policy,
OCR quality, Visual Item and text relation. The report keeps the original eleven pages as the
production regression gate and records adversarial gold/actual differences without claiming
accuracy. It writes both `reports/coverage.json` and `reports/coverage.html`.

Run from `backend/`:

```bash
python -m scripts.generate_document_profile_dataset
```

Generated output defaults to `.generated/document_profile_dcp0/` and is not committed. The tracked
contract is `tests/fixtures/document_profile_dcp0/generated_corpus_spec.json`.

Synthetic metrics are engineering coverage only and must not be presented as real MinerU
precision, recall or production accuracy.

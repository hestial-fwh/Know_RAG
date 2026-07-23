# Document relation Phase 0 fixtures

`dataset.json` freezes the 18 top-level acceptance cases and their five trace layers:

1. raw parser payload;
2. normalized `ParsedBlock` view;
3. Canonical artifact view;
4. Chunk relation metadata view;
5. retrieval output view.

The checked-in cases are synthetic rule contracts. They must never be counted as real-page accuracy
samples. The deterministic rendered corpus is generated separately by
`scripts.generate_document_relation_dataset`. Real MinerU samples belong in `real_documents` and need an explicit document type
(`album`, `calligraphy`, `paper`, or `product_manual`), at least five manually reviewed pages per
type, a reviewable raw-response path, and page/block annotations. Do not commit private documents,
copyrighted source files, credentials, base64 media, or MinIO URLs with secrets.

Run the offline contract report from `backend/`:

```bash
python scripts/document_relation_phase0.py
python scripts/document_relation_phase0.py --strict-real
```

The first command validates the fixed contract. The strict command exits with status 2 until the
optional real accuracy-validation gate passes. The Phase 0 engineering gate is owned by the rendered
corpus report and does not require `--strict-real`.

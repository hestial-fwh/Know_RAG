# DCP-0 tracked specification

`generated_corpus_spec.json` freezes the deterministic document types, scenarios, seed and
page count. It contains the original 11-page production regression baseline plus 30
adversarial/perturbation pages. Generated PDFs, PNGs, raw replay payloads, snapshots and JSON/HTML
coverage reports are written under `backend/.generated/document_profile_dcp0/` and are not committed.

The corpus is synthetic and verifies engineering coverage only. It must not be used to claim real
MinerU classification, visual-preservation or OCR-quarantine accuracy.

# Phase 0 document-relation dataset generator

This package creates a deterministic, fully local engineering corpus for the document-relation
pipeline. It does not download fonts, images, documents, or model output.

## Default corpus

- four document styles: album, calligraphy, paper, product manual;
- 40 pages per style, 160 pages total;
- 19 scenarios per style, repeated with deterministic visual variants;
- generated PNG assets and valid multipage PDFs;
- layout ground truth, MinerU-shaped raw responses, gold relations;
- replay through the current MinerU adapter, Canonical builder, chunk planner, P4 materializer, and
  P5 expansion helper;
- coverage and traceability report. The report rejects empty layers, broken gold-to-layout IDs,
  raw/parsed count drift, and incomplete retrieval replay coverage.

The `cross_page` template places its target on the following physical PDF page. The
`overlap_child` template uses sentence-level overlap so the same reference occurrence is covered
by two real child ranges. The `figure_range_reference` template replays Chinese `图1‑图2` as two
ordered explicit occurrences. Retrieval replay evaluates every materialized direct binding in each
document, not only the first one, and `contaminated_scope` injects real foreign-document rows that
must remain excluded.

The engineering gate also compares every `explicit_reference` occurrence currently implemented by
the production linker against gold status and bound target. Relation types whose resolvers do not
exist yet remain reported as limitations and are not presented as conformant.

From `backend/`, run:

```bash
python -m scripts.generate_document_relation_dataset
```

The default output is `.generated/document_relation_phase0/`, which is intentionally ignored by
Git. The tracked generator specification is
`tests/fixtures/document_relation_phase0/generated_corpus_spec.json`.

In Docker on Windows, bind the output directory explicitly:

```powershell
docker compose run --rm --no-deps `
  -v D:\know_rag\backend\.generated:/app/.generated `
  backend python -m scripts.generate_document_relation_dataset
```

## Live MinerU capture

Synthetic raw replay is the default and makes no network request. To send the generated PDFs to a
specific MinerU deployment and retain its exact response, explicitly provide a URL:

```bash
python -m scripts.generate_document_relation_dataset --mineru-url http://mineru-cpu:8000
```

Live responses are stored under `raw_live/` and then replayed through the same five-layer pipeline.
Never pass an external MinerU endpoint unless sending generated documents to it is intended.

## Output tree

```text
corpus_manifest.json
assets/<document-type>/*.png
documents/synthetic_<document-type>.pdf
layouts/synthetic_<document-type>.json
raw/synthetic_<document-type>.json
raw_live/*.json                 # only with --mineru-url
gold/synthetic_<document-type>.json
snapshots/synthetic-<document-type>.json
reports/coverage.json
```

The engineering report can pass without real documents. `accuracy_claim_allowed` always remains
false for a synthetic-only corpus; a separately held real validation set is still required before
publishing precision/recall or production accuracy claims.

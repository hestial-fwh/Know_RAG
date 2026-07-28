"""
Comprehensive debug artifact analysis script.
Reads ALL large JSON files for all 3 documents and produces detailed reports.
"""
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

ARTIFACTS_DIR = Path(r"d:\know_rag\cache\pipeline_debug_artifacts")

# Document definitions
DOCS = {
    "02292ffd": {
        "doc_id": "02292ffd-6cd4-46ce-91f7-d65590aa40d6",
        "run_id": "acae86e6-37a0-449f-987b-ad7073624cd6",
        "label": "Calligraphy Catalog",
    },
    "9420dda9": {
        "doc_id": "9420dda9-fdc8-4d7a-bf75-b5d1ed1b532d",
        "run_id": "57be1df1-bc26-4867-b065-e7eae01ef204",
        "label": "Academic Review",
    },
    "1fbc56a2": {
        "doc_id": "1fbc56a2-54b7-44ff-bb69-c531125a7299",
        "run_id": "6f8ff0ad-9389-4194-9762-857f8e8c8717",
        "label": "Patent",
    },
}


def get_path(doc_key, filename):
    """Get full path to a debug artifact file."""
    doc = DOCS[doc_key]
    return ARTIFACTS_DIR / doc["doc_id"] / doc["run_id"] / filename


def load_json(path):
    """Load a JSON file with progress reporting."""
    size_mb = os.path.getsize(path) / (1024 * 1024)
    print(f"  Loading {path.name} ({size_mb:.1f} MB)...", end=" ", flush=True)
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    print(f"done. Type: {type(data).__name__}", end="")
    if isinstance(data, list):
        print(f", len={len(data)}")
    elif isinstance(data, dict):
        print(f", keys={list(data.keys())[:10]}")
    else:
        print()
    return data


def analyze_field_sizes(obj, prefix="", top_n=30):
    """Recursively analyze JSON and report largest fields by total byte size."""
    field_sizes = defaultdict(int)
    field_counts = defaultdict(int)
    field_examples = {}

    def _walk(o, path):
        if isinstance(o, dict):
            for k, v in o.items():
                p = f"{path}.{k}" if path else k
                if isinstance(v, str):
                    field_sizes[p] += len(v)
                    field_counts[p] += 1
                    if p not in field_examples:
                        field_examples[p] = v[:200]
                elif isinstance(v, (int, float, bool, type(None))):
                    field_sizes[p] += len(str(v))
                    field_counts[p] += 1
                elif isinstance(v, (dict, list)):
                    _walk(v, p)
        elif isinstance(o, list):
            for i, item in enumerate(o):
                p = f"{path}[{i}]" if path else f"[{i}]"
                if isinstance(item, str):
                    field_sizes[p] += len(item)
                    field_counts[p] += 1
                elif isinstance(item, dict):
                    # For lists of dicts, aggregate by key across all items
                    for k, v in item.items():
                        kp = f"{path}[].{k}" if path else f"[].{k}"
                        if isinstance(v, str):
                            field_sizes[kp] += len(v)
                            field_counts[kp] += 1
                            if kp not in field_examples:
                                field_examples[kp] = v[:200]
                        elif isinstance(v, (int, float, bool, type(None))):
                            field_sizes[kp] += len(str(v))
                            field_counts[kp] += 1
                        elif isinstance(v, (dict, list)):
                            _walk(v, kp)

    _walk(obj, prefix)

    # Sort by total size
    sorted_fields = sorted(field_sizes.items(), key=lambda x: -x[1])

    print(f"\n  Top {top_n} fields by total size:")
    print(f"  {'Field':<60} {'Total KB':>10} {'Count':>8} {'Sample'}")
    print(f"  {'-'*60} {'-'*10} {'-'*8} {'-'*40}")
    for field, size in sorted_fields[:top_n]:
        count = field_counts.get(field, 0)
        sample = field_examples.get(field, "")[:80]
        sample = sample.replace("\n", "\\n")
        print(f"  {field:<60} {size/1024:>10.1f} {count:>8} {sample}")

    return sorted_fields


def find_large_strings(obj, min_len=10000):
    """Find all string values larger than min_len."""
    large = []

    def _walk(o, path):
        if isinstance(o, dict):
            for k, v in o.items():
                p = f"{path}.{k}"
                if isinstance(v, str) and len(v) > min_len:
                    large.append((p, len(v), v[:100], v[-50:]))
                elif isinstance(v, (dict, list)):
                    _walk(v, p)
        elif isinstance(o, list):
            for i, item in enumerate(o):
                p = f"{path}[{i}]"
                if isinstance(item, str) and len(item) > min_len:
                    large.append((p, len(item), item[:100], item[-50:]))
                elif isinstance(item, (dict, list)):
                    _walk(item, p)

    _walk(obj, "root")
    large.sort(key=lambda x: -x[1])
    return large


def detect_base64_patterns(s):
    """Detect if a string looks like base64 encoded data."""
    import re
    # Check for data URLs
    if s.startswith("data:") and ";base64," in s[:50]:
        return "data_url"
    # Check for base64-like patterns
    if re.match(r'^[A-Za-z0-9+/=]{100,}$', s[:200]):
        return "base64_blob"
    # Check for base64 in markdown images
    if "base64" in s[:500]:
        return "base64_in_markdown"
    return None


def analyze_document_stage(doc_key, stage, filename):
    """Full analysis of a single stage for a document."""
    path = get_path(doc_key, filename)
    if not path.exists():
        print(f"  {filename} not found, skipping.")
        return None

    print(f"\n{'='*80}")
    print(f"ANALYZING: {DOCS[doc_key]['label']} - {stage}")
    print(f"File: {path}")
    print(f"Size: {os.path.getsize(path) / (1024*1024):.1f} MB")
    print(f"{'='*80}")

    data = load_json(path)

    # Analyze field sizes
    sorted_fields = analyze_field_sizes(data)

    # Find large strings
    print(f"\n  Large strings (>10KB):")
    large = find_large_strings(data, min_len=10000)
    for path_str, length, start, end in large[:20]:
        b64_type = detect_base64_patterns(start)
        print(f"    [{b64_type or 'text'}] {path_str}: {length/1024:.1f} KB")
        print(f"      Start: {start[:80]}")
        print(f"      End: ...{end[:60]}")

    # Check for specific patterns
    print(f"\n  Base64/Data URL detection:")
    b64_fields = []

    def _find_b64(o, path):
        if isinstance(o, dict):
            for k, v in o.items():
                p = f"{path}.{k}"
                if isinstance(v, str):
                    b64 = detect_base64_patterns(v)
                    if b64:
                        b64_fields.append((p, b64, len(v)))
                elif isinstance(v, (dict, list)):
                    _find_b64(v, p)
        elif isinstance(o, list):
            for i, item in enumerate(o):
                p = f"{path}[{i}]"
                if isinstance(item, str):
                    b64 = detect_base64_patterns(item)
                    if b64:
                        b64_fields.append((p, b64, len(item)))
                elif isinstance(item, (dict, list)):
                    _find_b64(item, p)

    _find_b64(data, "root")
    if b64_fields:
        total_b64 = sum(x[2] for x in b64_fields)
        print(f"  Found {len(b64_fields)} base64/data URL fields, total {total_b64/1024:.1f} KB")
        for field, btype, size in b64_fields[:10]:
            print(f"    [{btype}] {field}: {size/1024:.1f} KB")
    else:
        print(f"  No base64/data URL patterns found.")

    return {
        "sorted_fields": sorted_fields,
        "large_strings": large,
        "b64_fields": b64_fields,
        "data": data,
    }


def analyze_children(doc_key):
    """Analyze children.json with focus on image children, orphan detection, content_kind."""
    path = get_path(doc_key, r"06_chunk_plan\children.json")
    if not path.exists():
        print(f"Children not found for {doc_key}")
        return None

    print(f"\n{'='*80}")
    print(f"ANALYZING CHILDREN: {DOCS[doc_key]['label']}")
    print(f"{'='*80}")

    data = load_json(path)
    children = data if isinstance(data, list) else data.get("children", data.get("data", []))

    print(f"\n  Total children: {len(children)}")

    # Categorize children
    categories = Counter()
    image_children = []
    text_children = []
    orphan_children = []
    parent_indices = set()
    ocr_children = []

    for i, c in enumerate(children):
        if not isinstance(c, dict):
            print(f"  WARNING: child[{i}] is not a dict: {type(c).__name__}")
            continue

        chunk_type = c.get("chunk_type", "unknown")
        content_kind = c.get("content_kind", "")
        block_type = c.get("block_type", "")
        parent_index = c.get("parent_index")
        metadata = c.get("metadata", {}) or {}
        flags = c.get("flags", {}) or {}
        context_header = c.get("context_header", "")
        content = c.get("content", "") or ""

        categories[chunk_type] += 1

        # Check for image indicators
        is_image = (
            chunk_type == "image"
            or content_kind == "image"
            or block_type == "image"
            or bool(metadata.get("asset_id"))
            or bool(metadata.get("image_path"))
            or bool(metadata.get("image_url"))
            or metadata.get("visual_role") == "page_primary_visual"
            or bool(flags.get("is_visual_asset"))
            or bool(flags.get("is_image"))
        )

        if is_image:
            image_children.append({
                "index": i,
                "chunk_type": chunk_type,
                "content_kind": content_kind,
                "block_type": block_type,
                "parent_index": parent_index,
                "asset_id": metadata.get("asset_id", ""),
                "image_path": metadata.get("image_path", ""),
                "image_url": metadata.get("image_url", ""),
                "visual_role": metadata.get("visual_role", ""),
                "context_header": context_header[:100],
                "flags_keys": list(flags.keys())[:10] if flags else [],
                "metadata_keys": list(metadata.keys())[:15] if metadata else [],
            })
        else:
            text_children.append(c)

        # Check for orphan
        if parent_index is None:
            orphan_children.append({
                "index": i,
                "chunk_type": chunk_type,
                "content_kind": content_kind,
                "context_header": context_header[:200],
                "content_preview": content[:200],
                "parent_materialized": c.get("parent_materialized", None),
                "metadata_parent_materialized": metadata.get("parent_materialized", None),
                "flags_keys": list(flags.keys())[:10] if flags else [],
                "metadata_keys": list(metadata.keys())[:15] if metadata else [],
            })
        else:
            parent_indices.add(parent_index)

        # Check for OCR content
        text = c.get("text", "") or c.get("content", "") or ""
        if "ocr" in str(metadata).lower() or "ocr" in str(flags).lower():
            ocr_children.append({
                "index": i,
                "chunk_type": chunk_type,
                "context_header": context_header[:100],
                "metadata_ocr_keys": [k for k in metadata.keys() if "ocr" in k.lower()] if metadata else [],
                "flags_ocr_keys": [k for k in flags.keys() if "ocr" in k.lower()] if flags else [],
            })

    print(f"\n  Chunk type distribution:")
    for ctype, count in categories.most_common():
        print(f"    {ctype}: {count}")

    print(f"\n  Image children: {len(image_children)}")
    for ic in image_children:
        print(f"    [{ic['index']}] chunk_type={ic['chunk_type']}, content_kind={ic['content_kind']}, "
              f"block_type={ic['block_type']}, visual_role={ic['visual_role']}, "
              f"parent_index={ic['parent_index']}, asset_id={ic['asset_id'][:40]}, "
              f"image_path={str(ic['image_path'])[:60]}")
        print(f"         flags: {ic['flags_keys']}")
        print(f"         metadata: {ic['metadata_keys']}")

    print(f"\n  Orphan children (parent_index=null): {len(orphan_children)}")
    for oc in orphan_children:
        print(f"    [{oc['index']}] chunk_type={oc['chunk_type']}, content_kind={oc['content_kind']}")
        print(f"         context_header: {oc['context_header'][:150]}")
        print(f"         content_preview: {oc['content_preview'][:150]}")
        print(f"         parent_materialized: {oc['parent_materialized']}")
        print(f"         metadata.parent_materialized: {oc['metadata_parent_materialized']}")
        print(f"         flags: {oc['flags_keys']}")
        print(f"         metadata: {oc['metadata_keys']}")

    print(f"\n  OCR children: {len(ocr_children)}")
    for oc in ocr_children:
        print(f"    [{oc['index']}] type={oc['chunk_type']}, header={oc['context_header'][:100]}")
        print(f"         ocr_metadata: {oc['metadata_ocr_keys']}, ocr_flags: {oc['flags_ocr_keys']}")

    print(f"\n  Unique parent_indices: {sorted(parent_indices)}")

    # Check DCP-4 isolation markers
    print(f"\n  DCP-4 / Quarantine / Isolation markers:")
    quarantine_count = 0
    skip_embedding_count = 0
    skip_es_count = 0
    for i, c in enumerate(children):
        if not isinstance(c, dict):
            continue
        metadata = c.get("metadata", {}) or {}
        flags = c.get("flags", {}) or {}

        is_quarantine = (
            metadata.get("ocr_quarantined")
            or metadata.get("skip_embedding_recommended")
            or flags.get("ocr_quarantined")
            or flags.get("skip_embedding")
            or metadata.get("visual_ocr_quarantine")
            or flags.get("visual_ocr_quarantine")
        )
        skips_embed = (
            metadata.get("skip_embedding_recommended")
            or flags.get("skip_embedding")
        )
        skips_es = (
            metadata.get("skip_keyword_index")
            or flags.get("skip_es")
            or metadata.get("skip_elasticsearch")
        )

        if is_quarantine:
            quarantine_count += 1
            print(f"    QUARANTINED [{i}]: chunk_type={c.get('chunk_type')}, "
                  f"header={c.get('context_header', '')[:100]}")
        if skips_embed:
            skip_embedding_count += 1
        if skips_es:
            skip_es_count += 1

    print(f"  Quarantine markers: {quarantine_count}")
    print(f"  skip_embedding: {skip_embedding_count}")
    print(f"  skip_es: {skip_es_count}")

    return {
        "total": len(children),
        "categories": dict(categories),
        "image_children": image_children,
        "orphan_children": orphan_children,
        "ocr_children": ocr_children,
        "parent_indices": sorted(parent_indices),
        "quarantine_count": quarantine_count,
        "skip_embedding_count": skip_embedding_count,
        "skip_es_count": skip_es_count,
    }


def analyze_parents(doc_key):
    """Analyze parents.json."""
    path = get_path(doc_key, r"06_chunk_plan\parents.json")
    if not path.exists():
        print(f"Parents not found for {doc_key}")
        return None

    print(f"\n{'='*80}")
    print(f"ANALYZING PARENTS: {DOCS[doc_key]['label']}")
    print(f"{'='*80}")

    data = load_json(path)
    parents = data if isinstance(data, list) else data.get("parents", data.get("data", []))

    print(f"  Total parents: {len(parents)}")

    for i, p in enumerate(parents):
        if not isinstance(p, dict):
            continue
        print(f"  [{i}] chunk_type={p.get('chunk_type')}, "
              f"context_header={p.get('context_header', '')[:120]}, "
              f"page_range={p.get('page_range', '')}, "
              f"num_children={p.get('num_children', 'N/A')}, "
              f"content_kind={p.get('content_kind', '')}")

    return parents


def analyze_assets(doc_key):
    """Analyze assets.json to understand canonical assets and image tracking."""
    path = get_path(doc_key, r"05_canonical\assets.json")
    if not path.exists():
        print(f"Assets not found for {doc_key}")
        return None

    print(f"\n{'='*80}")
    print(f"ANALYZING ASSETS: {DOCS[doc_key]['label']}")
    print(f"File size: {os.path.getsize(path) / (1024*1024):.1f} MB")
    print(f"{'='*80}")

    data = load_json(path)

    # Handle different formats
    if isinstance(data, list):
        assets = data
    elif isinstance(data, dict):
        assets = data.get("assets", data.get("data", []))
        if not assets:
            # Maybe the dict IS the assets by key
            possible_assets = [v for v in data.values() if isinstance(v, dict) and "asset_id" in v]
            if possible_assets:
                assets = possible_assets
            else:
                assets = [data]

    print(f"  Total assets: {len(assets)}")

    visual_assets = []
    image_assets = []
    asset_types = Counter()

    for i, a in enumerate(assets):
        if not isinstance(a, dict):
            print(f"  WARNING: asset[{i}] is not a dict: {type(a).__name__}, value preview: {str(a)[:100]}")
            continue

        asset_type = a.get("type", a.get("asset_type", "unknown"))
        asset_types[asset_type] += 1

        is_visual = a.get("is_visual", False) or a.get("visual_role") == "page_primary_visual"
        is_image = asset_type in ("image", "visual_asset", "page_primary_visual") or a.get("content_type", "").startswith("image/")

        if is_visual:
            visual_assets.append({"index": i, "asset_id": a.get("asset_id", ""), "type": asset_type,
                                  "page_no": a.get("page_no", ""), "visual_role": a.get("visual_role", ""),
                                  "image_path": str(a.get("image_path", ""))[:80]})

        if is_image:
            image_assets.append({"index": i, "asset_id": a.get("asset_id", ""), "type": asset_type,
                                 "page_no": a.get("page_no", ""), "content_type": a.get("content_type", ""),
                                 "has_image_data": bool(a.get("image_data") or a.get("data") or a.get("content"))})

    print(f"\n  Asset types: {dict(asset_types)}")
    print(f"  Visual assets (page_primary_visual): {len(visual_assets)}")
    for va in visual_assets:
        print(f"    [{va['index']}] id={va['asset_id'][:40]}, type={va['type']}, "
              f"page={va['page_no']}, role={va['visual_role']}")

    print(f"  Image assets: {len(image_assets)}")
    for ia in image_assets[:10]:
        print(f"    [{ia['index']}] id={ia['asset_id'][:40]}, type={ia['type']}, "
              f"page={ia['page_no']}, has_image_data={ia['has_image_data']}")

    # Check field sizes for assets
    print(f"\n  Asset field size analysis:")
    analyze_field_sizes(assets, prefix="assets")

    return assets


def analyze_parsed_document(doc_key):
    """Deep analysis of parsed_document.json focusing on blocks structure."""
    path = get_path(doc_key, r"02_parsed_document\02_parsed_document.json")
    if not path.exists():
        print(f"Parsed document not found for {doc_key}")
        return None

    print(f"\n{'='*80}")
    print(f"ANALYZING PARSED DOCUMENT: {DOCS[doc_key]['label']}")
    print(f"File size: {os.path.getsize(path) / (1024*1024):.1f} MB")
    print(f"{'='*80}")

    data = load_json(path)

    # Check structure - could be a ParsedDocument dict with blocks array
    if isinstance(data, dict):
        blocks = data.get("blocks", [])
        print(f"  Top-level keys: {list(data.keys())}")
        print(f"  Blocks count: {len(blocks)}")

        # Analyze blocks
        block_types = Counter()
        visual_blocks = []
        text_blocks = []
        for i, b in enumerate(blocks):
            if not isinstance(b, dict):
                continue
            btype = b.get("block_type", b.get("type", "unknown"))
            block_types[btype] += 1

            if btype in ("image", "visual", "page_primary_visual"):
                visual_blocks.append(b)
            elif btype in ("text", "paragraph", "heading"):
                text_blocks.append(b)

        print(f"  Block types: {dict(block_types)}")
        print(f"  Visual blocks: {len(visual_blocks)}")
        print(f"  Text blocks: {len(text_blocks)}")

        # Field size analysis on blocks
        analyze_field_sizes(blocks, prefix="blocks")

    return data


def analyze_enhanced_document(doc_key):
    """Deep analysis of enhanced_document.json."""
    path = get_path(doc_key, r"03_enhanced_document\03_enhanced_document.json")
    if not path.exists():
        print(f"Enhanced document not found for {doc_key}")
        return None

    print(f"\n{'='*80}")
    print(f"ANALYZING ENHANCED DOCUMENT: {DOCS[doc_key]['label']}")
    print(f"File size: {os.path.getsize(path) / (1024*1024):.1f} MB")
    print(f"{'='*80}")

    data = load_json(path)

    if isinstance(data, dict):
        blocks = data.get("blocks", [])
        print(f"  Top-level keys: {list(data.keys())}")

        # Hybrid block handling
        block_types = Counter()
        large_blocks = []
        for i, b in enumerate(blocks):
            if not isinstance(b, dict):
                block_types[f"<str:{len(b) if isinstance(b, str) else '?'}>"] += 1
                continue
            btype = b.get("block_type", b.get("type", "unknown"))
            block_types[btype] += 1

            # Estimate block size
            b_str = json.dumps(b, ensure_ascii=False)
            if len(b_str) > 50000:
                large_blocks.append((i, btype, len(b_str)))

        print(f"  Blocks count: {len(blocks)}")
        print(f"  Block types: {dict(block_types)}")
        print(f"  Large blocks (>50KB): {len(large_blocks)}")
        for idx, btype, size in large_blocks[:10]:
            print(f"    [{idx}] type={btype}, size={size/1024:.1f} KB")

        analyze_field_sizes(blocks, prefix="blocks")

    return data


def main():
    print("=" * 80)
    print("COMPREHENSIVE DEBUG ARTIFACT ANALYSIS")
    print("=" * 80)

    # ---- A. CALLIGRAPHY DOCUMENT (primary focus) ----
    calli_key = "02292ffd"
    review_key = "9420dda9"
    patent_key = "1fbc56a2"

    # 1. Analyze all stages of calligraphy doc
    print("\n\n########## CALLIGRAPHY DOCUMENT - FULL ANALYSIS ##########")

    # Parsed document
    parsed = analyze_parsed_document(calli_key)

    # Enhanced document (the 12.6MB one)
    enhanced = analyze_enhanced_document(calli_key)

    # Reflowed document
    analyze_document_stage(calli_key, "04_reflowed_document", r"04_reflowed_document\04_reflowed_document.json")

    # Assets
    analyze_assets(calli_key)

    # Parents
    analyze_parents(calli_key)

    # Children
    analyze_children(calli_key)

    # ---- B. ACADEMIC REVIEW DOCUMENT (orphan focus) ----
    print("\n\n########## ACADEMIC REVIEW - ORPHAN ANALYSIS ##########")
    analyze_parents(review_key)
    analyze_children(review_key)

    # ---- C. PATENT DOCUMENT (baseline) ----
    print("\n\n########## PATENT DOCUMENT - BASELINE ##########")
    analyze_children(patent_key)
    analyze_assets(patent_key)

    print("\n\n" + "=" * 80)
    print("ANALYSIS COMPLETE")
    print("=" * 80)


if __name__ == "__main__":
    main()

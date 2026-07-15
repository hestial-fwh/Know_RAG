from app.services.chunk_service import _normalize_image_info
from app.services.chunker.canonical_parent_child import CanonicalAsset, _asset_image_info


def test_canonical_image_info_keeps_legacy_display_aliases():
    info = _asset_image_info(
        CanonicalAsset(
            asset_id="image-1",
            asset_type="image",
            image_path="kb/doc/media/chart.png",
            image_url="/files/kb/doc/media/chart.png",
            image_base64="aGVsbG8=",
        )
    )
    assert info["url"] == info["image_url"]
    assert info["path"] == info["image_path"]
    assert info["image_base64"] == "aGVsbG8="


def test_chunk_api_normalizes_canonical_image_fields_for_frontend():
    info = _normalize_image_info(
        {
            "image_url": "/files/kb/doc/media/chart.png",
            "image_path": "kb/doc/media/chart.png",
            "image_base64": "aGVsbG8=",
        }
    )
    assert info["url"] == "/files/kb/doc/media/chart.png"
    assert info["relative_path"] == "kb/doc/media/chart.png"
    assert info["base64"] == "aGVsbG8="


def test_chunk_api_does_not_rewrite_data_image_url_as_file_path():
    data_url = "data:image/png;base64,aGVsbG8="
    info = _normalize_image_info({"image_url": data_url})
    assert info["url"] == data_url
    assert "relative_path" not in info

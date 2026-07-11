"""Link draft refs before persistence."""

from __future__ import annotations

from app.services.chunking.chunk_draft import ChildDraft, ParentDraft


class ChunkLinker:
    @staticmethod
    def link_parent_child_and_neighbors(parents: list[ParentDraft], children_by_parent: dict[str, list[ChildDraft]]) -> list[ParentDraft | ChildDraft]:
        ordered: list[ParentDraft | ChildDraft] = []
        for parent_index, parent in enumerate(parents):
            if parent_index > 0:
                parent.prev_ref = parents[parent_index - 1].draft_ref
            if parent_index < len(parents) - 1:
                parent.next_ref = parents[parent_index + 1].draft_ref
            ordered.append(parent)

            children = children_by_parent.get(parent.draft_ref, [])
            for child_index, child in enumerate(children):
                child.parent_ref = parent.draft_ref
                if child_index > 0:
                    child.prev_ref = children[child_index - 1].draft_ref
                if child_index < len(children) - 1:
                    child.next_ref = children[child_index + 1].draft_ref
                ordered.append(child)

        indexable = [draft for draft in ordered if draft.chunk_type in {"text", "table", "image", "image_ocr_text", "equation"}]
        for index, draft in enumerate(indexable):
            draft.prev_ref = None
            draft.next_ref = None
            if index > 0:
                draft.prev_ref = indexable[index - 1].draft_ref
            if index < len(indexable) - 1:
                draft.next_ref = indexable[index + 1].draft_ref

        for index, draft in enumerate(ordered):
            draft.chunk_index = index
            draft.order_index = index
        return ordered

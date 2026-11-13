"""同一知识库内的原文件 SHA-256 唯一，防止并发上传重复入库。"""

from alembic import op
import sqlalchemy as sa


revision = "0017_document_file_dedup"
down_revision = "0016_kb_retrieval_profile"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 不自动删除历史重复文档或其切片；有冲突时先报告，再由维护者选择保留版本。
    duplicates = op.get_bind().execute(sa.text(
        "SELECT knowledge_base_id, file_hash FROM documents WHERE file_hash <> '' "
        "GROUP BY knowledge_base_id, file_hash HAVING count(*) > 1 LIMIT 1"
    )).first()
    if duplicates is not None:
        raise RuntimeError("存在同知识库相同文件指纹的历史重复文档，请先处理后再执行迁移 0017")
    op.create_index(
        "uq_documents_kb_file_hash", "documents", ["knowledge_base_id", "file_hash"],
        unique=True, postgresql_where=sa.text("file_hash <> ''"),
        sqlite_where=sa.text("file_hash <> ''"),
    )


def downgrade() -> None:
    op.drop_index("uq_documents_kb_file_hash", table_name="documents")

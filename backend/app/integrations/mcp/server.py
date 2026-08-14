"""Know_RAG MCP server.

This module registers Know_RAG MCP tools.

Default usage:
    python -m app.integrations.mcp.server

That starts a stdio MCP server, which is suitable for:
- MCP Inspector local stdio mode
- Claude Desktop local MCP
- Cursor local MCP

Remote Streamable HTTP MCP is started by:
    python -m app.integrations.mcp.http_server

Important:
FastMCP may inspect raw Python annotations when registering tools.
Avoid:
- from __future__ import annotations
- Optional / Union annotations on @mcp.tool function parameters
- list[str] / dict[str, Any] annotations on @mcp.tool function parameters

Use simple runtime classes in tool function signatures.
"""

import inspect
import os

from mcp.server.fastmcp import FastMCP

from app.integrations.mcp import tools


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or str(value).strip() == "":
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _create_mcp() -> FastMCP:
    """Create FastMCP instance.

    Different mcp Python SDK versions expose slightly different FastMCP
    constructor parameters.  We inspect the signature and only pass supported
    kwargs, so the code remains compatible with the installed SDK.
    """
    kwargs = {}

    sig = inspect.signature(FastMCP)
    params = sig.parameters

    if "host" in params:
        kwargs["host"] = os.getenv("MCP_HOST", "0.0.0.0")

    if "port" in params:
        kwargs["port"] = _env_int("MCP_PORT", 8010)

    # Where the Streamable HTTP endpoint is mounted inside the Starlette app
    # built by mcp.streamable_http_app().  The auth middleware protects the
    # same path.
    if "streamable_http_path" in params:
        kwargs["streamable_http_path"] = (os.getenv("MCP_MOUNT_PATH") or "/mcp").strip() or "/mcp"

    # Do not pass unsupported kwargs.  Some SDK versions support more options,
    # but host/port are enough for this step.
    return FastMCP("know-rag", **kwargs)


mcp = _create_mcp()


@mcp.tool(
    name="know_rag_list_knowledge_bases",
    description="List available Know_RAG knowledge bases with IDs, names and counts.",
)
async def know_rag_list_knowledge_bases(page: int = 1, page_size: int = 20) -> dict:
    return await tools.list_knowledge_bases(page=page, page_size=page_size)


@mcp.tool(
    name="know_rag_search_context",
    description=(
        "Search a Know_RAG knowledge base and return structured evidence groups. "
        "Each group is a direct-hit chunk plus its nearby neighbors and related "
        "images (deduplicated by asset), ranked by relevance. Returns groups[] "
        "with item-level citations; does not return raw debug scores."
    ),
)
async def know_rag_search_context(
    query_text: str,
    knowledge_base_id: str,
    retrieval_mode: str = "hybrid",
    match_count: int = 10,
    score_threshold: float = -1.0,
    vector_threshold: float = -1.0,
    keyword_threshold: float = -1.0,
    include_parent_context: bool = True,
    parent_context_mode: str = "standard",
    parent_context_max_chars: int = 2000,
    neighbor_context_max_chars: int = 300,
    parent_children_limit: int = 10,
    max_parent_contexts: int = 5,
    merge_hits_by_parent: bool = True,
    max_context_chars: int = 8000,
    max_sources: int = 8,
    include_images: bool = True,
    include_image_urls: bool = False,
    include_tables: bool = True,
    include_equations: bool = True,
) -> dict:
    """Search Know_RAG and return clean LLM/MCP-ready context.

    Threshold parameters use -1.0 as the "not provided" sentinel because
    FastMCP tool parameters should avoid Optional/Union annotations.
    """
    return await tools.search_context(
        query_text=query_text,
        knowledge_base_id=knowledge_base_id,
        retrieval_mode=retrieval_mode,
        match_count=match_count,
        score_threshold=None if score_threshold is None or score_threshold < 0 else score_threshold,
        vector_threshold=None if vector_threshold is None or vector_threshold < 0 else vector_threshold,
        keyword_threshold=None if keyword_threshold is None or keyword_threshold < 0 else keyword_threshold,
        include_parent_context=include_parent_context,
        parent_context_mode=parent_context_mode,
        parent_context_max_chars=parent_context_max_chars,
        neighbor_context_max_chars=neighbor_context_max_chars,
        parent_children_limit=parent_children_limit,
        max_parent_contexts=max_parent_contexts,
        merge_hits_by_parent=merge_hits_by_parent,
        max_context_chars=max_context_chars,
        max_sources=max_sources,
        include_images=include_images,
        include_image_urls=include_image_urls,
        include_tables=include_tables,
        include_equations=include_equations,
    )


def main() -> None:
    """Run stdio MCP server."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
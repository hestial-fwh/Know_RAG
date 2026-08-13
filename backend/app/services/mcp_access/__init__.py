"""MCP signed access management.

External MCP clients authenticate to the MCP server with an HMAC-SHA256
signature built from a Client ID / Client Secret pair issued by the Know_RAG
admin API.  Secrets are stored AES-256-GCM encrypted; only the backend ever
sees the plaintext.
"""

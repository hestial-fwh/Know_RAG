"""Shared fixtures for MCP signed access integration tests.

Only the ``mcp_access`` fixture is defined here; existing tests that do not
request it are unaffected.
"""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import routes_mcp_access, routes_mcp_clients
from app.core.response import error_response
from app.core.security import get_current_user
from app.db.models import McpClient, User
from app.db.session import get_db


@pytest.fixture
def mcp_access():
    """TestClient + SQLite engine for the MCP access admin / verify routes."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpClient.__table__.create(engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()

    @app.exception_handler(HTTPException)
    async def http_exception_handler(_: Request, exc: HTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content=error_response(str(exc.detail)),
        )

    app.include_router(routes_mcp_clients.router, prefix="/api/v1")
    app.include_router(routes_mcp_access.router, prefix="/api/v1")
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: User(
        id="test-admin", username="admin", password_hash="x", role="admin"
    )

    with TestClient(app) as client:
        yield SimpleNamespace(client=client, engine=engine)

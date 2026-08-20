"""Dependencies wiring the signing key and the directory into routes.

Both are process-wide singletons attached to `app.state` at startup, so a test can replace
either with `dependency_overrides` — the whole sign-in flow is worth exercising without a
second service running or a key on disk.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from app.directory import Directory
from app.keys import KeyPair


def get_key(request: Request) -> KeyPair:
    return request.app.state.signing_key  # type: ignore[no-any-return]


def get_directory(request: Request) -> Directory:
    return request.app.state.directory  # type: ignore[no-any-return]


KeyDep = Annotated[KeyPair, Depends(get_key)]
DirectoryDep = Annotated[Directory, Depends(get_directory)]

"""
Authentication module for Worker and Controller WebSocket connections.
Uses secrets.compare_digest for constant-time comparison against configured tokens.
"""

import secrets
from typing import Optional
from fastapi import WebSocket, status

from server.config import WORKER_TOKEN, CONTROLLER_TOKEN
from shared.protocol import ROLE_WORKER, ROLE_CONTROLLER


def verify_worker_token(token: str) -> bool:
    """Validate a worker authentication token."""
    if not token or not WORKER_TOKEN:
        return False
    return secrets.compare_digest(token.strip(), WORKER_TOKEN.strip())


def verify_controller_token(token: str) -> bool:
    """Validate a controller authentication token."""
    if not token or not CONTROLLER_TOKEN:
        return False
    return secrets.compare_digest(token.strip(), CONTROLLER_TOKEN.strip())


def extract_token_from_websocket(websocket: WebSocket) -> Optional[str]:
    """
    Extract token from query parameters (?token=...), authorization header, or x-token header.
    """
    # 1. Check query parameters
    token = websocket.query_params.get("token")
    if token:
        return token

    # 2. Check headers
    auth_header = websocket.headers.get("authorization")
    if auth_header:
        if auth_header.lower().startswith("bearer "):
            return auth_header[7:].strip()
        return auth_header.strip()

    x_token = websocket.headers.get("x-token")
    if x_token:
        return x_token.strip()

    return None


def get_authorized_workers_for_token(token: str) -> Optional[frozenset[str]]:
    """
    Parse CONTROLLER_ALLOWED_WORKERS environment variable.
    Format: "token1:workerA,workerB;token2:workerC"
    Returns a frozenset of allowed worker_ids for the matching token,
    or None if the controller has unrestricted access.
    """
    from server.config import CONTROLLER_ALLOWED_WORKERS
    
    if not CONTROLLER_ALLOWED_WORKERS or not token:
        return None
        
    mapping_str = CONTROLLER_ALLOWED_WORKERS.strip()
    if not mapping_str:
        return None
        
    for pair in mapping_str.split(";"):
        if ":" not in pair:
            continue
        t_key, w_list = pair.split(":", 1)
        if secrets.compare_digest(token.strip(), t_key.strip()):
            workers = frozenset(w.strip() for w in w_list.split(",") if w.strip())
            return workers
            
    # If the token is valid overall (checked prior) but not in the ACL map,
    # default to unrestricted access to maintain backward compatibility,
    # unless you want to default to deny. Given the plan: "If unset, all controllers can access all workers".
    # However, if CONTROLLER_ALLOWED_WORKERS *is* set, and the token is *not* in it,
    # it implies unrestricted for that token (since the token itself was already verified by CONTROLLER_TOKEN).
    return None


def get_client_ip(websocket: WebSocket) -> str:
    """Extract client IP address from WebSocket connection."""
    forwarded = websocket.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    
    if websocket.client and websocket.client.host:
        return websocket.client.host
        
    return "unknown"

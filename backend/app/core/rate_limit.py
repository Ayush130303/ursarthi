"""Fixed-window rate limiter backed by the shared cache (Redis in production)."""
import time

from fastapi import HTTPException, Request, status

from app.core.cache import get_cache


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def enforce_rate_limit(bucket: str, identity: str, limit: int, window_seconds: int = 60) -> None:
    window = int(time.time() // window_seconds)
    key = f"rl:{bucket}:{identity}:{window}"
    count = get_cache().incr(key, ttl=window_seconds + 1)
    if count > limit:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please slow down and try again shortly.",
            headers={"Retry-After": str(window_seconds)},
        )

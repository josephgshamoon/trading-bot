"""Proxy configuration for py-clob-client's internal httpx transport.

The py-clob-client library uses a module-level httpx.Client singleton
(py_clob_client.http_helpers.helpers._http_client) for ALL HTTP requests.
Since httpx.Client doesn't allow changing the proxy after construction,
we replace the singleton with a new proxy-configured instance.

This is needed because Polymarket geo-blocks order placement (HTTP 403)
from certain countries (UK, US, France, etc.). Read-only operations
(market data, order books) work fine — only write operations
(post_order, cancel, etc.) are blocked.

Usage:
    Call configure_clob_proxy() BEFORE creating a ClobClient instance.
    The proxy URL is read from the POLYMARKET_PROXY_URL environment variable.

Supported proxy types:
    - HTTP:   http://host:port  or  http://user:pass@host:port
    - HTTPS:  https://host:port
    - SOCKS5: socks5://host:port  (requires 'socksio' package: pip install httpx[socks])

Recommended proxy locations (not geo-restricted by Polymarket):
    Singapore, Germany, Netherlands, Japan, Canada
"""

import os
import logging

logger = logging.getLogger("trading_bot.proxy")


def configure_clob_proxy(proxy_url: str | None = None) -> bool:
    """Replace the py-clob-client httpx singleton with a proxy-configured client.

    Args:
        proxy_url: Proxy URL string, e.g. "socks5://1.2.3.4:1080".
                   If None, reads from POLYMARKET_PROXY_URL env var.
                   If neither is set, does nothing and returns False.

    Returns:
        True if proxy was configured, False if no proxy URL was provided.

    Raises:
        ImportError: If socks5 proxy is requested but socksio is not installed.
        Exception: If the proxy-configured client fails its initial setup.
    """
    if proxy_url is None:
        proxy_url = os.environ.get("POLYMARKET_PROXY_URL", "").strip()

    if not proxy_url:
        logger.debug("No proxy URL configured (POLYMARKET_PROXY_URL not set)")
        return False

    # Validate proxy URL scheme
    scheme = proxy_url.split("://")[0].lower() if "://" in proxy_url else ""
    if scheme not in ("http", "https", "socks5", "socks5h", "socks4"):
        logger.error(
            f"Invalid proxy URL scheme '{scheme}'. "
            f"Supported: http, https, socks5, socks5h, socks4. "
            f"Got: {proxy_url}"
        )
        return False

    # Check for socksio if SOCKS proxy is requested
    if scheme.startswith("socks"):
        try:
            import socksio  # noqa: F401
        except ImportError:
            logger.error(
                f"SOCKS proxy requested ({scheme}://) but 'socksio' is not installed. "
                f"Install it with: pip install httpx[socks]"
            )
            raise ImportError(
                "SOCKS proxy support requires the 'socksio' package. "
                "Install with: pip install httpx[socks]"
            )

    import httpx
    from py_clob_client.http_helpers import helpers as clob_helpers

    # Mask credentials in log output
    display_url = _mask_proxy_url(proxy_url)
    logger.info(f"Configuring CLOB client proxy: {display_url}")

    # Close the existing client to release connections
    try:
        if clob_helpers._http_client is not None:
            clob_helpers._http_client.close()
    except Exception:
        pass  # If it's already closed or broken, that's fine

    # Create a new httpx.Client with the proxy configured.
    # We preserve http2=True which is what the original client uses.
    new_client = httpx.Client(
        http2=True,
        proxy=proxy_url,
    )

    # Replace the module-level singleton
    clob_helpers._http_client = new_client

    logger.info(
        f"CLOB client proxy configured successfully ({scheme} proxy). "
        f"All py-clob-client HTTP requests will route through the proxy."
    )
    return True


def _mask_proxy_url(url: str) -> str:
    """Mask credentials in a proxy URL for safe logging.

    socks5://user:secret@host:1080 -> socks5://user:****@host:1080
    http://host:8080 -> http://host:8080  (no change)
    """
    if "@" not in url:
        return url

    try:
        scheme_rest = url.split("://", 1)
        if len(scheme_rest) != 2:
            return url
        scheme, rest = scheme_rest
        creds_host = rest.split("@", 1)
        if len(creds_host) != 2:
            return url
        creds, host = creds_host
        if ":" in creds:
            user = creds.split(":", 1)[0]
            return f"{scheme}://{user}:****@{host}"
        return url
    except Exception:
        return "<proxy URL>"


def get_proxy_status() -> dict:
    """Return current proxy configuration status for diagnostics.

    Returns dict with:
        configured: bool — whether a proxy is active
        proxy_url: str — masked proxy URL or "none"
        scheme: str — proxy scheme or "none"
    """
    proxy_url = os.environ.get("POLYMARKET_PROXY_URL", "").strip()

    if not proxy_url:
        return {
            "configured": False,
            "proxy_url": "none",
            "scheme": "none",
        }

    scheme = proxy_url.split("://")[0].lower() if "://" in proxy_url else "unknown"
    return {
        "configured": True,
        "proxy_url": _mask_proxy_url(proxy_url),
        "scheme": scheme,
    }

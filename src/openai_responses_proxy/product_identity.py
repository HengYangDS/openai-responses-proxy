"""Canonical product identity shared by runtime and release projections."""

from __future__ import annotations

PRODUCT_SLUG = "openai-responses-proxy"
DISPLAY_NAME = "OpenAI Responses Proxy"
PACKAGE_NAME = PRODUCT_SLUG
COMMAND_NAME = PRODUCT_SLUG
ENVIRONMENT_PREFIX = PRODUCT_SLUG.replace("-", "_").upper()
SERVICE_ID = f"{PRODUCT_SLUG}.watchdog"
RELEASE_NAMESPACE = f"{PRODUCT_SLUG}-release"
RELEASE_PRINCIPAL = RELEASE_NAMESPACE
RELEASE_PLATFORMS = ("linux-x86_64", "macos-arm64")
# The first release whose assets are exactly RELEASE_PLATFORMS. An earlier release also carries
# platforms that the product no longer supports; its verification covers RELEASE_PLATFORMS alone.
RELEASE_PLATFORMS_SINCE = "7.0.0"
_NATIVE_RELEASE_PLATFORMS = {
    ("Darwin", "arm64"): "macos-arm64",
    ("Linux", "x86_64"): "linux-x86_64",
}


def environment_name(suffix: str) -> str:
    """Return one product-scoped environment-variable name."""
    return f"{ENVIRONMENT_PREFIX}_{suffix}"


def native_release_platform(system: str, machine: str) -> str:
    """Return the released platform identity for one native host."""
    try:
        platform_id = _NATIVE_RELEASE_PLATFORMS[system, machine]
    except KeyError:
        message = f"unsupported native release platform: {system}-{machine}"
        raise ValueError(message) from None
    if platform_id not in RELEASE_PLATFORMS:
        raise ValueError("native release platform is absent from the release inventory")
    return platform_id


def release_title(tag: str) -> str:
    """Return the canonical human-facing release title."""
    return f"{DISPLAY_NAME} {tag}"


def command(*arguments: str) -> str:
    """Return one user-facing command line rooted at the public executable."""
    return " ".join((COMMAND_NAME, *arguments))

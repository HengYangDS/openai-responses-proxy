"""Launchd observations shared by the macOS supervision tests."""


def service(pid: int) -> str:
    """Render one launchd user-domain service observation."""
    return f"user/501/example = {{\n\tpid = {pid}\n}}\n"

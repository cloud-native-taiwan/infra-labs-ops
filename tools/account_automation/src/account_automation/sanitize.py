import re


TOKEN_PATTERN = re.compile(r"\b[A-Za-z0-9_-]{20,}\b")


def sanitize_exception_message(message: str) -> str:
    return TOKEN_PATTERN.sub("[REDACTED]", message)

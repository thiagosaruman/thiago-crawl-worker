from __future__ import annotations


def chunk_utf8(text: str, max_bytes: int = 512 * 1024) -> tuple[str, ...]:
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    if not text:
        return ()

    chunks: list[str] = []
    current: list[str] = []
    current_bytes = 0

    for char in text:
        char_bytes = len(char.encode("utf-8"))
        if char_bytes > max_bytes:
            raise ValueError("single character/code point exceeds max_bytes")
        if current and current_bytes + char_bytes > max_bytes:
            chunks.append("".join(current))
            current = []
            current_bytes = 0
        current.append(char)
        current_bytes += char_bytes

    if current:
        chunks.append("".join(current))
    return tuple(chunks)

"""Configurable chunkers (ING-05). No LangChain, no embeddings.

Strategies are selected per collection via ``chunking_strategy`` +
``chunking_config`` and dispatched through :func:`chunk_document`.
"""

import re


def chunk_text(text: str, chunk_size: int = 800, overlap: int = 100) -> list[str]:
    if chunk_size <= 0:
        raise ValueError("chunk_size must be greater than 0")
    if overlap < 0:
        raise ValueError("overlap must be 0 or greater")
    if overlap >= chunk_size:
        raise ValueError("overlap must be smaller than chunk_size")

    text = text.strip()
    if not text:
        return []

    chunks: list[str] = []
    step = chunk_size - overlap
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        if end >= len(text):
            break
        start += step
    return chunks


def chunk_structure_aware(text: str, max_chars: int = 800) -> list[str]:
    """Split on blank-line / markdown-heading boundaries, then pack blocks up to
    ``max_chars``. Blocks larger than the budget fall back to fixed-size slicing
    so no chunk ever exceeds the limit. Preserves natural document structure.
    """
    text = text.strip()
    if not text:
        return []

    # Split on blank lines OR markdown headings (keep the heading with its body).
    raw_blocks = re.split(r"\n\s*\n|(?=^#{1,6}\s)", text, flags=re.MULTILINE)
    blocks = [b.strip() for b in raw_blocks if b and b.strip()]

    chunks: list[str] = []
    buffer = ""
    for block in blocks:
        if len(block) > max_chars:
            if buffer:
                chunks.append(buffer)
                buffer = ""
            chunks.extend(chunk_text(block, chunk_size=max_chars, overlap=0))
            continue
        candidate = f"{buffer}\n\n{block}" if buffer else block
        if len(candidate) > max_chars:
            chunks.append(buffer)
            buffer = block
        else:
            buffer = candidate
    if buffer:
        chunks.append(buffer)
    return chunks


def chunk_document(
    content: str,
    strategy: str = "fixed",
    config: dict | None = None,
) -> list[str]:
    """Dispatch to the configured chunking strategy (ING-05).

    - ``fixed``: fixed-size character window (config: chunk_size, overlap)
    - ``structure`` / ``semantic`` / ``markdown``: structure-aware packing
      (config: max_chars, defaulting to chunk_size)
    """
    config = config or {}
    if strategy == "fixed":
        return chunk_text(
            content,
            chunk_size=int(config.get("chunk_size", 800)),
            overlap=int(config.get("overlap", 100)),
        )
    if strategy in ("structure", "semantic", "markdown"):
        max_chars = int(config.get("max_chars", config.get("chunk_size", 800)))
        return chunk_structure_aware(content, max_chars=max_chars)
    raise ValueError(f"unknown chunking strategy: {strategy}")

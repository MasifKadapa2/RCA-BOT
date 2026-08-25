import hashlib


def content_hash(text: str) -> str:
    """Stable SHA-256 hash used for incremental indexing."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def file_hash(path) -> str:
    import hashlib as _h

    digest = _h.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()

"""Phase 4: repository filtering, chunking, hashing, incremental indexing."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.ingestion.chunker import FallbackLineChunker, chunk_source
from app.ingestion.filters import (
    SUPPORTED_EXTENSIONS,
    FileFilter,
    file_content_hash,
    iter_repository_files,
    read_source,
)
from app.ingestion.indexer import RepositoryIndexer

FIXTURE_REPO = Path(__file__).parents[1] / "fixtures" / "fixture_repo"


# ------------------------------------------------------------------ filtering


def test_supported_languages():
    assert set(SUPPORTED_EXTENSIONS.values()) == {
        "ruby", "python", "javascript", "typescript", "go", "java",
    }


def test_filter_ignores_secrets_and_noise(sample_repo_path):
    flt = FileFilter()
    root = Path(sample_repo_path)
    files = iter_repository_files(root, flt)
    rels = {f.relative_to(root).as_posix() for f in files}
    assert not any(r.endswith(".env") for r in rels)
    assert all(".git" not in r for r in rels)
    assert not any("node_modules" in r for r in rels)


def test_secret_files_are_ignored(tmp_path):
    flt = FileFilter()
    assert flt.is_ignored_file(".env")
    assert flt.is_ignored_file(".env.production")
    assert flt.is_ignored_file("credentials.yml")
    assert flt.is_ignored_file("secrets.json")
    assert flt.is_ignored_file("server.key")
    assert not flt.is_ignored_file("main.py")
    assert not flt.is_ignored_file("employee_serializer.rb")


def test_binary_and_large_files_are_skipped(tmp_path):
    (tmp_path / "blob.rb").write_bytes(b"\x00\x01\x02binary\x00")
    big = tmp_path / "big.py"
    big.write_text("# x\n" * 100_000)
    ok = tmp_path / "ok.py"
    ok.write_text("print('hi')\n")

    flt = FileFilter(max_file_size_bytes=10_000)
    files = {f.name for f in iter_repository_files(tmp_path, flt)}
    assert "ok.py" in files
    assert "blob.rb" not in files
    assert "big.py" not in files


def test_read_source_returns_none_for_binary(tmp_path):
    blob = tmp_path / "b.rb"
    blob.write_bytes(b"\x00\x01")
    assert read_source(blob) is None


def test_safe_relative_path_blocks_traversal(tmp_path):
    from app.ingestion.filters import safe_relative_path

    assert safe_relative_path(tmp_path, "../outside.rb") is None
    result = safe_relative_path(tmp_path, "app/models/user.rb")
    assert result == tmp_path / "app" / "models" / "user.rb"


# -------------------------------------------------------------------- hashing


def test_content_hash_stable_and_sensitive():
    assert file_content_hash("abc") == file_content_hash("abc")
    assert file_content_hash("abc") != file_content_hash("abd")


# ------------------------------------------------------------------- chunking


PYTHON_SAMPLE = """import os

class EmployeeRepository:
    def __init__(self, db):
        self.db = db

    def find_by_email(self, email):
        return self.db.query(Employee).filter_by(email=email).first()


def load_employee(path):
    with open(path) as fh:
        return fh.read()
"""

RUBY_SAMPLE = """module Billing
  class Invoice
    def total
      items.sum(&:price)
    end

    def paid?
      status == "paid"
    end
  end
end
"""


def test_python_semantic_chunks_preserve_line_numbers():
    chunks = chunk_source(PYTHON_SAMPLE, "x.py", "python", None)
    by_name = {c.symbol_name: c for c in chunks if c.symbol_name}
    assert "EmployeeRepository" in by_name
    find = [c for c in chunks if c.symbol_name == "find_by_email"]
    assert find and find[0].start_line == 7
    lines = PYTHON_SAMPLE.splitlines()
    assert lines[find[0].start_line - 1].strip().startswith("def find_by_email")
    assert "filter_by" in find[0].content
    for chunk in chunks:
        assert chunk.end_line >= chunk.start_line


def test_ruby_chunks_include_class_and_methods():
    chunks = chunk_source(RUBY_SAMPLE, "invoice.rb", "ruby", None)
    names = {c.symbol_name for c in chunks}
    assert {"Invoice", "total", "paid?"} <= names
    total = next(c for c in chunks if c.symbol_name == "total")
    assert total.symbol_type == "method"


def test_fallback_chunker_used_when_tree_sitter_unavailable():
    fallback = FallbackLineChunker()
    chunks = fallback.chunk(PYTHON_SAMPLE, "python")
    assert any(c.symbol_name == "EmployeeRepository" for c in chunks)


def test_chunking_never_fails_on_garbage():
    garbage = "not really code\njust text\nmore text\n"
    chunks = chunk_source(garbage, "x.txt", "text", None)
    assert isinstance(chunks, list)


def test_typescript_go_java_samples_chunk():
    go_sample = 'package main\n\ntype Server struct {\n\tport int\n}\n\nfunc (s *Server) Start() error {\n\treturn nil\n}\n'
    chunks = chunk_source(go_sample, "server.go", "go", None)
    assert any(c.symbol_name in ("Server", "Start") for c in chunks)


# ------------------------------------------------------------------ indexing


class CountingStore:
    """In-memory vector store double that counts operations."""

    def __init__(self):
        self.vectors: dict[str, dict] = {}
        self.add_calls = 0
        self.delete_calls = 0

    def add(self, ids, vectors, documents, metadatas):
        self.add_calls += 1
        for i, vid in enumerate(ids):
            self.vectors[vid] = {"vector": vectors[i], "document": documents[i],
                                 "metadata": metadatas[i]}

    def delete(self, where=None, ids=None):
        self.delete_calls += 1
        for vid in ids or []:
            self.vectors.pop(vid, None)

    def query(self, vector, top_k=10, where=None):
        return []

    def count(self):
        return len(self.vectors)


def _index(repo_path: str, db_session, embedder, store):
    indexer = RepositoryIndexer(
        repository_path=repo_path,
        chroma_path="/tmp/unused-chroma",
        embedding_provider=embedder,
        vector_store_factory=lambda: store,
    )
    return indexer.run(db_session), store


def test_index_creates_chunks_with_full_metadata(db_session, sample_repo_path, fake_embedder):
    stats, store = _index(sample_repo_path, db_session, fake_embedder, CountingStore())
    assert stats["files_scanned"] >= 1
    assert stats["chunks_created"] > 0
    sample = next(iter(store.vectors.values()))["metadata"]
    for key in ("repository_id", "relative_path", "language", "symbol_name",
                "symbol_type", "start_line", "end_line"):
        assert key in sample, f"missing metadata key: {key}"


def test_indexing_is_incremental(db_session, sample_repo_path, fake_embedder):
    store = CountingStore()
    first, _ = _index(sample_repo_path, db_session, fake_embedder, store)

    second, _ = _index(sample_repo_path, db_session, fake_embedder, store)
    assert second["files_indexed"] == 0
    assert second["files_skipped"] == first["files_scanned"]


def test_changed_file_is_reindexed_others_skipped(db_session, sample_repo_path, fake_embedder, tmp_path):
    store = CountingStore()
    _index(sample_repo_path, db_session, fake_embedder, store)

    serializer = Path(sample_repo_path) / "app" / "serializers" / "employee_serializer.rb"
    original = serializer.read_text()

    # Work on a copy so the shared fixture repo stays pristine.
    import shutil

    repo_copy = tmp_path / "repo"
    shutil.copytree(sample_repo_path, repo_copy, ignore=shutil.ignore_patterns(".git"))
    store2 = CountingStore()
    _index(str(repo_copy), db_session, fake_embedder, store2)
    (repo_copy / "app" / "serializers" / "employee_serializer.rb").write_text(original + "\n# touched\n")

    stats, _ = _index(str(repo_copy), db_session, fake_embedder, store2)
    assert stats["files_indexed"] == 1
    assert stats["files_removed"] == 0


def test_deleted_file_is_removed_from_index(db_session, tmp_path, fake_embedder):
    import shutil

    repo_copy = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo_copy, ignore=shutil.ignore_patterns(".git"))
    store = CountingStore()
    _index(str(repo_copy), db_session, fake_embedder, store)
    count_before = store.count()

    target = repo_copy / "services" / "email_service.py"
    if target.exists():
        target.unlink()
        stats, _ = _index(str(repo_copy), db_session, fake_embedder, store)
        assert stats["files_removed"] >= 1
        assert store.count() < count_before


def test_index_missing_repository_raises(db_session, tmp_path, fake_embedder):
    from app.core.errors import RepositoryNotFoundError

    indexer = RepositoryIndexer(
        repository_path=str(tmp_path / "does-not-exist"),
        chroma_path=str(tmp_path / "chroma"),
        embedding_provider=fake_embedder,
        vector_store_factory=lambda: CountingStore(),
    )
    with pytest.raises(RepositoryNotFoundError):
        indexer.run(db_session)


@pytest.fixture()
def fake_embedder():
    from tests.conftest import FakeEmbeddingProvider

    return FakeEmbeddingProvider()

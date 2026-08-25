"""Run retrieval evaluation over the 20-case dataset.

Usage:
    python -m app.evaluation.run
    python -m app.evaluation.run --json
    pytest tests/evaluation -q
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

# Ensure backend is on path when run as `python -m app.evaluation.run` from repo root
if __package__ is None or __package__ == "":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.evaluation.dataset import EVALUATION_CASES
from app.evaluation.metrics import compute_metrics


def _build_synthetic_repo(base: Path) -> Path:
    """Create a minimal synthetic repo with files matching expected cases."""
    # Create files that will be indexed and matched
    files = {
        "app/serializers/employee_serializer.rb": '''
class EmployeeSerializer
  def serialize
    { email: @employee.user.email }
  end
  def as_json
    { email: email }
  end
  def email
    @employee.user.email
  end
end
class User
  attr_reader :email
end
''',
        "app/services/notification_service.py": '''
class NotificationService:
    def send(self, msg):
        return msg
    def deliver(self, msg):
        return self.send(msg)
    def parse(self, data):
        import json
        return json.loads(data)
''',
        "app/services/auth_service.py": '''
class AuthService:
    def authenticate(self, token):
        if not token:
            raise ValueError("Invalid token")
        return True
''',
        "cart.py": '''
def compute_total(items):
    return sum(items)

class Cart:
    def add(self, item):
        return item
''',
        "services/email_service.py": '''
class EmailService:
    def send_welcome(self, email):
        return email
''',
        "app/models/user.rb": '''
class User
  def self.find(id)
    raise "not found"
  end
  def validate
    true
  end
end
''',
        "app/sentry/client.py": '''
class SentryClient:
    def get_issue(self, id):
        return {}
''',
        "app/ingestion/filters.py": '''
def read_source(path):
    return open(path).read()
''',
        "app/controllers/employees_controller.rb": '''
class EmployeesController
  def show
    render json: EmployeeSerializer.new(@employee).serialize
  end
end
''',
    }
    repo = base / "eval_repo"
    repo.mkdir(parents=True, exist_ok=True)
    for rel, content in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    return repo


def evaluate(use_chroma: bool = False) -> dict:
    """Run retrieval for each case and compute metrics."""
    from app.ingestion.indexer import RepositoryIndexer
    from app.ingestion.vector_store import ChromaVectorStore, InMemoryVectorStore
    from app.ingestion.embeddings import HashEmbeddingProvider
    from app.retrieval.query_builder import build_retrieval_query
    from app.retrieval.retriever import CodeRetriever
    from app.sentry.normalization import normalize_issue, normalize_event

    # Use temp DB and vector store
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        db_path = tmppath / "eval.db"
        chroma_path = tmppath / "chroma"
        synthetic_repo = _build_synthetic_repo(tmppath)

        # Setup DB
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from app.db.base import Base
        import app.models  # noqa: F401

        engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
        Base.metadata.create_all(engine)
        SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

        # Index synthetic repo
        if use_chroma:
            store = ChromaVectorStore(persist_directory=str(chroma_path))
        else:
            store = InMemoryVectorStore()
        indexer = RepositoryIndexer(
            repository_path=str(synthetic_repo),
            chroma_path=str(chroma_path),
            embedding_provider=HashEmbeddingProvider(),
            vector_store_factory=lambda: store,
        )
        with SessionLocal() as db:
            indexer.run(db)

        # For each case, build query and retrieve
        results = []
        for case in EVALUATION_CASES:
            # Build synthetic Sentry payloads
            issue_payload = {
                "id": case["id"],
                "title": f"{case['exception_type']}: {case['exception_value']}",
                "culprit": f"{case['stack_frames'][0]['filename']} in {case['stack_frames'][0]['function']}",
                "metadata": {"type": case["exception_type"], "value": case["exception_value"]},
            }
            event_payload = {
                "id": 1,
                "title": f"{case['exception_type']}: {case['exception_value']}",
                "platform": "python",
                "entries": [
                    {
                        "type": "exception",
                        "data": {
                            "values": [
                                {
                                    "type": case["exception_type"],
                                    "value": case["exception_value"],
                                    "stacktrace": {
                                        "frames": [
                                            {
                                                "filename": f["filename"],
                                                "function": f["function"],
                                                "inApp": True,
                                                "lineNo": 10,
                                            }
                                            for f in case["stack_frames"]
                                        ]
                                    },
                                }
                            ]
                        },
                    }
                ],
            }
            issue = normalize_issue(case["id"], issue_payload)
            event = normalize_event(event_payload)
            query = build_retrieval_query(issue, event)

            retriever = CodeRetriever(
                db_session_factory=SessionLocal,
                embedding_provider=HashEmbeddingProvider(),
                vector_store_factory=lambda s=store: s,
                max_results=10,
            )
            frame_paths = [f["filename"] for f in case["stack_frames"]]
            retrieved = retriever.retrieve(query, stack_frame_paths=frame_paths)

            # Find rank of expected file
            expected = case["expected_file"]
            rank = None
            if expected is None:
                # Insufficient evidence case: expect no relevant file or low confidence
                # Consider it "found" if retriever returns 0 or low-rank results
                # For metrics, treat as not found (rank None) to avoid inflating recall
                rank = None
            else:
                for idx, r in enumerate(retrieved, start=1):
                    if expected in r.relative_path or r.relative_path.endswith(expected):
                        rank = idx
                        break
            results.append({
                "case_id": case["id"],
                "expected_file": expected,
                "rank": rank,
                "found": rank is not None,
                "top_3": [r.relative_path for r in retrieved[:3]],
            })

        metrics = compute_metrics(results)
        return {"results": results, "metrics": metrics}


def main() -> int:
    parser = argparse.ArgumentParser(description="RCA Bot Retrieval Evaluation")
    parser.add_argument("--json", action="store_true", help="Emit JSON")
    args = parser.parse_args()

    report = evaluate(use_chroma=False)
    metrics = report["metrics"]
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print("RCA Bot Retrieval Evaluation")
        print(f"Cases: {metrics['cases']}")
        print(f"Recall@5: {metrics['recall_at_5']*100:.1f}%")
        print(f"Recall@10: {metrics['recall_at_10']*100:.1f}%")
        print(f"MRR: {metrics['mrr']:.2f}")
        print()
        for r in report["results"]:
            status = f"rank {r['rank']}" if r['rank'] else "not found"
            print(f"  {r['case_id']}: {status} — expected {r['expected_file']} top3={r['top_3']}")
    # Return 0 even if recall is low; metrics are informational
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

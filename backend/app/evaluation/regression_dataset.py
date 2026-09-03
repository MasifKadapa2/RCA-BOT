"""V2 regression-evaluation dataset builder.

Creates synthetic Git repositories with scripted commits/dates and ground
truth for candidate ranking + regression status. 20 cases covering the
required failure patterns. No benchmark leakage: the generator only uses
data available during a real investigation.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

BASE = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)
INCIDENT = BASE + timedelta(days=5)  # incident time for all cases


@dataclass
class RegressionCase:
    case_id: str
    pattern: str
    commits: list[tuple[str, dict[str, str], datetime]] = field(default_factory=list)
    failing_file: str | None = None
    failing_line: int = 1
    failing_symbol: str | None = None
    exception_value: str = "boom"
    retrieved_paths: list[str] = field(default_factory=list)
    expected_commit_message: str | None = None  # ground truth culprit (never fed to ranking)
    expected_status: str = "NO_CONFIDENT_REGRESSION"
    special: str | None = None  # rename | merge


def _c(message, files, minutes_before_incident):
    return (message, files, INCIDENT - timedelta(minutes=minutes_before_incident))


FILE_SVC = "app/services/payment_service.py"
SVC_BUGGY = ("class PaymentService:\n"
             "    def charge(self):\n"
             "        x = None\n"
             "        return x.email\n")
SVC_OK = "class PaymentService:\n    def charge(self):\n        return 1\n"


def build_cases() -> list[RegressionCase]:
    cases: list[RegressionCase] = []

    # 1 direct regression — one commit touches failing file/symbol near line
    cases.append(RegressionCase(
        case_id="direct-regression", pattern="direct",
        commits=[
            _c("initial service", {FILE_SVC: SVC_OK}, 2880),
            _c("charge reads user.email unguarded", {FILE_SVC: SVC_BUGGY}, 45),
            _c("unrelated docs", {"docs/a.md": "x"}, 40),
        ],
        failing_file=FILE_SVC, failing_line=4, failing_symbol="charge",
        exception_value="'NoneType' has no attribute 'email'",
        retrieved_paths=[FILE_SVC],
        expected_commit_message="charge reads user.email unguarded",
        expected_status="LIKELY_REGRESSION"))

    # 2 indirect — commit changes a helper called by the failing method
    cases.append(RegressionCase(
        case_id="indirect-regression", pattern="indirect",
        commits=[
            _c("initial", {"app/services/helper.py": "def resolve():\n    return 1\n",
                           FILE_SVC: "from app.services.helper import resolve\n"},
               2880),
            _c("resolve may return None", {"app/services/helper.py": "def resolve():\n    return None\n"},
               50),
        ],
        failing_file=FILE_SVC, failing_line=2, failing_symbol="resolve",
        exception_value="'NoneType' not callable", retrieved_paths=["app/services/helper.py"],
        expected_commit_message="resolve may return None",
        expected_status="POSSIBLE_REGRESSION"))

    # 3 unrelated recent commit only
    cases.append(RegressionCase(
        case_id="unrelated-recent", pattern="unrelated",
        commits=[_c("update readme", {"README.md": "hello\n"}, 30)],
        failing_file=FILE_SVC, failing_line=3, failing_symbol="charge",
        exception_value="'NoneType' has no attribute 'email'",
        expected_status="NO_CONFIDENT_REGRESSION"))

    # 4 multiple candidates
    cases.append(RegressionCase(
        case_id="multiple-candidates", pattern="multi",
        commits=[
            _c("tweak a", {"app/services/a_svc.py": "a\n"}, 90),
            _c("touch payment file", {FILE_SVC: SVC_OK.replace("return 1", "return 2")}, 60),
            _c("touch payment again", {FILE_SVC: SVC_BUGGY}, 55),
        ],
        failing_file=FILE_SVC, failing_line=4, failing_symbol="charge",
        exception_value="'NoneType' has no attribute 'email'",
        retrieved_paths=[FILE_SVC],
        expected_commit_message="touch payment again",
        expected_status="LIKELY_REGRESSION"))

    # 5 older relevant commit outside recency but inside window
    cases.append(RegressionCase(
        case_id="older-relevant", pattern="older",
        commits=[
            _c("older breaking change", {FILE_SVC: SVC_BUGGY}, 1300),
            _c("recent cosmetic", {"app/other.py": "b\n"}, 20),
        ],
        failing_file=FILE_SVC, failing_line=4, failing_symbol="charge",
        exception_value="'NoneType' has no attribute 'email'",
        retrieved_paths=[FILE_SVC],
        expected_commit_message="older breaking change",
        expected_status="LIKELY_REGRESSION"))

    # 6 newest commit is unrelated
    cases.append(RegressionCase(
        case_id="newest-unrelated", pattern="newest-unrelated",
        commits=[
            _c("breaking change in svc", {FILE_SVC: SVC_BUGGY}, 120),
            _c("logo update", {"assets/logo.txt": "^^\n"}, 10),
        ],
        failing_file=FILE_SVC, failing_line=4, failing_symbol="charge",
        exception_value="'NoneType' has no attribute 'email'",
        retrieved_paths=[FILE_SVC],
        expected_commit_message="breaking change in svc",
        expected_status="LIKELY_REGRESSION"))

    # 7 same file, unrelated method
    cases.append(RegressionCase(
        case_id="same-file-other-method", pattern="same-file-other-method",
        commits=[
            _c("refund tweak", {FILE_SVC: SVC_OK + "\n    def refund(self):\n        return 2\n"}, 35),
        ],
        failing_file=FILE_SVC, failing_line=99, failing_symbol="charge",
        exception_value="'NoneType' has no attribute 'email'",
        expected_status="POSSIBLE_REGRESSION"))

    # 8 same symbol modified
    cases.append(RegressionCase(
        case_id="same-symbol-modified", pattern="symbol",
        commits=[_c("rewrite charge", {FILE_SVC: SVC_BUGGY}, 25)],
        failing_file=FILE_SVC, failing_line=4, failing_symbol="charge",
        exception_value="'NoneType' has no attribute 'email'",
        expected_commit_message="rewrite charge",
        expected_status="LIKELY_REGRESSION"))

    # 9 nearby line change
    cases.append(RegressionCase(
        case_id="nearby-line-change", pattern="nearby",
        commits=[
            _c("add logging above charge", {FILE_SVC: SVC_OK.replace(
                "    def charge(self):", "    def charge(self):\n        log('charging')")}, 15),
        ],
        failing_file=FILE_SVC, failing_line=3, failing_symbol="charge",
        exception_value="'NoneType' has no attribute 'email'",
        expected_commit_message="add logging above charge",
        expected_status="LIKELY_REGRESSION"))

    # 10 renamed file
    cases.append(RegressionCase(
        case_id="renamed-file", pattern="rename",
        commits=[
            _c("initial old path", {"app/old_payment.py": SVC_OK}, 200),
            _c("rename to new module", {}, 100),  # handled specially below via git mv
            _c("introduce bug in renamed file", {}, 30),
        ],
        failing_file="app/new_payment.py", failing_line=3, failing_symbol="charge",
        exception_value="'NoneType' has no attribute 'email'",
        retrieved_paths=["app/new_payment.py"],
        special="rename",
        expected_commit_message="introduce bug in renamed file",
        expected_status="LIKELY_REGRESSION"))

    # 11 deleted code
    cases.append(RegressionCase(
        case_id="deleted-code", pattern="deletion",
        commits=[
            _c("initial with guard", {"app/services/p.py": "def charge(u):\n    if u is None:\n        u = default_user()\n    return u.email\n"}, 240),
            _c("remove guard", {"app/services/p.py": "def charge(u):\n    return u.email\n"}, 20),
        ],
        failing_file="app/services/p.py", failing_line=2, failing_symbol="charge",
        exception_value="'NoneType' has no attribute 'email'",
        retrieved_paths=["app/services/p.py"],
        expected_commit_message="remove guard",
        expected_status="LIKELY_REGRESSION"))

    # 12 refactor
    cases.append(RegressionCase(
        case_id="refactor", pattern="refactor",
        commits=[_c("large refactor of service", {FILE_SVC: SVC_BUGGY}, 45)],
        failing_file=FILE_SVC, failing_line=4, failing_symbol="charge",
        exception_value="'NoneType' has no attribute 'email'",
        retrieved_paths=[FILE_SVC],
        expected_commit_message="large refactor of service",
        expected_status="LIKELY_REGRESSION"))

    # 13 configuration change
    cases.append(RegressionCase(
        case_id="config-change", pattern="config",
        commits=[_c("flip feature flag", {"config/settings.yml": "payments:\n  async: true\n"}, 50)],
        failing_file=FILE_SVC, failing_line=1, failing_symbol="charge",
        exception_value="async payment failed", retrieved_paths=[],
        expected_status="NO_CONFIDENT_REGRESSION"))

    # 14 async behavior change
    cases.append(RegressionCase(
        case_id="async-change", pattern="async",
        commits=[_c("make charge async", {"app/services/queue.py": "queue.push(charge_task)\n"}, 40)],
        failing_file=FILE_SVC, failing_line=2, failing_symbol="charge",
        exception_value="task not awaited", retrieved_paths=["app/services/queue.py"],
        expected_commit_message="make charge async",
        expected_status="POSSIBLE_REGRESSION"))

    # 15 validation change
    cases.append(RegressionCase(
        case_id="validation-change", pattern="validation",
        commits=[_c("relax validation", {"app/validators.py": "def validate(v):\n    return True\n"}, 33)],
        failing_file="app/validators.py", failing_line=2, failing_symbol="validate",
        exception_value="invalid value accepted", retrieved_paths=["app/validators.py"],
        expected_commit_message="relax validation",
        expected_status="LIKELY_REGRESSION"))

    # 16 api response change
    cases.append(RegressionCase(
        case_id="api-response-change", pattern="api-response",
        commits=[_c("change payload shape", {"app/api/payload.py": "def shape():\n    return {'data': None}\n"}, 27)],
        failing_file="app/api/payload.py", failing_line=2, failing_symbol="shape",
        exception_value="'NoneType' has no attribute 'get'", retrieved_paths=["app/api/payload.py"],
        expected_commit_message="change payload shape",
        expected_status="LIKELY_REGRESSION"))

    # 17 dependency-related change
    cases.append(RegressionCase(
        case_id="dependency-bump", pattern="dependency",
        commits=[_c("bump sdk to 9.9.9", {"requirements.txt": "sdk==9.9.9\n"}, 70)],
        failing_file=FILE_SVC, failing_line=2, failing_symbol="charge",
        exception_value="sdk incompatibility", retrieved_paths=[],
        expected_status="NO_CONFIDENT_REGRESSION"))

    # 18 merge commit as candidate
    cases.append(RegressionCase(
        case_id="merge-commit", pattern="merge",
        commits=[
            _c("feature branch bug", {"app/services/pay_v2.py": SVC_BUGGY}, 65),
            _c("mainline work", {"app/mainline.py": "m2\n"}, 63),
            _c("Merge feature branch", {}, 60),
        ],
        failing_file="app/services/pay_v2.py", failing_line=4, failing_symbol="charge",
        exception_value="'NoneType' has no attribute 'email'",
        special="merge",
        expected_commit_message="feature branch bug",
        expected_status="LIKELY_REGRESSION"))

    # 19 no relevant commit at all
    cases.append(RegressionCase(
        case_id="no-relevant-commit", pattern="none",
        commits=[_c("unrelated css", {"web/app.css": "body{}\n"}, 15)],
        failing_file=FILE_SVC, failing_line=3, failing_symbol="charge",
        exception_value="'NoneType' has no attribute 'email'",
        expected_status="NO_CONFIDENT_REGRESSION"))

    # 20 insufficient evidence (no stack info at all)
    cases.append(RegressionCase(
        case_id="insufficient-evidence", pattern="insufficient",
        commits=[_c("random change", {"app/z.py": "z\n"}, 20)],
        failing_file=None, failing_line=1, failing_symbol=None,
        exception_value="something broke",
        expected_status="INSUFFICIENT_EVIDENCE"))

    return cases


def materialize_repo(base_dir: Path, case: RegressionCase) -> Path:
    """Create the synthetic repo on disk, including rename/merge specials."""
    repo = base_dir / case.case_id
    repo.mkdir(parents=True, exist_ok=True)

    def g(*args):
        subprocess.run(["git", *args], cwd=repo, check=True,
                       capture_output=True, text=True)

    g("init", "-q")
    env = os.environ.copy()

    def commit(files, message, when):
        for rel, content in files.items():
            p = repo / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
        stamp = when.strftime("%Y-%m-%dT%H:%M:%S %z")
        env["GIT_AUTHOR_DATE"] = stamp
        env["GIT_COMMITTER_DATE"] = stamp
        g("add", "-A")
        subprocess.run(["git", "commit", "-q", "-m", message], cwd=repo,
                       check=True, capture_output=True, text=True, env=env)

    if getattr(case, "special", None) == "rename":
        first, second, third = case.commits
        commit(first[1], first[0], first[2])
        g("mv", "app/old_payment.py", "app/new_payment.py")
        commit({}, second[0], second[2])
        (repo / "app/new_payment.py").write_text(SVC_BUGGY)
        commit({"app/new_payment.py": SVC_BUGGY}, third[0], third[2])
        return repo

    if getattr(case, "special", None) == "merge":
        c1, c2, c3 = case.commits
        # mainline initial
        commit({"app/mainline.py": "m\n"}, "mainline init", c1[2] - timedelta(minutes=5))
        # feature branch with the buggy file
        g("checkout", "-qb", "feature")
        commit(c1[1], c1[0], c1[2])
        g("checkout", "-q", "master")
        commit(c2[1], c2[0], c2[2])
        g("merge", "-q", "--no-ff", "feature", "-m", c3[0])
        # set merge commit date
        stamp = c3[2].strftime("%Y-%m-%dT%H:%M:%S %z")
        env["GIT_COMMITTER_DATE"] = stamp
        subprocess.run(["git", "commit", "-q", "--amend", "--no-edit"],
                       cwd=repo, check=True, capture_output=True, text=True, env=env)
        return repo

    for message, files, when in case.commits:
        commit(files, message, when)
    return repo


__all__ = ["build_cases", "materialize_repo", "RegressionCase", "INCIDENT"]

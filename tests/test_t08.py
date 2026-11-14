import json
from collections import deque
from pathlib import Path

import pandas as pd
import pytest

from src import main as runner

FIX = Path("fixtures/t08_deletion")


def _run(out, pipeline=None):
    """Chạy pipeline đúng như lệnh giảng viên chạy; trả về exit code."""
    args = ["--config", "configs/lab.yaml", "--track", "t08", "--output", str(out), "--offline", "--seed", "42"]
    if pipeline:
        args += ["--pipeline", pipeline]
    return runner.main(args)


@pytest.fixture(scope="module")
def poc(tmp_path_factory):
    """Chạy PoC 1 lần, dùng chung cho nhiều test (cho nhanh)."""
    out = tmp_path_factory.mktemp("poc")
    assert _run(out) == 0
    return out, pd.read_csv(out / "actions.csv")


def _graph():
    arts = json.loads((FIX / "data_graph.json").read_text(encoding="utf-8"))["artefacts"]
    return {a["artefact_id"]: a for a in arts}


def _descendants(by_id, root):
    """Tìm con cháu ĐỘC LẬP với pipeline (đi xuôi theo children) để kiểm chéo."""
    children = {}
    for a in by_id.values():
        for p in a["parent_ids"]:
            children.setdefault(p, []).append(a["artefact_id"])
    out, q = set(), deque([root])
    while q:
        for c in children.get(q.popleft(), []):
            if c not in out:
                out.add(c)
                q.append(c)
    return out


def _action(df, req, aid):
    row = df[(df.request_id == req) & (df.artefact_id == aid)]
    assert len(row) == 1, f"{req}/{aid} phải có đúng 1 dòng"
    return row.action.iloc[0]

def test_poc_writes_contract_files(poc):
    out, df = poc
    for f in ["actions.csv", "audit_log.jsonl", "review_queue.csv", "decision.json", "deletion_evidence.jsonl"]:
        assert (out / f).exists(), f
    assert {"request_id", "artefact_id", "action"} <= set(df.columns)
    assert not df.duplicated(["request_id", "artefact_id"]).any()
    assert set(df.action) <= {"delete", "retain_legal_hold", "escalate"}


def test_idempotent(tmp_path):
    assert _run(tmp_path / "r1") == 0
    assert _run(tmp_path / "r2") == 0
    assert (tmp_path / "r1/actions.csv").read_text() == (tmp_path / "r2/actions.csv").read_text()


def test_legal_hold_never_deleted(poc):
    _, df = poc
    by_id = _graph()
    held = {"a0043", "a0099"} | _descendants(by_id, "a0043") | _descendants(by_id, "a0099")
    wrong = df[df.artefact_id.isin(held) & (df.action == "delete")]
    assert wrong.empty, wrong
    assert _action(df, "DR-002", "a0043") == "retain_legal_hold"
    assert _action(df, "DR-005", "a0099") == "retain_legal_hold"


def test_exceptions_escalated(poc):
    _, df = poc
    assert _action(df, "DR-008", "a0156") == "escalate"          # ACL vendor-x
    for aid in ["a0075", "a0079", "a0083"]:                        # origin_known = false
        assert _action(df, "DR-004", aid) == "escalate"
    assert _action(df, "DR-001", "a0011") == "escalate"           # frame chung subj_00 + subj_01


def test_no_cross_subject_delete(poc):
    _, df = poc
    by_id = _graph()
    reqs = {r["request_id"]: r["subject_id"] for r in json.loads((FIX / "requests.json").read_text(encoding="utf-8"))}
    for r in df.itertuples():
        assert reqs[r.request_id] in by_id[r.artefact_id]["subject_ids"], (r.request_id, r.artefact_id)


def test_full_lineage_dr001(poc):
    _, df = poc
    for aid in ["a0015", "a0016", "a0019", "a0023", "a0027"]:    # raw, backup, 3 embedding
        assert _action(df, "DR-001", aid) == "delete"


def test_audit_covers_every_action(poc):
    out, df = poc
    lines = (out / "audit_log.jsonl").read_text(encoding="utf-8").splitlines()
    logged = {json.loads(l).get("item_id") for l in lines if l.strip()}
    assert set(df.artefact_id) <= logged

def test_reviewer_cannot_delete_legal_hold(tmp_path):
    out = tmp_path / "rev"
    assert _run(out) == 0
    q = pd.read_csv(out / "review_queue.csv", dtype=str).fillna("")
    i = q.index[q.suggested_action == "retain_legal_hold"][0]
    q.loc[i, ["reviewer", "reviewer_decision", "override_value"]] = ["Tester", "edit", "delete"]
    q.to_csv(out / "review_queue_reviewed.csv", index=False)
    assert _run(out) != 0                                            # pipeline phải dừng


def test_review_all_escalations_gives_go(tmp_path):
    out = tmp_path / "go"
    assert _run(out) == 0
    assert json.loads((out / "decision.json").read_text(encoding="utf-8"))["status"] == "HOLD"
    q = pd.read_csv(out / "review_queue.csv", dtype=str).fillna("")
    esc = q.suggested_action == "escalate"
    q.loc[esc, "reviewer"] = "Tester (DPO)"
    q.loc[esc, "reviewer_decision"] = "approve"
    q.to_csv(out / "review_queue_reviewed.csv", index=False)
    assert _run(out) == 0
    assert json.loads((out / "decision.json").read_text(encoding="utf-8"))["status"] == "GO"

def test_baseline_misses_lineage_and_hold(tmp_path):
    out = tmp_path / "base"
    assert _run(out, pipeline="src.baseline_t08") == 0
    df = pd.read_csv(out / "actions.csv")
    assert len(df) == 10                                             # chỉ 1 raw / request
    assert _action(df, "DR-002", "a0043") == "delete"               # xóa nhầm legal hold
    assert "a0016" not in set(df.artefact_id)                       # sót backup DR-001
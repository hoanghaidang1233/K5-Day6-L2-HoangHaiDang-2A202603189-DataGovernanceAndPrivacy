"""YOUR PoC PIPELINE — this is the file your group owns.

Lab contract = 5 automated steps, run by `python -m src.main ...`:
    01 load      input + provenance check (manifest.csv)
    02 process   your algorithm / model (the research part)
    03 rank      prioritise for a fixed review budget B
    04 review    human-in-the-loop: queue -> reviewer decisions -> overrides + audit log
    05 decide    GO / HOLD / REWORK (or track equivalent) with reason_code + owner, write outputs

Required output: out/<pred_file> with the columns listed for your track in
docs/tracks/TXX.md (also in src/metrics/tracks.py::TRACKS). Then evaluate with
    python -m src.evaluate --track tXX --pred out/<pred_file>

Rules: never read fixtures/*/gt/ here (the GT guard will stop you); seed everything via ctx.rng;
no network calls; fail loudly on missing models instead of downloading.
See src/examples/random_baseline.py for a minimal end-to-end wiring example.
"""
from __future__ import annotations

import json
from collections import deque

import pandas as pd

from .common.context import RunContext
from .common.decision import Decision, write_decision
from .common.manifest import verify_manifest
from .common.review import apply_overrides, load_review_decisions, make_review_queue, write_review_queue
from .metrics.tracks import get_track


# ---------------------------------------------------------------- 01 load
def load(ctx: RunContext) -> dict:
    manifest = ctx.input_dir / "manifest.csv"
    problems = verify_manifest(manifest) if manifest.exists() else [{"problem": "no_manifest"}]
    if problems:
        ctx.audit.log(actor="pipeline", action="provenance_problems", item_id=str(manifest), after=problems[:20])
        if ctx.config.get("strict_provenance", True):
            raise ValueError(f"provenance check failed ({len(problems)} problems), first: {problems[:3]}")
    data = {"manifest": pd.read_csv(manifest) if manifest.exists() else None}
    data["requests"] = json.loads((ctx.input_dir / "requests.json").read_text(encoding="utf-8"))
    data["artefacts"] = json.loads((ctx.input_dir / "data_graph.json").read_text(encoding="utf-8"))["artefacts"]
    for req in data["requests"]:
        if not req.get("subject_id") or not req.get("deadline"):
            raise ValueError(f"request không hợp lệ: {req}")
        ctx.audit.log(actor="pipeline", action="request_received", item_id=req["request_id"], after=req)
    return data

# ---------------------------------------------------------------- 02 process
def build_graph(artefacts: list[dict]) -> dict:
    """artefact_id -> artefact, để tra cứu nhanh."""
    return {a["artefact_id"]: a for a in artefacts}


def ancestors(by_id: dict, aid: str) -> list[str]:
    """BFS ngược theo parent_ids: trả về mọi tổ tiên của artefact."""
    seen: list[str] = []
    queue = deque(by_id[aid]["parent_ids"])
    while queue:
        p = queue.popleft()
        if p in seen or p not in by_id:
            continue
        seen.append(p)
        queue.extend(by_id[p]["parent_ids"])
    return seen


def hold_of(by_id: dict, aid: str) -> tuple[str | None, str | None]:
    """Legal hold của chính artefact hoặc của bất kỳ tổ tiên nào (hold lan xuống con cháu)."""
    for x in [aid] + ancestors(by_id, aid):
        if by_id[x].get("legal_hold"):
            return by_id[x]["legal_hold"], x
    return None, None


def lineage_path(by_id: dict, aid: str) -> str:
    """Chuỗi bằng chứng dạng raw > frame > crop > embedding."""
    path = [aid]
    while by_id[path[-1]]["parent_ids"]:
        path.append(by_id[path[-1]]["parent_ids"][0])
    return " > ".join(reversed(path))

def classify(req: dict, art: dict, hold_id: str | None) -> tuple[str, str]:
    """Bảng luật ưu tiên trong metric_contract.md — luật trên thắng luật dưới."""
    if hold_id:
        return "retain_legal_hold", "LEGAL_HOLD"
    if "data-platform" not in art["acl"]:
        return "escalate", "NO_PERMISSION"
    if not art["origin_known"]:
        return "escalate", "UNKNOWN_ORIGIN"
    if len(art["subject_ids"]) > 1:
        return "escalate", "MULTI_SUBJECT"
    if not req.get("identity_verified", False):
        return "escalate", "ID_NOT_VERIFIED"
    return "delete", "SUBJECT_ERASURE"

SCORE = {"retain_legal_hold": 3, "escalate": 2, "delete": 0}

def process(ctx: RunContext, data: dict) -> pd.DataFrame:
    by_id = build_graph(data["artefacts"])
    rows = []
    for req in data["requests"]:
        subj = req["subject_id"]
        for aid in sorted(by_id):                      # sorted -> chạy 2 lần ra cùng thứ tự
            art = by_id[aid]
            if subj not in art["subject_ids"]:         # chỉ đụng artefact của đúng subject
                continue
            hold_id, hold_src = hold_of(by_id, aid)
            action, reason = classify(req, art, hold_id)
            rows.append({
                "key": f"{req['request_id']}:{aid}",
                "request_id": req["request_id"], "artefact_id": aid, "action": action,
                "reason_code": reason, "type": art["type"], "uri": art["uri"],
                "hold_id": hold_id or "", "hold_source": hold_src or "",
                "lineage_path": lineage_path(by_id, aid), "deadline": req["deadline"],
                "score": SCORE[action],
            })
            ctx.audit.log(actor="pipeline", action=f"dry_run_{action}", item_id=aid,
                          reason=reason, request_id=req["request_id"], hold_id=hold_id)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- 03 rank
def rank(ctx: RunContext, scored: pd.DataFrame) -> pd.DataFrame:
    budget = int(ctx.param("budget", 60))
    q = make_review_queue(scored, id_col="key", score_col="score", budget=budget,
                          reason_col="reason_code", action_col="action", slice_col="type")
    write_review_queue(q, ctx.out("review_queue.csv"))
    n_exc = int((scored.action != "delete").sum())
    if n_exc > budget:
        ctx.audit.log(actor="pipeline", action="budget_too_small", item_id="review_queue",
                      after={"exceptions": n_exc, "budget": budget})
    return q


# ---------------------------------------------------------------- 04 review
def review(ctx: RunContext, scored: pd.DataFrame, queue: pd.DataFrame) -> pd.DataFrame:
    reviewed_path = ctx.output_dir / "review_queue_reviewed.csv"
    if not reviewed_path.exists():
        scored["reviewed"] = False
        return scored
    reviewed = load_review_decisions(reviewed_path)
    # CHẶN CỨNG: reviewer không được đổi artefact đang legal hold thành delete
    held = set(scored.loc[scored.action == "retain_legal_hold", "key"])
    bad = reviewed[reviewed.item_id.isin(held) & (reviewed.override_value.str.strip().str.lower() == "delete")]
    if len(bad):
        raise ValueError(f"Không được đổi artefact đang legal hold thành delete: {bad.item_id.tolist()}")
    final = apply_overrides(scored, reviewed, id_col="key", value_col="action", audit=ctx.audit)
    for r in reviewed.itertuples(index=False):
        if r.reviewer_decision in {"approve", "escalate"}:
            ctx.audit.log(actor=r.reviewer, action=f"review:{r.reviewer_decision}",
                          item_id=r.item_id.split(":")[1], reason=r.review_note)
    final["reviewed"] = final.key.isin(set(reviewed.item_id))
    ctx.stats["n_reviewed"] = len(reviewed)
    return final

# ---------------------------------------------------------------- 05 decide
def decide(ctx: RunContext, final: pd.DataFrame) -> Decision:
    # bằng chứng xóa giả lập (không xóa gì thật)
    with open(ctx.out("deletion_evidence.jsonl"), "w", encoding="utf-8") as f:
        for r in final.itertuples(index=False):
            f.write(json.dumps({"request_id": r.request_id, "artefact_id": r.artefact_id, "uri": r.uri,
                                "action": r.action, "reason_code": r.reason_code,
                                "dry_run": bool(ctx.param("dry_run", True))}, ensure_ascii=False) + "\n")
    pending = final[(final.action == "escalate") & (~final.reviewed)]
    owner = ctx.config.get("owner", "TODO-owner")
    counts = final.action.value_counts().to_dict()
    if len(pending):
        return Decision(status="HOLD", reason_code="PENDING_EXCEPTIONS",
                        reason=f"{len(pending)} artefact escalate chưa được người duyệt xử lý",
                        owner=owner, scope=ctx.track, metrics=counts)
    return Decision(status="GO", reason_code="ALL_RESOLVED",
                    reason="Mọi ngoại lệ đã được duyệt; delete chạy ở chế độ dry-run",
                    owner=owner, scope=ctx.track, metrics=counts)

def run(ctx: RunContext) -> None:
    spec = get_track(ctx.track)
    if spec.track != "t08":
        raise NotImplementedError(f"pipeline này chỉ cài cho t08, không hỗ trợ {spec.track}")
    data = load(ctx)
    scored = process(ctx, data)
    ctx.stats["n_items"] = len(scored)
    queue = rank(ctx, scored)
    final = review(ctx, scored, queue)
    final[spec.pred_columns + [c for c in final.columns if c not in spec.pred_columns]].to_csv(ctx.out(spec.pred_file), index=False)
    write_decision(decide(ctx, final), ctx.out("decision.json"), ctx.out("decision_log.csv"))

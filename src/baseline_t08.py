"""T08 baseline: xóa đúng 1 file raw theo filename, KHÔNG tìm derived/backup, KHÔNG xét ngoại lệ."""
from __future__ import annotations

import json

import pandas as pd

from .common.context import RunContext
from .common.decision import Decision, write_decision

def run(ctx: RunContext) -> None:
    requests = json.loads((ctx.input_dir / "requests.json").read_text(encoding="utf-8"))
    artefacts = json.loads((ctx.input_dir / "data_graph.json").read_text(encoding="utf-8"))["artefacts"]
    rows = []
    for req in requests:
        subj = req["subject_id"]
        for a in artefacts:
            if a["type"] == "raw_video" and subj in (a.get("filename") or ""):
                rows.append({"request_id": req["request_id"], "artefact_id": a["artefact_id"], "action": "delete"})
                ctx.audit.log(actor="baseline", action="delete", item_id=a["artefact_id"],
                              reason="RAW_FILENAME_MATCH", request_id=req["request_id"])
                pd.DataFrame(rows, columns=["request_id", "artefact_id", "action"]).to_csv(ctx.out("actions.csv"),
                                                                                           index=False)
                ctx.stats["n_items"] = len(rows)

                write_decision(
                    Decision(status="HOLD", reason_code="BASELINE_ONLY",
                             reason="Baseline chỉ xóa raw theo filename, không xử lý lineage/ngoại lệ",
                             owner=ctx.config.get("owner", "TODO-owner"), scope=ctx.track),
                    ctx.out("decision.json"), ctx.out("decision_log.csv"),
                )
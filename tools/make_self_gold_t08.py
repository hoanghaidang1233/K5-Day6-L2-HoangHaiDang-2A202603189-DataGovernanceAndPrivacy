"""Tạo bộ gold NỘI BỘ cho T08 theo bảng luật trong reports/metric_contract.md.

Cài độc lập với src/pipeline.py: hold lan XUÔI từ cha xuống con (pipeline thì tra NGƯỢC lên tổ tiên).
Đây KHÔNG phải ground truth của giảng viên — chỉ dùng để tự kiểm.
    python -m tools.make_self_gold_t08
"""
from __future__ import annotations

import json
import sys
from collections import deque
from pathlib import Path

import pandas as pd

FIX = Path("fixtures/t08_deletion")
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("reports/self_gold_t08.csv")


def main() -> None:
    arts = json.loads((FIX / "data_graph.json").read_text(encoding="utf-8"))["artefacts"]
    reqs = json.loads((FIX / "requests.json").read_text(encoding="utf-8"))
    by_id = {a["artefact_id"]: a for a in arts}
    children: dict[str, list[str]] = {}
    for a in arts:
        for p in a["parent_ids"]:
            children.setdefault(p, []).append(a["artefact_id"])

    # lan hold XUÔI: từ artefact bị hold đi xuống mọi con cháu
    held: set[str] = set()
    q = deque(a["artefact_id"] for a in arts if a.get("legal_hold"))
    while q:
        x = q.popleft()
        if x in held:
            continue
        held.add(x)
        q.extend(children.get(x, []))

    rows = []
    for r in reqs:
        for a in arts:
            if r["subject_id"] not in a["subject_ids"]:
                continue
            aid = a["artefact_id"]
            if aid in held:
                exp, why = "retain_legal_hold", "LEGAL_HOLD"
            elif "data-platform" not in a["acl"]:
                exp, why = "escalate", "NO_PERMISSION"
            elif not a["origin_known"]:
                exp, why = "escalate", "UNKNOWN_ORIGIN"
            elif len(a["subject_ids"]) > 1:
                exp, why = "escalate", "MULTI_SUBJECT"
            elif not r.get("identity_verified", False):
                exp, why = "escalate", "ID_NOT_VERIFIED"
            else:
                exp, why = "delete", "SUBJECT_ERASURE"
            rows.append({"request_id": r["request_id"], "artefact_id": aid, "type": a["type"],
                         "expected_action": exp, "gold_reason": why})
    df = pd.DataFrame(rows).sort_values(["request_id", "artefact_id"])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT, index=False)
    print(f"wrote {OUT}: {len(df)} rows", df.expected_action.value_counts().to_dict())


if __name__ == "__main__":
    main()

# Group Report — Day 06 Data Lab (Field-to-Release PoC)

> Điền đầy đủ, cụ thể, không viết chung chung. Mọi con số phải lấy từ file do script sinh ra
> (`reports/metrics_*.json`, `reports/table1_baseline_vs_poc.md`) — không gõ tay, không làm tròn mất đơn vị.
> 3–5 trang (hoặc README + report.md tương đương).

## 00 · Header

| Mục | Giá trị |
|---|---|
| Group ID |Không có (Làm 1 mình) |
| Challenge | T08 · Retention & Delete Request Simulator |git add reports/group_report.md
| Thành viên + vai trò | 1. Hoàng Hải Đăng — làm một mình (Lead/Integrator + Core algorithm + QA/metric + Review/report) |
| Git URL | https://github.com/hoanghaidang1233/K5-Day6-L2-HoangHaiDang-2A202603189-DataGovernanceAndPrivacy |
| Tag `demo-180m` (commit) | Không có |
| Tag `final-24h` (commit) | f3efccb |
| Deadline (giờ VN) | Demo: phút 180 buổi lab 14/11/2026 · Final: +24h (15/11/2026) |

## 01 · Problem statement

 **Ai bị ảnh hưởng:** chủ thể dữ liệu (người trong video lái xe) — nếu xóa thiếu, bản sao/embedding định danh vẫn tồn tại sau khi họ yêu cầu xóa; tổ chức — nếu xóa nhầm dữ liệu đang bị legal hold thì vi phạm nghĩa vụ lưu giữ chứng cứ.
- **Điểm nghẽn:** một yêu cầu xóa phải lần theo lineage raw → backup → frame → face_crop → embedding/annotation → training_export (212 artefact), đồng thời tôn trọng ngoại lệ (legal hold, ACL bên thứ ba, nguồn gốc không rõ, dữ liệu chung nhiều người).
- **Đầu vào → đầu ra:** `requests.json` (10 yêu cầu) + `data_graph.json` (212 artefact) → `actions.csv` (request_id, artefact_id, action ∈ {delete, retain_legal_hold, escalate}) + `audit_log.jsonl` + `review_queue.csv` + `deletion_evidence.jsonl` + `decision.json`.
- **Tiêu chí GO/HOLD (chốt 2026-11-14 15:19, commit f097a3b):** HOLD nếu còn artefact `escalate` chưa được người duyệt xử lý; GO khi mọi escalate đã có reviewer. Delete luôn chạy dry-run.
- **Giả định (cần DPO/legal xác nhận):** (1) legal hold trên raw lan xuống mọi con cháu, kể cả training export; (2) artefact chứa >1 subject → escalate, không tự xóa; (3) chỉ xử lý artefact có chứa subject của request.

## 02 · Data & provenance

| Split | N | Nguồn / link | License | Synthetic? |
|---|---|---|---|---|
| toàn bộ (T08 không chia split) | 10 request × 212 artefact → 158 cặp trong phạm vi | `fixtures/t08_deletion/` (synthetic, tools/make_fixtures.py seed 42) | CC0-1.0 | Có — mock, không có PII thật |

- **Schema:** artefact gồm `type, subject_ids, parent_ids, uri, owner, acl, legal_hold, origin_known, created_at, filename`; 7 loại: raw_video 15, backup 15, frame 45, face_crop 45, embedding 45, annotation 45, training_export 2.
- **Seed:** 42 · Không chia split: thuật toán là luật cố định, không có tham số học/tune.
- **manifest.csv verified (sha256):** có — `python -m tools.verify_manifests`; pipeline kiểm lại ở bước load (`strict_provenance: true`).
- **Dữ liệu thật:** không dùng (đề cấm xóa dữ liệu thực trong lab). GT nội bộ: `reports/self_gold_t08.csv` (sinh bởi `tools/make_self_gold_t08.py`, kiểm tay DR-002/004/008).

## 03 · Research & design

- **References & lý do:** GDPR Art. 17 và Luật 91/2025 cho phép ngoại lệ với quyền xóa → cần luật ưu tiên legal hold; Data Cards Playbook → ghi provenance/lineage làm bằng chứng. Chọn **BFS trên đồ thị lineage + bảng luật ưu tiên** vì minh bạch, giải thích được từng quyết định, idempotent, không cần model.
- **Data flow:**
  `requests.json + data_graph.json` → **01 load** (kiểm sha256, log request) → **02 trace** (lọc artefact theo subject, BFS tổ tiên để tìm legal hold, sinh `lineage_path`) → **03 dry-run + exception** (bảng luật 6 bậc, score) → **review queue top-B=60** → **04 human review** (approve/edit, chặn hold→delete) → **05 decide** (GO/HOLD + `deletion_evidence.jsonl`).
- **Bảng luật (luật trên thắng luật dưới):** LEGAL_HOLD → retain_legal_hold · NO_PERMISSION (ACL không có data-platform) → escalate · UNKNOWN_ORIGIN → escalate · MULTI_SUBJECT → escalate · ID_NOT_VERIFIED → escalate · còn lại → delete.
- **Thành phần offline:** không có model/weights; chỉ Python + pandas.
- **Trade-offs:** hold lan xuống cả training export → an toàn cho false-delete nhưng giữ lại export của 8 subject không bị hold; escalate dữ liệu nhiều người thay vì xóa → không xóa nhầm nhưng tăng tải người duyệt (16 dòng).

## 04 · Baseline vs PoC

- **Baseline** (`src/baseline_t08.py`, commit e0be309): mỗi request chỉ xóa 1 `raw_video` có filename chứa subject_id; không duyệt lineage, không xét ngoại lệ.
- **PoC** (`src/pipeline.py`, commit a8654b8 + a206a31; config `configs/tracks/t08.yaml`, budget=60, dry_run=true).
- **Cùng điều kiện:** cùng fixture, cùng seed 42, cùng gold nội bộ; cả hai chạy ở git 1e7bbfd.

**Bảng 1** — sinh bởi `python -m src.compare reports/metrics_baseline.json reports/metrics_poc.json` (chấm trên **self-gold**, không phải GT chính thức):

| Metric (unit) | Baseline | PoC | Δ & chú thích |
|---|---|---|---|
| **Primary: Deletion Completeness** (must-delete đã xóa / must-delete) | 0.0784 (8/102) | 1.0000 (102/102) | +0.9216; higher tốt. Wilson 95% CI: base [0.040, 0.147], PoC [0.964, 1.000] |
| False-delete count (target 0) | 2 | 0 | baseline xóa `a0043` (LH-2026-03), `a0099` (LH-2026-07) |
| Exception handling accuracy | 0.0000 (0/56) | 1.0000 (56/56) | baseline = 0 → không tính % cải thiện |
| Audit trace completeness | 1.0000 (10/10) | 1.0000 (158/158) | |
| Runtime / 100 items | 0.333 s | 0.071 s | CPU AMD64 Family 25, Windows 11, Python 3.13 |

> ⚠️ **PoC = 1.0 là kết quả vòng tròn:** self-gold và PoC cùng cài một bảng luật, nên số này chỉ chứng minh *code cài đúng luật*, không chứng minh *luật đúng*. Giá trị kiểm chứng thật: (1) baseline vi phạm false-delete = 0; (2) hai cách cài độc lập (gold lan hold xuôi cha→con, PoC tra ngược con→tổ tiên) cho kết quả khớp 158/158.

## 05 · Evaluation protocol

- **Primary:** Deletion Completeness = #(cặp có expected_action=delete mà pred=delete) / #(cặp có expected_action=delete). Đơn vị: cặp (request_id, artefact_id), ghép chính xác theo khóa. False-delete = #ngoại lệ bị delete + #delete ngoài phạm vi gold.
- **Budget:** B = 60, chốt trước eval (đếm 56 ngoại lệ > 40 dự kiến ban đầu → nâng lên, ghi trong metric_contract.md). Không có threshold số.
- **Độ bất định:** N = 102 must-delete; Wilson 95% CI ở Bảng 1; báo thêm từng request (mục 06) vì chỉ có 10 request.
- **Lệnh evaluate:**
  `python -m src.evaluate --track t08 --gt reports/self_gold_t08.csv --pred out/poc/actions.csv --run-name poc --out reports/metrics_poc.json`
  (chấm chính thức: giảng viên chạy với `fixtures/t08_deletion/gt/gt_actions.csv`).

## 06 · Result slices

**Theo loại artefact** (must-delete đã xóa | ngoại lệ xử lý đúng):

| Slice | N | Baseline | PoC | Ghi chú |
|---|---|---|---|---|
| raw_video | 10 | 8/8 \| 0/2 | 8/8 \| 2/2 | baseline xóa 2 raw đang hold |
| backup | 10 | 0/7 \| 0/3 | 7/7 \| 3/3 | baseline sót toàn bộ backup; `a0156` (vendor-x) → escalate |
| frame | 33 | 0/21 \| 0/12 | 21/21 \| 12/12 | |
| face_crop | 30 | 0/24 \| 0/6 | 24/24 \| 6/6 | |
| embedding | 30 | 0/21 \| 0/9 | 21/21 \| 9/9 | embedding định danh — baseline sót hết |
| annotation | 33 | 0/21 \| 0/12 | 21/21 \| 12/12 | |
| training_export | 12 | 0/0 \| 0/12 | 0/0 \| 12/12 | luôn bị hold (chứa crop của subj_03/subj_07) |

**Theo request** (Completeness): DR-001 1/14 → 14/14 · DR-003 1/12 → 12/12 · DR-004 1/11 → 11/11 · DR-006 1/12 → 12/12 · DR-007 1/14 → 14/14 · DR-008 1/13 → 13/13 · DR-009 1/12 → 12/12 · DR-010 1/14 → 14/14 · DR-002, DR-005: 0 must-delete (toàn bộ bị legal hold).

- **Failure case 1 — xóa nhầm dữ liệu thuộc ngoại lệ:** baseline ghi `DR-002,a0043,delete` và `DR-005,a0099,delete`; metadata: `a0043.legal_hold = LH-2026-03`, `a0099.legal_hold = LH-2026-07`. Root cause: baseline không đọc trường `legal_hold`. PoC sửa bằng luật #1 + chặn cứng ở bước review. Bằng chứng: `test_legal_hold_never_deleted`, `test_reviewer_cannot_delete_legal_hold`.
- **Failure case 2 — chỉ xóa raw, giữ bản sao và embedding định danh:** với DR-001 baseline chỉ xóa `a0015`, còn sót backup `a0016`, frame `a0017/a0021/a0025`, embedding `a0019/a0023/a0027`, annotation và export `a0212`. Root cause: không duyệt lineage. PoC: BFS + cột `lineage_path` (vd `a0015 > a0017 > a0018 > a0019`). Bằng chứng: `test_full_lineage_dr001`, `test_baseline_misses_lineage_and_hold`.
- **Latency:** PoC 0.112 s cho 158 cặp (≈0.7 ms/cặp); baseline 0.033 s cho 10 dòng. Máy: Windows 11, AMD64 Family 25, Python 3.13.0.

## 07 · Human & decision

- **Review queue:** `out/poc/review_queue.csv`, B = 60, sắp theo score (retain_legal_hold = 3 > escalate = 2 > delete = 0) → cả 56 ngoại lệ nằm trong queue; nếu ngoại lệ > B thì log `budget_too_small`.
- **Override + audit:** reviewer điền `review_queue_reviewed.csv`; `edit` + `override_value` → đổi action qua `apply_overrides`, log `before/after/actor`; `approve` → log `review:approve`. **Chặn cứng:** đổi artefact đang legal hold thành delete → pipeline dừng (exit 2). Bằng chứng: `reports/review_queue_reviewed_t08.csv` — 16 dòng escalate do Hoàng Hải Đăng (DPO) duyệt (12 MULTI_SUBJECT, 3 UNKNOWN_ORIGIN, 1 NO_PERMISSION), mỗi dòng có review_note.
- **Decision policy:** HOLD / `PENDING_EXCEPTIONS` khi còn escalate chưa duyệt → GO / `ALL_RESOLVED` khi đã duyệt hết; owner = "Hoang Hai Dang (Data Owner)". Trước review: HOLD; sau review: GO (commit 1e7bbfd).
- **Khi nào HOLD:** còn escalate chưa có người duyệt; legal hold không bao giờ được tự động xóa — chỉ xóa sau khi hold được gỡ (cần ghi hẹn xóa).

## 08 · Reproduce & tests

```bash
python -m venv .venv && .venv\Scripts\pip install -r requirements.txt
python -m src.main --config configs/lab.yaml --output out/poc --run-name poc --offline --seed 42
python -m src.main --track t08 --pipeline src.baseline_t08 --output out/baseline --run-name baseline --offline --seed 42
python -m tools.make_self_gold_t08
python -m src.evaluate --track t08 --gt reports/self_gold_t08.csv --pred out/poc/actions.csv --run-name poc --out reports/metrics_poc.json
pytest -q
```

- **pytest:** 57 passed, 39 skipped (skip = test cần `gt/` của giảng viên), 0 failed; riêng `tests/test_t08.py` 10/10 passed (idempotency, legal hold, ACL/unknown origin, phạm vi subject, lineage DR-001, audit, review gate, baseline).
- **Máy:** Windows 11 (10.0.26200), AMD64 Family 25, Python 3.13.0 · git sha: f3efccb

## 09 · Limitations & ethics

- **Pháp lý / PII:** dữ liệu mock CC0, không có PII thật; lab không xóa dữ liệu thực (dry_run = true).
- **Chưa biết / rủi ro:** điểm chính thức có thể thấp hơn nếu GT hiểu khác giả định 1 (hold lan sang training export `a0211`, `a0212` → PoC giữ export cho cả 8 subject không bị hold) hoặc giả định 2 (frame/annotation chung như `a0011`, `a0014`, `a0067`, `a0070` → escalate thay vì xóa). Self-gold không phát hiện được sai ở cấp luật.
- **Scale 10×:** BFS tổ tiên gọi cho từng cặp → nên cache tập artefact bị hold một lần; cần index subject → artefact thay vì quét 212 artefact mỗi request.
- **Không tuyên bố tuân thủ:** đây là mô phỏng workflow; không tuyên bố "đạt GDPR / Luật 91/2025 / NĐ 356/2025". Việc legal hold có chặn quyền xóa hay không và thời hạn giữ cần DPO/legal xác nhận.
- **Cải tiến:** ghi ngày hẹn xóa khi hold hết hạn; SLA theo deadline; chạy tiếp được khi bị ngắt (checkpoint theo request_id); tách/che phần của subject trong frame chung thay vì escalate.

## 10 · Team contribution

**Bảng 2 · Individual contribution evidence** (đối chiếu với `git shortlog -sne --all` và `reports/team_contributions.csv`)

| Học viên | Git author/email | Module / vai trò | Commit SHA / diff chứng minh | Trình bày? |
|---|---|---|---|---|
| Hoàng Hải Đăng (solo) | Hoàng Hải Đăng <d0512203hhd@gmail.com> | Toàn bộ: config, contract, baseline, PoC, tests, review, metrics, report | 4f34e2f · f097a3b · e0be309 · a8654b8 · a206a31 · c648506 · 1e7bbfd · 336b0a7 · 62ca3e6 | Có |

## 11 · References

| URL | Ngày xem | Ý nghĩa |
|---|---|---|
| https://thuvienphapluat.vn/van-ban/Bo-may-hanh-chinh/Law-91-2025-QH15-Personal-Data-Protection-665440.aspx (Luật Bảo vệ dữ liệu cá nhân 91/2025/QH15, ban hành 26/06/2025, hiệu lực 01/01/2026) | 14/11/2026| Quyền xóa dữ liệu cá nhân và ngoại lệ theo luật Việt Nam |
| https://thuvienphapluat.vn/van-ban/Quyen-dan-su/Nghi-dinh-356-2025-ND-CP-huong-dan-Luat-Bao-ve-du-lieu-ca-nhan-687428.aspx (Nghị định 356/2025/NĐ-CP, ban hành 31/12/2025; bản Bộ Tư pháp: https://vbpl.moj.gov.vn/TW/Lists/vbpq/Attachments/187276/ND.356.2025.doc) | 14/11/2026 | Hướng dẫn thi hành, nghĩa vụ của bên kiểm soát dữ liệu |
| https://sites.research.google/datacardsplaybook/ | 14/11/2026 | Ghi lineage/provenance làm bằng chứng cho quyết định dữ liệu |

---

**Bảng 3 · Checklist chốt khi nộp**

- [x] Link repo và tag cuối cùng trong README; input mẫu không chứa PII thật.
- [x] Đúng 1 lệnh run, 1 lệnh evaluate, 1 lệnh tests; không cần download khi chạy.
- [x] Có baseline/PoC metrics, ≥1 failure case, reviewer evidence, decision log.
- [x] Có license/source provenance, data splits, seed, offline environment, GT/eval N.
- [x] Mỗi thành viên có ≥1 commit thực chất và được nêu minh bạch trong bảng trên.

# Metric contract (khai báo bắt buộc — chốt ở phút 30, trước khi chạy eval)

> Các metric chuẩn đã được cài trong `src/metrics/tracks.py`. File này là nơi nhóm **cam kết** cách dùng chúng.
> Không đổi testset, threshold hay trọng số sau khi đã thấy kết quả eval.

| Mục | Khai báo của nhóm |
|---|---|
| Track | T08 |
| Primary metric (tên đúng như evaluator in ra) |Deletion Completeness|
| Tăng tốt hay giảm tốt? | higher  |
| Đơn vị đánh giá (image / region / pair / release / batch / case …) |cặp (request_id, artefact_id) |
| Công thức + numerator / denominator |số artefact có expected_action=delete mà pipeline gán `delete` / tổng số artefact có expected_action=delete |
| Cách ghép prediction ↔ GT (IoU? id? tolerance?) |ghép chính xác theo khóa (request_id, artefact_id); mỗi cặp chỉ 1 dòng |
| Candidate pool cố định | 10 request trong `requests.json` × 212 artefact trong `data_graph.json` (fixture `t08_deletion`, hash khớp manifest) |
| Review budget B / K | B = 60 (đếm thực tế 56 ngoại lệ > 40 dự kiến; nâng trước khi chạy eval)|
| Seed | 42 |
| Threshold policy (chọn trên dev như thế nào) |Không có threshold số. Dùng bảng luật ưu tiên cố định bên dưới, chốt trước khi chạy eval |
| Eval split (không dùng để tune) |Toàn bộ 10 request (không có split dev/heldout cho T08); GT chỉ giảng viên chấm|
| Chỉ số lỗi xấu (miss / false alarm / false reject) |**False-delete count = 0 (ràng buộc cứng)**: xóa artefact thuộc ngoại lệ hoặc ngoài phạm vi request. Lỗi phụ: miss (artefact phải xóa nhưng bị bỏ sót) |
| Slices sẽ báo cáo (≥2) |(1) theo expected_action: delete / retain_legal_hold / escalate; (2) theo loại artefact: raw_video / backup / frame / face_crop / embedding / annotation / training_export; (3) theo request|
| Cách báo độ bất định khi N nhỏ (Wilson CI / bootstrap) |N=10 request → báo từng request (case by case) + Wilson 95% CI cho Completeness |
| Ngày giờ chốt + người chốt |2026-11-14 HH:MM — Hoang Hai Dang (Data Owner) |

Nếu baseline = 0 thì ghi rõ "baseline = 0" thay vì phần trăm cải thiện.
## Bảng luật ưu tiên (chốt trước khi code; luật trên thắng luật dưới)

| # | Điều kiện | Action | reason_code |
|---|---|---|---|
| 1 | Artefact hoặc tổ tiên có `legal_hold` | `retain_legal_hold` | `LEGAL_HOLD` |
| 2 | `acl` không chứa `data-platform` | `escalate` | `NO_PERMISSION` |
| 3 | `origin_known == false` | `escalate` | `UNKNOWN_ORIGIN` |
| 4 | Artefact chứa > 1 subject | `escalate` | `MULTI_SUBJECT` |
| 5 | Request có `identity_verified == false` | `escalate` | `ID_NOT_VERIFIED` |
| 6 | Còn lại | `delete` | `SUBJECT_ERASURE` |

## Giả định (cần DPO/legal xác nhận — không phải tư vấn pháp lý)

1. Legal hold trên raw lan xuống toàn bộ con cháu (backup, frame, crop, embedding, annotation).
2. Training export chứa nhiều subject → `escalate`, không tự xóa cả file.
3. Chỉ xử lý artefact có chứa subject của request; không gán action cho artefact của người khác.

## Kiểm tra nội bộ (repo học viên không có `gt/`)

Bộ gold nội bộ = các test trong `tests/test_t08.py` (legal hold, ACL, unknown origin, phạm vi subject, lineage đầy đủ DR-001, idempotency).
# Kết quả đợt đối chứng 20 case — review_v4 (09/11/2026)

v4 chạy lại đúng thiết kế của v3: cùng 20 case, cùng các agent, công cụ, bộ chặn vòng gọi công cụ và hai cấu hình ngân sách. Điểm khác duy nhất là chính sách ngân sách v2 (`templates/multi_agent_v2/budget.json`): trần đầu vào nâng từ 6.000 lên **12.000 token** mỗi yêu cầu. Chính sách v2 dùng sổ ngân sách riêng với trần 2 USD do người dùng duyệt. Kết quả v3 và sổ ngân sách cũ giữ nguyên (`docs/agent_comparison_v3_results_2026-11-09.md`).

Lý do nâng trần: ở v3, mọi lượt dừng đều do yêu cầu vượt 6.000 token. Yêu cầu bị chặn lớn nhất ở v3 là 8.372 token. Yêu cầu lớn nhất được gửi ở v4 là 8.374 token, vẫn dưới trần mới.

- Đã chạy 80/80 lượt. Theo quy tắc chung của mọi đợt chạy, lượt bị gián đoạn do lỗi kỹ thuật không liên quan đến phương án được chạy lại một lần với mã lượt chạy riêng. Đợt này có 1 lượt thay thế (`replacements/`); lượt gốc vẫn được lưu.
- Phát lại offline khớp cả 80 lượt và lượt thay thế (263 yêu cầu), 0 lời gọi API mới.
- Chi phí suy luận theo sổ ngân sách v2: **0,1432 USD**, cộng 0,0021 USD đang giữ chỗ cho một yêu cầu không có biên nhận. Cộng dồn toàn dự án: khoảng 0,139 USD ở sổ v1 và 0,143 USD ở sổ v2.

## Kết quả theo phương án

| Phương án | Cấu hình | Có đầu ra công bố | Không đạt | Nêu đủ cảnh báo (8 case có cảnh báo) | Khớp nguồn | Phản hồi mô hình | Chi phí USD | Thời gian trung vị |
| --- | --- | ---: | --- | ---: | ---: | ---: | ---: | ---: |
| Quy tắc cố định | — | 20/20 | — | 8/8 | 100% | 0 | 0 | — |
| Một agent | native (4 lượt) | 19/20 | 1 không đủ kết luận | 7/8 | 100% | 36 | 0,0192 | 6,1 giây |
| Một agent | matched (4 lượt) | 19/20 | 1 không đủ kết luận | 7/8 | 100% | 36 | 0,0122 | 5,4 giây |
| Ba agent | native (12 lượt) | **20/20** | — | **8/8** | 100% | 105 | 0,0685 | 18,5 giây |
| Ba agent | matched (4 lượt) | 18/20 | 2 chưa nêu đủ cảnh báo | 6/8 | 100% | 80 | 0,0425 | 16,7 giây |

Không có mục nào bị Evidence Verifier loại. Vòng bổ sung cảnh báo bị bỏ sót được dùng 3 lần ở nhánh một agent và đều hoàn tất. Ở nhánh ba agent, matched, vòng này không chạy được vì đã hết lượt (2 lượt).

## So sánh với v3

| Phương án | Có đầu ra v3 → v4 | Nêu đủ cảnh báo v3 → v4 |
| --- | --- | --- |
| Một agent, native | 16 → 19 | 5 → 7 |
| Một agent, matched | 16 → 19 | 4 → 7 |
| Ba agent, native | 11 → 20 | 4 → 8 |
| Ba agent, matched | 10 → 18 | 6 → 6 |

## Đọc kết quả

1. **Giới hạn token là nguyên nhân chính khiến v3 dừng.** Nâng trần lên 12.000 token loại bỏ hoàn toàn các lượt dừng do vượt trần. Hệ ba agent hưởng lợi nhiều nhất, vì gói kiểm chứng của Evidence Verifier là yêu cầu lớn nhất.
2. **Với đủ tài nguyên (native), hệ ba agent đạt độ hoàn thành và độ đầy đủ cảnh báo bằng quy tắc cố định (20/20; 8/8),** và cao hơn một agent (19/20; 7/8). Chênh lệch giữa ba agent và một agent nhỏ: một case về hoàn thành và một case về cảnh báo, trên 20 case chạy một lần. Chênh lệch này chưa đủ để khẳng định ưu thế tổng quát.
3. **Chi phí của sự phối hợp.** Ba agent cần khoảng 2,9 lần số phản hồi mô hình, khoảng 3,6 lần chi phí và khoảng 3 lần thời gian so với một agent. Khi bị giới hạn 4 lượt như một agent (matched), ba agent đạt kém hơn: 18/20 và 6/8. Nguyên nhân là ba vai trò phải chia nhau 4 lượt nên không còn lượt để bổ sung cảnh báo.
4. **Hai lượt "không đủ kết luận" của một agent** có chung một nguyên nhân: mô hình gọi công cụ thống kê nhóm cho một bằng chứng không phải phép so sánh nhóm, và công cụ từ chối (`Not a group comparison`). Đây là lỗi dùng công cụ của mô hình, được giữ nguyên trong kết quả.
5. **Khớp nguồn 100%** vẫn là kết quả của thiết kế ràng buộc bằng chứng, không chứng minh AI tốt hơn quy tắc. Quy tắc cố định đạt độ đầy đủ cảnh báo tối đa với chi phí 0. Giá trị bổ sung của AI, nếu có, phải được đánh giá qua chất lượng giải thích bằng chấm định tính độc lập.
6. **Phạm vi kết luận:** 20 case phát triển, đã được xem trước, mỗi phương án chạy một lần. Đây không phải tập kiểm chứng độc lập; chưa đo độ ổn định qua nhiều lần chạy; không phải độ chính xác phát hiện tắc nghẽn.

## Dữ liệu và kiểm chứng

- `data/experiments/olist_agent_comparison_v4/`: `slots/`, `replacements/`, `requests.jsonl`, `token_counts.jsonl`, `summary.json`, `plan.json`, `manifest.json`.
- Kiểm chứng: `scripts/06_agent_evaluation/live/run_replacement_v4.py --verify` (phát lại toàn bộ 80 lượt và lượt thay thế, offline).
- Đã cập nhật theo v4: dashboard (20 giải thích ba agent), `docs/latency_summary.json`, phiếu chấm ẩn danh `data/experiments/olist_agent_rating_v1/`. Bản phiếu dựng từ v3 được lưu riêng ở `olist_agent_rating_v1_from_v3/` và không dùng để chấm.

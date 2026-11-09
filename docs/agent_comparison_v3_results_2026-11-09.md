# Kết quả đợt đối chứng 20 case (review_v3) — 09/11/2026

Đợt chạy được người dùng duyệt ngày 09/11/2026, với trần 0,65 USD. Model `gpt-6-luna`; 20 case phát triển đã cố định (`olist_agent_comparison_v1/cohort.json`); chế độ `assumed_static_context_at_purchase`; trần mỗi yêu cầu 6.000 token đầu vào và 1.200 token đầu ra. Mỗi lượt chỉ chạy một lần: không chạy lại khi lỗi, không chọn đầu ra tốt nhất trong nhiều lần.

- Đã chạy 80/80 lượt AI. Phát lại offline (`--verify`) khớp cả 80 lượt, 263 yêu cầu, 0 lời gọi API mới.
- Chi phí suy luận thực tế: **0,113 USD**. Hạn mức local còn 4,861 USD.
- Dữ liệu gốc: `data/experiments/olist_agent_comparison_v3/` (`slots/`, `requests.jsonl`, `token_counts.jsonl`, `summary.json`, `plan.json`, `manifest.json`).

## Kết quả theo phương án

| Phương án | Cấu hình | Có đầu ra công bố | Dừng do vượt trần token | Chưa nêu đủ cảnh báo | Khớp nguồn (trên mục đã công bố) | Nêu đủ cảnh báo ở 8 case có cảnh báo | Phản hồi mô hình | Chi phí USD | Thời gian trung vị (đầu ra công bố) |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Quy tắc cố định | — | 20/20 | 0 | 0 | 100% | 8/8 | 0 | 0 | — |
| Một agent | native (4 lượt) | 16/20 | 3 | 1 | 100% | 5/8 | 31 | 0,0213 | 4,5 giây |
| Một agent | matched (4 lượt) | 16/20 | 3 | 1 | 100% | 4/8 | 33 | 0,0086 | 4,5 giây |
| Ba agent | native (12 lượt) | 11/20 | 9 | 0 | 100% | 4/8 | 84 | 0,0540 | 17,9 giây |
| Ba agent | matched (4 lượt) | 10/20 | 9 | 1 | 100% | 6/8 | 71 | 0,0293 | 13,8 giây |

Kết quả của quy tắc cố định lấy từ `olist_agent_comparison_v1` (chạy offline trước đó). "Có đầu ra công bố" gồm các trạng thái `code_checked` (một agent, kiểm tra bằng code) và `evidence_checked` (ba agent, có thêm bước Evidence Verifier). Không có mục nào bị Verifier loại.

## Đọc kết quả

1. **Nguyên nhân dừng.** Mọi lượt dừng đều do yêu cầu vượt trần 6.000 token và bị hủy trước suy luận, không tốn chi phí cho yêu cầu đó. Phân theo vai trò, các yêu cầu bị hủy gồm: Evidence Verifier 29 (20 ở native, 9 ở matched), Context Analyst 1, một agent 14.
   Gói của Verifier chứa toàn bộ nhận định, bằng chứng và phần tính lại số liệu, nên lớn nhất. Yêu cầu của Verifier lại là yêu cầu nộp bắt buộc, nên bộ chặn vòng công cụ không còn cách nào thu nhỏ nó. Bộ chặn đã có tác dụng ở chỗ khác: buộc nộp khi không có bằng chứng mới, giữ lượt cho các vai trò sau, gửi lại một lần khi vượt trần. Tuy vậy, nó không giải quyết được trường hợp bản thân gói kiểm chứng đã quá lớn.
2. **Khớp nguồn 100% ở mọi phương án** là kết quả của thiết kế: agent chỉ chọn mục từ danh mục có nguồn, và code kiểm tra từng giá trị. Con số này chứng minh ràng buộc với bằng chứng hoạt động đúng. Nó không chứng minh AI tốt hơn quy tắc.
3. **Độ đầy đủ cảnh báo.** Quy tắc cố định nêu đủ cả 8/8 case có cảnh báo. Các phương án AI chỉ đạt 4–6/8, chủ yếu vì có lượt bị dừng. Trong các lượt ba agent đã công bố được, không có lượt nào thiếu cảnh báo ở cấu hình native.
4. **Chi phí và thời gian.** Ba agent cần khoảng 2,2–2,7 lần số phản hồi mô hình và gấp 3–4 lần thời gian so với một agent. Tổng chi phí của cả hai đều rất thấp.
5. **Kết luận được phép rút ra.** Với giới hạn token hiện tại, đợt đối chứng *không* cho thấy hệ ba agent vượt trội về độ hoàn thành hoặc độ đầy đủ. Lợi thế về chất lượng giải thích, nếu có, chỉ đánh giá được qua phần chấm định tính độc lập đang chờ thực hiện. Đây là 20 case phát triển, đã được xem trước, chạy một lần. Kết quả không phải tập kiểm chứng độc lập, chưa đo được độ ổn định qua nhiều lần chạy, và không phải độ chính xác phát hiện tắc nghẽn.

## Hướng xử lý (chưa thực hiện, cần duyệt riêng)

- **Đổi thiết kế:** chia nhỏ gói kiểm chứng của Verifier theo từng công đoạn, hoặc bỏ phần bằng chứng trùng lặp. Phải kiểm tra offline trước, và phải là một phiên bản mới.
- **Đổi giới hạn:** nâng trần token đầu vào, ví dụ lên 8.000. Đây là thay đổi chính sách ngân sách nên cần người dùng duyệt; chi phí tăng không đáng kể.

Nếu chạy lại, phải ghi là phiên bản và đợt chạy mới. Không ghi đè kết quả của đợt này.

## Đầu ra đi kèm

- Phiếu chấm ẩn danh đầy đủ: `data/experiments/olist_agent_rating_v1/` gồm `packet.html` và `rating_sheet_rater1.csv`, `rating_sheet_rater2.csv`. Chỉ gửi ba file này cho người chấm, **không** gửi `rating_key.json`.
- Dashboard đã nạp 11 giải thích ba agent đã kiểm chứng: `data/dashboard/index.html`.
- Độ trễ: `docs/latency_summary.json`. Giải thích ba agent mất trung vị 14,9 giây mỗi case, tính cả lượt dừng; một agent 5,0 giây.

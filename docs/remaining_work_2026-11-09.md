# Hoàn thiện các hạng mục còn lại — 09/11/2026

Phạm vi: các việc được nêu là còn thiếu trong `docs/thesis_readiness_2026-11-07.md`. Các mục 1, 3, 4, 5 làm offline. Mục 2 là lần gọi API duy nhất, được người dùng duyệt riêng; sổ ngân sách `data/runtime/multi_agent_budget.sqlite` không thay đổi. Kết quả của fixture kiểm thử là kiểm thử luồng điều khiển, không phải kết quả AI.

## 1. Quản lý vòng gọi công cụ (`review_v3`)

Vấn đề ngày 07/11: một agent gọi lại công cụ cho bằng chứng đã có, lịch sử dài thêm và yêu cầu thứ ba (6.024 token) bị hủy trước suy luận, nên không có kết luận.

`scripts/06_agent_evaluation/review_v3/loop_guard.py` đứng giữa lớp nén dữ liệu review_v2 và provider. Bộ chặn không sửa bằng chứng, detector hay danh mục; nó chỉ buộc agent nộp kết quả (chỉ cung cấp hàm nộp) khi:

- gọi lại đúng công cụ với cùng tham số;
- lần gọi trước không đem lại bằng chứng mới (`new_evidence` rỗng);
- cần giữ lượt cho các vai trò sau (cấu hình `matched_4`);
- provider từ chối yêu cầu trước suy luận vì vượt trần token: gửi lại đúng một lần dưới dạng yêu cầu nộp kết quả, với mã yêu cầu mới.

`runner_v3.py` thêm hai cấu hình ngân sách (native 12/4 lượt, matched 4/4 lượt) và mã yêu cầu riêng theo phiên bản/cấu hình, để không trùng với lượt cũ trong sổ ngân sách. Kiểm thử: `tests/test_agent_review_v3.py` (10 kiểm thử, gồm 80 tổ hợp trên 20 đơn thật).

## 2. Đợt đối chứng 20 case (đã chạy ngày 09/11 sau khi người dùng duyệt)

Kết quả: `docs/agent_comparison_v3_results_2026-11-09.md` (80/80 lượt, 0,113 USD, phát lại offline khớp cả 80 lượt).

`scripts/06_agent_evaluation/live/run_comparison_v3.py`:

| Chế độ | Việc làm |
| --- | --- |
| `--plan` | In phạm vi, trần và chi phí xấu nhất; không gọi mạng. |
| `--live --approve-usd X` | Chạy 80 lượt; từ chối nếu X thấp hơn trần đã tính. Chạy lại sẽ tiếp tục từ chỗ dừng; không gửi lại yêu cầu đã có. |
| `--verify` | Phát lại toàn bộ từ biên nhận, offline. |

Kế hoạch hiện tại: 80 lượt AI (20 case × ba agent/một agent × native/matched), tối đa 480 yêu cầu suy luận, **chi phí xấu nhất 0,648 USD**; hạn mức còn 4,974 USD; số case phân biệt sau chạy là 20/20. Theo chi phí thực của cặp thử 07/11 (0,0045 USD), chi phí thực tế dự kiến khoảng 0,1–0,2 USD; đây là ước tính, không phải cam kết. Cần kiểm tra lại giá trên trang nhà cung cấp trước khi duyệt. Kiểm thử chạy trọn luồng trên sổ ngân sách tạm, với API giả: `tests/test_agent_comparison_v3.py`.

Lệnh chạy sau khi được duyệt:

```sh
.venv/bin/python -B scripts/06_agent_evaluation/live/run_comparison_v3.py --plan
.venv/bin/python -B scripts/06_agent_evaluation/live/run_comparison_v3.py --live --approve-usd 0.65
.venv/bin/python -B scripts/06_agent_evaluation/live/run_comparison_v3.py --verify
```

## 3. Đánh giá định tính và Kappa (sẵn sàng, chờ người chấm)

- `rating/rubric_v2.json` (tạm thời, chờ giảng viên góp ý): thang 1–5 + NA, sáu tiêu chí. Bổ sung **mức độ bằng chứng hỗ trợ nhận định** và nêu rõ **tính phù hợp của đề xuất kiểm tra**, theo mục tiêu 5 của Chương 1.
- `rating/render_vi.py`: đưa mọi đầu ra về cùng một cách trình bày tiếng Việt cố định, để người chấm không nhận ra phương án qua văn phong.
- `rating/build_packet.py`: tạo `packet.html` ẩn danh (5 đầu ra A–E mỗi đơn, thứ tự xáo riêng từng đơn), phiếu CSV cho từng người chấm và file khóa riêng. Bản đầy đủ chỉ tạo được khi đã có đủ 80 đầu ra AI.
- `rating/kappa.py`: hai người chấm dùng Cohen's kappa và kappa có trọng số bậc hai (khoảng bootstrap 95%); từ ba người chấm dùng Fleiss' kappa. Loại mục NA theo từng tiêu chí và báo số mục bị loại. Kiểm thử đối chiếu ví dụ chuẩn của Fleiss (κ ≈ 0,210): `tests/test_rating_kappa.py`.

Kappa chỉ đo mức đồng thuận giữa những người chấm. Nó không chứng minh lời giải thích là đúng, và không phải độ chính xác phát hiện tắc nghẽn.

## 4. Dashboard BI local (đã có bản chạy được)

`scripts/06_dashboard/build_dashboard.py` tạo `data/dashboard/index.html`. Trang là HTML tĩnh, mở trực tiếp bằng trình duyệt, không cần cài thêm gì và không gọi mạng. Luồng xem: tổng quan → nhóm (công đoạn, bang khách hàng, người bán của đơn một người bán) → danh sách đơn → bằng chứng từng đơn, kèm giải thích AI nếu đơn đã có đầu ra được kiểm chứng.

- Tập test hồi cứu: 1.691 đơn, 1.523 đánh giá được, 70 có cảnh báo, 58 giao sớm có cảnh báo, 11 trễ hẹn có cảnh báo.
- Tập validation phát lại sự kiện: 1.853 đơn, 1.726 đánh giá được, 221 có cảnh báo, 225 công đoạn có cảnh báo. Mốc sau 01/06/2018 bị ẩn. Trạng thái giao hàng lấy từ phân tích giao hàng đã lưu, với mốc cắt riêng 24/05/2018.
- Đơn nhiều người bán không được quy cho người bán nào. Nhóm dưới 30 đơn được đánh dấu "chỉ tham khảo".

Kiểm thử đối chiếu với các con số đã lưu: `tests/test_dashboard.py`. Sau đợt đối chứng, chạy lại script để nạp giải thích của 20 case.

## 5. Độ trễ

`scripts/06_agent_evaluation/latency_summary.py` ghi `docs/latency_summary.json` từ các số đo đã lưu:

| Thành phần | Kết quả hiện có |
| --- | --- |
| Phát lại + chấm + ghi một sự kiện | trung vị 13,9 ms, p95 49,7 ms, 52 sự kiện/giây (một lần chạy local) |
| Giải thích ba agent | 21,2 giây (một lượt thật 07/11, 7 phản hồi) |
| Một agent | 4,15 giây đến khi bị chặn, không có kết luận |
| Dựng lại dashboard | khoảng 0,55 giây |

Các thành phần được đo riêng, không phải phép đo đầu-cuối trong môi trường vận hành. Sẽ cập nhật bằng 80 lượt của đợt đối chứng.

## Việc cần người dùng quyết định

1. Đã duyệt và đã chạy. Còn cần quyết định: có sửa gói kiểm chứng của Verifier hoặc nâng trần token để chạy một phiên bản mới hay không.
2. Đã chốt hai người chấm độc lập. Mỗi người chấm đủ 100 đầu ra của cùng 20 đơn, dùng CSV riêng; không trao đổi trước khi nộp. Gói chuyển giao chỉ gồm `packet.html`, `rating_sheet_rater1.csv`, `rating_sheet_rater2.csv`; `rating_key.json` giữ nội bộ. Chưa có điểm chấm thực tế; không dùng AI điền thay hai phiếu này.
3. Gửi bộ tiêu chí `rubric_v2` cho giảng viên góp ý trước khi chấm.

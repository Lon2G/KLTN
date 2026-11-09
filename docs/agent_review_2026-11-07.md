# Kiểm tra offline gói bằng chứng và độ đầy đủ của giải thích — 07/11/2026

Đã hoàn tất gói được duyệt: rút gọn phần lựa chọn bằng chứng và bổ sung kiểm tra giải thích có nêu đủ công đoạn mang cảnh báo. Không gọi API, không đếm token qua mạng, không đổi dữ liệu Olist, detector, ngưỡng hoặc các phản hồi AI lịch sử. Đây là phiên bản ứng viên đã kiểm tra offline; chưa thay kết quả chạy live cũ thành kết quả thành công mới.

## Vấn đề được xử lý

Lần live 06/11 có 12 mục được kiểm chứng khớp nguồn nhưng không có nhận định chính nêu cờ vận chuyển vượt ngưỡng. Nhánh một agent dừng trước lượt suy luận thứ hai vì gói yêu cầu đạt 6.050 token, vượt trần 6.000. Hai vấn đề này khác nhau: thiếu thông tin cần báo và vượt hạn mức đầu vào.

## Thay đổi triển khai

Mã mới nằm ở `scripts/06_agent_evaluation/review_v2/`, giữ nguyên các engine và snapshot đã đóng băng.

| File | Vai trò |
| --- | --- |
| `catalog_wire.py` | Biểu diễn danh mục lựa chọn bằng bảng cột, kế thừa cơ chế dẫn lại bằng chứng v1. Giữ nguyên selection ID, evidence ID, giá trị, kiểu dữ liệu, các trường thiếu và phạm vi thời điểm. Giải mã lại phải khớp chính xác yêu cầu gốc. Gói verifier không có danh mục lựa chọn không bị thêm hướng dẫn dư. |
| `warning_coverage.py` | Đọc các cờ `long_stage_warning=true` từ detector đóng băng. Kiểm tra từng cờ đã có trong phần nhận định được công bố và khớp bằng chứng nguồn chưa. Đề xuất kiểm tra một công đoạn không được tính thay cho nhận định cảnh báo. |
| `runner.py` | Bọc engine một/ba agent bằng định dạng mới và kiểm tra đầy đủ. Cho phép một chu kỳ sửa trong ngân sách lượt còn lại. Lưu yêu cầu, phản hồi, trạng thái trước sửa và kết quả kiểm chứng để đối soát. |
| `validate_review.py` | Dựng lại lịch sử thật, kiểm tra gói mới offline, bổ sung chú giải kết quả cũ vào snapshot riêng và kiểm tra nguồn/ledger không đổi. Không có chế độ gọi API. |
| `tests/test_agent_review_v2.py` | 14 kiểm thử mới, gồm cả hai engine trên 20 đơn pilot thật, các tình huống bỏ sót, từ chối, hết lượt và sai bằng chứng. |

Trạng thái `source_verification_status` cho biết các mục đã chọn được kiểm chứng như thế nào. `warning_coverage` báo các công đoạn cần nêu, đã nêu, còn thiếu và tỷ lệ bao phủ. Nếu các mục đúng nguồn nhưng còn thiếu cảnh báo, trạng thái giải thích là `incomplete_warning_coverage`. Khi không có cờ cảnh báo đủ điều kiện, tỷ lệ là `null`, không tự đặt 100%.

Với ba agent, bản sửa của Process Analyst phải qua một phản hồi Evidence Verifier mới trước khi được công bố. Nếu không còn lượt cho verifier, hệ thống không bắt đầu lượt sửa của analyst. Bản sửa vẫn thiếu, bị từ chối, phản hồi sai định dạng hoặc lỗi dịch vụ đều giữ phần công bố cũ và đánh dấu chưa đầy đủ. Với đối chứng một agent, bản sửa qua cùng bộ kiểm tra nguồn bằng code, không gán cho nó một verifier AI giả.

Giới hạn vẫn là tối đa 4 lượt mỗi vai trò, 12 lượt cho ba agent và 4 lượt cho một agent; 6.000 token đầu vào và 1.200 đầu ra mỗi yêu cầu ở provider. Lượt sửa không có ngân sách riêng vượt các giới hạn này. Định dạng mới có run key/request ID riêng, dùng cùng cơ chế ledger khi một thử nghiệm live mới được duyệt.

## Kết quả kiểm tra

| Gói | Trước, byte UTF-8 | Sau, byte UTF-8 gồm hướng dẫn mới | Nhận xét |
| --- | ---: | ---: | --- |
| Một agent, gói đầu | 20.504 | 19.144 | Giảm 6,63% |
| Một agent, gói đã bị chặn | 22.254 | 20.800 | Giảm 6,53% |
| Cả 8 gói đã lưu | 172.975 | 167.635 | Giảm 3,09%; hai gói verifier giữ nguyên kích thước |

Cả 8 gói khôi phục chính xác nội dung nguồn. Đây là số byte của yêu cầu JSON chuẩn hóa. Chưa có số token chính thức cho định dạng mới; không suy từ phần trăm byte sang phần trăm token, chưa xác nhận gói mới nằm dưới 6.000 token.

Dựng lại kết quả live ba agent vẫn cho 12/12 mục khớp nguồn, cờ cảnh báo vận chuyển được nêu 0/1. Chú giải mới ghi `incomplete_warning_coverage`; phản hồi và trạng thái lịch sử gốc không bị sửa. Nhánh một agent vẫn có trạng thái lịch sử `unavailable_budget`. Không dùng phản hồi cũ để giả định mô hình sẽ chọn giống hệt khi nhận prompt mới.

125 kiểm thử liên quan đạt, gồm 111 kiểm thử có sẵn và 14 kiểm thử mới; không lỗi, không bỏ qua kiểm thử. Phản hồi điều khiển trong các test được tạo riêng để kiểm tra phần mềm; chúng không phải suy luận AI thực tế và không được tính vào kết quả nghiên cứu. Dữ liệu nghiệp vụ dùng trong test là các đơn Olist đã có. Không tạo đơn hoặc nhãn tắc nghẽn giả.

Kiểm tra 90 file nguồn, triển khai cũ, kết quả lịch sử và ledger cho thấy hash không đổi trong lượt kiểm chứng. Có 0 lần gọi HTTP, 0 suy luận mới, chi phí mới 0 USD. Ledger giữ 62 yêu cầu đã quyết toán trên 6 mã case phân biệt, 3 yêu cầu hủy trước suy luận, tổng chi tiêu ghi nhận 0,021577160 USD. Đây là tổng các phiên bản đã thử, không phải 62 đơn hoặc một benchmark đồng nhất của phiên bản mới.

Kết quả kiểm thử: `docs/agent_review_validation_2026-11-07.json`.

Snapshot mới: `data/experiments/olist_agent_review_v2_offline/`, gồm `summary.json`, `prepared_requests.json`, hai bản chú giải kết quả lịch sử, `preservation.json` và `manifest.json`. Snapshot không chứa phản hồi live mới. Kết quả đối chiếu, dữ liệu và ledger vẫn được giữ local theo chính sách repository.

```sh
.venv/bin/python -B scripts/06_agent_evaluation/review_v2/validate_review.py --verify
.venv/bin/python -B -m unittest discover -s tests -p 'test_agent_review_v2.py' -v
```

Lệnh tạo snapshot không có `--verify` sẽ từ chối nếu thư mục đã tồn tại. `run_review(case, provider, arm)` là điểm tích hợp của phiên bản mới. CLI live cũ vẫn thuộc phiên bản cũ; cần gói chạy live có kế hoạch và snapshot mới trước khi sử dụng phiên bản này qua API.

## Phần còn phải kiểm chứng

Cần một cặp thử live trên cùng đơn phát triển để đo token thực, kiểm tra AI có chọn đủ cảnh báo và nhánh một agent có hoàn tất không. Nếu đạt, mới mở rộng pilot đã chọn trước và so sánh nguồn đúng, độ đầy đủ, lỗi ghi chú, thời gian, chi phí, tỷ lệ hoàn thành. Kết quả kỹ thuật offline chưa chứng minh ba agent tốt hơn một agent hoặc quy tắc.

Độ bao phủ cờ cảnh báo là phép đo tính đầy đủ của phần giải thích. Nó không xác nhận đơn thực sự tắc nghẽn, không phải nhãn để tính AUC phát hiện và không chứng minh nguyên nhân nghiệp vụ.

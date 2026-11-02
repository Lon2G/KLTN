# Tiến độ — thiết kế Multi-Agent và kiểm soát chi phí, 30/10/2026

## Đã hoàn tất trong gói này

Đã chuyển các câu trả lời được người dùng chấp thuận thành thiết kế và nền
kiểm soát kỹ thuật. Chưa chạy các agent trên API hoặc triển khai dashboard.

### 1. Quy ước điều tra theo công đoạn trước khi nghi địa lý

- Kiểm tra dữ liệu và xác định công đoạn có dấu hiệu chậm trước.
- Đối chiếu những đơn tương đồng theo các yếu tố có dữ liệu, phù hợp với
  công đoạn: sản phẩm/ngành, số lượng, seller có thể quy thuộc, thời điểm;
  địa lý sử dụng theo câu hỏi so sánh và chất lượng nguồn.
- Công bố nhóm đối chiếu, mẫu số, tỷ lệ, độ chênh, bao phủ và yếu tố chưa xét.
- Chỉ nêu liên hệ mô tả hoặc giả thuyết điều tra; không khẳng định đã loại
  trừ hết nguyên nhân khác hoặc coi vị trí là nguyên nhân đã xác nhận.
- Không biến chậm duyệt thành lỗi khoảng cách; không ghép nhóm theo chính
  duration/score/kết quả giao hàng hoặc thông tin tương lai.

Đây là hợp đồng cho phép phân tích tiếp theo. Chưa chạy thêm phép so sánh
địa lý, chưa đổi điểm, ngưỡng hoặc nhãn hiện có.

### 2. Thiết kế dashboard và phối hợp ba agent

Giữ ba vai trò: phân tích quy trình, phân tích bối cảnh, kiểm chứng bằng
chứng. Đã khai báo công cụ, thông điệp, loại nhận định, yêu cầu sửa và điều
kiện dừng. Scheduler bằng code không được tính như một agent AI bổ sung.

Dashboard thiết kế theo các trục độc lập: chất lượng dữ liệu, quan sát công
đoạn, kết quả detector, phân tích nhóm, tiến độ giải thích và mức xác nhận
nguyên nhân. Không gộp “chưa xác định” với “thiếu dữ liệu”; không gán bình
thường cho phần chưa đánh giá. Giữ cảnh báo chậm dù đơn giao sớm.

Cảnh báo có thể hiển thị trước, giải thích cập nhật sau. Agent không tự xóa
điểm/cờ detector. Thiếu bằng chứng hoặc lỗi API được hiển thị riêng. Hệ thống
multi-agent chính vẫn được giữ; một/không agent chỉ phục vụ đối chứng.

### 3. Bộ ghi sổ chi phí đã có code và được kiểm thử

- Tổng ngân sách người dùng: 200.000 đồng; phần gọi mô hình dự kiến 150.000
  đồng và dự phòng 50.000 đồng. Với mức quy đổi kế hoạch 30.000 đồng/USD,
  trần phần API là 5 USD, chưa phải tỷ giá hoặc hóa đơn thực tế.
- Dự phòng chi phí trước request, tính cả output/suy luận và trường hợp ghi
  cache; giới hạn token, số request, vai trò và 20 case pilot.
- Ghi sổ SQLite giữ chi phí qua các lần chạy; đối chứng và chạy lặp cùng
  dùng một ngân sách. Các giới hạn request áp dụng theo case/lượt phân tích.
- Không gửi lại cùng request ID; không giải phóng tiền khi đã gửi nhưng
  chưa biết kết quả. Quyết toán bằng usage; usage vượt dự phòng được ghi
  đầy đủ và khóa yêu cầu tiếp theo.
- Có kiểm thử hai kết nối tranh cùng số dư, khởi động lại, receipt trùng,
  thay policy, model không có giá và token vượt giới hạn.

Thư viện này không có API client. Lớp tích hợp sau phải thực thi đếm token,
gửi/nhận, khóa đường dẫn ledger chung và giới hạn payload. Chưa thể gọi nó
là trần hóa đơn đã được kiểm chứng với nhà cung cấp. Chưa nạp tiền, đọc key,
gửi dữ liệu nghiên cứu hoặc phát sinh lần gọi mô hình API nào trong gói này.

## Kiểm tra lại phần chấm điểm sự kiện

Đã làm rõ thông tin thiếu ở lượt trao đổi trước: **kết quả chấm điểm sự kiện
có tồn tại**, nhưng phần code/config/test/docs tương ứng chưa đầy đủ trong
checkout hiện tại. Không nên kết luận toàn bộ phần này chưa từng chạy.

| Kiểm tra | Kết quả thực tế |
| --- | --- |
| Replay gốc | Tái lập đúng 7.076 sự kiện / 1.853 đơn bằng `--verify` |
| Output chấm điểm sự kiện | 13/13 file kê trong manifest khớp hash |
| Source được snapshot chấm điểm tham chiếu | 76/76 file khớp hash |
| Code/config snapshot chấm điểm tham chiếu | 7 khớp, 3 thiếu, không có file khác hash trong các file còn tồn tại |
| Test và tài liệu của gói chấm điểm | 1 file test và 2 tài liệu chưa có |

Ba file cần xử lý:

1. `scripts/04_streaming/run_stage_scoring_replay.py`
2. `scripts/04_streaming/score_event_stages.py`
3. `templates/event_stage_scoring_v1/protocol.json`

Git chỉ liệt kê worktree hiện tại và không có lịch sử các script thiếu trong
các ref local đã kiểm tra. Chưa xác định lý do mất/không hiện diện. Hash output
khớp chưa đủ thay thế chạy lại phép tính; chưa thể xác nhận tái lập bộ chấm
điểm trong checkout này. Không sửa manifest cũ để che sự thiếu mã.

## Kiểm chứng gói mới

- **21 kiểm thử mới đạt:** 17 kiểm thử ngân sách, 4 kiểm thử kiểm tra dữ liệu
  lưu sẵn/manifest. Các dữ liệu hạch toán giả định và lỗi cố ý chỉ trong test
  kỹ thuật/thư mục tạm, không được xuất thành kết quả nghiên cứu.
- Replay gốc `--verify` đạt, xác nhận tái lập ngữ nghĩa sự kiện/trạng thái.
- Kiểm tra cú pháp Python, JSON và whitespace của các file mới đạt;
  `git diff --check` đạt.
- Chế độ `audit_readiness.py --require-ready` trả mã 2 đúng thiết kế vì pilot
  chưa đủ điều kiện. Không có API request nào được gửi.
- Hash **1.060 file có sẵn trước lượt này** được đối chiếu: không thay đổi,
  không xóa. Không sửa dữ liệu thô, snapshot, mô hình hoặc ngưỡng cũ.
- Không commit hoặc push Git trong lượt này.

Test phần mềm đạt không phải bằng chứng độ chính xác phát hiện tắc nghẽn
hoặc chất lượng giải thích của LLM. Chưa có kết quả mới cho các chỉ số đó.

## File để xem lại và ghi vào báo cáo

- [Thiết kế và phương pháp chi tiết](multi_agent_design_2026-10-30.md)
- [Hợp đồng vai trò, dashboard, địa lý và đánh giá](../templates/multi_agent_v1/protocol.json)
- [Chính sách giá và ngân sách](../templates/multi_agent_v1/budget.json)
- [Code ghi sổ chi phí](../scripts/05_multi_agent/api_budget.py)
- [Code kiểm tra tính toàn vẹn](../scripts/05_multi_agent/audit_readiness.py)
- [Kết quả kiểm tra tính toàn vẹn](multi_agent_readiness_2026-10-30.json)
- [Kiểm thử ngân sách](../tests/test_multi_agent_budget.py)
- [Kiểm thử audit](../tests/test_multi_agent_readiness.py)

## Bước tiếp theo

Ưu tiên phục hồi mã nguồn chấm điểm từ bản sao nếu có. Nếu không tìm được,
tái triển khai thành phiên bản mới và đối chiếu số liệu với snapshot cũ,
giữ nguyên các giả định thời điểm biết bối cảnh. Không cần import thêm Olist.

Sau đó mới nối công cụ truy vấn bằng chứng/đối chiếu nhóm, API sender có
ngân sách, chọn 20 đơn thật và chạy pilot được hạch toán. Người dùng rà soát
giải thích; kết quả pilot quyết định điều chỉnh mô hình/prompt và quy mô
thực nghiệm tiếp. Chưa hứa tốc độ, chi phí thực hoặc chất lượng trước phép đo.

# Tiến độ tích hợp phân tích và phối hợp agent — 30/10/2026

Đã hoàn thành phần code và kiểm thử local của bộ công cụ bằng chứng, đối chiếu
nhóm và phối hợp ba agent. Đã chạy bộ công cụ trên 20 đơn Olist thật. **Chưa chạy
suy luận mô hình qua API** vì môi trường hiện chưa có `OPENAI_API_KEY`; phí API
trong đợt triển khai này là 0. Kết quả kiểm thử phối hợp không được xem là kết quả
đánh giá năng lực AI.

## 1. Những phần đã làm

- Thêm 9 công cụ đọc bằng chứng, kiểm lại số liệu, đối chiếu peer và kiểm tra thời gian.
- Đọc journal theo event_id để không lấy kết quả tương lai giải thích quá khứ.
- Tách Process Analyst, Context Analyst và Evidence Verifier với quyền công cụ riêng.
- Cho agent chọn phép kiểm tra, trao đổi bằng chứng, yêu cầu sửa tối đa một lượt.
- Kiểm giá trị số và nguồn bằng code trước khi chấp nhận nhận định; giữ toàn bộ trace.
- Tích hợp Responses API với ledger ngân sách chung, đếm token trước inference,
  giữ dự phòng trước khi gửi, ghi usage và tránh gửi lại request đã có receipt.
- Nếu lỗi API/ngân sách hoặc nhận định bị bác, giữ nguyên detector và báo trạng thái riêng.
- Chuẩn bị snapshot 20 đơn, evidence packets và template review lời giải thích để trống.
- Cập nhật README, hướng dẫn sử dụng và audit readiness; không ghi đè tài liệu lịch sử.

Mã mới nằm trong `scripts/05_multi_agent/`: `evidence_tools.py`,
`agent_contracts.py`, `provider.py`, `coordinator.py`, `run_multi_agent.py`.
Điều kiện đối chiếu và giới hạn lượt gọi nằm ở
`templates/multi_agent_v1/integration.json`.

## 2. Kết quả trên dữ liệu thật

Snapshot: `data/experiments/olist_multi_agent_tools_v1/`.

| Nội dung | Kết quả |
|---|---:|
| Số đơn validation, không dùng test để chọn pilot | 20 |
| Đơn có cảnh báo theo detector đã đóng băng | 8 |
| Đơn đủ ba công đoạn dưới ngưỡng detector | 8 |
| Đơn chưa đủ điều kiện / chưa đánh giá đủ công đoạn | 4 |
| Trạng thái công đoạn kiểm tra | 60 |
| Công đoạn có điểm, tính lại duration và chênh lệch mean | 52 |
| Công đoạn chưa được chấm điểm, giữ null / lý do | 8 |
| Kiểm tra ở chế độ chỉ dùng tính sẵn có đã xác minh | 60, đều giữ chưa chấm điểm |
| Phép đối chiếu công đoạn × kế hoạch | 240 |
| Nhãn xác nhận tắc nghẽn mới tạo | 0 |
| Lượt suy luận mô hình API thật | 0 |

Trong 240 phép đối chiếu:

- 12 phép đủ số mẫu tham chiếu mô tả ở cấp ngành.
- 120 phép không đủ đơn tương đồng / độ bao phủ.
- 72 phép so vùng không phù hợp với công đoạn đang xét và bị chặn.
- 4 phép không quy được về một sản phẩm duy nhất.
- 32 phép không thực hiện vì công đoạn chưa đủ điều kiện đánh giá.

**Chưa có so sánh vùng nào trong pilot đáp ứng đầy đủ điều kiện hỗ trợ mẫu.**
Không tự nới điều kiện và không suy ra “do địa lý”. Số 240 là số phép kiểm tra,
không phải 240 đơn độc lập. Mẫu chọn có chủ đích để thử luồng, không dùng 8/20
để ước lượng tỷ lệ bất thường toàn ngành.

Các phân tích có điểm dùng chế độ `assumed_static_context_at_purchase`: dữ liệu
tĩnh là dữ liệu thật, nhưng giả định chúng đã sẵn có khi đặt hàng chưa được Olist
xác nhận. Chế độ chỉ cho phép tính sẵn có đã xác minh vẫn để kết quả chưa đánh giá.

## 3. Ví dụ để hiểu đầu ra

Đơn thật `980bc014d29830d87b56bbc7720a6b6b`, mốc quan sát
`2018-04-17 20:56:40`:

- Công đoạn vận chuyển quan sát được: **36,05 ngày**.
- Mean nhóm tham chiếu detector cũ: **12,23 ngày**, 277 đơn.
- Dài hơn mean: **23,81 ngày**.
- Biên đuôi trên của detector: **31,26 ngày**; cờ công đoạn chậm được giữ nguyên.
- Score cũ: **49,46/100**, không phải xác suất tắc nghẽn 49,46%.
- Đối chiếu mới chặt hơn, cùng seller/số món/dải khoảng cách/bang nhận ở cấp ngành,
  chỉ còn **7 đơn lịch sử** cho vận chuyển, dưới mức hỗ trợ 30.

Hai nhóm trên có mục đích khác nhau: 277 đơn phục vụ detector đã đóng băng; 7 đơn
phục vụ kiểm tra bối cảnh chặt hơn. Không thay tham chiếu detector bằng 7 đơn và
không dùng thiếu mẫu để xóa cảnh báo. Kết luận hợp lệ là công đoạn này dài bất
thường theo detector, còn phần giải thích bối cảnh chưa đủ bằng chứng.

Một ví dụ khác, đơn `4fd47db0e1bb9f59384ff16f8d533f80`: phép đối chiếu vùng có
48 đơn đích và 58 đơn đối chứng sau ghép, nhưng coverage chỉ 64% và 74,36%, thấp
hơn yêu cầu 80%. Dù tính được chênh lệch trung bình khoảng 5,62 ngày, hệ thống
vẫn đánh dấu chưa đủ mẫu tương đồng/bao phủ để công bố liên hệ được hỗ trợ.
Đây không phải bằng chứng vùng đó gây tắc nghẽn.

## 4. Bằng chứng kiểm thử và bảo toàn

- **46 test đạt**, không lỗi, không bỏ qua: 22 test nền tảng và 24 test tích hợp mới.
- Kiểm tra cả tiền tố sự kiện, giả định thông tin tĩnh, nhóm train trước thời điểm
  đánh giá, loại case bảo lưu, đối chiếu số liệu, coverage và dữ liệu gửi API.
- Mô phỏng kỹ thuật số liệu sai, tool trái quyền, yêu cầu sửa, timeout, thiếu usage,
  token vượt giới hạn, receipt lặp và khởi động lại. Các fixture nằm trong tests,
  không được nhập thành dữ liệu hoặc nhãn nghiên cứu.
- Lệnh API thật được kiểm tra với môi trường thiếu key: dừng trước gửi request.
- Đối chiếu **1.078 file đã tồn tại trước đợt triển khai**: không mất file; chỉ đổi
  `README.md` và `scripts/05_multi_agent/audit_readiness.py` theo phạm vi tích hợp.
- Dữ liệu cũ, mô hình, output cũ, nguồn replay và nguồn chấm điểm đã phục hồi còn nguyên.
- Hai snapshot replay/scoring cũ vẫn khớp toàn bộ hash kiểm tra. Không chạy lại
  training, không đổi threshold và không viết lại kết quả lịch sử.

Chi tiết kiểm thử:
`docs/multi_agent_integration_validation_2026-10-30.json`.
Chi tiết bảo toàn:
`docs/multi_agent_integration_preservation_2026-10-30.json`.
Readiness mới:
`docs/multi_agent_readiness_after_integration_2026-10-30.json`.

## 5. Đang ở đâu và việc tiếp theo

Hiện ở giai đoạn **tích hợp kỹ thuật và chuẩn bị pilot multi-agent**. Chưa đến
kết luận multi-agent tốt hơn baseline, chưa phải hệ thống near real-time đã được
đo end-to-end, chưa có dashboard tích hợp và chưa có nhãn tắc nghẽn độc lập.

1. Cấu hình API key trong môi trường local, kiểm tra quyền model; không cần import
   thêm dữ liệu Olist cho bước này và không gửi key qua chat.
2. Chạy một đơn thật qua ba agent, kiểm tra tool calling, usage, độ trễ và bằng chứng.
   Nếu chạy được, thực hiện phần pilot còn lại trong cùng ledger ngân sách.
3. Rà soát lời giải thích theo template: đúng số, đúng nguồn, nêu giả định, nêu thiếu
   bằng chứng và đưa hướng kiểm tra phù hợp. Đây là review lời giải thích, không
   phải nhãn xác nhận nguyên nhân vận hành.
4. So sánh multi-agent với baseline cùng dữ liệu, điều kiện và tiêu chí; sau đó nối
   kết quả vào dashboard local và đo riêng độ trễ detector / giải thích / hiển thị.

Hướng dẫn và giới hạn đầy đủ: `docs/multi_agent_integration_2026-10-30.md`.

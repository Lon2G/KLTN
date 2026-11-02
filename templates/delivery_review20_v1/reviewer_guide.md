# Rà soát thử 20 đơn Olist

## Mục đích

Đây là vòng thử tiêu chí, chưa phải bộ đánh giá độ chính xác hoặc bộ test cuối.
20 đơn là dữ liệu thật của ngành bed_bath_table. Mỗi đơn có ba phạm vi kiểm tra:
quy trình/thời gian, thanh toán, nội dung đơn. Tổng cộng có 60 mục ghi nhận,
nhưng bạn có thể bắt đầu với phần quy trình/thời gian của từng đơn.

Chỉ dùng tài liệu trong `reviewer_packet`. Không mở `analyst_only`, bảng điểm
mô hình hoặc bảng kết quả thực nghiệm trong lúc rà soát. Mã BR là mã rà soát,
không phải thứ hạng hoặc mức độ bất thường.

## Quy ước đã thống nhất

- Giao trước ngày dự kiến: ghi nhận giao sớm, không tự gán bất thường.
- Giao trong ngày dự kiến: đúng hẹn theo ngày, không tính từ 00:00 để gọi là trễ.
- Giao sau ngày dự kiến: ghi nhận giao trễ, chưa đủ để xác định tắc nghẽn hay lỗi seller.
- Một đơn giao sớm vẫn có thể có mâu thuẫn dữ liệu hoặc vấn đề ở công đoạn khác.
- Thời gian ngắn nhưng không âm: tự nó không chứng minh lỗi hoặc bất thường.
- Ngày dự kiến là của toàn đơn. Không tự tạo thời hạn cho từng công đoạn.
- Không chắc hoặc thiếu bằng chứng: ghi `unresolved`, không đoán nhãn bình thường.

## Cách làm

1. Mở `20_don_can_ra_soat.md` và xem dữ kiện của từng mã BR. Các mốc ngày giờ là
   giá trị nguồn; không có kết luận mô hình trong tài liệu.
2. Kiểm tra trình tự đặt hàng, phê duyệt, bàn giao vận chuyển, giao khách.
3. Đối chiếu ngày giao với ngày dự kiến; tách kết quả này khỏi đánh giá bất thường.
4. Kiểm tra từng công đoạn. Nếu thiếu mức tham chiếu/bối cảnh để kết luận thời
   gian khác thường, ghi rõ điều còn thiếu; không tự đặt một ngưỡng ngày.
5. Khi xem thanh toán và nội dung đơn, dùng các dòng nguồn trong cùng tài liệu
   hoặc `reviewer/payment_records.csv`, `reviewer/item_records.csv`.
6. Gửi nhận xét theo mã BR, phạm vi, điều quan sát được, kết luận tạm thời và lý do.
   Tôi sẽ hỗ trợ ghi nhận riêng, không tự chuyển lời chưa chắc thành nhãn xác nhận.

Ví dụ cấu trúc trả lời, không phải nhận xét đã được tạo:
`BR.... | process_timing | điều quan sát | unresolved/completed | lý do và bằng chứng còn thiếu`.

`assessment_template.json` giữ nguyên 60 mục chưa đánh giá để làm mẫu đầu vào.
`--check-note` kiểm tra cấu trúc một nhận xét theo bộ tiêu chí, không lưu nhãn và
không xác thực tính đúng của nhận định. Không sửa các file nguồn/bản mẫu đã đóng
phiên bản. Chưa có buổi rà soát hoặc nhận xét nào được ghi thay bạn.

## Nhãn và giới hạn

- `no_supported_anomaly`: chỉ áp dụng cho phạm vi đã kiểm tra đủ, không phải toàn đơn.
- `anomaly/data_error`: có bằng chứng mâu thuẫn hoặc giá trị không hợp lệ; không xác định ai nhập sai.
- `anomaly/valid_deviation`: cần cả mức so sánh và bằng chứng tính hợp lệ; khớp tổng tiền chưa đủ.
- `anomaly/unclassified`: đã có bằng chứng bất thường nhưng chưa xác định được loại.
- `unresolved`: chưa đủ bằng chứng để kết luận, để trống nhãn cha và loại con.

Đây là nhận định của người nghiên cứu, không phải nhãn chuyên gia độc lập. Có thể
không kết luận được nhiều đơn từ Olist; cần ghi đúng giới hạn đó. Không dùng độ
đồng thuận với mô hình làm bằng chứng độc lập. 20 đơn không chứng minh đủ độ tin cậy
để chốt mô hình; vòng này dùng để hoàn thiện tiêu chí và cách thu thập bằng chứng.

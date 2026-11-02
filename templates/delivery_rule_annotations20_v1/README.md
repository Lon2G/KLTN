# Nhãn tự động theo quy ước cho 20 đơn

Đây là kết quả gán nhãn cơ học theo dữ kiện và quy ước đã thống nhất, được tạo theo
yêu cầu của người dùng. Không phải nhận xét do người dùng tự rà soát, nhãn chuyên
gia độc lập, nhãn fraud hoặc kết quả kiểm chứng độ chính xác mô hình.

## Kết quả

- `case_annotations.csv`: một dòng cho mỗi mã BR, nhãn giao sớm/đúng ngày/trễ và
  trạng thái kiểm tra dữ liệu. Đáp ứng ngày hẹn chỉ nói về ngày hẹn, không có nghĩa
  toàn đơn là bình thường.
- `rule_checks.csv`: 9 mục kiểm tra mỗi đơn, cùng dữ kiện, lý do và vị trí nguồn.
- `scope_annotations.csv`: 3 phạm vi mỗi đơn. Nhãn bất thường tổng quát vẫn để
  trống khi thiếu mức tham chiếu độc lập hoặc bằng chứng xác minh; trạng thái là
  `unresolved`, không chuyển thành bình thường.
- `20_case_da_doi_chieu.md`: bảng tiếng Việt cho người dùng, kèm giải thích từng đơn.
- `exposure_notice.json`: ghi nhận 20 mã đã được tạo diễn giải tự động để hiển thị
  cho người dùng; đánh giá con người sau đó không còn được gọi là rà soát mù độc
  lập với các diễn giải này. Không suy ra rằng người dùng đã hoàn tất rà soát.

Chỉ đọc các bảng dành cho người rà soát trong bộ 20 đơn. Không đọc bảng điểm,
cờ mô hình, khóa chọn mẫu hoặc ánh xạ mã đơn. Kiểm tra mã băm không phải sử dụng
kết quả mô hình để gán nhãn. Bộ 20 đơn và bộ 120 đơn gốc giữ nguyên toàn bộ file,
bao gồm các trường nhận xét con người còn trống.

## Giới hạn

Thời gian dài/ngắn khác thường, tính hợp lệ nghiệp vụ của giá trị hiếm và nguyên
nhân tắc nghẽn chưa thể kết luận chỉ từ các phép kiểm tra này. Khoản tiền chênh
lệch dù nhỏ vẫn được ghi đúng giá trị, không tự xóa hoặc quy cho làm tròn. Khớp số
tiền, đủ mốc thời gian, giao sớm hoặc có nhiều khoản thanh toán không tự tạo ra
nhãn bất thường hay nhãn bình thường cho toàn đơn.

Không dùng chính nhãn sinh từ quy tắc để tuyên bố mô hình học được quy tắc đó có
độ chính xác nghiệp vụ độc lập. Nếu sau này đo mức khớp với quy tắc, phải gọi đúng
là mức khớp quy tắc. Chưa train, thay ngưỡng, loại dữ liệu, chấm test cuối hoặc ghi
nhận phê duyệt của thầy trong bước này.

## Chạy lại và kiểm tra

```sh
.venv/bin/python scripts/03_anomaly_detection/annotate_delivery_review20.py --summary
```

Lệnh không có `--summary` chỉ tạo bản đầu tiên và từ chối ghi đè. Nguồn, quy ước,
code và đầu ra có mã băm; nhãn có thể tái lập từ dữ liệu nguồn. Các bảng có thể
phục vụ diễn giải ban đầu, chưa phải tập nhãn chuẩn để chọn mô hình cuối.

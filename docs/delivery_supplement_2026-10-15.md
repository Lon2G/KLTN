# Tiến độ: đối chiếu bổ sung 40 đơn giao hàng, 15/10/2026

## Phạm vi và kết quả

Tiếp nối 20 đơn đã đối chiếu tự động, bước này dùng dữ liệu Olist bed_bath_table
đã nhập, không cần nguồn mới và không tạo đơn giả. Chưa train lại, chọn mô hình,
đổi ngưỡng detector, chấm test hoặc kích hoạt cách chia 80/20 mới.

Nguồn hiệu suất có 7.101 đơn phát triển được phép phân tích sau khi giữ riêng
120 đơn pilot và các đơn thuộc vùng test đề xuất. Chỉ lấy mẫu trong 1.359 đơn
validation có đủ mốc thực tế nhìn thấy trước giới hạn thời gian, không có mâu
thuẫn nguồn và so sánh được với ngày hẹn. Không bao phủ đơn đang xử lý hoặc
thiếu/sai mốc; các trường hợp đó vẫn cần luồng đánh giá riêng.

| Nhóm chọn mẫu | Ứng viên trước loại chồng lấn | Đã chọn |
| --- | ---: | ---: |
| Các cấu hình bất đồng về cảnh báo vận chuyển dài | 43 | 10 |
| Giao sau ngày hẹn | 149 | 10 |
| Công đoạn tương đối dài dù đáp ứng hẹn | 62 | 10 |
| Đối chiếu, không có các dấu hiệu chọn mẫu quy định | 808 | 10 |

Các nhóm ứng viên có thể chồng lấn, không cộng thành tổng. Lấy nhóm bất đồng
trước, sau đó trễ hẹn, công đoạn dài và đối chiếu; mã băm cố định quyết định thứ
tự trong mỗi nhóm. Có 40 đơn duy nhất: **16 giao trễ, 24 giao sớm**. Không mặc
định nhóm đối chiếu là 10 đơn bình thường đã xác nhận. Cỡ mẫu này nhằm mở rộng
tình huống quan sát, không phải cỡ mẫu đã chứng minh đủ để tính độ chính xác.

## Cách so sánh công đoạn

- Tính riêng phê duyệt, chuẩn bị/bàn giao và vận chuyển từ mốc thực tế; không
  tự tạo SLA từng công đoạn từ ngày dự kiến giao của cả đơn.
- Tham chiếu 15.931 dòng công đoạn thuộc 5.461 đơn train, chỉ dùng thời gian
  không âm đã quan sát được trước validation. Không đưa đơn đang đánh giá vào
  chính nhóm tham chiếu của nó.
- Ưu tiên cùng bang khách, nhóm khoảng cách và loại đơn một/nhiều seller.
  Tối thiểu 30 quan sát mới dùng; nếu thiếu thì mở rộng nhóm và ghi rõ.
- Trong 120 phép đối chiếu: 108 dùng đủ ba bối cảnh, 9 bỏ điều kiện cùng bang,
  3 dùng bang và loại seller nhưng không khớp khoảng cách.
- Phân vị thực nghiệm = số thời gian train không dài hơn đơn đang xem / số
  quan sát trong nhóm. Không phải xác suất bất thường. Mốc >95% chỉ là tiêu
  chí chọn mẫu sơ bộ, không phải ngưỡng đã tối ưu bằng độ chính xác.

Nhóm train chưa có nhãn bình thường; kết quả chịu ảnh hưởng bởi dữ liệu đã
quan sát được ở mốc cắt, thiếu kết quả của một số đơn, mùa vụ và lựa chọn nhóm.
Các yếu tố bối cảnh chưa được kiểm soát đầy đủ. Khoảng cách ZIP là đường thẳng,
không phải tuyến vận chuyển thực tế. Số dòng hàng không thay thế trọng lượng,
kích thước hay cấp dịch vụ; chưa suy ra lỗi hoặc đóng góp nhân quả của seller.

## Ví dụ thật

**DS003**, order_id `0f916489801ab47804cc7da10fe0dd6d`: giao sớm 8 ngày.
Chuẩn bị/bàn giao mất khoảng 8,94 ngày, so với trung vị 1,91 ngày và P95 7,48
ngày của 648 đơn train cùng bang SP, nhóm khoảng cách 50–200 km, một seller.
Đây là ví dụ đáp ứng hẹn giao hàng nhưng vẫn có công đoạn tương đối dài.
Không tự gán lỗi nghiệp vụ, bất thường đã xác nhận hoặc lỗi seller.

**DS027** trễ 4 ngày nhưng chưa có kết quả mô hình được lưu. Giữ trạng thái
chưa có, không thay bằng 0 cảnh báo để gọi đơn bình thường.

## Đầu ra và giới hạn đánh giá

Đã lưu 8 bảng, báo cáo từng đơn, quy ước và manifest mã băm. Bao gồm 120 phép
so sánh công đoạn, 468 dòng cấu hình lịch sử của 39 đơn, 40 dòng thanh toán và
50 dòng hàng. Cả 40 đơn khớp tổng thanh toán với tiền hàng + vận chuyển.

12 cấu hình lịch sử cùng mục tiêu vận chuyển được dùng để tìm bất đồng. Đây
không phải 12 họ thuật toán và không phải lần chạy mới của IF/LOF/OCSVM. Không
lấy tỷ lệ mô hình cảnh báo làm xác suất hoặc lấy đa số để tạo nhãn chuẩn.

40 kết luận bất thường rộng vẫn `unresolved`; không giả nhãn người dùng,
chuyên gia hoặc phê duyệt của giảng viên. Các nhóm được chọn có chủ đích và có
hỗ trợ mô hình nên không dùng trực tiếp để suy ra tỷ lệ bất thường toàn ngành
hoặc độ chính xác không chệch. Người đã xem kết quả này không được coi là đang
rà soát mù không có hỗ trợ khi đánh giá lại cùng đơn.

Đã có dữ kiện khách quan về trễ hẹn và độ dài công đoạn. Phần còn thiếu để
chốt detector là quy trình kết luận/đối chiếu độc lập với chính các cờ chọn
mẫu. Olist có thể hỗ trợ nghiên cứu dấu hiệu thống kê; để khẳng định nguyên
nhân tắc nghẽn, lỗi nhập liệu hay lỗi seller cần bằng chứng vận hành bổ sung
mà nguồn hiện tại không cung cấp. Không cần nhập thêm để hoàn tất bước này.

## Tái lập và kiểm tra

```sh
.venv/bin/python -B scripts/03_anomaly_detection/prepare_delivery_supplement.py --summary
PYTHONPATH=tests .venv/bin/python -B -m unittest test_delivery_supplement -v
```

Lệnh không có `--summary` chỉ tạo bản mới khi thư mục đầu ra chưa tồn tại.
Không ghi đè kết quả cũ. Mã băm ràng buộc nguồn, code, quy ước và đầu ra.

Báo cáo: [40 đơn và bằng chứng](../data/experiments/olist_delivery_supplement_v1/bao_cao_doi_chieu.md).
Code: [chuẩn bị nhóm bổ sung](../scripts/03_anomaly_detection/prepare_delivery_supplement.py).
Kiểm thử: [các bất biến dữ liệu thật](../tests/test_delivery_supplement.py).

Đã chạy 256 kiểm thử liên quan, gồm 19 kiểm thử mới: tất cả đạt trong 536,874
giây. Đây là kiểm thử phần mềm, không phải độ chính xác mô hình và không phải
toàn bộ test suite của repository. Bộ mới kiểm tra dữ liệu thực, các nhóm
tham chiếu, fallback, chọn mẫu tái lập, thiếu bằng chứng, tiền chính xác,
bảo vệ pilot/test, đọc lại CSV, từ chối ghi đè và phát hiện sửa payload.

Đã đọc lại bản lưu bằng `--summary` và xác minh manifest thành công. Đối chiếu
mã băm trước/sau xác nhận 783 file cũ trong data/models/outputs/scripts/
templates/tests/docs không thay đổi và không bị xóa. Chỉ thêm file mới.
Chưa commit/push; data/model bị loại khỏi Git nên cần bản sao lưu riêng.

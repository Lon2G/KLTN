# Quyết định về tiêu chí và bằng chứng, 16/10/2026

## Đã thực hiện và đã chọn

Theo yêu cầu người dùng, đã triển khai và chạy một phiên bản thử nghiệm để
đối chiếu tiêu chí trên toàn bộ validation được phép dùng, không chỉ 40 đơn
đã chọn. Không sửa các module hoặc snapshot đã đóng phiên bản trước đó.

**Chọn chính sách tách ba lớp:**

1. Chất lượng nguồn: mốc mâu thuẫn, dữ kiện chưa thấy ở mốc quan sát và chênh
   lệch tiền cần xác minh. Không tự sửa/xóa nguồn hay quy kết người nhập sai.
2. Hiệu suất giao hàng: so ngày lịch thực tế với ngày dự kiến. Giao sớm là
   thông tin hiệu suất; giao trễ là quan sát trễ hẹn, không phải nguyên nhân.
3. Cảnh báo thống kê theo tham chiếu train có bối cảnh: giữ dấu hiệu dài/ngắn
   dương và phần không chấm được. Không tự nâng thành nhãn bất thường xác minh.

Chọn tham chiếu có bối cảnh cho phiên bản phát triển vì phù hợp yêu cầu xét
địa lý/đặc điểm đơn và giữ được thông tin về mức tương đồng. Mức 95%/5% hiện
là mặc định mô tả để tiếp tục phát triển, **không phải ngưỡng đã chứng minh
chính xác nhất**. Không lấy số cảnh báo thấp nhất làm hàm tối ưu.

Đây là quyết định trong phạm vi người dùng ủy quyền, không phải phê duyệt của
giảng viên, không phải chọn IF/LOF/OCSVM thắng cuộc hoặc thay model đang lưu.
Chưa tích hợp vào một dịch vụ/dashboard vận hành; đã có bảng kết quả cho bước đó.

## Kết quả thực tế

- 1.621 đơn validation được bảo vệ, 1.359 đơn đủ so ngày hẹn và ba công đoạn.
- 4.077 dòng bằng chứng công đoạn; train kết thúc quan sát trước validation.
- 1.307 đơn đủ tham chiếu bối cảnh cho cả ba công đoạn, 314 đơn chưa chấm đầy đủ.
- 165 đơn có dấu hiệu dài, 178 có dấu hiệu ngắn dương; chồng lấn 15 đơn, tổng
  328 đơn có ít nhất một dấu hiệu ở mức mô tả đang dùng. Không phải 328 nhãn
  bất thường đã xác nhận; không công bố tỷ lệ báo nhầm 5%.
- 979 đơn không có dấu hiệu theo phép so sánh hiện tại. Không phải 979 đơn
  được xác nhận bình thường toàn diện.
- 1.615 đơn khớp tiền, 6 đơn có chênh lệch cần xác minh. Tiền giữ bằng đơn vị
  nhỏ nhất của đồng tiền nguồn, không tự xóa chênh lệch hoặc suy ra gian lận.

So timestamp với ngày hẹn 00:00 sẽ gắn trễ 166 đơn, so ngày lịch là 149 đơn.
Có 17 đơn giao trong ngày hẹn. Vì vậy giữ cách so ngày lịch đã có. Timestamp
là đối chứng về tiêu chí, không phải bug mới được tìm thấy trong pipeline cũ.

Đã so ngưỡng chung và theo bối cảnh ở 90%, 95%, 97,5%, 99%, trên cùng 1.307
đơn. Ở 95%:

| Phương án | Cảnh báo dài | Trễ hẹn có cảnh báo | Đáp ứng hẹn có cảnh báo |
| --- | ---: | ---: | ---: |
| Tham chiếu chung | 164 | 90 | 74 |
| Tham chiếu theo bối cảnh | 165 | 103 | 62 |

Đây là các giao cắt quan sát, không phải TP/FP/FN của bất thường. Không lấy
giao trễ làm ground truth cho tắc nghẽn; đơn đáp ứng hẹn vẫn có thể có công
đoạn dài. Ngưỡng cao hơn giảm số cảnh báo nhưng không chứng minh giảm sai lệch.

Đã so 12 cấu hình lịch sử cùng mục tiêu vận chuyển trên đúng cùng 1.229 đơn.
Cảnh báo dài dao động 78–92 đơn. Không phải lần huấn luyện mới hay so sánh lại
toàn bộ detector IF/LOF/OCSVM. Không dùng MAE/đồng thuận để chọn detector chính.

## Chưa giải quyết được bằng dữ liệu hiện có

Chưa có reference bất thường độc lập, chưa đo được báo nhầm/bỏ sót thực sự,
chưa chốt detector hoặc ngưỡng tối ưu. Các quy tắc mới không được dùng làm
đáp án để chứng minh chính mô hình tạo cờ là đúng. Không giả nhãn con người,
không gọi việc không thấy lỗi là bằng chứng dị biệt chắc chắn hợp lệ.

Nhóm train không được xác nhận toàn bình thường và chịu giới hạn mốc quan
sát. Các nhóm bối cảnh mới kiểm soát một phần địa lý và số seller, chưa đủ
phương thức thanh toán, trọng lượng/kích thước, mùa vụ, hãng và cấp dịch vụ.
Chưa chứng minh nguyên nhân, trách nhiệm seller hay near real-time.

Để hoàn tất lựa chọn detector bằng độ chính xác cần một mục tiêu/nguồn đối
chiếu độc lập có thể xác minh. Nếu nghiên cứu chỉ đánh giá mức khớp quy tắc
hoặc dấu hiệu thống kê, phải công bố đúng phạm vi đó, không nâng thành độ
chính xác bất thường nghiệp vụ. Chính sách hiện tại có thể dùng để phát triển
BI và giải thích bằng chứng mà không cần đợi gán nhãn cho mọi đơn vận hành.

## Kiểm thử và tái lập

Đã chạy 74 kiểm thử liên quan, gồm 16 kiểm thử mới, tất cả đạt trong 29,107
giây. Bao phủ ngày lịch, mốc cắt, thiếu dữ liệu, tham chiếu train, cùng tập so
sánh, bảo toàn tiền, không tạo nhãn, tái lập CSV, ghi đè và phát hiện sửa file.
Không phải toàn bộ test suite và không phải độ chính xác mô hình.

Đối chiếu SHA-256 xác nhận 801 file cũ trong phạm vi data/models/outputs/
scripts/templates/tests/docs không bị thay đổi hoặc xóa. Có 8 kiểm tra quy ước
phần mềm với 0 vi phạm; không diễn giải thành 0 lỗi phát hiện bất thường.

```sh
.venv/bin/python -B scripts/03_anomaly_detection/resolve_evidence_criteria.py --summary
PYTHONPATH=tests .venv/bin/python -B -m unittest test_evidence_criteria test_delivery_supplement test_delivery_performance test_delivery_rule_annotations20 -v
```

Lệnh tạo mới từ chối ghi đè. Chưa commit/push. Dữ liệu/kết quả bị loại khỏi
Git nên cần sao lưu riêng, GitHub không thay thế bản sao thực nghiệm.

- [Báo cáo đầy đủ](../data/experiments/olist_evidence_criteria_v1/bao_cao_tieu_chi.md)
- [Code](../scripts/03_anomaly_detection/resolve_evidence_criteria.py)
- [Quy ước](../templates/evidence_criteria_v1/protocol.json)
- [Kiểm thử](../tests/test_evidence_criteria.py)

Nguồn phương pháp: [scikit-learn outlier detection](https://scikit-learn.org/stable/modules/outlier_detection.html),
[data leakage](https://scikit-learn.org/stable/common_pitfalls.html#data-leakage).

# Tiến độ ngày 15/10/2026: hiệu suất giao hàng và 20 đơn rà soát

## 1. Quy ước đã đưa vào code

- Giao trước hẹn là thông tin hiệu suất, không tự sinh nhãn bất thường.
- Giao trong ngày hẹn là đúng hẹn theo ngày, kể cả khi ngày dự kiến lưu 00:00:00.
- Giao sau hẹn là thông tin trễ hạn, chưa chứng minh bất thường thống kê hoặc nguyên nhân tắc nghẽn.
- Một đơn giao sớm vẫn có thể có mâu thuẫn dữ liệu hoặc vấn đề ở công đoạn khác.
- Thời gian ngắn nhưng dương không tự chứng minh lỗi. Thời gian âm, bằng không và thiếu dữ liệu được giữ riêng.
- Ngày dự kiến thuộc toàn đơn. Không tự tạo thời hạn cho phê duyệt, chuẩn bị hay vận chuyển.
- Chưa quyết định lại cách hiển thị mọi loại dị biệt hợp lệ khác, ví dụ thanh toán giá trị cao.

Phần mới là dữ liệu nền cho Process-Aware BI và đánh giá phương pháp, không đổi đề tài thành dự báo thời gian.

## 2. Kết quả chạy trên dữ liệu thật

Nguồn là 7.333 đơn phát triển Olist bed_bath_table đã có. Phân tích này bảo vệ toàn bộ
120 đơn pilot và loại thêm 112 đơn nằm trong vùng test theo phương án 80/20 đang đề xuất.
Còn 7.101 đơn được đưa vào bảng phân tích mới. Không kích hoạt phương án chia dữ liệu mới.

| Kết quả | Số đơn |
| --- | ---: |
| Giao trước ngày hẹn, đủ điều kiện so sánh | 5.918 |
| Giao trong ngày hẹn, đủ điều kiện so sánh | 65 |
| Giao sau ngày hẹn, đủ điều kiện so sánh | 454 |
| Chưa đủ dữ liệu tại mốc quan sát | 605 |
| Mâu thuẫn nguồn, giữ riêng khỏi thống kê hợp lệ | 59 |
| Tổng | 7.101 |

Mẫu số để tính tỷ lệ giao sớm/đúng hẹn/trễ là **6.437 đơn**, không phải toàn bộ 7.101 đơn.
Các con số này không phải tỷ lệ bất thường hoặc độ chính xác mô hình. Đây không phải toàn bộ
9.024 đơn của ngành, càng không phải toàn bộ Olist. Đơn thiếu kết quả tại mốc quan sát không
được hiểu là vẫn chưa giao đến hôm nay hoặc đã giao trễ.

Ngày giờ thực tế phải trước cả mốc quan sát cũ của từng phần dữ liệu và ranh giới bảo vệ
`2018-05-24 11:07:21`. Các kết quả chịu ảnh hưởng bởi thời gian quan sát và việc chỉ tính đơn
đủ điều kiện; không dùng chúng như ước lượng không chệch cho toàn ngành. Lịch sử từng xem dữ
liệu trước đây không bị xóa bởi bước bảo vệ này.

Nguồn kết quả: [summary](../data/experiments/olist_delivery_performance_v1/summary.json),
[quy ước](../data/experiments/olist_delivery_performance_v1/protocol.json).

## 3. Những bảng đã tạo

Đã tạo 11 bảng dữ liệu, gồm:

- Một dòng cho mỗi đơn, tách quan sát ngày hẹn và điều kiện sử dụng số liệu.
- 28.404 dòng công đoạn: phê duyệt, chuẩn bị/bàn giao, vận chuyển và tổng thời gian.
- Tổng hợp hiệu suất chung và theo tháng mua hàng, khu vực, nhóm khoảng cách.
- Liên kết và thống kê cho 2.312 sản phẩm và 153 seller.
- Tổng hợp thời gian công đoạn theo trạng thái giao hàng và bối cảnh.
- Bảng riêng cho các tín hiệu đã lưu của thí nghiệm cũ.

Mỗi nhóm sản phẩm/seller đếm đơn duy nhất, không đếm lặp theo dòng hàng. Một đơn nhiều sản
phẩm có thể xuất hiện ở nhiều nhóm nên không cộng tổng các nhóm thành tổng đơn toàn hệ thống.
Seller gắn với một đơn không đồng nghĩa chịu trách nhiệm cho kết quả của đơn đó. Đơn nhiều
seller và đơn một seller được thống kê riêng.

Khoảng cách là khoảng cách đường thẳng từ tọa độ đại diện ZIP đã qua bộ lọc chất lượng hiện
có, không phải quãng đường giao thực tế. Các bảng mô tả bối cảnh, chưa xác lập quan hệ nhân quả.
Chưa có xếp hạng seller tốt/xấu hoặc ngưỡng thống kê mới cho từng công đoạn.

492 dòng tín hiệu cũ được ẩn vì có sự kiện thực tế sau mốc quan sát mới. Điều này ngăn cảnh
báo cũ tiết lộ kết quả giao hàng tương lai trong bảng phân tích mới. Những tín hiệu được giữ
vẫn là bằng chứng lịch sử, không trở thành một thực nghiệm chọn mô hình trước ranh giới mới.

Xem: [mô tả các bảng](../data/experiments/olist_delivery_performance_v1/README.md).

## 4. Bộ 20 đơn dành cho bạn

Đã chuẩn bị **20 đơn thật, 24 dòng thanh toán, 33 dòng hàng** từ bộ pilot gốc. Không sửa bộ
120 đơn và không tạo đơn giả. Các đơn và mốc sự kiện thực tế được chọn đều trước ranh giới
bảo vệ. Bộ này có cả trường hợp được và không được cảnh báo, nhưng không hiển thị kết quả
mô hình, nhóm lấy mẫu hoặc ánh xạ mã đơn cho người rà soát.

Mỗi đơn có ba phạm vi: quy trình/thời gian, thanh toán và nội dung đơn. Có 60 mục ghi nhận
đang trống, **0 mục đã đánh giá**. Chưa tạo buổi rà soát, nhận xét, nhãn chuyên gia hoặc
phê duyệt của thầy. Bộ pilot gốc là các đơn đủ điều kiện thời gian đã hoàn tất, nên vòng
này không đại diện đầy đủ cho đơn thiếu hoặc sai mốc trong toàn bộ dữ liệu.

Bạn dùng hai tài liệu này:

1. [Hướng dẫn rà soát](../data/experiments/olist_delivery_review20_v1/reviewer_packet/README.md).
2. [20 đơn cần rà soát](../data/experiments/olist_delivery_review20_v1/reviewer_packet/20_don_can_ra_soat.md).

Chỉ mở `reviewer_packet` trong lúc rà soát. Không mở `analyst_only` hoặc tự nối sang bảng
điểm mô hình. Đây là bảo vệ theo quy trình, không phải cơ chế phân quyền chặn mọi khả năng
tra cứu trên máy. Khi không đủ bằng chứng, ghi rõ điều còn thiếu và dùng `unresolved`.

Bạn có thể bắt đầu với phần quy trình/thời gian và gửi nhận xét theo mã BR. Phần mềm có
lệnh kiểm tra cấu trúc nhận xét nhưng chưa ghi nhận nhãn thay bạn. 20 đơn là vòng thử tiêu
chí, không mặc định đủ để chọn mô hình hoặc công bố Precision/Recall/F1.

## 5. Kiểm thử và bảo toàn

Hai module kiểm thử mới có 37 kiểm thử đều đạt. Chúng dùng các quan sát Olist đã lưu để
kiểm tra cùng ngày hẹn, thiếu dữ liệu, thời gian âm/bằng không, bảo vệ mốc quan sát,
đếm đơn duy nhất, tính đúng mẫu số, tái lập lựa chọn mẫu, giữ trống nhãn và không lộ mô hình.
Các kiểm tra từ chối dữ liệu hỏng/chỉnh sửa chỉ diễn ra trong bộ nhớ hoặc thư mục tạm,
không thêm dữ liệu giả vào nghiên cứu.

Các kết quả mới có manifest, schema, mã băm nguồn/code/output, kiểm tra đọc lại chính xác
và từ chối ghi đè thư mục cũ. Dữ liệu gốc, mô hình, ngưỡng và bộ pilot cũ không bị thay đổi.

Đợt kiểm tra liên quan mở rộng đạt **219/219 kiểm thử** trong 137,010 giây, gồm 37 kiểm thử
mới và 182 kiểm thử liên quan đã có. Đây không phải toàn bộ test suite của repository và
không phải độ chính xác mô hình. Đối chiếu mã băm trước/sau xác nhận 729 file cũ trong phạm
vi kiểm tra giữ nguyên, gồm 598 file dữ liệu/mô hình/kết quả; không có file cũ bị xóa.

## 6. Chưa làm và bước tiếp

- Chưa thực hiện 20 lượt rà soát thay bạn, chưa có bộ nhãn chính thức hoặc độ chính xác bất thường.
- Chưa chọn mô hình, thay ngưỡng, train lại hoặc chấm tập test cuối.
- Chưa có giao diện dashboard mới. Các bảng vừa tạo là đầu vào cho giao diện đó.
- Chưa hoàn tất Multi-Agent hoặc chứng minh near real-time bằng thực nghiệm phát lại sự kiện.

Tiếp theo: bạn thử tiêu chí trên 20 đơn, ghi cả các trường hợp chưa kết luận được. Chúng ta
hoàn thiện tiêu chí/bằng chứng và thiết kế đánh giá chính thức, rồi so sánh các detector
trên cùng mục tiêu. Hoàn thiện tự động hóa và dữ liệu BI có thể tiếp tục song song; không
dùng việc rà soát làm điều kiện người dùng phải duyệt từng đơn trong hệ thống vận hành.

## 7. Lệnh tái lập

```sh
.venv/bin/python scripts/03_anomaly_detection/build_delivery_performance.py --summary
.venv/bin/python scripts/03_anomaly_detection/prepare_delivery_review20.py --summary
PYTHONPATH=tests .venv/bin/python -m unittest test_delivery_performance test_delivery_review20 -v
PYTHONPATH=tests .venv/bin/python -m unittest test_delivery_performance test_delivery_review20 test_code_cleanup test_hierarchical_review test_temporal_evaluation test_data_disposition test_evidence_inspection test_integrated_evidence test_review_readiness test_business_review -v
```

Hai lệnh không có `--summary` tạo bản mới khi thư mục mặc định chưa tồn tại; khi đã có kết
quả chúng sẽ từ chối ghi đè. Không chạy lại để sửa bản đã đóng phiên bản.

Code: [hiệu suất giao hàng](../scripts/03_anomaly_detection/build_delivery_performance.py),
[chuẩn bị 20 đơn](../scripts/03_anomaly_detection/prepare_delivery_review20.py).
Chưa commit/push trong đợt này. Dữ liệu và mô hình bị loại khỏi Git theo cấu hình hiện có;
GitHub không thay thế bản sao lưu các kết quả thực nghiệm.

# Tiến độ: Phân tích bối cảnh và đề xuất kiểm tra tắc nghẽn

## Đã triển khai

Bổ sung theo phản hồi của giảng viên: không dừng ở danh sách đơn được cảnh
báo, mà nối thành luồng phân tích có bằng chứng và đề xuất xử lý có điều kiện.

`process_signals_v1 -> analyze_bottleneck_context.py -> nhóm bối cảnh ->
đối chiếu -> giả thuyết cần xác minh -> đề xuất kiểm tra/can thiệp có điều kiện`.

Đây là phần bổ sung vào pipeline hiện có, không thay đề tài thành dự báo
thời gian giao hàng. Không sửa code/snapshot đã phát hành, không tạo dữ
liệu giả, nhãn rà soát giả, xác suất bất thường hoặc xác nhận nguyên nhân.

Các file mới:

- `scripts/03_anomaly_detection/analyze_bottleneck_context.py`.
- `templates/bottleneck_context_v1/protocol.json` và `README.md`.
- `tests/test_bottleneck_context.py`: 21 kiểm thử.
- Kết quả: `data/experiments/olist_bottleneck_context_v1`.

## Phạm vi và công thức

Đầu vào tính toán là hai bảng order/stage signals đã đóng băng, gồm 1.621
đơn validation thật của ngành bed_bath_table. Kiểm tra nguồn có đọc lại các
bảng lineage/kiểm toán lịch sử nhưng không dùng nhãn cũ để làm đáp án hoặc
đưa đặc trưng test vào phép tính mới. Không tuyên bố validation là dữ liệu
mới độc lập hay chưa từng được phân tích trước đây.

Giữ 4.863 dòng cho ba công đoạn. Có 1.307 đơn chấm được ở mỗi công đoạn,
314 đơn còn lại vẫn giữ nguyên trạng thái và lý do chưa đánh giá được.

Tỷ lệ dấu hiệu = số đơn có công đoạn dài / số đơn chấm được trong nhóm.

So theo bang khách hàng: khớp nhóm khoảng cách, phạm vi một/nhiều seller và
tháng bắt đầu công đoạn. So theo tháng: khớp bang, nhóm khoảng cách và phạm
vi seller. Đối chứng là đơn ngoài nhóm đích, cùng công đoạn và validation.
Không có đối chiếu phù hợp thì để trống, không chuyển thành 0 hoặc tự nới
điều kiện. Thống kê theo khoảng cách chỉ là mô tả, không xếp đơn xa thành
tắc nghẽn chỉ vì thời lượng lớn hơn đơn gần.

Với ô bối cảnh k trong phần đủ điều kiện:

`w_k = số đơn đích ở ô k / tổng số đơn đích thuộc phần khớp`.

`Tỷ lệ đối chiếu = tổng(w_k * số cảnh báo đối chiếu ở ô k / số đơn đối chiếu ở ô k)`.

Xuất cả tỷ lệ nhóm đầy đủ và tỷ lệ phần khớp; hai mẫu số không được đánh
tráo. Cảnh báo gốc sử dụng ranh giới train theo bối cảnh, không phải tất cả
các đơn đều được so với cùng một số ngày cố định.

Quy ước báo cáo: mỗi ô ít nhất 5 đơn mỗi phía; phần khớp ít nhất 30 đơn mỗi
phía và bao phủ ít nhất 80% số đơn đích chấm được. Nhóm được đề xuất rà soát
khi có ít nhất 5 tín hiệu trong phần khớp và tỷ lệ cao hơn đối chiếu. Đây
không phải ngưỡng mô hình tối ưu, kiểm định ý nghĩa hay quy ước đã được thầy
xác nhận. Chênh lệch dương rất nhỏ vẫn có thể vào danh sách.

Mốc quan sát vẫn là `< 2018-05-24 11:07:21`. Khi so theo tháng, tháng 5 bị
loại khỏi cả phía đích và đối chứng vì chưa quan sát hết tháng. Các tháng
trước vẫn có thể thiếu theo dõi; bước này chưa giải quyết hoàn toàn sai
lệch do thời gian theo dõi khác nhau hoặc đơn chưa hoàn tất.

## Kết quả thực tế

| Công đoạn | Chấm được | Dấu hiệu dài | Tỷ lệ |
| --- | ---: | ---: | ---: |
| Phê duyệt | 1.307 | 41 | 3,14% |
| Chuẩn bị/bàn giao | 1.307 | 17 | 1,30% |
| Vận chuyển | 1.307 | 112 | 8,57% |

170 tín hiệu công đoạn thuộc 165 đơn. Có 149 đơn trễ hẹn trên phạm vi kết
quả giao hàng riêng; hai nhóm có giao nhau. Không được cộng thành số đơn lỗi.

Tạo 104 nhóm mô tả/đối chiếu; 14 nhóm đủ quy ước đối chiếu, trong đó 6 nhóm
được đưa vào danh sách rà soát. Hai chênh lệch lớn hơn trong bảng:

| Nhóm vận chuyển | Tín hiệu / đơn khớp | Tỷ lệ đích | Tỷ lệ đối chiếu có trọng số | Chênh lệch |
| --- | ---: | ---: | ---: | ---: |
| Bắt đầu vận chuyển tháng 3/2018 | 85/458 | 18,56% | 4,41% | 14,15 điểm % |
| Bang khách hàng MG | 30/141 | 21,28% | 10,18% | 11,10 điểm % |

Nhóm tháng 3 có 553 đơn, 521 chấm được, 458 khớp; đối chứng khớp có 437
đơn bắt đầu vận chuyển tháng 4. Nhóm MG có 195 đơn, 156 chấm được, 141
khớp; đối chứng khớp có 775 đơn ở các bang khác. Tỷ lệ đối chiếu là tỷ lệ
có trọng số, không lấy một số đếm thô chia cho 437 hoặc 775 để thay thế.

Bốn nhóm còn lại có chênh lệch nhỏ, khoảng 0,09-0,38 điểm phần trăm.
Không coi chúng là những khác biệt đáng kể hoặc lý do đủ để can thiệp.
Sáu nhóm có giao nhau; phần tín hiệu khớp thuộc 118 đơn duy nhất, không
phải 6 nguyên nhân hay 118 lỗi đã được xác minh.

## Đầu ra và đề xuất

6 CSV được tạo:

- `stage_context.csv`: bằng chứng của 4.863 dòng đơn/công đoạn.
- `stage_overview.csv`: tổng hợp ba công đoạn, giữ các mẫu số riêng.
- `context_groups.csv`: đầy đủ 104 nhóm, không chỉ các nhóm nổi bật.
- `matched_cells.csv`: 605 ô, 195 ô đủ điều kiện; có trọng số và lý do loại.
- `group_membership.csv`: 34.498 dòng vai trò đích/đối chiếu, truy vết về đơn
  và công đoạn. Không phải 34.498 đơn hay nhãn mới.
- `investigation_actions.csv`: 6 đề xuất, tách quan sát, giả thuyết, dữ liệu
  cần có, hành động kiểm tra và can thiệp có điều kiện.

Có `bao_cao_boi_canh.md` bằng tiếng Việt, summary, protocol và manifest/hash.

Ví dụ đề xuất cho nhóm vận chuyển: rà soát tracking, tuyến thực tế, dịch
vụ và các lần giao. Nếu có bằng chứng xác nhận điểm chờ, mới cân nhắc thử
điều chỉnh tuyến hoặc lịch. Không kết luận kẹt xe từ bang khách hàng, không
quy lỗi seller/carrier và không tự kéo dài cam kết giao hàng để che trễ.

Các đề xuất là checklist theo quy tắc, chưa phải kết quả của multi-agent AI.
Chưa có dữ liệu can thiệp nên chưa tuyên bố giải pháp đã giảm tắc nghẽn.

## Kiểm chứng và bảo toàn

21 kiểm thử mới; chạy cùng process signals, evidence criteria và signal
detector comparison đạt **74/74** trong 44,327 giây. Không phải toàn bộ test
của repository. Kiểm tra mẫu số, mã đơn, loại trừ tự đối chiếu, khớp bối cảnh,
trọng số, phần thiếu, thời điểm, tháng chưa hoàn tất, đọc/ghi CSV, chống sửa
kết quả và chống ghi đè snapshot. Các tình huống lỗi dùng bản sao tạm hoặc
tập con dữ liệu thật trong bộ nhớ, không đưa vào kết quả nghiên cứu.

Kiểm tra hash 855 file đã có từ bước trước: không đổi và không bị xóa.
Không train lại, đổi ngưỡng, chọn ML cuối, chấm test cuối hoặc gán nhãn mới.

## Bước kế tiếp

Ưu tiên kiểm tra độ ổn định của hai nhóm vận chuyển trên các cửa sổ quan
sát/thời gian theo dõi phù hợp trước khi trình bày thành kết luận mạnh.
Có thể đối chiếu các đơn thật qua bảng membership, sau đó đưa các bảng và
đề xuất vào dashboard. Không cần import thêm để tiếp tục phân tích hiện có.

Muốn xác nhận nguyên nhân thực tế cần log vận hành mà Olist hiện tại chưa
cung cấp: tracking chi tiết, carrier/dịch vụ, tồn kho, mốc picking/packing,
lịch lấy hàng hoặc thông tin sự cố. Việc thiếu log không ngăn phát hiện
dấu hiệu, nhưng giới hạn mức khẳng định về nguyên nhân và hiệu quả giải pháp.

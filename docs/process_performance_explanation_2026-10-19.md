# Hoàn tất đầu ra diễn giải từng đơn, 19/10/2026

## Phạm vi

Triển khai ba yêu cầu đã thống nhất trên 1.621 đơn validation Olist thật,
ngành `bed_bath_table`, bằng một lớp đầu ra riêng. Nguồn là bản đã đóng
phiên bản `olist_process_signals_v1`; không tạo đơn, thời điểm, kế hoạch
công đoạn hoặc nhãn đánh giá giả. Không chạy lại huấn luyện hay đổi ngưỡng.

## Đã bổ sung

1. Mọi đơn có trạng thái **Chưa xác minh được cơ sở ngày hẹn**. Nguồn hiện
   có chưa cung cấp phương pháp tạo ngày hẹn; không suy ra hẹn ngẫu nhiên
   hoặc hẹn sai. Chỉ đề xuất rà soát, không sửa ngày hẹn lịch sử.
2. Mỗi công đoạn có thời lượng thực tế, trung vị nhóm train tham chiếu,
   chênh lệch có dấu và diễn giải nhanh/chậm. Công thức là thực tế trừ
   trung vị; âm là nhanh hơn, dương là chậm hơn. Đây không phải kế hoạch
   vận hành đã xác minh. Cờ và ngưỡng cảnh báo dài/ngắn cũ được giữ riêng.
3. Giao sớm không xóa cảnh báo công đoạn dài. Chỉ kết luận không có công
   đoạn nhanh hơn khi cả ba công đoạn đủ so sánh. Thiếu dữ liệu hoặc thiếu
   tham chiếu giữ là chưa đủ bằng chứng; không chuyển thành bình thường.

Thời lượng bằng 0 không tự thành bằng chứng xử lý nhanh hoặc lỗi nhập liệu.
Không cộng chênh lệch các công đoạn để giải thích chính xác số ngày giao
sớm/trễ: nhóm train tham chiếu và ngày hẹn là hai chuẩn so sánh khác nhau.
Phần diễn giải không xác nhận nguyên nhân, trách nhiệm seller hoặc gian lận.

## Kết quả từ dữ liệu thật

| Nội dung | Kết quả |
| --- | ---: |
| Đơn validation được giữ lại | 1.621 |
| Dòng công đoạn | 4.863 |
| Đơn đủ so sánh nhanh/chậm cả ba công đoạn | 1.307 |
| Đơn chưa đủ điều kiện so sánh đầy đủ | 314 |
| Dòng công đoạn nhanh hơn trung vị | 1.965 |
| Dòng công đoạn chậm hơn trung vị | 1.956 |
| Dòng công đoạn chưa so sánh được | 942 |

Trong **1.193 đơn giao sớm**, ba nhóm loại trừ nhau là:

| Diễn giải giao sớm | Số đơn |
| --- | ---: |
| Có ít nhất một công đoạn nhanh hơn, đã so đủ ba công đoạn | 1.063 |
| Không có công đoạn nhanh hơn, đã so đủ ba công đoạn | 90 |
| Chưa đủ dữ liệu/tham chiếu để kết luận | 40 |

Có **61 đơn giao sớm vẫn giữ cảnh báo công đoạn dài**. Trong đó 14 đơn
không có công đoạn nhanh hơn khi so đủ. Đây là các nhóm giao nhau với bảng
trên, không được cộng thành một tổng mới hoặc gọi là nhãn lỗi đã xác nhận.

Ví dụ thật: `0f916489801ab47804cc7da10fe0dd6d` giao sớm 8 ngày lịch.

| Công đoạn | Thực tế (ngày) | Trung vị (ngày) | Chênh lệch (ngày) | Cảnh báo dài |
| --- | ---: | ---: | ---: | --- |
| Phê duyệt | 1,007535 | 0,012176 | +0,995359 | Không |
| Chuẩn bị/bàn giao | 8,944155 | 1,912228 | +7,031927 | Có |
| Vận chuyển | 6,053252 | 4,283854 | +1,769398 | Không |

Cả ba đều chậm hơn trung vị, nhưng chỉ khâu chuẩn bị/bàn giao vượt ngưỡng
cảnh báo dài. Đơn này không cần bị ép có một khâu nhanh để giải thích giao
sớm. Ngày hẹn được giữ nguyên và cơ sở của ngày hẹn vẫn chưa xác minh.

## Đầu ra và cách xem

- [Báo cáo tự sinh từ đơn thật](../data/experiments/olist_process_performance_explanation_v1/bao_cao_dien_giai.md).
- [Theo từng đơn](../data/experiments/olist_process_performance_explanation_v1/order_explanations.csv): một dòng/đơn, cơ sở ngày hẹn, phạm vi so sánh, danh sách công đoạn nhanh/chậm và diễn giải.
- [Theo từng công đoạn](../data/experiments/olist_process_performance_explanation_v1/stage_comparisons.csv): giữ mọi cột nguồn, thêm loại tham chiếu, chênh lệch và diễn giải tiếng Việt.
- [Code](../scripts/03_anomaly_detection/explain_process_performance.py), [quy ước](../templates/process_performance_explanation_v1/protocol.json), [kiểm thử](../tests/test_process_performance_explanation.py).

CSV giữ số liệu đầy đủ; các bảng Markdown chỉ làm tròn để đọc. Các cột mới
không thay `assessable`, `long_stage_warning`, `short_stage_note`,
`calendar_late_signal` hoặc `primary_attention_status`. Trạng thái không
biết vẫn là giá trị thiếu, không bị thay bằng `False` hay số 0.

```sh
PYTHONPATH=scripts/01_data_preparation:scripts/03_anomaly_detection .venv/bin/python -B scripts/03_anomaly_detection/explain_process_performance.py --summary
PYTHONPATH=scripts/01_data_preparation:scripts/03_anomaly_detection .venv/bin/python -B scripts/03_anomaly_detection/explain_process_performance.py --order 0f916489801ab47804cc7da10fe0dd6d
```

Hai lệnh chỉ kiểm tra và đọc bản đã lưu. Lệnh tạo bản mới từ chối ghi đè
và các đường dẫn được bảo vệ. Manifest lưu schema, nguồn và SHA-256 của
code/quy ước/kết quả; thay đổi chính sách cần phiên bản mới.

## Kiểm thử đã chạy

**91/91 kiểm thử đạt**, gồm 17 kiểm thử mới, trong 219,383 giây. Phạm vi
gồm lớp diễn giải, dấu hiệu quy trình, tiêu chí bằng chứng, phân tích bối
cảnh và bảng so sánh detector; không phải toàn bộ test suite của repository.

Bao phủ phép trừ chính xác và tách cờ ngưỡng, đơn giao sớm vẫn có cảnh báo,
không ép tìm công đoạn nhanh, dữ liệu thiếu/so sánh một phần, bảo toàn mọi
cột nguồn, mốc quan sát, CSV đọc lại đúng, phát hiện file bị sửa và từ chối
ghi đè. Kiểm thử ngăn nạp/fit model và kiểm tra không chấm test cuối.
Lệnh tra cứu ví dụ thật ở trên cũng đã chạy thành công trên bản được lưu.

```sh
MPLCONFIGDIR=/private/tmp/kltn-check-mpl PYTHONPATH=tests .venv/bin/python -B -m unittest test_process_performance_explanation test_process_signals test_evidence_criteria test_bottleneck_context test_signal_detector_comparison -v
```

## Bảo toàn và giới hạn

Đối chiếu SHA-256 trước/sau xác nhận **705 file dữ liệu, mô hình và kết quả
cũ** không bị thay đổi. Bản mới có bảy file, ghi ở thư mục riêng.
Phần tính diễn giải chỉ nhận hai bảng `order_signals.csv` và
`stage_signals.csv`. Kiểm tra nguồn đệ quy có đọc các bảng kiểm toán/import/
rà soát lịch sử để xác minh, không truyền các nhãn đó vào phép tính diễn giải.

Chưa tính AUC/AP hay độ chính xác bất thường độc lập, chưa chọn detector,
chưa chấm test cuối, chưa triển khai dashboard/Multi-Agent/near real-time.
Ngưỡng cảnh báo hiện tại vẫn là quy ước phát triển, không được kết luận tối
ưu từ việc bộ kiểm thử phần mềm chạy đạt.

Không cần import thêm dữ liệu cho lớp diễn giải này. Muốn xác minh cơ sở
ngày hẹn hoặc nguyên nhân vận hành cần nguồn bằng chứng tương ứng. Bước
tiếp theo là kiểm tra phạm vi/bằng chứng của bộ đối chiếu độc lập theo tiêu
chí đã thống nhất, không tự lấy cờ mô hình làm đáp án để chọn chính nó.

# Tiến độ: Chuẩn hóa đầu ra phát hiện dấu hiệu

## Phạm vi đã làm

Hoàn thành bước 1 và 2 theo trao đổi: tổ chức đầu ra theo từng đơn/công đoạn
và tách phát hiện dấu hiệu khỏi yêu cầu xác định nguyên nhân gốc. Chưa chạy
bước so sánh/chọn lại mô hình ML. Không đổi đề tài thành dự báo giao hàng.

Đã thêm:

- `scripts/03_anomaly_detection/build_process_signals.py`.
- `templates/process_signals_v1/protocol.json` và `README.md`.
- `tests/test_process_signals.py` với 19 kiểm thử.
- Kết quả riêng tại `data/experiments/olist_process_signals_v1`.

Không sửa/xóa code, dữ liệu hoặc snapshot cũ. Các trường nguyên nhân/lỗi
seller trong kết quả lịch sử vẫn được giữ để tái lập nghiên cứu; chúng không
tham gia quyết định của lớp đầu ra mới. Không gọi API ngoài, tạo dữ liệu giả,
nhãn người rà soát giả hay suy diễn nguyên nhân.

## Đầu ra sử dụng

| File | Nội dung |
| --- | --- |
| `order_signals.csv` | 1.621 đơn validation thật, mỗi đơn một dòng; trạng thái giao hàng, cảnh báo dài, ghi chú ngắn, phạm vi đã chấm và chất lượng nguồn. |
| `stage_signals.csv` | 4.863 dòng cho ba công đoạn của từng đơn, gồm cả phần chưa đánh giá được; giữ mốc quan sát, thời lượng và tham chiếu. |
| `signal_evidence.csv` | 505 dòng bằng chứng: 149 trễ hẹn, 170 công đoạn dài, 186 công đoạn ngắn. Một đơn có thể có nhiều dòng. |
| `reference_groups.csv` | 119 nhóm train tham chiếu, định nghĩa nhóm, số đơn, thống kê và ranh giới quyết định chính xác. |
| `evaluation_contract.csv` | Tách kiểm tra code, đối chiếu quy tắc, đánh giá dấu hiệu độc lập và kết luận nguyên nhân. |
| `bao_cao_dau_hieu.md` | Báo cáo tiếng Việt và ví dụ từ các đơn thật. |

Có manifest, hash, protocol và summary để kiểm tra nguồn và tái lập kết quả.

## Kết quả thực tế

- 1.193 đơn giao sớm, 17 đúng ngày hẹn, 149 trễ hẹn, 211 chưa đủ thông tin
  kết quả giao hàng, 51 tách riêng do mâu thuẫn nguồn.
- 1.307 đơn đủ điều kiện so sánh cả ba công đoạn; 314 đơn chưa đủ điều kiện.
  Đây là phạm vi của chính sách nguồn hiện tại, không phải toàn bộ Olist.
- 165 đơn có ít nhất một công đoạn dài; 178 đơn có ghi chú thời gian ngắn dương.
- 103 đơn vừa trễ hẹn vừa có công đoạn dài. Tổng hợp hai dấu hiệu chính có
  **211 đơn**: 149 + 165 - 103. Không được gọi đây là 211 bất thường đã xác nhận.
- 61 đơn giao sớm vẫn có công đoạn dài; 1 đơn đúng ngày hẹn có công đoạn dài.
- 11 đơn trễ hẹn nhưng chưa đủ điều kiện so sánh công đoạn vẫn giữ dấu hiệu
  trễ hẹn. Chưa chấm được công đoạn không có nghĩa là không có dấu hiệu khác.
- 1.107 đơn không có dấu hiệu chính theo phần đã chấm đầy đủ; 303 đơn chưa
  đánh giá đầy đủ và chưa quan sát thấy dấu hiệu chính. Không coi hai nhóm
  này là nhãn bình thường đã được kiểm chứng.

Các con số về hiệu suất, phạm vi chấm và cảnh báo là các cách phân nhóm khác
nhau, có giao nhau. Không cộng tất cả thành tổng đơn.

## Cách hiểu ngưỡng

Giữ nguyên quy tắc hiện có: thời lượng dương và F(d) > 0,95 thì cảnh báo dài;
thời lượng dương và F(d) < 0,05 thì ghi chú ngắn. F(d) là tỷ lệ thời lượng
train tham chiếu nhỏ hơn hoặc bằng d, không phải xác suất bất thường.

Bổ sung ranh giới số ngày tương đương chính xác với quy tắc trên, có toán tử
so sánh rõ ràng. P95 nội suy là thống kê mô tả, không luôn trùng ranh giới
quyết định của ECDF. Không đổi ngưỡng, không tối ưu thêm trên validation.

Ví dụ đơn `0f916489801ab47804cc7da10fe0dd6d` (DS003 trước đây): giao sớm 8 ngày,
nhưng chuẩn bị/bàn giao mất khoảng 8,944155 ngày. Nhóm tham chiếu có 648 đơn,
trung vị khoảng 1,912228 ngày, ranh giới cảnh báo dài khoảng 7,508796 ngày
với toán tử >= và thời lượng dương. Kết luận là dấu hiệu công đoạn kéo dài,
không phải bằng chứng thiếu hàng, nhân viên chậm hoặc seller có lỗi.

## Kiểm chứng

19 kiểm thử mới đạt. Chạy cùng các bộ liên quan đạt **93/93 kiểm thử** trong
36,132 giây: process signals, evidence criteria, delivery supplement,
delivery performance và rule annotations20. Không phải toàn bộ test của repo.

Các kiểm tra bao gồm đối chiếu giá trị thật, ngày hẹn, thời gian bị che sau
mốc quan sát, tham chiếu train, ranh giới ECDF khi có giá trị trùng, trạng thái
thiếu dữ liệu, cờ cảnh báo trùng với nguồn và đọc/ghi CSV không mất thông tin.
Loại các cột nguyên nhân/nhãn rộng khỏi đầu vào vẫn cho cùng kết quả.

Kiểm thử trạng thái thiếu một công đoạn chỉ giữ lại một phần các dòng quan
sát thật trong bộ nhớ; kiểm thử chống sửa dữ liệu dùng bản sao tạm. Không
đưa các tình huống kiểm thử này vào số liệu nghiên cứu hoặc output chính.

## Còn lại

Bước tiếp theo là so sánh các detector hiện có theo mục tiêu phát hiện dấu
hiệu và định vị công đoạn, trên cùng phạm vi đánh giá. Không yêu cầu biết
nguyên nhân gốc. Có thể chọn cấu hình chạy thử theo tiêu chí vận hành công
khai; tuyên bố mô hình chính xác nhất cần đánh giá độc lập phù hợp. Nhãn do
chính ngưỡng sinh ra không phải đáp án độc lập để chấm ngưỡng đó.

Chưa tạo nhãn tham chiếu độc lập, chưa tính precision/recall/F1, chưa chọn
mô hình ML cuối, chưa chấm test cuối, chưa kích hoạt chia 80/20 đề xuất.
Chưa triển khai dashboard, multi-agent hay kiểm chứng near real-time ở bước
này. Các kết quả vẫn là hồi cứu với giới hạn đơn đủ quan sát và tham chiếu
bối cảnh chưa đầy đủ. Hiện không cần nhập thêm data để dùng đầu ra mới.

Lệnh tạo/kiểm tra/tra cứu có trong `templates/process_signals_v1/README.md`.

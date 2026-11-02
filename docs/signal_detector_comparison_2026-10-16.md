# Tiến độ: So sánh detector theo dấu hiệu quy trình

## Đã hoàn thành

Thêm `scripts/03_anomaly_detection/compare_signal_detectors.py`, bộ quy ước
`templates/signal_detector_comparison_v1` và 18 kiểm thử mới. Đã chạy trên
dữ liệu Olist thật và xuất riêng vào:

`data/experiments/olist_signal_detector_comparison_v1`

Đây là bước tiếp sau `olist_process_signals_v1`: đối chiếu detector với dấu
hiệu công đoạn dài đã được định nghĩa. Không yêu cầu biết nguyên nhân gốc,
không thay đề tài thành dự báo giao hàng, không tạo dữ liệu hoặc nhãn giả.

## Phạm vi so sánh

- Có 120 cấu hình, 21 cách tính điểm, 7 nhóm phương pháp: Isolation Forest,
  LOF novelty, One-Class SVM, IQR, MAD, phân vị trên và ensemble thứ hạng.
- Đây là các detector dùng ba thời lượng: phê duyệt, chuẩn bị/bàn giao và
  vận chuyển. Không coi 120 cấu hình là 120 mô hình độc lập.
- Không gộp profile thanh toán/đơn hàng hoặc các mô hình hồi quy phân vị
  theo bối cảnh vào bảng này vì khác đầu vào và mục tiêu. Những kết quả đó
  vẫn được giữ nguyên cho phạm vi nghiên cứu tương ứng.
- Từ 1.621 đơn validation của lớp dấu hiệu, có 1.307 đơn đủ tham chiếu theo
  bối cảnh và đủ kết quả detector đã lưu. Tất cả cấu hình dùng đúng cùng
  1.307 đơn này; 314 đơn ngoài phạm vi không bị coi là âm tính.
- Kiểm tra lại mốc thời gian, ba thời lượng đầu vào, điểm số và cờ cảnh báo
  theo ngưỡng gốc. Không đưa sự kiện sau mốc quan sát hiện tại vào so sánh.
- Bản replay cũ có README sai hash không được sử dụng; dùng bản đã kiểm tra
  `olist_validation_batches_comparison_input_v1`.

Trong tập chung có 165 đơn mang dấu hiệu công đoạn dài, 138 đơn trễ ngày hẹn
và 200 đơn có ít nhất một trong hai dấu hiệu. Báo cáo trước có 149 đơn trễ
và 211 đơn có dấu hiệu chính trên toàn bộ 1.621 đơn; chênh lệch là do 11 đơn
trễ chưa đủ tham chiếu công đoạn, không phải số liệu bị sửa hoặc mất.

## Kết quả dễ hiểu

Chọn một mặt cắt tham số đã khai báo trước khi tính bảng mới: tỷ lệ đuôi
hiệu chuẩn 5% cho các cách tính điểm ML/ensemble, cùng 5 baseline cố định.
Mặt cắt này gồm 18 cấu hình, không phải nhóm thắng cuộc. Tỷ lệ 5% không phải
xác suất bất thường hoặc cam kết sẽ cảnh báo đúng 5% trên validation.

| Cấu hình | Tổng cảnh báo | Trùng dấu hiệu công đoạn dài | Ngoài dấu hiệu dài | Dấu hiệu dài không được mô hình cảnh báo |
| --- | ---: | ---: | ---: | ---: |
| `if_raw_256_tail050` | 53 | 51 | 2 | 114 |
| `if_raw_512_tail050` | 56 | 53 | 3 | 112 |
| `upper_q950` | 241 | 149 | 92 | 16 |
| `ensemble_rank_mean_tail050` | 55 | 39 | 16 | 126 |

Cách đọc: Isolation Forest raw ở các cấu hình trên cảnh báo ít hơn và
phần lớn trùng với quy tắc công đoạn dài; phân vị trên bao phủ nhiều dấu
hiệu dài hơn nhưng tạo thêm nhiều cảnh báo ngoài quy tắc. Đây là đánh đổi
cần xem xét cho mục tiêu thời gian kéo dài, không chứng minh một phương
pháp tốt hơn trên mọi dạng bất thường.

Không gọi hai cột bất đồng là false positive/false negative thực tế. Detector
và quy tắc đều dùng thời lượng; sự đồng thuận không phải nhãn độc lập. Một
cảnh báo ngoài quy tắc có thể là thời gian ngắn, tổ hợp thời lượng khác thường
hoặc cảnh báo không hữu ích. Chưa tự gán một trong các cách giải thích đó.

## Đã thu hẹp việc đối chiếu thế nào?

Tính nhóm đánh đổi không bị ứng viên khác lấn át theo hai mục tiêu riêng:
tăng số dấu hiệu dài trùng và giảm số cảnh báo ngoài dấu hiệu dài. Đối thủ
phải không kém cả hai mặt và tốt hơn ít nhất một mặt mới được coi là lấn át.
Không tạo tổng điểm với trọng số tùy ý, giữ cả các trường hợp hòa.

Có 11 cấu hình trong nhóm này, thuộc Isolation Forest raw, phân vị trên và
MAD raw. Đây chỉ là nhóm đối chiếu sâu theo quy tắc công đoạn dài, không
phải mô hình tối ưu về độ chính xác. Không xóa hoặc tự loại các ứng viên
khác khỏi nghiên cứu. Danh sách đầy đủ ở `rule_tradeoff_frontier.csv`.

Xuất thêm `focus_disagreement_cases.csv`: 3.618 cặp cấu hình/đơn bất đồng
thuộc 646 đơn thật trong mặt cắt 18 cấu hình. Mỗi dòng có mã đơn, trạng thái
giao hàng, nhóm khoảng cách, điểm số, ngưỡng và danh sách công đoạn dài theo
tham chiếu. Nhãn độc lập để trống. Đây là bằng chứng đã lộ kết quả mô hình,
không được gọi là bộ rà soát mù và không phải 3.618 đơn lỗi.

## Các file chính

- `comparison_scope.csv`: vì sao một đơn được hoặc chưa được đối chiếu.
- `common_cases.csv`, `common_scores.csv`, `common_flags.csv`: cùng tập đơn,
  điểm số và quyết định gốc, giúp kiểm tra số liệu.
- `candidate_comparison.csv`: toàn bộ 120 cấu hình.
- `focus_comparison.csv`: mặt cắt 18 cấu hình dễ đọc.
- `stage_overlap.csv`: đồng xuất hiện với dấu hiệu từng công đoạn, không
  phải giải thích thuộc tính gây điểm ML hoặc nguyên nhân gây trễ.
- `group_comparison.csv`: số liệu và mẫu số từng tháng, nhóm khoảng cách.
- `family_summary.csv`: khoảng kết quả của từng nhóm phương pháp.
- `bao_cao_so_sanh.md`: báo cáo tiếng Việt đầy đủ.

Số cảnh báo thêm nếu ghép ML với chính sách hiện tại cũng được tính riêng.
Đây là tình huống đối chiếu, chưa kích hoạt chính sách ghép. Không giảm
cảnh báo hiện có chỉ vì ML chưa cảnh báo, không tự biến thời gian ngắn
hoặc giao sớm thành nhãn bất thường.

## Kiểm chứng và giới hạn

Chạy 73/73 kiểm thử liên quan đạt trong 33,317 giây: 18 kiểm thử mới, các bộ
process signals, evidence criteria và process candidate comparison. Không
phải toàn bộ test của repository. Kiểm tra gồm phạm vi đơn, ranh giới thời
gian, công thức giao tập, mẫu số nhóm, ngưỡng gốc, cặp bất đồng, nhóm đánh
đổi, đọc/ghi CSV và từ chối ghi đè hay dữ liệu bị sửa.

Không deserialize mô hình, train lại, thay ngưỡng, tạo nhãn, chấm test cuối
hoặc sửa chính sách cảnh báo chính. Chưa tính độ chính xác độc lập hoặc
chọn detector ML cuối cùng. Kết quả vẫn là hồi cứu trên validation đã từng
được dùng; không phải dữ liệu mới độc lập, near real-time hay kiểm chứng
hiệu quả khi đổi sàn.

## Tiếp theo

Dùng các đơn bất đồng đã xuất để đối chiếu tính hữu ích của dấu hiệu trong
phạm vi đã thống nhất. Với mục tiêu công đoạn quá dài, ưu tiên xem sâu các
cấu hình Isolation Forest raw và phân vị trên thể hiện hai cách đánh đổi
rõ rệt, nhưng giữ các phương pháp khác làm đối chứng. Có thể chốt cấu hình
vận hành thử với tiêu chí công khai; kết luận chính xác nhất vẫn cần đánh
giá độc lập phù hợp, không cần điều tra nguyên nhân gốc cho từng đơn.

Không cần import thêm data để đọc kết quả hoặc tiếp tục đối chiếu hiện tại.

# Ghi nhận đánh giá trên bằng chứng pilot hiện tại

## Mục đích

Luồng nhập mới dùng trực tiếp hồ sơ công đoạn và đối chiếu 120 đơn đã có.
Không tạo thêm mẫu, không thay đổi quy ước đã thống nhất, không yêu cầu phê
duyệt lại và không ghi đè bộ pilot hay nhật ký cũ.

Có 118 đơn được xem xét; BR0043 và BR0119 giữ riêng theo ranh giới test đề
xuất. Mỗi đơn có bốn phạm vi: ngày hẹn, đặt-duyệt, duyệt-bàn giao và vận
chuyển. Vì vậy có 472 vị trí nhận xét, không phải 472 đơn hay yêu cầu phải
gán nhãn ngay toàn bộ. Có thể bắt đầu từng đơn, từng công đoạn.

Đây là đánh giá có hỗ trợ máy: hồ sơ hiển thị cả tham chiếu và cờ quy tắc.
Không gọi quy trình này là rà soát mù hoặc tự động xem nhận xét là nhãn
kiểm chứng độc lập. Công cụ chỉ kiểm tra dữ liệu nhập và nguồn truy vết;
nó không xác thực danh tính người nhập hoặc tính đúng đắn của nhận xét.

## Mỗi nhận xét trả lời gì?

Trong đúng phạm vi được chọn, bằng chứng có hỗ trợ việc cần kiểm tra thêm
hay không? Không hỏi người rà soát phải biết nguyên nhân gốc, và không yêu
cầu dự đoán ngày giao mới.

| Quyết định | Ý nghĩa |
| --- | --- |
| `needs_investigation` | Người thực hiện thấy có dấu hiệu đáng kiểm tra trong phạm vi này và giải thích bằng chứng. Không đồng nghĩa gian lận, lỗi đã xác minh hay biết nguyên nhân. |
| `no_supported_issue` | Chưa thấy căn cứ yêu cầu kiểm tra trong phạm vi đủ dữ liệu đang xét. Không có nghĩa toàn bộ đơn bình thường. |
| `insufficient_evidence` | Chưa thể kết luận; phải nói rõ còn thiếu gì, không chuyển thành nhãn âm. |

Giữ riêng nhận xét về ngày hẹn và công đoạn: giao sớm không xóa cảnh báo
công đoạn; chậm hơn trung vị không tự trở thành vượt ngưỡng. Không cần tìm
ra một công đoạn nhanh nếu số liệu không thể hiện. Cơ sở đặt ngày hẹn vẫn
chưa xác minh, không suy ra ngày hẹn ngẫu nhiên hoặc sai.

Không cần biết nguyên nhân để ghi nhận dấu hiệu có ích. Nếu chưa biết
phân loại nhập sai hay dị biệt hợp lệ, dùng `unclassified`. Hai phân loại
`data_error` và `valid_deviation` chỉ được ghi kèm nguồn bổ sung và giải
thích riêng; chúng vẫn là nhận định được khai báo, không được công cụ
xác nhận. Không tự loại dữ liệu hoặc train lại từ các nhận xét này.

## Cách nhập

Chạy từ thư mục project. Không cần cài thêm thư viện hoặc import dữ liệu mới.

```sh
.venv/bin/python -B scripts/03_anomaly_detection/record_pilot_judgments.py --status
.venv/bin/python -B scripts/03_anomaly_detection/record_pilot_judgments.py --preview BR0001
.venv/bin/python -B scripts/03_anomaly_detection/record_pilot_judgments.py --template BR0001 --scope approval
```

Các lệnh trên chỉ đọc, không tạo nhật ký, không điền quyết định. Mẫu trả về
để trống nhận xét. Các phạm vi hợp lệ: `delivery_promise`, `approval`,
`preparation_handoff`, `transport`.

Bạn cũng có thể trả lời bằng tiếng Việt trong cuộc trao đổi: mã đơn,
công đoạn, quyết định, lý do và phần còn thiếu. Trợ lý chuyển đúng nhận xét
của bạn sang cấu trúc nhập, không tự viết thay rồi nhận là đánh giá của bạn.
Nhận xét có AI tham gia quyết định phải mang nguồn `ai_assisted`.

`evidence_fields` trích đúng các trường trong đầu ra preview. Quyết định
có kết luận phải dẫn mốc đầu/cuối và bối cảnh cần thiết. Một công đoạn
không đủ điều kiện đánh giá trong cửa sổ đóng băng chỉ nhận trạng thái
chưa đủ bằng chứng; hồ sơ bổ sung không được dùng để lách cửa sổ này.

Các loại cơ sở nhận xét (`basis`): quan sát nguồn (`source_observations`),
nguồn và thống kê (`source_and_statistical_reference`), nguồn và tài liệu
bổ sung (`source_and_additional_evidence`), hoặc chỉ đọc kết quả máy
(`model_output_only`). Loại cuối vẫn được lưu nhưng không được xem là
kiểm chứng độc lập. Không có loại nào tự trở thành ground truth.

Nguồn bổ sung là danh sách `{path, sha256, locator, supports}`: đường dẫn
tuyệt đối tới file thực, mã SHA256 đúng, vị trí trích dẫn và nội dung được
hỗ trợ. Công cụ kiểm tra file không đổi, không chứng nhận nội dung là đúng.
Không tải file lên dịch vụ ngoài. Nguồn trong quá khứ bị thay đổi sẽ làm
kiểm tra nhật ký dừng lại, không âm thầm diễn giải lại nhận xét cũ.

Kiểm tra file nhận xét có thực mà chưa lưu:

```sh
.venv/bin/python -B scripts/03_anomaly_detection/record_pilot_judgments.py --check-note /absolute/path/to/note.json
```

Để lưu, dùng `--record-note`, khai báo `--reviewer-id`, `--origin`,
`--model-exposure` và `--expected-journal-sha256` lấy từ `--status`.
Chỉ khi nhật ký chưa tồn tại mới dùng giá trị `absent`.
`--origin human_declared` cần thêm `--human-decision-confirmed` và phải có
nhận xét thực sự do người đó đưa ra, không phải lựa chọn mặc định của trợ lý.
`--origin ai_assisted`, `automated` hoặc `software_test` không được bật cờ này.
Mức tiếp xúc là `seen_model_outputs` hoặc `unknown`; không khai báo mù cho
luồng đã hiển thị cờ/thống kê. Các tham số không xác thực người đánh giá.

## Lịch sử và tiến độ

Nhật ký chỉ được tạo sau lần ghi nhận thực sự đầu tiên, mặc định ở
`data/manual_review/pilot_signal_judgments_v1/journal.json`. Đọc lịch sử
bằng `--history`. Mỗi lần sửa nối thêm một bản ghi, chỉ thay thế hiệu lực
nhận xét trước của cùng người, cùng nguồn nhận xét, đơn và phạm vi.
Không xóa ý kiến khác, không để nhận xét AI thay thế nhận xét người.

Nguồn bằng chứng, code, lý do, các giá trị đã trích, thời điểm ghi và chuỗi
hash được kiểm tra. Khóa ghi và hash phiên bản ngăn các lệnh cùng luồng
ghi đè thay đổi mới hơn. Hash không phải chữ ký số hoặc xác thực tác giả.

Tiến độ đếm riêng người, AI, tự động và kiểm thử; báo riêng chưa rà soát,
chưa đủ bằng chứng và bất đồng. Bất đồng không được bỏ phiếu hoặc giải
quyết tự động. Không cộng số nhận xét của nhiều người thành số đơn độc lập.

## Giới hạn đánh giá

Chưa tính precision/recall/F1/AUC, không chọn mô hình từ nhật ký này.
Trước khi dùng nhận xét để so sánh mô hình phải xác định đúng mục tiêu,
mức độc lập của bằng chứng và người đánh giá, xử lý bất đồng/thiếu dữ liệu,
phạm vi lấy mẫu và chỉ số chọn mô hình. Các quy ước Q1-Q5 đã chốt vẫn giữ
nguyên; không cần chốt lại vì công cụ mới.

Pilot đã được xem kết quả máy và lấy mẫu phân tầng theo tháng/cờ cũ.
Tỷ lệ thô không đại diện toàn Olist, và không thể dùng cùng bộ này để vừa
chọn mô hình vừa tuyên bố kiểm thử cuối độc lập.

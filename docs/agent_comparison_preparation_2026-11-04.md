# Chuẩn bị so sánh quy tắc, một agent và ba agent — 04/11/2026

Đã triển khai gói so sánh offline trong thời gian chờ góp ý của giảng viên. Hệ thống chính vẫn là ba agent; quy tắc và một agent là các đối chứng để xem cơ chế phối hợp có đem lại lợi ích đo được hay không. Chưa gọi API mới hoặc thay mã Multi-Agent v3 đã đóng băng.

Gói này đã tạo 20 đầu ra quy tắc từ Olist thật, bổ sung luồng đối chứng một agent và bộ giới hạn chung cho thí nghiệm ba agent, chuẩn bị tiêu chí, mẫu rà soát và bảng chờ kết quả. 95 kiểm thử đạt. Chưa thực hiện so sánh AI thật, chưa chọn phương án tốt nhất.

## Thiết kế so sánh tạm thời

Dùng lại đúng 20 case trong pilot v3 thuộc `bed_bath_table`: tám case có cảnh báo, tám case hoàn tất dưới ngưỡng và bốn case chưa đánh giá hoặc đánh giá một phần. Cùng đơn, cùng sự kiện đánh giá, cùng dữ liệu và detector, cùng giả định về thời điểm có bối cảnh, cùng công cụ và các kế hoạch ghép nhóm đã khai báo. Giới hạn đầu ra của hai miền process/context được giữ tương đương: mỗi miền tối đa ba nhận định số liệu, ba ghi chú và hai đề xuất kiểm tra.

Các miền process/context trong nhánh một agent chỉ là hai phần của cùng một câu trả lời. Có một vai trò gọi mô hình là `single_agent_baseline`, không chạy hai agent ẩn bên trong. Nhánh một agent dùng tập hợp các công cụ của hệ thống chính và được tự chọn công cụ trong giới hạn. Nhánh này có kiểm chứng bằng code, không có một AI Verifier riêng. Nhánh quy tắc cũng không được gán một quyết định Verifier giả.

| Chế độ | Một agent | Ba agent | Ý nghĩa |
|---|---:|---:|---|
| `native_bounded` | Tối đa 4 lượt | Tối đa 12 lượt | So sánh cách vận hành hiện có, có chênh lệch hạn mức tài nguyên |
| `matched_4` | Tối đa 4 lượt | Tối đa 4 lượt toàn hệ thống | So sánh dưới cùng trần số lượt và giới hạn token mỗi yêu cầu |

Giới hạn mỗi yêu cầu vẫn là 6.000 input tokens và 1.200 output tokens. Trần số lượt bằng nhau không bảo đảm số token thực dùng, chi phí hay thời gian bằng nhau; phải ghi nhận các giá trị thực khi chạy API. Cấu hình ba agent cần ít nhất ba lượt hoàn tất vai trò, nên `matched_4` là điều kiện tài nguyên thấp và phải được diễn giải như vậy. Không dùng riêng kết quả ở trần bốn lượt để kết luận một kiến trúc luôn tốt hơn.

Nhánh ba agent sử dụng Coordinator v3 hiện hành qua bộ giới hạn ngoài. Khi đạt trần chung, bộ giới hạn dừng trước yêu cầu tiếp theo. Kết quả hết lượt, không kết luận được hoặc lỗi dịch vụ vẫn được lưu, không loại khỏi mẫu. Số yêu cầu thực sự chuyển cho provider được ghi riêng với số lần Coordinator thử yêu cầu; lần bị chặn trước dispatch không tính thành một lần API đã gửi.

Một agent nhận tập hợp bằng chứng ban đầu; ba agent nhận các phần bằng chứng theo vai trò và trao đổi kết quả. Đây là khác biệt có chủ đích của kiến trúc, được khai báo cùng việc có/không có Verifier. Nhánh quy tắc chọn kế hoạch so sánh cố định: `same_product_peers` cho các công đoạn cảnh báo; nếu không có cảnh báo thì chọn công đoạn đầu tiên chưa đánh giá được, hoặc chặng vận chuyển khi cả ba công đoạn đã được đánh giá. Không tự nới điều kiện ghép nhóm để có kết quả thuận lợi.

## Phần đã chạy và kết quả

Đã chạy nhánh quy tắc trên 20 đơn thật, tạo 20 file kết quả và bảng chỉ số nguồn. Không sinh phản hồi AI bằng quy tắc rồi ghi đó là kết quả của mô hình.

- 265 mục xuất bản sau khi loại trùng trong từng đơn khớp bằng chứng nguồn theo bộ kiểm chứng hiện có.
- Với tám đơn có cảnh báo, đầu ra trình bày đủ các công đoạn có cờ cảnh báo.
- Với hai đơn có mốc chưa quan sát, đầu ra trình bày đủ các ghi chú tương ứng.
- Với một đơn có thời gian mâu thuẫn, đầu ra giữ ghi chú mâu thuẫn và không biến điểm `null` thành 0 hay “bình thường”.

Các số trên là kiểm tra tính nhất quán và mức độ trình bày những quan sát đã có. Nhánh quy tắc chủ động chọn từ bằng chứng hợp lệ nên tỷ lệ khớp nguồn cao là điều có thể xảy ra do cách xây dựng. Không được gọi đây là accuracy 100%, không dùng để chứng minh phát hiện đúng tắc nghẽn, và không suy ra AI tốt hoặc kém hơn.

Thời gian trung bình đo cho nhánh quy tắc trong lần chuẩn bị là khoảng 0,00564 giây/case. Nó không gồm toàn bộ quá trình làm sạch/huấn luyện/nạp dữ liệu hoặc yêu cầu mạng. Không so trực tiếp số này với các lượt API v2 trước đó rồi kết luận lợi ích của v3.

Đã tạo 80 vị trí chờ thử AI: 20 case × hai kiến trúc AI × hai chế độ hạn mức. Cả 80 đều là `not_run`, với số lượt gọi, chi phí và chỉ số kết quả để `null`. Đây không phải 80 lượt đã chạy và không phải sự cho phép tự động chạy API. Nhánh quy tắc được dùng chung cho hai chế độ, không nhân đôi kết quả để tăng cỡ mẫu.

## Cách đánh giá đã chuẩn bị

Phần tự động kiểm tra bằng chứng độc lập với cờ `passed` do kết quả tự khai báo: tính lại điều kiện từ nguồn, kiểm tra detector không thay đổi, không chấp nhận kết quả của case/thời điểm khác và không đếm các mục lặp thành nhiều bằng chứng. Các chỉ số gồm:

- Tỷ lệ mục xuất bản khớp nguồn, với mẫu số là các mục khác nhau thực sự xuất bản.
- Mức độ trình bày rõ các công đoạn có cờ cảnh báo.
- Mức độ trình bày đúng các mốc chưa quan sát và các khoảng thời gian âm.
- Trạng thái lỗi, hết lượt hoặc chưa chạy được giữ nguyên.

Nếu không có công đoạn cảnh báo hoặc không có mốc thiếu, chỉ số tương ứng là không áp dụng (`null`), không tự cho 100%. Nếu chưa chạy một nhánh, toàn bộ chỉ số kết quả của nhánh đó để trống. Các chỉ số này không đánh giá độc lập xem detector phát hiện đúng bất thường hay không. Vì detector được giữ cố định ở mọi nhánh, không dùng AUC của cùng detector để chứng minh đóng góp của Multi-Agent.

Phần đánh giá tính hữu ích vẫn cần rà soát đầu ra: có công khai phạm vi và giả định không, có bỏ sót quan sát quan trọng không, đề xuất kiểm tra có phù hợp không, có ngôn ngữ quy kết nguyên nhân vượt bằng chứng không, và người quản lý có sử dụng được không. Đã tạo mẫu có thang mô tả 1–5, cho phép “chưa đánh giá được”, nhưng toàn bộ người rà soát, điểm và ghi chú đều để trống.

Mẫu dùng nhãn A–E theo từng case, với khóa ánh xạ phương án lưu riêng. Đây mới là chuẩn bị che tên phương án, chưa có một cuộc đánh giá mù hoàn tất. Người biết code hoặc mở khóa vẫn có thể nhận ra phương án. Việc rà soát giải thích không tạo nhãn ground truth tắc nghẽn.

## Kiểm thử và bảo toàn

95 test đạt, gồm 77 test trước đó và 18 test mới. Test mới kiểm tra các nhánh đối chứng, cùng dữ liệu/đầu ra detector, giới hạn bốn lượt, lưu lỗi và vòng sửa, không đưa metadata sai vào đầu ra, không biến dữ liệu thiếu thành bình thường, không tính kết quả chưa chạy thành điểm số, và không làm tăng chỉ số bằng cách lặp nhận định.

Phản hồi được tạo trong test là fixture giao thức có ghi rõ test-only. Chúng không được đưa vào bảng kết quả nghiên cứu. Các adapter của gói này từ chối provider API thật; CLI chỉ có chuẩn bị và kiểm tra snapshot. Không đọc key, không gửi yêu cầu API, không phát sinh phí.

60 file được bảo vệ giữ nguyên, bao gồm đầu vào/đầu ra cũ, mã v3 đã đóng băng và sổ ngân sách. Các script mới nằm ở `scripts/06_agent_evaluation/`, nên không làm thay đổi hash triển khai của snapshot v3 trong `scripts/05_multi_agent/`.

Sáu case đã có phản hồi API v2 từ trước; 14 case còn lại chưa có phản hồi v2 được ghi nhận trong các biên nhận của pilot. Tất cả đều là dữ liệu phát triển đã có thể được xem qua bằng code, không có case nào được tuyên bố là chưa từng được người phát triển nhìn thấy. Không trộn phản hồi v2 vào bảng so sánh v3 như thể cùng phiên bản. Một lần thử mỗi cấu hình cũng chưa đủ đánh giá độ ổn định của AI.

## File và lệnh sử dụng

- `templates/agent_comparison_v1/protocol.json`: tiêu chí và điều kiện so sánh tạm thời, có thể tạo phiên bản mới sau phản hồi của thầy.
- `scripts/06_agent_evaluation/comparison_core.py`: đầu vào chung, biên dịch lựa chọn và kiểm tra chỉ số nguồn.
- `scripts/06_agent_evaluation/comparison_runners.py`: đối chứng quy tắc, luồng một agent và adapter ba agent được kiểm thử offline.
- `scripts/06_agent_evaluation/prepare_comparison.py`: chuẩn bị và kiểm tra snapshot; không có lệnh chạy API.
- `tests/test_agent_comparison.py`: 18 test mới.
- `docs/agent_comparison_validation_2026-11-04.json`: kết quả 95 test và hash mã kiểm thử.
- `data/experiments/olist_agent_comparison_v1/`: cohort, bằng chứng chung, 20 kết quả quy tắc, chỉ số, bảng chờ, mẫu rà soát, khóa và manifest.

Kiểm tra snapshot đã lưu:

```sh
.venv/bin/python -B scripts/06_agent_evaluation/prepare_comparison.py --verify data/experiments/olist_agent_comparison_v1
```

Chạy kiểm thử riêng phần so sánh:

```sh
.venv/bin/python -B -m unittest discover -s tests -p 'test_agent_comparison.py'
```

Các snapshot không bị ghi đè. Dữ liệu thực nghiệm bị Git bỏ qua và cần sao lưu local. Chưa commit/push trong gói này.

## Phần tiếp theo

Chưa có kết quả API v3 mới hoặc đối chứng một agent thật, chưa đo usage/độ trễ AI theo hai chế độ, chưa có đánh giá tính hữu ích, chưa có kết quả thử lặp. Vì vậy bảng xếp hạng và phương án tốt nhất đều để trống.

Trước khi chạy AI thật cần hoàn thiện điểm vào live có kiểm tra snapshot, dùng đúng sổ ngân sách chung, kiểm tra token và lưu biên nhận; sau đó đề xuất một nhóm thử nhỏ để người dùng duyệt phạm vi và chi phí. Không chạy hàng loạt 80 vị trí chờ trong gói này. Phản hồi của giảng viên sẽ được dùng để điều chỉnh phiên bản tiêu chí kế tiếp, giữ nguyên lịch sử đã lưu.

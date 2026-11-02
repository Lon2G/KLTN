# Tích hợp công cụ phân tích và phối hợp ba agent — 30/10/2026

Phạm vi: Olist thật, ngành `bed_bath_table`, pilot kỹ thuật trên validation. Đây là
lớp giải thích các tín hiệu thời gian xử lý bất thường / điểm nghẽn tiềm năng.
Không thay thuật toán phát hiện, không tạo nhãn tắc nghẽn, không huấn luyện lại.

## Luồng đã triển khai

```text
Journal sự kiện và kết quả chấm điểm đã đóng băng
    → công cụ đọc bằng chứng tại một event_id cụ thể
    → giữ kết quả detector để sử dụng độc lập
    → Process Analyst: chọn công đoạn / kiểm tra nhóm tham chiếu
    → Context Analyst: chọn phép đối chiếu được khai báo trước
    → kiểm tra số liệu, nguồn và thời gian bằng code
    → Evidence Verifier: chấp nhận / bác bỏ / yêu cầu sửa
    → tối đa một lượt sửa và kiểm chứng lại
    → JSON có nhận định kèm bằng chứng, giới hạn, hướng kiểm tra tiếp
```

Ba agent có prompt riêng, phạm vi công cụ riêng, có thể tự chọn lời gọi công cụ
trong phạm vi cho phép, trao đổi kết quả có cấu trúc. Scheduler chỉ điều phối,
không được tính là agent AI thứ tư. `Coordinator` nhận provider qua một interface;
provider thật là OpenAI Responses. Provider lập trình sẵn trong tests chỉ kiểm
tra luồng code, hoàn toàn không phải thực nghiệm năng lực suy luận của AI.

## Công cụ và dữ liệu

| Nhóm | Công cụ | Công dụng |
|---|---|---|
| Quy trình | `get_observed_state` | Chỉ đọc sự kiện đã xuất hiện đến event đang xét |
| Quy trình | `get_frozen_stage_assessment` | Giữ nguyên duration, score, cờ cảnh báo và trạng thái chưa đánh giá |
| Quy trình | `get_reference_membership` | Kiểm lại số đơn, mean, median, dấu vết thành viên train |
| Bối cảnh | `get_available_context` | Nêu giả định tính sẵn có của thông tin tĩnh và các yếu tố chưa quan sát |
| Bối cảnh | `compare_declared_peer_groups` | Chọn một trong bốn kế hoạch đối chiếu đã khai báo |
| Bối cảnh | `get_group_support_and_coverage` | Kiểm tra số mẫu và mức bao phủ của phép so sánh |
| Kiểm chứng | `resolve_evidence` | Tra đúng bằng chứng của case, thời điểm và chế độ đang xét |
| Kiểm chứng | `recompute_reported_metrics` | Tính lại duration và chênh lệch so với mean từ nguồn |
| Kiểm chứng | `check_temporal_and_comparison_contract` | Kiểm tra mốc tương lai, tách train và giới hạn kết luận |

`evidence_tools.py` đọc `events.jsonl`, `stage_decisions.jsonl`,
`static_context_scenario_inputs.jsonl`, `reference_groups.csv` và
`train_stage_reference.csv`. SHA-256 được đối chiếu trước khi sử dụng.
`latest_stage_scores.jsonl` không được dùng để giả lập trạng thái quá khứ:
trạng thái được chọn theo tiền tố sự kiện và journal quyết định tương ứng.

Mỗi evidence có ID theo nội dung, case giả danh, event time, revision, chế độ
sẵn có dữ liệu và chữ ký nguồn. ID đơn/sản phẩm/seller thật và danh sách thành viên
nhóm chỉ lưu local; provider nhận thống kê, tên công đoạn, vùng cấp bang và dải
khoảng cách. Không gửi review tự do, địa chỉ chính xác hoặc toàn bộ bảng nguồn.

Giữ hai chế độ: `verified_availability_only` tiếp tục chưa chấm được điểm nếu
chưa xác minh thông tin tĩnh có sẵn tại lúc đặt; `assumed_static_context_at_purchase`
cho phép phân tích dưới giả định được ghi rõ. Không chuyển giả định thành sự thật.

## So sánh nhóm: ý nghĩa và giới hạn

1. `same_product_peers`: cùng product, seller, item count, distance band và customer state.
2. `category_peers`: cùng seller, item count, distance band và customer state, trong ngành;
   khác product được ghi thành yếu tố chưa kiểm soát. Không tự động hạ xuống mức này.
3. `region_within_product`: chỉ vận chuyển; cùng product/seller/item count/distance band,
   ghép theo tháng bắt đầu công đoạn trong lịch sử; so bang nhận của case với các bang khác.
4. `region_within_category`: như trên ở cấp ngành, ghi rõ product identity chưa kiểm soát.

Phải kiểm tra peer không phân tích vùng tương ứng trước khi gọi phép so sánh vùng.
Điều đó **không có nghĩa đã loại trừ mọi nguyên nhân khác**. Các yếu tố chưa được
kiểm soát gồm kích thước/khối lượng, hãng vận chuyển, tải công việc, tồn kho,
giao thông/thời tiết, vị trí seller chi tiết và thời kỳ của case hiện tại.

Các ngưỡng hỗ trợ kế thừa định hướng thiết kế: ít nhất 30 đơn cho peer; so vùng
cần ít nhất 5 đơn mỗi phía trong từng ô ghép, 30 mỗi phía sau ghép và bao phủ ít
nhất 80% mỗi phía. Đây là điều kiện hỗ trợ mẫu được khai báo, chưa được chứng minh
là ngưỡng tối ưu hoặc kiểm định ý nghĩa thống kê. Không thay chúng để tìm kết quả đẹp.

So sánh vùng dùng trung bình đối chứng có trọng số theo số đơn nhóm đích:

`mean_control = Σ(n_target_cell × mean_control_cell) / Σ(n_target_cell)`

`difference_days = mean_target - mean_control`

Mẫu số và coverage tính trên tập lịch sử đã thỏa các điều kiện trước khi ghép
tháng; không phải coverage của toàn Olist. Chỉ dùng công đoạn train đã hoàn tất
trước thời điểm đánh giá. Đây là liên hệ mô tả trong lịch sử, có nguy cơ sai lệch
do chỉ quan sát các công đoạn hoàn tất và các yếu tố chưa đo. Không phải xác suất
tắc nghẽn, tỷ lệ lỗi thật hay bằng chứng nguyên nhân của đơn hiện tại.

## Kiểm chứng và phối hợp

Agent chỉ gửi tối đa ba nhận định có cấu trúc, gồm loại nhận định, evidence ID,
tên trường và giá trị; không cho xuất câu nguyên nhân tự do. Code đối chiếu đúng
giá trị và loại dữ liệu, phạm vi role và điều kiện đủ mẫu. Agent kiểm chứng không
thể bỏ qua một kiểm tra số liệu thất bại. Bất đồng dẫn đến tối đa một lượt sửa;
nhận định không đạt bị loại, detector vẫn được giữ. Toàn bộ trao đổi và tool calls
có trace. Kết quả được chọn có kèm đầy đủ evidence, mẫu số và hạn chế.

Các `recommended_checks` là lựa chọn từ danh sách việc điều tra tiếp đã định nghĩa,
không phải giải pháp đã được chứng minh sẽ khắc phục nguyên nhân.

## Ngân sách và API

Mỗi role tối đa 3 request ban đầu và 1 request sửa; tổng không quá 12 mỗi case/run.
Giới hạn input là 6.000 token, output là 1.200 token/request. Toàn bộ các run dùng
một ledger `data/runtime/multi_agent_budget.sqlite`, trần model calls 5 USD theo
phương án ngân sách 150.000 VND, giữ 50.000 VND dự phòng. Tỷ giá này chỉ để lập kế
hoạch; đối chiếu hóa đơn thực tế, thuế/phí và tỷ giá thanh toán riêng.

Adapter giữ trước chi phí trần, dùng endpoint đếm input gồm schema và lịch sử,
đánh dấu đã gửi trước HTTP inference, rồi ghi usage thật. Tính cả cache read/write
và toàn bộ output. Lỗi/timeout sau khi gửi giữ khoản dự phòng; không tự retry.
Receipt lưu SQLite tránh gửi lại request đã có kết quả khi khởi động lại.
Không xóa ledger hoặc đổi run key để lách giới hạn. Chưa xác nhận quyền truy cập
model của tài khoản cho đến khi chạy smoke test thật.

Đối chiếu tài liệu chính thức ngày 30/10/2026:
[function calling](https://developers.openai.com/api/docs/guides/function-calling),
[token counting](https://developers.openai.com/api/docs/guides/token-counting),
[GPT-6 Luna](https://developers.openai.com/api/docs/models/gpt-6-luna),
[prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching),
[spending controller](https://developers.openai.com/cookbook/articles/per_run_spending_controller_responses_api).
Model này là lựa chọn chi phí cho pilot; chưa phải kết luận mô hình giải thích tốt nhất.

## Cách chạy

Từ thư mục gốc project, kiểm thử kỹ thuật không gọi mạng:

```sh
.venv/bin/python -B -m unittest discover -s tests -p 'test_multi_agent*.py' -v
```

Chuẩn bị snapshot mới, chỉ đọc dữ liệu thật và xuất bộ bằng chứng:

```sh
.venv/bin/python -B scripts/05_multi_agent/run_multi_agent.py --prepare
```

Lệnh từ chối ghi đè snapshot đã tồn tại. Bản mặc định được tạo trong lần triển khai
này là `data/experiments/olist_multi_agent_tools_v1`. Mẫu có 8 đơn cảnh báo, 8 đơn
đủ ba công đoạn dưới ngưỡng và 4 đơn chưa đánh giá đủ; đây là mẫu kỹ thuật có chủ
đích, không đại diện tỷ lệ bất thường của toàn bộ dữ liệu. Hai case bảo lưu bị loại.

Sau khi có `OPENAI_API_KEY` trong môi trường local và kiểm tra quyền dùng model,
chạy một đơn trước, không cần nạp thêm dữ liệu Olist:

```sh
.venv/bin/python -B scripts/05_multi_agent/run_multi_agent.py --run-api --case-index 0
```

Không dán key vào chat hoặc commit key. Lệnh chỉ chạy một case; có thể tiếp tục
các index còn lại sau khi kiểm tra kết quả. Snapshot và code phải khớp hash đã
đóng băng. Output nằm tại `data/runtime/multi_agent_results/`; không ghi vào kết
quả detector cũ. Template review để trống dùng đánh giá lời giải thích, không
dùng làm nhãn xác nhận tắc nghẽn. Nếu chưa có key, lệnh dừng trước khi tạo ledger.

## Chưa hoàn tất

API thật và chất lượng ba agent chưa được kiểm chứng; chưa có so sánh hiệu quả
multi-agent với single-agent / không agent. Chưa có nhãn tắc nghẽn độc lập, chưa
tích hợp giao diện hoặc chạy các agent bất đồng bộ theo từng sự kiện mới.
Thời gian xử lý local của bộ công cụ không phải độ trễ end-to-end near real-time.
Các bước đó được thực hiện sau pilot kỹ thuật và rà soát lời giải thích.

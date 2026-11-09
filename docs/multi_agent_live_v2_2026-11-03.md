# Lượt API v2 trên cùng một đơn Olist — 03/11/2026

Người dùng đã duyệt thử lại đúng một đơn sau gói sửa offline. Lượt v2 hoàn tất qua API thật với trạng thái `evidence_checked`. Có sáu nhận định được xuất bản kèm bằng chứng, không có nhận định bị từ chối hoặc vi phạm hợp đồng, không cần chu kỳ sửa.

Kết quả này xác nhận luồng v2 hoạt động trên đơn đã thử. Chưa xác nhận nguyên nhân tắc nghẽn, độ chính xác phát hiện trên tập lớn hoặc lợi ích của ba agent so với một agent. Đây là so sánh kỹ thuật trên một đơn development đã biết kết quả của lượt v1.

## Điều kiện thử giữ nguyên

- Đơn `980bc014d29830d87b56bbc7720a6b6b`, case key `olist-008086b1fd1e8a618081`, index 0 trong pilot.
- Thời điểm đánh giá `2018-04-17 20:56:40`, ở sự kiện đã ghi nhận giao hàng.
- Mô hình `gpt-6-luna`, cùng giới hạn 6.000 input tokens, 1.200 output tokens và tối đa 12 lượt gọi mô hình cho một case/run.
- Cùng dữ liệu, nhóm tham chiếu, detector, score và ngưỡng. Ba bản ghi detector giống v1 hoàn toàn.
- Cùng chế độ `assumed_static_context_at_purchase`. Nguồn chưa xác minh thời điểm sẵn có của thông tin tĩnh.
- Cùng sổ ngân sách chung 5 USD. Run key mới `multi-agent-pilot-v2` phân biệt lượt này với lịch sử v1.
- Key chỉ được nạp cục bộ vào tiến trình. HTTPS vẫn được xác minh bằng kho chứng chỉ macOS. Không thay code hoặc cấu hình detector trong lượt này.

## Đối chiếu v1 và v2

| Chỉ tiêu của lượt thử | V1 | V2 |
|---|---:|---:|
| Lượt gọi mô hình | 12 | 6 |
| Process / Context / Verifier | 4 / 4 / 4 | 1 / 3 / 2 |
| Nhận định được xuất bản | 0 | 6 |
| Nhận định bị từ chối cuối luồng | 5 | 0 |
| Chu kỳ sửa | Có | Không |
| Thời gian Coordinator, giây | 43,611 | 17,273 |
| Thời gian toàn lượt từ wrapper, giây | 44,182 | 17,864 |
| Chi phí tính từ usage, USD | 0,004158135 | 0,001899390 |
| Quy đổi dự trù 30.000 VND/USD | Khoảng 125 đồng | Khoảng 57 đồng |

Số lượt gọi trong bảng chỉ đếm yêu cầu sinh phản hồi. Provider còn thực hiện đếm token trước mỗi yêu cầu. Input lớn nhất của một yêu cầu v2 là 4.116, output lớn nhất là 182, đều dưới giới hạn.

Chi phí đã được tính lại từng biên nhận, bao gồm cached input và cache write, theo [bảng giá GPT-6 Luna](https://developers.openai.com/api/docs/models/gpt-6-luna) được đối chiếu trong phiên này. Tổng tính lại khớp ledger. Chưa đối chiếu Billing hoặc hóa đơn. Quy đổi VND chỉ là tỷ giá dự trù, chưa gồm thuế/phí và tỷ giá thanh toán thực tế.

Trong lần thử này, v2 có ít lượt gọi và chi phí thấp hơn. Không suy rộng mức tiết kiệm này cho mọi đơn. Đây chỉ là một lượt cho mỗi phiên bản, chưa kiểm soát biến động mạng, dịch vụ và cache. Thời gian trên cũng không phải độ trễ detector hoặc bằng chứng về near real-time trong sản xuất.

## Ba agent đã thực hiện gì

Process Analyst dùng trực tiếp gói đánh giá công đoạn đã được cung cấp và chọn ba bằng chứng của chặng vận chuyển. Agent không gọi lại công cụ chỉ để lấy thông tin đã có, nên hoàn tất sau một lượt mô hình.

Context Analyst đọc bối cảnh, gọi `compare_declared_peer_groups` với `same_product_peers` cho chặng vận chuyển, rồi chọn ba nhận định. Kết quả phép đối chiếu là không có đơn lịch sử đáp ứng đồng thời các điều kiện cùng sản phẩm, seller, số item, dải khoảng cách và bang khách hàng, trong khi mức hỗ trợ tối thiểu là 30. Agent giữ trạng thái thiếu mẫu, không chuyển thành một kết luận về nguyên nhân.

Evidence Verifier gọi `check_temporal_and_comparison_contract` rồi kiểm chứng đủ sáu nhận định. Tất cả đều có code gate đạt và được chấp nhận với lý do `supported`. Không xuất hiện trường tự đặt hoặc quyết định chấp nhận một nhận định đã bị code bác trong lượt này.

## Sáu nhận định đầu ra

| Vai trò | Trường được chọn | Giá trị và cách hiểu |
|---|---|---|
| Process | `duration_days` của transport | 36,04707175925926 ngày từ bàn giao đến giao hàng |
| Process | `difference_from_reference_mean_days` | Dài hơn trung bình tham chiếu 23,813057686522264 ngày |
| Process | `delay_score_0_100` | 49,458483754512635/100 theo thang phân vị hiện hành, không phải xác suất tắc nghẽn |
| Context | `status` của phép so sánh cùng sản phẩm | `insufficient_comparable_orders`, số mẫu 0 so với mức tối thiểu 30 |
| Context | `single_seller_available` | Có thể quy thuộc đơn cho một seller trong dữ liệu, không phải bằng chứng seller gây chậm |
| Context | `context_availability_source_verified` | `false`, nguồn chưa xác minh thời điểm sẵn có của bối cảnh |

Mỗi nhận định kèm evidence ID và toàn bộ bằng chứng hỗ trợ. Agent chọn ID, còn giá trị được code lấy nguyên từ dữ liệu. Do đó sáu con số/trạng thái khớp nguồn không phải sáu dự đoán đúng độc lập của AI, và không được dùng như một chỉ số accuracy.

Tham chiếu detector cho chặng vận chuyển có 277 đơn ở mức `category_context_fallback`, trung bình 12,234014072736995 ngày và biên cảnh báo 31,258854166666666 ngày. Đây là nhóm khác với phép đối chiếu cùng mã sản phẩm của Context Analyst. Có 277 đơn cho tham chiếu cấp ngành không đồng nghĩa có đủ mẫu cho điều kiện ghép chặt hơn.

Diễn giải phù hợp cho đơn này là thời gian vận chuyển kéo dài so với tham chiếu đã khai báo. Nguyên nhân vẫn `unverified`. Dữ liệu chưa đủ để quy kết cho seller, địa lý hoặc đơn vị vận chuyển. Cơ sở ngày hẹn cũng chưa được xác minh.

Các hướng kiểm tra tiếp được lưu dưới dạng mã gồm rà lại timestamp, tìm hiểu phương pháp đặt ngày hẹn, rà tuyến vận chuyển, xác minh thời điểm sẵn có của bối cảnh, bổ sung lịch sử tương đồng và thông tin khối lượng/kích thước. Đây là đề xuất kiểm tra, không phải hành động nghiệp vụ đã được thực hiện hoặc xác nhận hiệu quả.

## Kiểm chứng sau lượt chạy

- Sáu nhận định khớp chính xác kiểu và giá trị của trường nguồn. Evidence kèm theo khớp kết quả dựng lại bằng công cụ local.
- Các ID agent chọn tạo lại đúng danh sách nhận định được xuất bản.
- Verifier có đúng một quyết định cho mỗi nhận định và không có quyết định mâu thuẫn với gate.
- Cả ba duration được tính lại từ timestamp Olist raw và khớp detector.
- 21 file được bảo vệ ngoài ledger giữ nguyên hash. Ledger chỉ bổ sung sáu yêu cầu v2 đã quyết toán.
- 12 biên nhận v1 vẫn giống bản sao đã đóng băng, không bị sửa hoặc ghi đè.
- Manifest pilot v2 và mã nguồn vẫn khớp. Không thay mã sau bộ 60 kiểm thử offline.
- Không tạo nhãn tắc nghẽn hoặc nhãn fraud. Trạng thái rà soát của người dùng vẫn đang chờ, không bị tự đánh dấu hoàn tất bởi lần đối chiếu bằng code này.

Tổng chi phí hai lượt trong ledger là 0,006057525 USD, khoảng 182 đồng theo tỷ giá dự trù. Ngân sách local còn 4,993942475 USD, không có khoản giữ chỗ chưa quyết toán. Đây không phải số dư Billing đã kiểm tra lại bằng giao diện.

## Kết quả lưu để tái kiểm tra

- [Kết quả API v2 gốc](../data/runtime/multi_agent_results/316ba4c337a6f1102a21a575834d16ab22bafc45dcc503f785f3a085886954a8.json).
- [So sánh và kiểm chứng máy đọc được](../data/experiments/olist_multi_agent_live_v2_case0/comparison.json).
- [Biên nhận nguyên gốc của cả hai lượt](../data/experiments/olist_multi_agent_live_v2_case0/provider_receipts.json).
- [Manifest của bản so sánh](../data/experiments/olist_multi_agent_live_v2_case0/manifest.json).

Biên nhận đã được xuất thành snapshot riêng để nguồn lịch sử không phụ thuộc vào các lần bổ sung ledger sau này. Các dữ liệu thực nghiệm này bị Git bỏ qua và cần được giữ trong bản sao lưu local. Chưa commit hoặc push Git trong lượt này.

## Gói tiếp theo đề xuất, chưa thực hiện

Đề xuất kiểm tra v2 trên năm đơn khác trong pilot đã đóng băng, chọn các đơn kế tiếp theo thứ tự sẵn có trước khi xem phản hồi AI. Mục tiêu là kiểm tra ba tình huống vận hành, không đánh giá độ chính xác phát hiện trên toàn Olist.

| Case index | Case key | Nhóm |
|---:|---|---|
| 1 | `olist-00849c2ca5cabdd83c6a` | Có cảnh báo |
| 2 | `olist-04029aa370b3a937565a` | Có cảnh báo |
| 8 | `olist-0011ca6490627587e0f9` | Hoàn tất, dưới ngưỡng cảnh báo |
| 9 | `olist-0033b978c403c7f5e425` | Hoàn tất, dưới ngưỡng cảnh báo |
| 16 | `olist-00dd84e78643f5a6a28d` | Chưa đánh giá hoặc đánh giá một phần |

Kiểm tra khả năng giữ bất định, tránh tạo cảnh báo không có trong detector, chất lượng chọn bằng chứng, số vòng sửa, thời gian và chi phí. Không gọi nhóm dưới ngưỡng là bình thường đã được xác nhận.

Dự trù theo cấu hình tối đa 0,081 USD cho cả năm đơn, khoảng 2.430 đồng theo tỷ giá dự trù, trong ngân sách chung hiện có. Chưa chạy thêm đơn nào, chờ người dùng duyệt gói này.

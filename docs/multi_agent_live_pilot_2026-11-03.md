# Lượt thử API ba agent trên một đơn Olist — 03/11/2026

Người dùng đã duyệt chạy đúng một đơn và cung cấp ảnh số dư 5 USD với Auto-reload OFF. Lượt thử đã chạy bằng OpenAI API thật, mô hình `gpt-6-luna`. Kết nối, gọi công cụ, lưu phản hồi và kế toán ngân sách hoạt động. Đầu ra giải thích chưa đạt yêu cầu. Trạng thái cuối là `inconclusive`, không có nhận định nào được xuất bản.

Đây là thử nghiệm tích hợp trên một đơn thuộc validation. Không phải đánh giá độ chính xác phát hiện tắc nghẽn, không tạo nhãn tham chiếu và không chứng minh hiệu quả trên toàn bộ dữ liệu.

## Phạm vi và dấu vết

- Case index 0 trong pilot đã đóng băng gồm 20 đơn thật.
- Order ID `980bc014d29830d87b56bbc7720a6b6b`.
- Case key `olist-008086b1fd1e8a618081`.
- Thời điểm sự kiện đang xét `2018-04-17 20:56:40`.
- Event ID `30f49e9e37af8b466c9bc0cc92a8e4efb80f8c061ea92e032fc512ec4157c8aa`.
- Run key `multi-agent-pilot-v1`.
- Chế độ `assumed_static_context_at_purchase`. Thông tin tĩnh được giả định có sẵn tại thời điểm mua, chưa được nguồn xác minh.
- Kết quả gốc nằm tại [JSON lượt chạy](../data/runtime/multi_agent_results/aa695ac621d975fa000376fede2284b4ae06ae4e0c19146ccc94cc26c06dbcee.json).
- Usage và phản hồi API gốc nằm trong [sổ ngân sách SQLite](../data/runtime/multi_agent_budget.sqlite), các bảng `budget_requests` và `provider_receipts`.
- [Pilot và manifest](../data/experiments/olist_multi_agent_tools_v1/manifest.json) giữ nguyên. Dữ liệu runtime thuộc phần local bị Git bỏ qua, cần giữ bản sao cục bộ để tái kiểm tra.

Key được đọc từ `.env` vào bộ nhớ, không in ra hoặc ghi vào kết quả. Python thiếu đường dẫn CA mặc định nên lượt chạy dùng `SSL_CERT_FILE=/etc/ssl/cert.pem` trong riêng tiến trình. Xác minh HTTPS vẫn bật. Không sửa chứng chỉ hệ thống, mã nguồn hoặc các cấu hình đã đóng băng.

## Thời gian và chi phí

| Agent | Lượt gọi mô hình | Input tokens | Output tokens | Chi phí tính theo usage, USD |
|---|---:|---:|---:|---:|
| Process Analyst | 4 | 13.781 | 770 | 0,001552555 |
| Context Analyst | 4 | 11.163 | 439 | 0,001242540 |
| Evidence Verifier | 4 | 16.376 | 436 | 0,001363040 |
| Tổng | 12 | 41.320 | 1.645 | 0,004158135 |

Mỗi agent có ba lượt ban đầu và một lượt trong chu kỳ sửa duy nhất. Trước mỗi lượt gọi mô hình, provider đếm input tokens. Số 12 trong bảng chỉ đếm lượt sinh phản hồi, không bao gồm các yêu cầu đếm token.

Chi phí được tính lại từ usage thực tế của 12 phản hồi, có xét cached input và cache write, theo [bảng giá GPT-6 Luna](https://developers.openai.com/api/docs/models/gpt-6-luna) đã đối chiếu trong phiên ngày 03/11. Input 0,10 USD, cached input 0,01 USD, cache write 0,125 USD và output 0,50 USD trên một triệu token. Kết quả tính lại khớp sổ ngân sách. Chưa đối chiếu hóa đơn hoặc số dư Billing sau lượt chạy.

Theo tỷ giá dự trù 30.000 VND/USD, chi phí tương đương khoảng 125 đồng, chưa gồm thuế/phí và chênh lệch tỷ giá thực tế. Ngân sách còn lại trong sổ local là 4,995841865 USD. Cả 12 yêu cầu đã được quyết toán, không còn khoản giữ chỗ và không vượt hạn mức. Input lớn nhất một yêu cầu là 5.807, output lớn nhất là 366, trong giới hạn 6.000 và 1.200 đã khai báo.

Thời gian Coordinator là 43,61 giây. Toàn lượt chạy từ wrapper là 44,182 giây. Đây là thời gian giải thích một đơn trong lượt thử này, không phải độ trễ detector, thông lượng hệ thống hay chứng minh near real-time trong sản xuất.

## Số liệu detector được giữ nguyên

| Công đoạn | Thời gian thực tế, ngày | Trung bình tham chiếu, ngày | Chênh lệch so với trung bình, ngày | Biên cảnh báo, ngày | Số đơn tham chiếu | Cảnh báo |
|---|---:|---:|---:|---:|---:|---|
| Đặt hàng → duyệt | 0,028206 | 0,296654 | -0,268448 | 1,705382 | 298 | Không |
| Duyệt → bàn giao | 3,152002 | 3,079615 | +0,072387 | 8,299803 | 294 | Không |
| Bàn giao → giao hàng | 36,047072 | 12,234014 | +23,813058 | 31,258854 | 277 | Có |

Cả ba nhóm tham chiếu đều là `category_context_fallback`, không phải nhóm cùng mã sản phẩm. Chặng vận chuyển có score 49,458484/100 và mức `low` theo thang phân vị hiện tại. Score không phải xác suất tắc nghẽn và mức này không phải lượng hóa thiệt hại kinh doanh. Cơ sở ngày hẹn vẫn chưa xác minh.

Đã tính lại ba duration từ các mốc trong `data/raw/olist_orders_dataset.csv`. Cả mốc đầu, mốc cuối và duration đều khớp. Ba evidence của detector khớp hoàn toàn với pilot đã đóng băng, không bị nhận định của agent thay đổi.

## Kết quả từng agent

Process Analyst đọc trạng thái quan sát và gọi công cụ lấy đánh giá công đoạn duyệt. Gói đầu vào đã có sẵn đánh giá cả ba công đoạn. Agent nộp ba nhận định. Có số liệu đúng từ bằng chứng nhưng hình thức tham chiếu không đáp ứng hợp đồng máy kiểm tra.

Context Analyst đọc bối cảnh và chọn so sánh `same_product_peers` cho công đoạn vận chuyển. Với các điều kiện cùng product, seller, số item, dải khoảng cách và bang khách hàng, công cụ tìm được 0 đơn tham chiếu, dưới mức tối thiểu 30. Đây là thiếu mẫu cho phép đối chiếu cụ thể này, không phải khẳng định Olist không có nhóm tham chiếu nào khác. Agent chưa thực hiện phương án `category_peers` trong lượt chạy này. Agent nộp hai nhận định, cũng không đáp ứng hợp đồng tham chiếu.

Evidence Verifier gọi công cụ đọc hai bằng chứng, sau đó chấp nhận cả năm nhận định dù gói kiểm chứng đã chỉ rõ chúng bị code từ chối. Điều này lặp lại sau chu kỳ sửa. Phần kiểm soát bằng code tiếp tục có quyền quyết định cuối cùng nên không xuất bản bất kỳ nhận định nào.

## Sai lệch quan sát được

| Agent | Trường agent nộp | Vấn đề trực tiếp |
|---|---|---|
| Process | `events` | Bằng chứng là danh sách, agent chuyển thành chuỗi diễn giải, trong khi hợp đồng chỉ nhận giá trị đơn có kiểu khớp nguồn |
| Process | `transport_duration_days_and_warning` | Tên trường không tồn tại trong bằng chứng, gộp nhiều số liệu thành một chuỗi |
| Process | `preparation_handoff_duration_days` | Tên trường không tồn tại, trường thực là `duration_days` trong bằng chứng của công đoạn tương ứng |
| Context | `context_status` | Tên trường không tồn tại trong bằng chứng được viện dẫn |
| Context | `same_product_transport_peer_comparison` | Tên trường không tồn tại, trạng thái thiếu mẫu phải tham chiếu đúng `status` và loại nhận định phù hợp |

Cả năm evidence ID đều tồn tại. Bốn nhận định dùng tên trường tự đặt và một nhận định đổi kiểu giá trị. Ngoài các lỗi trực tiếp này, một số nhận định dùng `descriptive_association` cho sự kiện/công đoạn hoặc kết quả thiếu mẫu, không phù hợp quy tắc phân loại của gate.

Kết quả cuối gồm 0 nhận định được xuất bản, 5 nhận định bị từ chối và `cause_status=unverified`. Các mã gợi ý kiểm tra tiếp vẫn được lưu trong JSON dưới trạng thái giải thích chưa kết luận, không phải nguyên nhân đã xác nhận hoặc hành động vận hành đã được duyệt.

Không được trình bày lượt này là ba agent đã giải thích thành công, cũng không suy rộng tỷ lệ lỗi của một đơn thành độ chính xác của mô hình. Lượt thử cho thấy lớp chặn bằng code đã giữ được đầu ra, đồng thời phát hiện điểm yếu thực tế của hợp đồng tương tác và agent kiểm chứng.

## Kiểm tra bảo toàn sau lượt chạy

- 6 hash nguồn, 10 hash mã nguồn/cấu hình và 4 hash đầu ra pilot đều khớp, tổng cộng 20/20.
- Ba duration khớp mốc nguồn Olist và ba bản ghi detector khớp pilot đã lưu.
- Chi phí tính từ usage khớp tổng trong ledger. Không còn yêu cầu `reserved` hoặc `in_flight`.
- Không thêm đơn giả, không tạo nhãn tắc nghẽn, không thay ngưỡng hoặc huấn luyện lại.
- Không gọi API thêm sau khi hoàn tất đúng một đơn và chu kỳ sửa đã được giới hạn.

## Gói tiếp theo đề xuất, chưa triển khai

Ràng buộc nhận định vào các trường có thật và đúng kiểu trong từng evidence, cung cấp hướng dẫn sửa theo lỗi cụ thể, và buộc quyết định kiểm chứng tuân thủ kết quả gate. Giữ nguyên phản hồi gốc của mô hình để không che sai lệch bằng hậu xử lý. Không nới gate nhằm biến nhận định sai cấu trúc thành hợp lệ.

Trước hết kiểm thử offline bằng phản hồi thật của lượt này. Không tự động gọi API trong regression. Bất kỳ thay đổi mã nguồn hoặc prompt nào đều cần phiên bản pilot mới vì manifest hiện tại đã đóng băng. Bộ detector, dữ liệu, ngưỡng và lịch sử lượt thất bại được giữ nguyên.

Chờ người dùng duyệt gói sửa và kiểm thử offline. Sau đó mới trình kết quả để duyệt lượt API tiếp theo nếu cần, vẫn sử dụng sổ ngân sách chung.

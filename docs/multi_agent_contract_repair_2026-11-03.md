# Sửa hợp đồng nhận định và kiểm chứng — 03/11/2026

Đã hoàn tất gói sửa và kiểm thử offline được người dùng duyệt sau lượt thử API đầu tiên. Không gọi thêm API hoặc đọc key trong gói này. Cả ba vai trò agent được giữ lại. Dữ liệu, cách tính duration, nhóm tham chiếu, ngưỡng, score và quy tắc phát hiện không thay đổi.

Kết quả kỹ thuật là 60 kiểm thử thành công, không lỗi hoặc bỏ qua. Đây là kiểm chứng code và giao thức phối hợp. Chưa có phản hồi AI mới dưới phiên bản v2 để kết luận chất lượng chọn bằng chứng hoặc giải thích đã cải thiện.

## Vấn đề được sửa

[Lượt API đầu tiên](multi_agent_live_pilot_2026-11-03.md) đã kết thúc với trạng thái chưa kết luận. Năm nhận định đều bị code bác, gồm bốn tên trường không tồn tại và một trường danh sách bị chuyển thành chuỗi. Agent kiểm chứng vẫn chấp nhận cả năm trong hai vòng. Code cuối luồng đã chặn xuất bản, nhưng hợp đồng gửi nhận còn cho phép agent tạo các nhận định sai cấu trúc đó.

Phiên bản v2 sửa cách trao đổi bằng chứng, không sửa kết quả lịch sử của lần thử đầu.

## Cơ chế mới

Agent phân tích nhận danh sách lựa chọn được sinh từ bằng chứng thực sự đã được cung cấp cho vai trò đó. Mỗi `claim_id` trong danh sách lựa chọn gắn với một evidence, một trường có thật, loại nhận định được phép và một giá trị nguyên gốc. ID phụ thuộc vào vai trò, phiên bản hợp đồng và nội dung bằng chứng. Bằng chứng đã chứa case, thời điểm sự kiện và chế độ sẵn có dữ liệu.

Agent nộp tối đa ba `claim_ids` khác nhau cùng mã bất định và hướng kiểm tra tiếp. Agent không còn tự gõ tên trường hoặc chép lại con số. Code kiểm tra ID và lấy giá trị từ bằng chứng để tạo nhận định. Nguồn giá trị được ghi rõ là `code_copied_from_agent_selected_evidence_not_model_generated_numbers`.

Ví dụ ở đơn đã thử, lựa chọn trường `duration_days` của chặng vận chuyển dẫn đến giá trị nguồn 36,04707175925926 ngày. Một tên tự đặt như `transport_duration_days_and_warning` không có trong lựa chọn hợp lệ. Kết quả vẫn kèm toàn bộ bằng chứng, gồm nhóm tham chiếu, mẫu số và giới hạn của phép so sánh.

Danh sách lựa chọn được bổ sung khi agent gọi công cụ mới. Công cụ phân tích nhóm và cơ chế phối hợp ba vai trò vẫn hoạt động. Agent vẫn quyết định công cụ nào cần dùng và bằng chứng nào đáng đưa ra, hoặc không chọn nhận định nào khi chưa đủ cơ sở. Code không tự chọn nguyên nhân hoặc ép hệ thống có kết luận.

Với nhóm so sánh thiếu mẫu, chỉ trạng thái thiếu mẫu được phép chọn như một nhận định `unknown`. Không dùng trung bình của nhóm đó để công bố liên hệ. Giá trị chưa đánh giá vẫn là `null`, không bị đổi thành 0 hoặc bình thường.

## Kiểm chứng và sửa có giới hạn

Schema của agent kiểm chứng được tạo riêng cho tập nhận định cần xét. Mỗi nhận định phải có đúng một quyết định. Chỉ nhận định đã qua code gate mới có lựa chọn `accept` với lý do `supported`. Các nhận định bị gate bác chỉ có lựa chọn sửa hoặc từ chối. Quyết định thiếu, trùng, sai ID hoặc ghép lý do không phù hợp bị bộ kiểm tra local bác tiếp.

Thiết kế sử dụng enum và `anyOf` lồng trong schema, phù hợp với [quy định Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs). Khả năng endpoint chấp nhận toàn bộ schema và cách mô hình sử dụng lựa chọn vẫn cần kiểm tra bằng lượt API v2 sau khi được duyệt.

Khi vi phạm hợp đồng, chương trình lưu phản hồi nguyên gốc và lỗi, rồi cung cấp hướng dẫn sửa trong hạn mức lượt gọi còn lại. Giới hạn vẫn là tối đa ba lượt ban đầu cho mỗi vai trò và một lượt trong chu kỳ sửa duy nhất. Không thêm vòng lặp hoặc tự động thử lại HTTP.

`raw_agent_outputs` giữ đầu ra nguyên gốc của mô hình. `contract_violations` giữ lỗi kiểm tra. Các nhận định được code tạo từ ID đã chọn được lưu riêng. Vì vậy, việc code chặn hoặc dựng giá trị không bị trình bày thành khả năng suy luận thành công của agent.

## Các file chính

| File | Thay đổi |
|---|---|
| [agent_contracts.py](../scripts/05_multi_agent/agent_contracts.py) | Danh sách trường được chọn, ID theo bằng chứng, lấy giá trị từ nguồn, schema kiểm chứng theo từng nhận định, lý do từ chối cụ thể |
| [coordinator.py](../scripts/05_multi_agent/coordinator.py) | Theo dõi bằng chứng mỗi vai trò đã thấy, cập nhật lựa chọn sau công cụ, lưu đầu ra gốc, phản hồi lỗi trong hạn mức, giữ nguyên detector |
| [run_multi_agent.py](../scripts/05_multi_agent/run_multi_agent.py) | Phiên bản hợp đồng v2, snapshot và run key mới, từ chối dùng pilot v1 với code v2 |
| [replay_saved_agent_outputs.py](../scripts/05_multi_agent/replay_saved_agent_outputs.py) | Đọc biên nhận thật qua SQLite chỉ đọc, dựng lại bằng chứng từ Olist, phát lại offline với HTTP bị chặn |
| [test_multi_agent_contract_v2.py](../tests/test_multi_agent_contract_v2.py) | Kiểm thử các lỗi thật, quyền theo vai trò/đơn/thời điểm, dữ liệu thiếu và quyết định kiểm chứng |
| [test_multi_agent_integration.py](../tests/test_multi_agent_integration.py) | Cập nhật fixture kỹ thuật theo giao thức chọn ID, kiểm tra sửa có giới hạn và lưu nguyên quyết định sai |

## Kiểm chứng đã thực hiện

[Báo cáo kiểm thử máy đọc được](multi_agent_contract_validation_2026-11-03.json) ghi 60/60 kiểm thử thành công trong khoảng 5,31 giây. Tất cả chạy offline, có chặn HTTP. Các phản hồi lập trình sẵn trong tests được đánh dấu là fixture kỹ thuật, không phải kết quả AI hoặc dữ liệu kinh doanh bổ sung.

Các nhóm kiểm tra bao gồm chọn ID hợp lệ và giữ đúng giá trị, chặn tên trường/giá trị tự nhập, chặn ID lặp hoặc không tồn tại, chặn dùng bằng chứng của đơn khác/vai trò khác/thời điểm tương lai, giữ `null`, xử lý nhóm thiếu mẫu, bắt quyết định kiểm chứng mâu thuẫn và giới hạn chu kỳ sửa. Các kiểm thử ngân sách, biên nhận, lỗi API và tính toàn vẹn dữ liệu có liên quan cũng đều qua.

[Kết quả phát lại phản hồi thật](../data/experiments/olist_multi_agent_contract_regression_v2/summary.json) xác nhận các điểm sau.

- Năm nhận định cũ tiếp tục bị gate bác với lý do cụ thể.
- Bốn bản nộp của hai agent phân tích theo định dạng v1 không được nhập nhầm vào hợp đồng v2.
- Hai bản nộp của agent kiểm chứng chấp nhận các nhận định bị gate bác đều bị hợp đồng mới từ chối.
- Luồng phát lại đọc bộ 12 biên nhận đã lưu và sử dụng ba phản hồi đầu của Process Analyst. Luồng dừng tại vi phạm hợp đồng đầu tiên ở lượt thứ ba, trước khi xuất bản nhận định. Không giả lập rằng các agent còn lại đã chạy thành công theo v2.
- Detector trong kết quả phát lại khớp hoàn toàn kết quả của lượt API thật.
- Không thêm suy luận AI, nhãn tắc nghẽn hoặc chi phí API.

Việc một nhận định sai bị chặn không chứng minh mô hình đã biết sửa nhận định đó. Các ca đường đi thành công dùng fixture kỹ thuật trên bằng chứng Olist thật để kiểm tra code, không được dùng làm số liệu chất lượng AI.

## Phiên bản và bảo toàn

Đã tạo [pilot v2](../data/experiments/olist_multi_agent_tools_v2/manifest.json) với đúng 20 đơn cũ. Các file `pilot.json`, `evidence_packets.json` và `explanation_review_template.json` giống v1 từng byte. Có 60 lần tính kiểm lại công đoạn, gồm 52 đã chấm và 8 chưa đánh giá. Trong chế độ chỉ dùng thông tin đã xác minh sẵn có, cả 60 kiểm tra giữ trạng thái chưa chấm như trước.

[Báo cáo bảo toàn](multi_agent_contract_preservation_2026-11-03.json) xác nhận 22/22 file được bảo vệ không đổi, gồm chín CSV raw, nguồn tham chiếu/sự kiện, pilot v1, kết quả API thật và sổ ngân sách.

Trước khi sửa đã sao lưu nguyên gốc 10 file mã nguồn/cấu hình theo manifest v1 vào `data/experiments/olist_multi_agent_implementation_v1`. Các hash của bản sao khớp bản gốc. Đây là bản lưu để đối chiếu/khôi phục, không phải một thư mục chạy độc lập. Manifest v1 không bị viết lại để khớp code mới. Vì code hiện hành đã là v2, đối chiếu hash mã v1 phải dùng bản sao v1 hoặc phiên bản Git tương ứng.

Một [bản sao sổ ngân sách sau lượt thử v1](../data/experiments/olist_multi_agent_implementation_v1/live_pilot_v1_budget.sqlite) cũng được giữ nguyên để truy lại 12 biên nhận lịch sử khi sổ ngân sách đang dùng được bổ sung ở các lượt được duyệt sau này. Manifest hồi quy ghi hash của sổ đang dùng tại thời điểm kiểm tra. Khi sổ này thay đổi do lượt API sau, có thể đối chiếu hash với bản sao đã đóng băng và truyền bản sao qua tham số `ledger_path` của `run_offline` để tái kiểm tra lịch sử.

Các snapshot dữ liệu, biên nhận và bản sao SQLite vẫn thuộc phần local bị Git bỏ qua. Cần giữ chúng khi sao lưu thực nghiệm. Gói này chưa commit hoặc push Git.

## Chi phí và giới hạn còn lại

Sổ ngân sách vẫn có đúng 12 lượt đã quyết toán của lần thử trước, tổng 0,004158135 USD. Gói offline phát sinh 0 lượt gọi API và 0 USD. Ngân sách local còn 4,995841865 USD, chưa đối chiếu Billing sau lượt đầu.

Chưa đo chất lượng giải thích, độ trễ hoặc token thực tế của v2 bằng API. Chưa biết mô hình có chọn đúng các bằng chứng hữu ích, có sửa được sai sót về diễn giải hoặc có tiết kiệm lượt gọi hay không. Độ đúng của con số được lấy bằng code không phải bằng chứng về năng lực suy luận độc lập của LLM. Tất cả kiểm tra về ngữ nghĩa, hữu ích và hiệu quả phối hợp vẫn cần đánh giá riêng.

Đã xem kích thước payload của luồng fixture kỹ thuật để tránh gửi thừa bằng chứng khi sửa. Đây không phải số token thực tế. Trước lượt API kế tiếp, cơ chế đếm token và giữ chỗ ngân sách vẫn chặn yêu cầu vượt giới hạn 6.000 input hoặc 1.200 output, thay vì tăng hạn mức để chạy cho qua.

## Bước tiếp theo cần duyệt

Đề xuất chạy API v2 trên đúng đơn cũ để so sánh với lần thất bại trước, dùng cùng mô hình, dữ liệu và sổ ngân sách chung. Theo cấu hình hiện tại, dự trù tối đa thêm 0,0162 USD cho 12 lượt gọi, khoảng 486 đồng theo tỷ giá dự trù 30.000 VND/USD. Đây là hạn mức dự trù theo token, chưa gồm thuế/phí hoặc chênh lệch tỷ giá.

Sau lượt đó cần kiểm tra nhận định được xuất bản, lựa chọn bằng chứng, bất định, phản hồi kiểm chứng và chi phí. Chưa chạy thêm đơn khác hoặc thực nghiệm API cho đến khi người dùng duyệt.

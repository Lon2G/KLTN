# Kết quả thử gói bằng chứng rút gọn, cập nhật 06/11/2026

Đã thực hiện một cặp thử được duyệt trên cùng đơn Olist thứ ba trong pilot ngành bed_bath_table, sử dụng GPT-6 Luna và định dạng evidence_refs_v1. Trần là 6.000 token đầu vào mỗi lượt, 1.200 token đầu ra, tối đa 4 lượt cho một agent và 12 lượt cho ba agent. Tổng hạn mức phép thử là 0,0216 USD.

Hai gói đã chặn ở lần trước được đếm lại chính thức sau tối ưu. Gói một agent có 5.624 token, giảm từ 6.032. Gói ba agent có 5.866 token, giảm từ 6.259. Đây là phép đếm trên các lịch sử đã lưu. Trong lượt chạy mới, lựa chọn công cụ của mô hình có thể tạo thêm dữ kiện và lịch sử khác.

| Cấu hình | Phản hồi thật | Token đầu vào / đầu ra đã ghi nhận | Thời gian đến khi hoàn tất hoặc dừng | Chi phí theo usage (USD) | Trạng thái |
| --- | ---: | ---: | ---: | ---: | --- |
| Ba agent | 6 | 32.244 / 637 | 20,31 giây | 0,003088835 | Hoàn thành, 12 mục được agent kiểm chứng chấp nhận |
| Một agent | 1 | 5.535 / 20 | 4,18 giây | 0,000701800 | Gói tiếp theo 6.050 token, bị chặn trước suy luận |

Tổng chi phí suy luận từ biên nhận là 0,003790635 USD. Sổ ngân sách chung ghi tổng chi tiêu lũy kế 0,021577160 USD, còn 4,978422840 USD trong hạn mức project, khoản đang giữ bằng 0. Số dư này được tính trong project. Việc đối chiếu hóa đơn nhà cung cấp thực hiện ở Billing.

Luồng ba agent đã đi qua phân tích quy trình, truy vấn ngữ cảnh và kiểm chứng. Agent ngữ cảnh yêu cầu dữ kiện hiện có cùng phép so sánh các đơn cùng sản phẩm ở công đoạn vận chuyển. Agent kiểm chứng gọi công cụ kiểm tra điều kiện thời gian và phân chia dữ liệu rồi chấp nhận tất cả 12 mục. Không có lần sửa định dạng trong lượt này. Gói lớn nhất đã gửi có 5.963 token, cách trần 37 token.

Sáu nhận định số liệu/trạng thái được chọn gồm ba thông tin về chuẩn bị bàn giao, một thông tin về giả định dữ liệu ngữ cảnh sẵn có, một về sự hiện diện của một người bán và một về một sản phẩm trong đơn. Ba ghi chú là cơ sở ngày hẹn chưa xác minh, tính sẵn có của dữ liệu ngữ cảnh chưa xác minh và thiếu mẫu tương đồng cho vận chuyển. Ba đề xuất kiểm tra gồm kiểm tra mốc thời gian vận chuyển, tìm phương pháp đặt ngày hẹn và xác minh thời điểm dữ liệu ngữ cảnh có sẵn.

12/12 mục riêng biệt khớp dữ liệu và điều kiện nguồn. Ghi chú sai thiếu mốc ở v2 đã hết xuất hiện trong kết quả của đơn thử này. Tuy nhiên, bộ chọn nhận định tập trung vào chuẩn bị bàn giao đang dưới ngưỡng và chưa chọn cờ vận chuyển vượt ngưỡng cho phần nhận định chính. Chỉ số bao phủ cờ cảnh báo là 0/1. Đề xuất kiểm tra có nhắc đến vận chuyển, nhưng đầu ra scalar thiếu thông tin trọng tâm về cảnh báo. Kết quả quy tắc đã lưu đạt 1/1 ở chỉ số này. Trạng thái evidence_checked hiện xác nhận các mục đã chọn khớp nguồn, còn tính đầy đủ cần dùng chỉ số bao phủ riêng.

Nhánh một agent đã gọi get_reference_membership cho công đoạn vận chuyển. Công cụ cung cấp thống kê nhóm tham chiếu và tạo thêm lựa chọn cho agent. Gói tiếp theo tăng lên 6.050 token. Lượt này có trạng thái cancelled_unsent, chỉ lượt trước có phản hồi suy luận và chi phí. Toàn bộ diễn biến được giữ trong kết quả so sánh.

Đã dựng lại cả hai lịch sử từ nguồn, đối chiếu chính xác yêu cầu gửi, biên nhận, các câu trả lời gốc, giá trị công bố và dữ liệu ngân sách lịch sử. 111 kiểm thử liên quan đạt, bao gồm hai kiểm thử mới cho ghi nhận token và chặn trước suy luận. Bước kiểm tra lại chạy offline với 0 lần gọi mạng. Kết quả kỹ thuật nằm trong docs/agent_live_compact_validation_2026-11-06.json.

Thư mục data/experiments/olist_agent_live_compact_v1_case2/ lưu kế hoạch trước chạy, bộ đếm token chính thức, từng yêu cầu gửi, kết quả và biên nhận của hai nhánh, sổ ngân sách trước thử cùng manifest. Từ thư mục gốc project, có thể đối chiếu lại bằng lệnh sau.

```sh
.venv/bin/python -B scripts/06_agent_evaluation/live/run_compact_pilot.py --verify
```

Tiến độ hiện tại là đã có một lượt multi-agent v3 hoàn chỉnh trên đơn phát triển đã từng bộc lộ lỗi. Chất lượng chọn thông tin và khả năng chạy trọn nhánh một agent cần được xử lý trước khi mở rộng pilot hoặc xếp hạng các cấu hình. Các kết quả này chưa cung cấp nhãn tắc nghẽn độc lập hay ước lượng độ chính xác phát hiện.

Đề xuất bước kế tiếp là tối ưu thêm gói phát sinh khi truy xuất nhóm tham chiếu và bổ sung kiểm tra tính đầy đủ của phần giải thích. Mọi công đoạn có cờ cảnh báo cần được trình bày rõ trong đầu ra, hoặc được ghi nhận là giải thích còn thiếu để agent sửa trong số lượt cho phép. Cần kiểm tra offline trên các lịch sử thật vừa lưu, rồi duyệt phạm vi phép thử mới. Mục tiêu demo khoảng 18–20/11 sẽ được đánh giá lại sau khi hai điểm này được xử lý và pilot mở rộng có kết quả.

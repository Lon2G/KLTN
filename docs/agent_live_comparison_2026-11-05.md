# Thử AI trên một đơn Olist ngày 05/11/2026

Hai cấu hình GPT-6 Luna đã nhận tổng cộng bốn phản hồi thật. Cả hai dừng ở bước đếm token trước khi có giải thích cuối cùng. Kết quả này cho biết hệ thống dừng ở đâu trong điều kiện chạy đã chốt. Việc sửa lỗi ghi chú thiếu mốc vẫn cần được kiểm tra trên một đầu ra AI hoàn chỉnh.

## Đơn thử và tiêu chí

Chọn trước đơn thứ ba (index 2) trong 20 đơn phát triển ngành `bed_bath_table`. Phiên bản v2 từng ghi thiếu mốc trên đơn này dù cả bốn mốc đều có. Hai cấu hình dùng cùng thời điểm quan sát, dữ liệu thực, kết quả detector và giá token. Profile `native_bounded` giới hạn một agent tối đa 4 lượt và ba agent tối đa 12 lượt. Nhánh quy tắc lấy từ lần chạy đã lưu. Chi phí tối đa đã định cho phép thử là 0,0216 USD.

Khi sự kiện giao hàng được ghi nhận ngày 31/05/2018, thời gian từ bàn giao đến giao khách là 22,28 ngày. Trong điều kiện giả định các thuộc tính tĩnh đã có từ lúc đặt hàng, bộ phát hiện sử dụng nhóm tham chiếu mở rộng theo ngành gồm 69 mẫu, trung bình 9,89 ngày và ngưỡng trên 18,28 ngày. Công đoạn vận chuyển vượt trung bình 12,39 ngày. Hai công đoạn duyệt đơn và chuẩn bị bàn giao đều dưới ngưỡng cảnh báo.

## Kết quả theo cấu hình

| Cấu hình | Phản hồi mô hình nhận được | Token đầu vào / đầu ra đã ghi nhận | Thời gian tạo giải thích đến khi dừng | Chi phí từ usage (USD) | Đầu ra |
| --- | ---: | ---: | ---: | ---: | --- |
| Quy tắc | 0 | 0 / 0 | 0,0063 giây | 0 | 12 mục riêng biệt khớp nguồn, nêu đúng công đoạn có cảnh báo |
| Một agent | 1 | 5.710 / 16 | 3,08 giây | 0,000721675 | Đã yêu cầu truy xuất lại trạng thái quan sát. Lượt kế tiếp bị chặn trước suy luận |
| Ba agent | 3 | 15.086 / 156 | 12,80 giây | 0,001372310 | Agent quy trình nộp bản sơ bộ. Agent ngữ cảnh dừng sau khi truy vấn nhóm cùng sản phẩm. Agent kiểm chứng chưa nhận lượt |

Trong bản sơ bộ, agent quy trình chọn ghi chú chưa xác minh được cơ sở ngày hẹn và đề xuất kiểm tra tuyến vận chuyển cùng các mốc thời gian. Kết quả công cụ của agent ngữ cảnh tìm được 0 đơn thỏa toàn bộ điều kiện so sánh cùng sản phẩm, với tối thiểu 30 đơn theo quy định. Nhóm này có tiêu chí chặt hơn nhóm 69 mẫu mà detector đã sử dụng. Hệ thống lưu các trao đổi trung gian và hiển thị cảnh báo của detector riêng. Hai nhánh AI có 0 mục được xuất bản ở lần thử này. Chất lượng giải thích cuối, khả năng xác định nguyên nhân và độ chính xác phát hiện vẫn chưa có kết quả đánh giá từ phép thử này.

## Chẩn đoán và kiểm tra lại

Đã dựng lại từng yêu cầu từ dữ liệu Olist và biên nhận thật rồi khớp mã băm với yêu cầu gốc. Hai lần gọi riêng tới endpoint đếm token của OpenAI xác nhận gói tiếp theo của một agent dài 6.032 token và gói của ba agent dài 6.259 token. Chúng vượt trần 6.000 token lần lượt 32 và 259 token. Hai phép đếm phục vụ chẩn đoán không sinh phản hồi mô hình. Bốn lượt suy luận có biên nhận, hai lượt bị chặn có trạng thái `cancelled_unsent`. Tổng chi phí theo sổ nội bộ là 0,002093985 USD. Tất cả biên nhận cũ vẫn khớp, không có khoản chờ quyết toán. Trong trần 5 USD của project còn 4,982213475 USD theo sổ nội bộ. Đơn giá đã đối chiếu với [trang GPT-6 Luna của OpenAI](https://developers.openai.com/api/docs/models/gpt-6-luna).

Kết quả gốc và biên nhận ở thư mục `data/experiments/olist_agent_live_comparison_v1_case2/`. Có thể kiểm tra lại hoàn toàn offline từ thư mục gốc của project bằng lệnh sau.

```sh
.venv/bin/python -B scripts/06_agent_evaluation/live/audit_case2.py
```

96 kiểm thử liên quan đã chạy đạt sau lần thử. Bài kiểm thử hồi quy v2 dùng bản sổ ngân sách đã sao lưu trước thử nghiệm để tái tạo đúng trạng thái lịch sử và tiếp tục kiểm tra các mã băm gốc.

Bước đề xuất là tạo phiên bản gói bằng chứng gọn hơn, tham chiếu bằng ID khi công cụ trả lại dữ kiện đã có, giữ số liệu, điều kiện so sánh và dấu vết nguồn cho từng lựa chọn. Kiểm tra offline trước, sau đó chạy lại một cặp trên chính đơn này khi được duyệt. Kết quả lần thử mới sẽ lưu riêng để đối chiếu với lần dừng này.

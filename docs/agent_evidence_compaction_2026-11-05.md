# Tiến độ tối ưu bằng chứng ngày 05/11/2026

Đã hoàn thành phần tối ưu và kiểm tra offline được duyệt. Bộ mã hóa lưu mỗi bằng chứng một lần trong lịch sử gửi cho một agent, rồi dùng mã tham chiếu khi công cụ trả lại cùng bằng chứng. Thông tin chung về đơn, thời điểm quan sát, chế độ phân tích, phiên bản đánh giá và nguồn dữ liệu được lưu một lần. Các trường dữ liệu, mã bằng chứng gốc, mã lựa chọn, điều kiện so sánh và mốc thời gian đều truy xuất lại được qua bộ giải mã.

Mỗi yêu cầu được dựng lại độc lập. Agent chỉ được tham chiếu những bằng chứng nằm trong gói hiện tại hoặc lịch sử đã gửi cho chính agent đó. Thông tin mới từ công cụ tiếp tục được bổ sung đầy đủ. Nếu dữ liệu đi kèm mã bằng chứng bị sửa, hoặc gói trộn đơn hàng, thời điểm hay chế độ khác nhau, bộ mã hóa dừng với lỗi kiểm tra. Giá trị null, thời gian âm và điều kiện thiếu mẫu đều được kiểm tra riêng.

Tôi chọn triển khai thành lớp mã hóa đặt trước provider để hai nhánh một agent và ba agent dùng chung cách gửi bằng chứng. Mỗi cấu hình có hậu tố phiên bản và mã yêu cầu riêng, giúp theo dõi lần thử sau bằng sổ ngân sách hiện hành. Các script nằm trong `scripts/06_agent_evaluation/compact/`. Gói này có lệnh kiểm tra offline, sử dụng các engine đã có và cơ chế giới hạn token của provider.

| Gói từng bị chặn | Kích thước gốc | Kích thước sau tối ưu | Giảm |
| --- | ---: | ---: | ---: |
| Một agent | 22.039 byte | 21.040 byte | 4,53% |
| Ba agent | 23.691 byte | 22.524 byte | 4,93% |

Các số trên đo toàn bộ JSON yêu cầu UTF-8, bao gồm hướng dẫn và định nghĩa công cụ. Số token chính thức sau tối ưu còn chờ phép đếm của provider trong bước thử được duyệt tiếp theo. Trần cấu hình hiện hành là 6.000 token đầu vào và 1.200 token đầu ra mỗi lượt. Chi phí API của bước offline này là 0 USD.

Đã kiểm tra cả 6 yêu cầu gốc của lần thử trước, gồm 4 lượt có phản hồi và 2 lượt bị chặn. Mỗi gói rút gọn giải mã lại khớp mã băm của yêu cầu gốc. Phát lại bốn phản hồi đã lưu cho cùng diễn biến và điểm dừng. Phản hồi cho gói mới khi gửi AI thật vẫn cần thử riêng.

Đã kiểm tra 540 bản ghi bằng chứng của 20 đơn Olist trong hai chế độ sử dụng dữ liệu. Lần gửi đầu và lần yêu cầu lặp đều khôi phục chính xác nội dung nguồn. 109 kiểm thử liên quan đạt, gồm 13 kiểm thử mới. Kiểm thử mới bao phủ việc cô lập ngữ cảnh giữa các agent, sửa đổi bằng chứng, mốc thiếu, thời gian âm, thiếu mẫu, sửa câu trả lời, agent kiểm chứng bác bỏ và giới hạn lượt gọi. Fixture chỉ dùng để kiểm tra giao thức phần mềm. Dữ liệu nghiệp vụ trong các phép kiểm tra đến từ snapshot Olist thật.

70 file được đối chiếu mã băm. Kết quả kiểm tra bốn snapshot đều đạt và sổ ngân sách khớp mã băm trước/sau. Kết quả chi tiết ở `docs/agent_evidence_compaction_validation_2026-11-05.json`. Gói bằng chứng và sáu yêu cầu rút gọn ở `data/experiments/olist_agent_evidence_compaction_v1/`.

Từ thư mục gốc của project, có thể chạy kiểm thử bằng các lệnh sau.

```sh
.venv/bin/python -B -m unittest discover -s tests -p 'test_agent_evidence_compaction.py'
.venv/bin/python -B scripts/06_agent_evaluation/prepare_comparison.py --verify data/experiments/olist_agent_evidence_compaction_v1
```

Project đang ở giai đoạn tích hợp và kiểm chứng multi-agent. Nền dữ liệu, phát lại sự kiện, tính điểm công đoạn và công cụ phân tích đã có snapshot để truy vết. V3 cần một lần chạy hoàn chỉnh qua agent kiểm chứng để đánh giá đầu ra cuối, sau đó mới mở rộng so sánh và nối dashboard. Thư mục `scripts/06_dashboard/` hiện đang trống.

Kế hoạch dưới đây là mục tiêu có điều kiện, tính theo mốc nộp 30/11/2026 và ngày bắt đầu hiện tại 05/11/2026. Mốc đầu phụ thuộc việc duyệt phép thử API tiếp theo và khả năng hoàn tất trong ngân sách.

| Thời gian mục tiêu | Công việc và điều kiện hoàn thành |
| --- | --- |
| 06–08/11 | Đếm token và thử lại cùng đơn, hoàn tất cả hai nhánh, đối chiếu nguồn và các ghi chú cuối. Nếu còn vượt trần, rà lại gói phát sinh ở chính lượt đó |
| 09–13/11 | Mở rộng có giới hạn trên nhóm phát triển, so sánh quy tắc/một agent/ba agent, lưu cả lượt thất bại, chi phí, độ trễ và đánh giá lời giải thích |
| 14–20/11 | Tích hợp dashboard local, xem cảnh báo theo nhóm và từng đơn, trình bày bằng chứng cùng phần còn thiếu, đo thời gian xử lý và chạy demo đầu-cuối |
| 21–26/11 | Hoàn thiện báo cáo khóa luận từ dữ liệu thực nghiệm đã lưu và chuẩn bị nội dung bảo vệ |
| 27–29/11 | Rà soát số liệu, biểu đồ, khả năng tái lập, chạy thử demo và sửa lỗi cuối |

Mục tiêu bản demo ổn định là khoảng 18–20/11. Khả năng giữ lịch phụ thuộc kết quả pilot AI, khối lượng nhận xét cần sửa và yêu cầu bổ sung của giảng viên. Cần đánh giá lại lịch sau khi hai cấu hình hoàn thành trên đơn thử đầu tiên. Từ nay cần lưu kết quả và ghi chú thực nghiệm theo từng bước để phục vụ thời gian viết báo cáo cuối kỳ.

# Phục hồi mã chấm điểm sự kiện — 30/10/2026

## Kết quả

Đã phục hồi **đúng bản gốc**, không cần triển khai thuật toán thay thế hoặc
tạo phiên bản scoring mới. Hai script và file cấu hình có SHA-256 trùng hoàn
toàn với manifest của snapshot đã lưu. Bộ kiểm thử và hai tài liệu đi kèm
cũng được phục hồi từ lịch sử bản vá.

| File | Cách xác nhận |
| --- | --- |
| [score_event_stages.py](../scripts/04_streaming/score_event_stages.py) | SHA-256 khớp manifest cũ |
| [run_stage_scoring_replay.py](../scripts/04_streaming/run_stage_scoring_replay.py) | SHA-256 khớp manifest cũ |
| [protocol.json](../templates/event_stage_scoring_v1/protocol.json) | SHA-256 khớp manifest cũ |
| [test_event_stage_scoring.py](../tests/test_event_stage_scoring.py) | Khôi phục chuỗi patch, kiểm tra cú pháp và chạy 28 test đạt |
| [event_stage_scoring.md](event_stage_scoring.md) | Khôi phục chuỗi patch, đối chiếu byte với bản dựng tạm |
| [progress_event_scoring_2026-10-28.md](progress_event_scoring_2026-10-28.md) | Khôi phục đủ các lần cập nhật ngày 28/10 |

## Nguồn phục hồi

Không tìm được các file trong stash, lịch sử các ref Git local hoặc worktree
đính kèm. Sau đó tìm thấy **9 bản vá ngày 28/10 trong lịch sử chính cuộc trò
chuyện này**. Chỉ trích nội dung patch của sáu đường dẫn cần phục hồi; không
thực thi các lệnh shell lưu trong lịch sử.

Ghép từng patch theo thứ tự trong bộ nhớ/thư mục tạm, yêu cầu context khớp
chính xác và không mơ hồ. Trước khi phục hồi vào project đã kiểm tra đủ sáu
đường dẫn đang thiếu và hash của ba file được manifest ràng buộc. Không dùng
kết quả đầu ra để suy ra hoặc sửa công thức, cũng không sửa manifest cho khớp
một phiên bản code mới.

Việc tìm lại nguồn đã giải quyết vấn đề tái lập. Chưa xác định nguyên nhân
các file trước đó không còn trong checkout.

## Đối chiếu kết quả đã lưu

Đã chạy 28 test của gói chấm điểm, bao gồm hàm `verify_snapshot()` phát lại
đầy đủ bằng code phục hồi, tính kết quả trong bộ nhớ và đối chiếu snapshot.

| Nội dung | Kết quả |
| --- | --- |
| Sự kiện thật | 7.076 |
| Đơn trong phạm vi | 1.853 |
| Bản ghi đánh giá công đoạn ở hai chế độ | 11.118, gồm cả phần chưa chấm được |
| Nhật ký thay đổi đánh giá | 28.972 bản ghi; không phải 28.972 đơn |
| Công đoạn chung với kết quả batch cũ | 4.233, khớp chính xác 11 trường đối chiếu |
| Output kê trong manifest | 13/13 khớp hash |
| Source kê trong manifest | 76/76 khớp hash |
| Code/config kê trong manifest | 10/10 khớp hash, hết file thiếu |

Verifier so sánh toàn bộ kết quả JSONL, CSV, summary, nhóm tham chiếu, nhật ký
quyết định, dữ liệu SQLite và trạng thái sự kiện gốc. Không chỉ kiểm tra tổng
số dòng. Các lần đo thời gian thực thi có thể khác theo máy/tải; không yêu
cầu hoặc tuyên bố tái lập thời gian chạy từng request.

Chế độ giả định bối cảnh tĩnh đã biết từ lúc đặt đơn vẫn là **giả định nghiên
cứu**, không trở thành dữ liệu khả dụng đã xác minh nhờ việc phục hồi code.
Số cảnh báo không phải số tắc nghẽn đã được xác nhận. Kiểm chứng này xác nhận
tính tái lập, không chứng minh độ chính xác nghiệp vụ độc lập.

## Kiểm thử và bảo toàn

- **28 test chấm điểm đạt**, 75,619 giây. Gồm tính lại toàn bộ snapshot, không
  dùng tương lai, nhóm tham chiếu, mốc thiếu/âm/bằng nhau, case bảo lưu, rollback,
  chống trùng và khởi động lại.
- **22 test ngân sách/readiness đạt**, 1,312 giây. Có thêm kiểm tra danh sách
  việc cần phục hồi phải phản ánh tình trạng file thực tế.
- Tổng **50 test liên quan đạt**, không phải toàn bộ test suite của repository.
- Python/JSON parse và whitespace của sáu file phục hồi đạt; `git diff --check`
  đạt. Không phát sinh gọi API tính phí, train lại, đổi ngưỡng hoặc tạo nhãn.
- Đối chiếu **1.070 file có sẵn trước lượt này**: 1.068 giữ nguyên, không xóa
  file nào. Hai file sửa có chủ đích là công cụ audit readiness và test tương
  ứng, để hết báo thiếu nguồn khi nguồn đã được khôi phục.
- Toàn bộ dữ liệu, mô hình, snapshot và báo cáo lịch sử có sẵn giữ nguyên.
  Các báo cáo audit trước lúc phục hồi được giữ như ghi nhận tại thời điểm đó.
- Chưa commit/push Git trong lượt này.

Các lỗi cố ý trong test chỉ phục vụ kiểm thử kỹ thuật trong bộ nhớ/SQLite tạm;
không được thêm vào dữ liệu nghiên cứu.

## Kiểm tra lại khi cần

```sh
env MPLCONFIGDIR=/tmp/kltn-matplotlib .venv/bin/python -B scripts/04_streaming/run_stage_scoring_replay.py --verify
.venv/bin/python -B scripts/05_multi_agent/audit_readiness.py
```

Không chạy builder mặc định để ghi lại snapshot cũ; builder có cơ chế từ chối
ghi đè. Muốn tạo một lần chạy mới phải dùng thư mục output mới.

Audit chỉ kiểm tra hash nên trường `semantic_reproduction_performed` của
riêng audit vẫn là false; phép tái lập ngữ nghĩa đã chạy qua test của scorer
được ghi riêng trong [bằng chứng phục hồi JSON](event_stage_recovery_2026-10-30.json).

## Phần còn lại

Vướng mắc thiếu mã chấm điểm đã được giải quyết. Chưa cần import lại dữ liệu.
Bước tiếp theo là nối các công cụ bằng chứng/đối chiếu nhóm và cơ chế điều
phối ba agent, tích hợp chặn chi phí trước API, rồi chọn 20 đơn thật cho pilot.
`api_pilot_ready` vẫn false vì những phần tích hợp đó chưa được triển khai;
không còn do thiếu ba file đã phục hồi.

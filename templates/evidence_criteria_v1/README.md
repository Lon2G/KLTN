# Đối chiếu và lựa chọn cách xử lý bằng chứng

Phiên bản này chọn **chính sách diễn giải**, không chọn detector chính xác nhất.
Dùng dữ liệu thật Olist bed_bath_table trong tập phát triển được bảo vệ. Không
chấm test, thay mô hình cũ, tạo nhãn chuyên gia hay coi cảnh báo máy là đáp án.

Các bảng:

- `criteria_decisions.csv`: tiêu chí đã giải quyết, căn cứ, phạm vi và giới hạn.
- `case_decisions.csv`: ba lớp độc lập là chất lượng nguồn, kết quả ngày hẹn,
  dấu hiệu công đoạn; trường hợp chưa chấm được giữ riêng.
- `stage_evidence.csv`: bằng chứng và mốc train tham chiếu cho mỗi công đoạn.
- `threshold_sensitivity.csv`: bốn phân vị trên cùng các đơn đủ điều kiện;
  ngưỡng chung và theo bối cảnh. Không phải bảng độ chính xác.
- `threshold_disagreements.csv`: mã đơn có kết quả khác nhau giữa hai cách,
  không tự gọi một phía là đúng hoặc sai.
- `saved_model_comparison.csv`: 12 cấu hình đã lưu, so trên cùng các đơn.
  Các cột giao trễ/giao sớm là giao cắt quan sát, không phải TP/FP/FN của
  bất thường nghiệp vụ. Không chọn cấu hình có ít cảnh báo nhất.
- `contract_checks.csv`: kiểm tra các quy ước phần mềm; không phải nhãn chuẩn.
- `bao_cao_tieu_chi.md`: kết quả, quyết định và giới hạn bằng tiếng Việt.

Chọn so sánh theo ngày lịch vì ngày dự kiến không cung cấp giờ hẹn cụ thể và
quy ước người dùng đã thống nhất. Không coi giao sau 00:00 trong cùng ngày là
trễ. Thiếu dữ kiện, đảo mốc, chênh tiền đều có trạng thái riêng; giữ nguyên
dữ liệu nguồn. Cảnh báo dài/ngắn không bị nâng thành nhãn lỗi hoặc gian lận.

Nhóm tham chiếu là train đã quan sát trước validation, không có nhãn bình
thường đã xác nhận. So sánh toàn bộ validation đủ điều kiện, không dùng riêng
40 đơn đã chọn có chủ đích. Ngưỡng 95%/5% là cấu hình mô tả tạm thời. Thử bốn
mức để thấy độ nhạy, không tối ưu độ chính xác bằng cách giảm số cảnh báo.
Không hiệu chỉnh xác suất, không bảo đảm tỷ lệ báo nhầm 5%; xét nhiều công
đoạn, dịch chuyển phân phối và các bối cảnh chưa kiểm soát đều ảnh hưởng kết quả.

Các cấu hình mô hình lịch sử có cùng mục tiêu vận chuyển. Không so MAE để
chọn detector chính của đề tài; không lấy đồng thuận làm ground truth. Điểm
số và ngưỡng trong detector là cơ chế ra cờ, không mặc nhiên là xác suất đã
được kiểm chứng. Giữ test ngoài lựa chọn và hiệu chỉnh theo hướng dẫn nguồn:
[outlier detection](https://scikit-learn.org/stable/modules/outlier_detection.html),
[data leakage](https://scikit-learn.org/stable/common_pitfalls.html#data-leakage).

Kết quả là phân tích hồi cứu, không chứng minh near real-time, nguyên nhân
tắc nghẽn hoặc trách nhiệm seller. Người dùng đã cho phép lựa chọn phương án;
không ghi giả phê duyệt của giảng viên. Việc chọn chính sách phù hợp quy ước
không chứng minh nó ít sai lệch nhất trong mọi phương pháp.

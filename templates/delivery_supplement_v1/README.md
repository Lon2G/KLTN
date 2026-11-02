# Nhóm đối chiếu bổ sung về quy trình giao hàng

Chỉ dùng dữ liệu Olist bed_bath_table đã có. Đây là nhóm đối chiếu có hỗ trợ bằng
phân tích, không phải tập kiểm chứng mù, nhãn chuẩn hay kết quả chọn mô hình.

- `case_evidence.csv`: đơn được chọn, ngày hẹn/thực tế và bối cảnh nguồn.
- `stage_comparisons.csv`: ba công đoạn, nhóm train tham chiếu, cỡ nhóm,
  trung vị, phân vị 95% và vị trí của đơn trong phân phối tham chiếu.
- `candidate_audit.csv`: toàn bộ đơn đủ điều kiện lấy mẫu và các cờ chọn mẫu;
  bao gồm nhóm chồng lấn và kết quả chọn cuối cùng. Không phải nhãn bất thường.
- `reference_members.csv`: dữ kiện train dùng để tái tính các nhóm tham chiếu.
- `saved_model_evidence.csv`: tín hiệu đã lưu của các cấu hình cùng mục tiêu
  thời gian vận chuyển. Không phải mô hình vừa train hay kết quả chấm test.
- `payment_records.csv`, `item_records.csv`: dòng nguồn thực tế để đối chiếu.
- `assessment_status.csv`: phần đã xác định được và phần chưa đủ bằng chứng.
- `bao_cao_doi_chieu.md`: bảng và giải thích tiếng Việt từng đơn.
- `protocol.json`, `summary.json`, `manifest.json`: quy ước, số đếm, mã băm
  nguồn/code/kết quả; chương trình từ chối ghi đè và kiểm tra khi đọc lại.

Mỗi nhóm dự kiến 10 đơn, tổng 40, để mở rộng loại tình huống cần xem xét. Đây
không phải cỡ mẫu đã được chứng minh đủ để ước lượng độ chính xác. Chọn nhóm
bất đồng trước, sau đó trễ hẹn, công đoạn dài dù đáp ứng hẹn, rồi nhóm đối chiếu;
một đơn chỉ được chọn một lần. Thiếu ứng viên sẽ báo thiếu, không tạo dữ liệu.

Phân vị >95% chỉ giúp tìm trường hợp công đoạn tương đối dài để xem xét, không
phải ngưỡng bất thường đã tối ưu. Nhóm train chỉ xét dữ kiện đã kết thúc trước
đơn validation đầu tiên. Ưu tiên cùng bang khách hàng, nhóm khoảng cách và đơn
một/nhiều seller; nếu ít hơn 30 đơn thì dùng nhóm rộng hơn và ghi rõ. Cỡ nhóm 30
là quy ước kỹ thuật sơ bộ, không phải bảo đảm độ tin cậy hay tính tương đồng.
Nhóm tham chiếu có thể vẫn chứa bất thường chưa biết, không được coi là dữ liệu
bình thường đã xác nhận. Chưa kiểm soát mùa vụ, hãng vận chuyển hoặc cấp dịch vụ.

Giao sớm/đúng hẹn không có nghĩa toàn đơn bình thường. Giao trễ/công đoạn dài/
mô hình bất đồng không chứng minh tắc nghẽn, nhập sai hay lỗi seller. Không dùng
những cờ chọn mẫu này làm đáp án để chấm chính mô hình đã tạo ra chúng. Chưa
chốt mô hình, chưa tính Precision/Recall/F1. Nhóm này chỉ gồm đơn hoàn tất có
mốc nhất quán, không thay thế đánh giá riêng đơn thiếu/sai dữ liệu hoặc đang xử lý.

Các dữ kiện thanh toán, hàng hóa, trạng thái cuối là snapshot hồi cứu. Kết quả
này không chứng minh near real-time. Giữ nguyên 120 đơn pilot, 20 đơn đã đối chiếu,
dữ liệu raw, mô hình, ngưỡng và tập test; không kích hoạt cách chia 80/20 mới.

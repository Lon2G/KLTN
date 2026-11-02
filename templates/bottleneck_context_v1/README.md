# Phân tích bối cảnh dấu hiệu tắc nghẽn

Lớp bổ sung sau `process_signals_v1`, không sửa nguồn hoặc ngưỡng. Chỉ dùng
đơn validation thật đã được bảo vệ phạm vi. Không tạo dữ liệu nghiên cứu,
nhãn người rà soát, xác suất bất thường hoặc nguyên nhân giả.

Đầu vào phép tính là hai bảng order/stage signals. Bộ kiểm tra tính toàn vẹn
nguồn có đọc lại các bảng lineage và kiểm toán lịch sử của pipeline cha;
không đưa nhãn cũ hoặc đặc trưng test vào phân tích mới. Không tuyên bố rằng
chưa từng truy cập dữ liệu lịch sử hoặc tập validation này là dữ liệu mới độc lập.

Luồng: dấu hiệu từng đơn -> công đoạn -> nhóm khu vực/thời điểm -> đối chiếu
bối cảnh -> giả thuyết cần xác minh -> đề xuất kiểm tra. Đây là phân tích hồi
cứu và checklist theo quy tắc, chưa phải hệ thống multi-agent/near real-time.

## Cách so sánh

- Theo bang khách hàng: đối chiếu các bang khác, cùng công đoạn, nhóm khoảng
  cách, phạm vi một/nhiều seller và tháng bắt đầu công đoạn.
- Theo tháng bắt đầu công đoạn: đối chiếu các tháng khác, cùng công đoạn,
  bang khách hàng, nhóm khoảng cách và phạm vi một/nhiều seller.
- Nhóm khoảng cách và toàn bộ công đoạn chỉ có thống kê mô tả. Không xếp
  hạng đơn xa là tắc nghẽn chỉ vì lâu hơn đơn gần.
- Mỗi ô đối chiếu cần ít nhất 5 đơn mỗi phía; tổng phần khớp cần 30 đơn mỗi
  phía và bao phủ ít nhất 80% số đơn đích đã chấm. Đưa vào diện kiểm tra khi
  có ít nhất 5 tín hiệu trong phần khớp và tỷ lệ đích cao hơn tỷ lệ đối chiếu.
  Đây là quy ước báo cáo tạm thời, không phải ngưỡng tối ưu hoặc kiểm định.
- Tỷ lệ đối chiếu được tính theo trọng số phân bố các ô của nhóm đích.
  Xuất cả tỷ lệ nhóm đầy đủ và tỷ lệ phần khớp; không đánh tráo hai mẫu số.
- Không có ô khớp thì để kết quả so sánh trống; không tự nới điều kiện hoặc
  dùng 0. Khi so theo tháng, loại tháng chứa mốc chặn khỏi cả phía đích và
  đối chiếu; so khu vực vẫn khớp tháng. Các tháng còn lại vẫn có thể thiếu
  theo dõi; chưa phải thực nghiệm với thời gian theo dõi bằng nhau.

Tỷ lệ là số đơn vượt ranh giới train tương ứng từng bối cảnh, không phải so
cùng một số ngày cố định. Chênh lệch dương rất nhỏ vẫn có thể vào danh sách,
không chứng minh khác biệt đáng kể hay cần can thiệp ngay.

Các nhóm giao nhau, các đối chứng được tái sử dụng và các đơn có thể cùng
seller. Không coi các nhóm là phát hiện độc lập, không tính xác suất nguyên
nhân, p-value, độ chính xác hoặc suy ra tác động can thiệp. Chuẩn hóa theo
một số biến quan sát không loại bỏ nhiễu, sai lệch chọn mẫu hoặc việc đơn
chưa hoàn tất có thể bị thiếu trong phần đánh giá. Khoảng cách ZIP đường
thẳng không phải tuyến đi thực tế; bang khách hàng không phải nơi xảy ra sự cố.

## Đầu ra

- `stage_context.csv`: bằng chứng theo đơn/công đoạn và tháng bắt đầu.
- `stage_overview.csv`: tổng số, mẫu số chấm được, thời lượng và kết quả giao.
- `context_groups.csv`: nhóm, tỷ lệ, mức bao phủ và trạng thái đối chiếu.
- `matched_cells.csv`: từng ô và trọng số, gồm cả ô không đủ điều kiện.
- `group_membership.csv`: đơn thuộc nhóm đích/đối chiếu, có vào phép so sánh
  hay không; truy ngược về bằng chứng thật, không phải nhãn độc lập.
- `investigation_actions.csv`: chỉ tạo cho nhóm đạt quy ước; tách quan sát,
  giả thuyết, dữ liệu cần xác minh, bước kiểm tra và can thiệp có điều kiện.
- `bao_cao_boi_canh.md`, protocol, summary và manifest kiểm tra hash.

Số ngày vượt ranh giới là tổng hợp tín hiệu mô tả, không phải thời gian chờ
thực tế, thiệt hại hoặc số ngày có thể tiết kiệm. Trễ hẹn có mẫu số riêng;
đồng xuất hiện với công đoạn dài không chứng minh công đoạn đó gây trễ.

## Chạy

```bash
env PYTHONPATH=scripts/01_data_preparation:scripts/03_anomaly_detection MPLCONFIGDIR=/private/tmp/kltn-matplotlib XDG_CACHE_HOME=/private/tmp/kltn-cache .venv/bin/python -B scripts/03_anomaly_detection/analyze_bottleneck_context.py
env PYTHONPATH=scripts/01_data_preparation:scripts/03_anomaly_detection MPLCONFIGDIR=/private/tmp/kltn-matplotlib XDG_CACHE_HOME=/private/tmp/kltn-cache .venv/bin/python -B scripts/03_anomaly_detection/analyze_bottleneck_context.py --summary
```

Snapshot mới ở `data/experiments/olist_bottleneck_context_v1`. Từ chối ghi đè
và ghi vào nguồn/snapshot cũ. Muốn đổi phương pháp sau khi phát hành phải
tạo phiên bản mới. Dữ liệu nghiên cứu đang bị git ignore cần được sao lưu
riêng, không được coi một lần push code là đã sao lưu các kết quả.

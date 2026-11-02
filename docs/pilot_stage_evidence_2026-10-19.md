# Bổ sung bằng chứng công đoạn cho 120 đơn pilot

## Mục đích

Tiếp nối việc rà soát kỹ thuật 120 đơn, chuẩn bị gói phân tích có hỗ trợ máy
để đọc từng công đoạn theo các quy ước đã thống nhất. Đây không phải bản
thảo luận văn, một vòng huấn luyện mới hay một bộ nhãn độc lập.

Giữ nguyên pilot cũ, báo cáo audit trước, ngưỡng đã lưu và đầu ra 1.621 đơn.
Gói mới nằm riêng tại
`data/experiments/olist_pilot_stage_evidence_v1/`.

## Đã thực hiện

- Giữ đủ 120 mã trong sổ phạm vi; BR0043 và BR0119 chỉ có thông tin phạm vi,
  không chấm kết quả vì nằm từ ranh giới test đề xuất chưa kích hoạt trở đi.
- Tạo 354 dòng công đoạn cho 118 đơn còn lại; giữ phần không quan sát được
  là thiếu dữ liệu, không thay bằng 0.
- Tái sử dụng phép tính thời lượng, chọn nhóm tham chiếu, ECDF, cờ và diễn
  giải trong các module hiện có. Không viết lại thuật toán phát hiện.
- Dùng nguồn tham chiếu train có 5.461 đơn và 15.931 dòng công đoạn khả dụng,
  kết thúc trước 2018-03-01 01:04:05. Không đưa bất kỳ đơn pilot nào vào
  nhóm dùng làm tham chiếu cho chính nó.
- Ghi số lượng và mã nhóm tham chiếu, trung vị, chênh lệch thời lượng,
  ranh giới ngày chính xác và cờ cảnh báo riêng biệt.
- Ghi nhận nguồn kết quả là phân tích có hỗ trợ máy trong
  `exposure_notice.json`; không tuyên bố người dùng đã thực sự rà soát hoặc
  rằng phiếu mới còn mù đối với kết quả quy tắc.

## Kết quả

| Nội dung | Số lượng |
| --- | ---: |
| Đơn so đủ ba công đoạn | 104 |
| Đơn chưa so đủ, trong 118 đơn | 14 |
| Công đoạn nhanh hơn trung vị tham chiếu | 150 |
| Công đoạn chậm hơn trung vị tham chiếu | 162 |
| Công đoạn chưa so được | 42 |
| Đơn có ít nhất một công đoạn vượt ngưỡng dài | 30 |
| Đơn giao sớm nhưng vẫn có công đoạn vượt ngưỡng dài | 16 |
| Đơn giao sớm, so đủ nhưng không có công đoạn nhanh hơn | 9 |

Các nhóm đơn giao nhau. Chậm hơn trung vị không tự đồng nghĩa với vượt
ngưỡng cảnh báo. Pilot phân tầng theo tháng và cờ mô hình cũ, nên tỷ lệ
trong mẫu không đại diện tỷ lệ của toàn Olist.

Trong 14 đơn chưa so đủ: 10 đơn có mốc giao ngoài cửa sổ quan sát;
BR0025, BR0051, BR0091 không đạt chất lượng địa lý; BR0115 thiếu nhóm
khớp khoảng cách có tối thiểu 30 đơn. Theo chính sách nguồn, 10 đơn chưa
hoàn tất chưa được chấm cả các công đoạn đầu dù thời lượng đã quan sát được.
Đây là giới hạn của phiên bản, không phải khẳng định mọi công đoạn đều thiếu.

BR0079 giao sớm 13 ngày nhưng duyệt-bàn giao mất 18,182813 ngày, so với
trung vị 2,366019 ngày và ngưỡng dài 9,030984 ngày. Cả ba công đoạn không
nhanh hơn trung vị tương ứng; giữ cảnh báo duyệt-bàn giao và không suy ra
cơ chế tạo ngày hẹn, nguyên nhân hay lỗi của seller.

## Cách chạy

Từ thư mục project:

```sh
PYTHONPATH=scripts/01_data_preparation:scripts/03_anomaly_detection MPLCONFIGDIR=/private/tmp/kltn-check-mpl .venv/bin/python -B scripts/03_anomaly_detection/prepare_pilot_stage_evidence.py --verify
```

Bỏ `--verify` để tạo gói khi đường dẫn đầu ra chưa tồn tại. Script từ chối
ghi đè. Kết quả chỉ dùng dữ liệu Olist đã nhập; không tải thêm, không thêm
quan sát hoặc timestamp giả.

## Những gì chưa kết luận

- Cơ sở ngày hẹn chưa xác minh, không đồng nghĩa ngày hẹn ngẫu nhiên.
- F(d) là thứ hạng thực nghiệm, không phải xác suất bất thường. Ngưỡng
  0,95/0,05 giữ nguyên để đối chiếu, chưa chứng minh tối ưu.
- Tham chiếu chỉ gồm các công đoạn đã quan sát trước mốc thời gian; cần
  lưu ý thiên lệch do các đơn chưa hoàn tất và giới hạn bối cảnh.
- Khoảng cách ZIP là đại diện địa lý, không phải tuyến vận chuyển thật.
  Chưa kiểm soát đầy đủ hàng hóa, dịch vụ vận chuyển, mùa vụ và sự cố.
- Chưa tạo nhãn người dùng/chuyên gia; chưa tính accuracy, F1 hay AUC;
  chưa chọn mô hình, xác minh nguyên nhân hoặc chứng minh near real-time.

Bước sau là đối chiếu tính hữu ích của cảnh báo và phần thiếu bằng chứng,
ghi nhận đánh giá thực sự khi có, đồng thời tách nó khỏi kết quả do chính
quy tắc sinh ra. Không dùng gói này để tự chứng minh độ chính xác của quy tắc.

# Trung bình sản phẩm và điểm cảnh báo 0-100

## Phạm vi

Triển khai yêu cầu ngày 20/10/2026: tính trung bình thời gian từng công đoạn
theo mã sản phẩm trước khi so sánh, và có thang điểm/mức cảnh báo dễ đọc.
Đây là phiên bản thử nghiệm riêng, không sửa các kết quả, ngưỡng, code
đã đóng băng hoặc hồ sơ rà soát trước. Không dùng ví dụ số liệu giả làm
đầu vào thực nghiệm.

Mã sản phẩm là `product_id`, không phải `order_id` hay mã rà soát BR.
Nguồn chỉ gồm Olist thật trong ngành bed_bath_table ở phạm vi development
đã có. Tham chiếu lấy từ train trước validation; không dùng đơn đang chấm
hoặc kết quả tương lai để tính trung bình cho chính nó.

## Tính trung bình thế nào?

Với sản phẩm p, công đoạn s và n đơn quan sát phù hợp:

`mean(p,s) = sum(duration_i) / n`

Ba công đoạn: đặt hàng đến duyệt; duyệt đến bàn giao vận chuyển; bàn giao
đến giao khách. Không diễn giải toàn bộ khoảng duyệt-bàn giao thành thời
gian đóng gói, vì Olist không có đầy đủ các mốc nhỏ bên trong.

Mỗi đơn là một quan sát cho một công đoạn. Một đơn mua ba item cùng mã
không được đếm thành ba lần thực hiện độc lập. Trung bình sản phẩm chỉ
dùng lịch sử đơn một sản phẩm/một seller, vì nguồn ghi thời gian cấp đơn,
không ghi thời gian hoàn tất riêng cho từng item hoặc seller.

Thống kê lưu trung bình, trung vị, độ lệch chuẩn mẫu và n. N=0 thì để
trống trung bình; n=1 thì độ lệch chuẩn mẫu để trống. Không thay bằng 0,
không bịa lịch sử cho sản phẩm mới. Các thời lượng quan sát hợp lệ rất
dài vẫn được giữ; việc trung bình nhạy với đuôi dài là lý do cần báo cả
trung vị. [NIST: Measures of Location](https://www.itl.nist.gov/div898/handbook/eda/section3/eda351.htm).

Đây là thời lượng các đơn có sản phẩm đó đã quan sát được, chưa phải
thời gian xử lý nội tại của sản phẩm hoặc cam kết SLA. Các đơn chưa kết
thúc trước mốc tham chiếu không có thời lượng hoàn chỉnh và không được
điền giả, nên cần lưu ý thiên lệch do chỉ quan sát các công đoạn đã kết thúc.

## Chọn nhóm so sánh

Trung bình toàn bộ lịch sử một sản phẩm được xuất để mô tả. Điểm cảnh báo
dùng nhóm được chọn theo thứ tự:

1. Cùng sản phẩm, seller, số item, bang khách và dải khoảng cách.
2. Cùng sản phẩm, seller, số item và dải khoảng cách.
3. Cùng sản phẩm, số item, bang khách và dải khoảng cách.
4. Cùng sản phẩm, số item và dải khoảng cách.
5. Nếu vẫn thiếu dữ liệu, dùng đúng nhóm ngành hàng có khoảng cách đã
   kiểm tra chất lượng của pipeline cũ, ghi `category_context_fallback`.

Mỗi nhóm chấm điểm cần ít nhất 30 đơn độc lập theo `order_id`; đây là
quy ước tối thiểu đang dùng, không chứng minh có đủ độ chính xác thống kê.
Các mức n và cách bỏ dần điều kiện được công khai. Khi bỏ điều kiện seller,
không tuyên bố đã kiểm soát hiệu ứng seller. Nhóm fallback có thể không
khớp số item hoặc sản phẩm: phải giữ cảnh báo hạn chế này.

Đơn nhiều sản phẩm hoặc nhiều seller không được quy thời gian của cả đơn
cho từng mã. Chỉ giữ điểm cấp đơn theo bối cảnh, kèm trạng thái không thể
quy riêng cho một sản phẩm. Các trường hợp thiếu mốc, chất lượng địa lý
không đạt hoặc thiếu nhóm hợp lệ vẫn chưa có điểm.

## Chênh lệch và điểm

Hai loại chênh lệch được lưu riêng:

- `difference_from_product_mean_days`: so với trung bình lịch sử của
  riêng mã đó, chưa hiệu chỉnh bối cảnh, chỉ có ý nghĩa mô tả.
- `difference_from_reference_mean_days`: so với trung bình của nhóm thực
  sự được chọn để tính điểm.

Chênh = thời lượng thực tế - trung bình. Chênh âm là nhanh hơn, dương là
chậm hơn. Chênh phần trăm = 100 * chênh / trung bình; không chia cho 0.
Chậm hơn trung bình không tự động đồng nghĩa bất thường hoặc nghẽn.

Gọi `F(d) = count(reference_duration <= d) / n`, là thứ hạng tích lũy
thực nghiệm. Dùng dấu `<=` và báo rõ cách xử lý bằng nhau. ECDF mô tả
phân bố mẫu, không phải xác suất đơn có vấn đề.
[NIST: ECDF](https://www.itl.nist.gov/div898/handbook/eda/section3/eda35g.htm).

Quy ước điểm thử nghiệm:

```text
Thiếu điều kiện đánh giá: score = missing
Nếu d <= mean hoặc d <= 0: score = 0
Nếu còn lại: score = 100 * clip((F(d) - 0.95) / 0.05, 0, 1)
```

Điểm chỉ tăng trong đuôi dài trên 95% của nhóm và khi thời lượng lớn hơn
trung bình. Mốc 0,95 tiếp nối quy ước phát triển hiện có; không tuyên bố
được NIST khuyến nghị cho riêng Olist. Điều kiện lớn hơn trung bình là
thay đổi ở phiên bản này, được đối chiếu với cờ cũ chứ không sửa cờ cũ.

| Khoảng điểm chưa làm tròn | Mức |
| --- | --- |
| 0 | Chưa có cảnh báo dài theo quy ước |
| (0, 50] | Nhẹ |
| (50, 80] | Vừa |
| (80, 100] | Cao |
| Trống | Chưa đủ dữ liệu |

Các mốc 50/80 là lựa chọn hiển thị thử nghiệm, tương ứng F ở mức
0,975/0,99 khi qua điều kiện trung bình. Không phải ngưỡng tối ưu, tiêu
chuẩn quốc tế, xác suất đúng hay mức thiệt hại. Code so trực tiếp các
tỷ lệ đếm bằng số nguyên để không đổi mức do làm tròn số thực.

Nhóm nhỏ có điểm nhảy bậc: bước danh nghĩa là 2000/n điểm, và giá trị
bằng nhau có thể làm bước nhảy lớn hơn. Gắn cờ độ phân giải đuôi hạn chế
khi n<100, không gọi n>=100 là chứng minh độ tin cậy. Điểm 100 bão hòa
ở cực trị; phải xem kèm số ngày/% vượt trung bình để phân biệt mức kéo dài.

Điểm đơn lấy công đoạn có điểm cao nhất, không lấy trung bình ba điểm
để làm mờ một công đoạn dài. Báo số công đoạn chấm được. Điểm bằng 0
ở phần đã chấm không biến các phần thiếu thành bình thường.

## Trễ hẹn, nhanh/chậm và nghẽn

Trễ ngày hẹn vẫn tính từ ngày giao thực tế so ngày hẹn gốc, tách riêng
với điểm. Giao sớm không xóa cảnh báo công đoạn. Giữ chênh lệch nhanh
hơn trung bình và không ép phải có công đoạn nhanh.

Tên kết luận là "công đoạn kéo dài cần kiểm tra", không phải nguyên nhân
nghẽn đã chứng minh. Cơ sở đặt ngày hẹn chưa được xác minh. Không tính
ngày hẹn mới, không sửa lịch sử và không tạo mốc thời gian vận hành giả.

## Chạy và đọc đầu ra

```sh
.venv/bin/python -B scripts/03_anomaly_detection/build_product_stage_scores.py
.venv/bin/python -B scripts/03_anomaly_detection/build_product_stage_scores.py --verify
```

Thư mục mặc định: `data/experiments/olist_product_stage_scores_v1`.
Từ chối ghi đè. Đọc `bao_cao_diem_cong_doan.md`, thống kê sản phẩm,
điểm từng công đoạn và điểm từng đơn. Nhóm tham chiếu lưu danh sách mã
đơn train để tính lại chính xác; ánh xạ sản phẩm và nguồn thời lượng
được lưu riêng. Hai mã pilot giữ riêng vẫn chỉ có thông tin phạm vi.

Không tạo nhãn người, không tính AUC/độ chính xác, không chọn mô hình.
Các bậc điểm cần được đánh giá bằng bằng chứng phù hợp trước khi gọi là
tối ưu. Chạy lại các quy tắc trên chính kết quả của chúng không xác nhận
độ chính xác phát hiện bất thường.

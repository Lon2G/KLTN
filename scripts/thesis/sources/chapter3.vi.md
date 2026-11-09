# CHƯƠNG 3. DỮ LIỆU VÀ PHƯƠNG PHÁP PHÁT HIỆN BẤT THƯỜNG

Chương này trình bày nguồn dữ liệu, quá trình chuẩn bị dữ liệu và phương pháp phát hiện thời gian công đoạn bất thường. Mục 3.1 đến 3.3 mô tả dữ liệu Olist, cách rà soát, chuẩn hóa và phân tích khám phá dẫn đến lựa chọn ngành hàng. Mục 3.4 và 3.5 trình bày cách xây dựng nhật ký sự kiện, đặc trưng và phân chia dữ liệu theo thời gian. Mục 3.6 đến 3.8 trình bày nhóm tham chiếu, cách tính điểm cảnh báo và quy ước diễn giải kết quả.

## 3.1. Nguồn dữ liệu Olist và phạm vi thực nghiệm

### 3.1.1. Các bảng dữ liệu

Bộ dữ liệu công khai của Olist ghi nhận đơn hàng trên sàn thương mại điện tử Brazil, với thời điểm đặt hàng từ tháng 9/2016 đến tháng 10/2018 (Olist, 2018). Dữ liệu gồm chín bảng; Bảng 3.1 trình bày số dòng trước và sau chuẩn hóa.

**Bảng 3.1.** Các bảng dữ liệu Olist và số dòng trước, sau chuẩn hóa

| Bảng | Nội dung | Số dòng gốc | Số dòng sau chuẩn hóa | Ô còn thiếu |
| --- | --- | ---: | ---: | ---: |
| orders | Đơn hàng, trạng thái và các mốc thời gian | 99.441 | 99.441 | 4.908 |
| order_items | Dòng sản phẩm của đơn, người bán, giá, phí vận chuyển | 112.650 | 112.650 | 0 |
| order_payments | Các bản ghi thanh toán | 103.886 | 103.886 | 0 |
| order_reviews | Đánh giá của khách hàng | 99.224 | 99.224 | 145.903 |
| customers | Khách hàng và mã vùng (ZIP) | 99.441 | 99.441 | 0 |
| sellers | Người bán và mã vùng | 3.095 | 3.095 | 0 |
| products | Sản phẩm, ngành hàng, kích thước, khối lượng | 32.951 | 32.951 | 2.448 |
| geolocation | Tọa độ theo tiền tố ZIP | 1.000.163 | 738.332 | 0 |
| translation | Tên ngành hàng tiếng Bồ Đào Nha và tiếng Anh | 71 | 71 | 0 |

Nguồn: Tác giả tổng hợp từ hồ sơ đối chiếu của bản dữ liệu chuẩn hóa.

Phần lớn ô thiếu trong bảng đánh giá là tiêu đề và nội dung bình luận không bắt buộc, nên không được xem là lỗi dữ liệu. Bảng tọa độ là bảng duy nhất có dòng bị loại: 261.831 dòng trùng hoàn toàn sau khi chuẩn hóa.

### 3.1.2. Đơn vị quan sát và quan hệ giữa các bảng

Đơn vị tổ chức dữ liệu là đơn hàng (order_id); đơn vị chấm điểm thời gian là từng công đoạn của đơn hàng. Hình 3.1 trình bày quan hệ giữa các bảng phục vụ phân tích. Các quan hệ cần lưu ý gồm:

- Mỗi customer_id chỉ gắn với một đơn hàng. Mã customer_unique_id mới nhận diện một khách hàng qua nhiều đơn, với tối đa 17 đơn cho một khách trong dữ liệu.
- Một đơn có thể có nhiều dòng sản phẩm, nhiều bản ghi thanh toán (tối đa 29) và nhiều đánh giá (tối đa 3). Ngược lại, một mã đánh giá có thể gắn với nhiều đơn.
- Bảng tọa độ có nhiều quan sát cho cùng một tiền tố ZIP, nên không được ghép trực tiếp vào đơn hàng mà phải qua một bước tham chiếu địa lý có kiểm tra chất lượng.

Do các quan hệ một–nhiều, thao tác ghép bảng có thể nhân bản số đơn nếu không tổng hợp đúng cấp trước khi ghép. Các bảng con được tổng hợp về cấp đơn hàng trước khi tính đặc trưng.

> [Vị trí chèn Hình 3.1]

**Hình 3.1.** Quan hệ giữa các bảng dữ liệu Olist

Nguồn: Tác giả xây dựng từ cấu trúc dữ liệu Olist (Olist, 2018).

## 3.2. Rà soát, làm sạch và chuẩn hóa dữ liệu

### 3.2.1. Nguyên tắc chuẩn hóa

Quá trình chuẩn hóa tuân theo một chính sách cố định, áp dụng giống nhau cho mọi bảng:

- Ô trống được giữ là giá trị thiếu, không chuyển thành 0.
- Các trường không phải văn bản tự do được cắt khoảng trắng và chuẩn hóa Unicode (NFC). Mã định danh, ngành hàng và thành phố được viết thường; mã bang viết hoa; tiền tố ZIP được lưu thành chuỗi năm ký tự.
- Nội dung đánh giá của khách hàng được giữ nguyên văn.
- Thời gian phải đúng định dạng YYYY-MM-DD HH:MM:SS và được đọc theo giờ địa phương của nguồn, không chuyển múi giờ.
- Trường số được giữ nguyên giá trị, không điền giá trị thiếu, không cắt ngưỡng.

Bản dữ liệu chuẩn hóa giữ toàn bộ đơn hàng, dòng sản phẩm, thanh toán, đánh giá, khách hàng, người bán và sản phẩm. Mỗi thao tác được ghi vào hồ sơ đối chiếu, gồm vị trí bản ghi nguồn, lý do và hành động. Mã băm của dữ liệu gốc, kết quả và mã nguồn được lưu trong tệp manifest để có thể tái lập.

### 3.2.2. Các vấn đề chất lượng còn lại

Bảng 3.2 trình bày các vấn đề chất lượng chính của bảng đơn hàng. Các vấn đề này được ghi nhận và gắn cờ, không tự sửa. Một đơn có thể có nhiều vấn đề cùng lúc, nên số lượng giữa các dòng có thể chồng lấn.

**Bảng 3.2.** Các vấn đề chất lượng chính của bảng đơn hàng

| Vấn đề | Số mục ghi nhận | Cách xử lý |
| --- | ---: | --- |
| Thiếu ít nhất một mốc thời gian | 2.980 đơn | Giữ đơn; không tạo sự kiện cho mốc thiếu |
| Mốc ghi nhận đảo thứ tự | 1.382 đơn | Giữ nguyên; không dùng để tính thời lượng công đoạn liên quan |
| Đã giao nhưng thiếu thời điểm giao | 8 đơn | Gắn cờ mâu thuẫn trạng thái |
| Chưa giao nhưng có thời điểm giao | 6 đơn | Gắn cờ mâu thuẫn trạng thái |
| Không có bản ghi con (dòng sản phẩm, thanh toán) | 1.544 mục | Giữ đơn; không đủ điều kiện cho phân tích cần bản ghi con |

Nguồn: Tác giả tổng hợp từ hồ sơ chất lượng của bản dữ liệu chuẩn hóa.

Vì vậy, khóa luận dùng cách diễn đạt "dữ liệu đã chuẩn hóa cấu trúc và có truy vết", không gọi là dữ liệu sạch tuyệt đối về nghiệp vụ. Mỗi phân tích áp dụng điều kiện sử dụng riêng. Chẳng hạn, phép tính thời lượng công đoạn yêu cầu đơn đã giao, có đủ bốn mốc và các mốc không giảm theo thời gian. Đơn không đạt điều kiện vẫn có trong dữ liệu và được báo cáo trong mẫu số với lý do cụ thể.

### 3.2.3. Xử lý đơn có mốc đảo thứ tự

Một đơn có mốc đảo thứ tự, ví dụ mốc bàn giao vận chuyển sớm hơn mốc duyệt đơn, vừa là một dấu hiệu bất thường về trình tự, vừa có thể là lỗi ghi nhận dữ liệu. Nguồn không cung cấp đủ thông tin để phân biệt hai khả năng này (Bose et al., 2013). Vì vậy, khóa luận áp dụng ba quy tắc:

- **Giữ đơn và gắn cờ.** Đơn không bị loại khỏi dữ liệu. Cờ đảo thứ tự được báo cáo như một nhóm riêng là dấu hiệu bất thường trình tự hoặc chất lượng dữ liệu cần kiểm tra. Nhóm này không được tính là kết quả phát hiện đúng hay sai, vì không có nhãn xác nhận.
- **Vô hiệu hóa cục bộ.** Chỉ các khoảng thời gian dùng tới ít nhất một mốc thuộc cặp bị đảo mới bị đặt là giá trị thiếu. Các khoảng còn lại, kể cả tổng thời gian từ đặt hàng đến giao khách nếu hai mốc đầu, cuối hợp lệ, vẫn được giữ.
- **Không để khoảng âm thành chặng chậm.** Thời lượng âm là bằng chứng mâu thuẫn, không phải thời gian xử lý kéo dài. Nó không được đưa vào xây dựng tham chiếu, huấn luyện mô hình đối chứng hay tính điểm.

Trên dữ liệu thực nghiệm, mọi đơn có mốc đảo thứ tự đều có cặp mốc duyệt đơn và bàn giao vận chuyển bị đảo: 79 đơn trên tập test và 51 đơn trên tập validation. Vì cả ba công đoạn đều dùng ít nhất một trong hai mốc này, quy tắc vô hiệu hóa cục bộ cho kết quả giống với việc tạm dừng đánh giá mọi công đoạn của đơn. Tổng thời gian vẫn hợp lệ ở 63/79 đơn test, nhưng bộ phát hiện của khóa luận đánh giá theo công đoạn nên không sử dụng đại lượng này.

## 3.3. Phân tích khám phá dữ liệu

### 3.3.1. Tiêu chí lựa chọn ngành hàng

Mỗi ngành hàng có đặc điểm sản phẩm, người bán và tuyến giao khác nhau. Gộp nhiều ngành vào một phân tích có thể làm nhóm tham chiếu không đồng nhất. Vì vậy, khóa luận chọn một ngành hàng chính cho thực nghiệm. Các tiêu chí được khai báo trước khi xem xét khác biệt về thời lượng:

- Có ít nhất 2.000 đơn chỉ chứa sản phẩm của một ngành đã biết.
- Có ít nhất 12 tháng, mỗi tháng tối thiểu 30 đơn.
- Giai đoạn so sánh chung là chuỗi tháng liên tục dài nhất mà mọi ngành ứng viên đều đạt ngưỡng tháng: từ 03/2017 đến 08/2018.

Đây là các lựa chọn vận hành để bảo đảm đủ dữ liệu, không phải yêu cầu thống kê phổ quát. Có 16 ngành đạt tiêu chí. Bảng 3.3 trình bày sáu ngành có quy mô lớn nhất trong giai đoạn so sánh.

**Bảng 3.3.** Sáu ngành hàng lớn nhất trong giai đoạn 03/2017–08/2018

| Ngành hàng (tên tiếng Anh) | Đơn một ngành | Đơn đủ điều kiện tính thời gian | Tỷ lệ bị loại | Trung vị tổng thời gian (ngày) | Phân vị 95 (ngày) |
| --- | ---: | ---: | ---: | ---: | ---: |
| bed_bath_table | 9.024 | 8.751 | 3,0% | 10,83 | 29,97 |
| health_beauty | 8.484 | 8.168 | 3,7% | 9,56 | 29,74 |
| sports_leisure | 7.428 | 7.172 | 3,5% | 10,13 | 28,95 |
| computers_accessories | 6.498 | 6.265 | 3,6% | 11,17 | 29,45 |
| furniture_decor | 5.844 | 5.652 | 3,3% | 10,96 | 29,91 |
| housewares | 5.672 | 5.436 | 4,2% | 8,98 | 26,17 |

Nguồn: Tác giả tổng hợp từ dữ liệu Olist.

### 3.3.2. Lý do chọn bed_bath_table

Ngành bed_bath_table (tên gốc cama_mesa_banho) được chọn vì ba lý do. Thứ nhất, đây là ngành có số đơn lớn nhất, cả khi tính theo đơn chứa sản phẩm của ngành (9.417 đơn) và theo đơn chỉ chứa sản phẩm của ngành trong giai đoạn so sánh (9.024 đơn). Quy mô này giúp nhiều nhóm tham chiếu đạt số mẫu tối thiểu. Thứ hai, tỷ lệ đơn bị loại khỏi phép tính thời gian thấp (3,0%), cho thấy dữ liệu mốc thời gian của ngành tương đối đầy đủ. Thứ ba, ngành có độ phân tán thời gian giao tương đương các ngành lớn khác, nên không phải một trường hợp cực đoan.

Việc lựa chọn ngành hàng dựa trên khám phá toàn bộ lịch sử dữ liệu, bao gồm cả giai đoạn sau này được dùng làm tập kiểm tra. Đây là giới hạn được công bố: tập test không phải một tập hoàn toàn chưa từng được xem xét.

Trong 9.417 đơn có sản phẩm thuộc ngành, 196 đơn chứa thêm sản phẩm ngành khác, 8 đơn có sản phẩm chưa rõ ngành và 189 đơn nằm ngoài giai đoạn nghiên cứu. Các đơn này được lưu trong một bảng theo dõi riêng, không bị xóa khỏi nguồn. Tập dữ liệu chính gồm 9.024 đơn.

### 3.3.3. Phân bố thời gian và địa lý

Thời lượng công đoạn có phân phối lệch phải mạnh. Trên các đơn phát triển của ngành, khoảng cách đường thẳng lớn nhất giữa người bán và khách hàng có trung vị khoảng 393 km, phân vị 90 khoảng 952 km và phân vị 99 khoảng 2.290 km. Khoảng cách này được tính từ tọa độ đại diện của tiền tố ZIP, nên chỉ là xấp xỉ đường thẳng, không phải quãng đường xe chạy thực tế. Sự phân tán lớn về khoảng cách và khu vực là lý do khóa luận so sánh thời gian vận chuyển theo bối cảnh thay vì dùng một ngưỡng chung.

## 3.4. Xây dựng nhật ký sự kiện và các đặc trưng

### 3.4.1. Nhật ký sự kiện và ba công đoạn

Nhật ký sự kiện được sinh từ các mốc thực sự có trong nguồn: 392.856 sự kiện cho toàn bộ 99.441 đơn. Mã đơn hàng đóng vai trò mã thể hiện quy trình. Mốc bị thiếu không sinh sự kiện; mốc đảo thứ tự và thời lượng bằng 0 được giữ nguyên. Quy trình tham chiếu gồm bốn hoạt động: đặt hàng, duyệt đơn, bàn giao vận chuyển và giao khách. Từ bốn mốc này, khóa luận tính ba công đoạn như Hình 3.2.

> [Vị trí chèn Hình 3.2]

**Hình 3.2.** Bốn mốc sự kiện và ba công đoạn của đơn hàng

Nguồn: Tác giả xây dựng.

Ngày giao dự kiến là một trường riêng, không phải sự kiện trong chuỗi công đoạn. Trường này chỉ dùng để đối chiếu kết quả giao hàng. Khoảng duyệt đơn đến bàn giao không được diễn giải là toàn bộ thời gian đóng gói, vì nguồn không ghi các mốc trung gian.

Khóa luận đã dùng thư viện PM4Py để khám phá biến thể quy trình và kiểm tra sự phù hợp với quy trình tham chiếu. Trong 7.333 đơn phát triển của ngành, sau khi giới hạn sự kiện theo mốc cắt thời gian, có 6.542 vết đầy đủ với các thời điểm phân biệt được. Có 678 vết chưa đủ sự kiện quan sát và 113 vết có các mốc trùng thời điểm. Các số liệu này mô tả bằng chứng quy trình, không phải số đơn có sự cố.

### 3.4.2. Các nhóm đặc trưng

Theo góp ý của giảng viên hướng dẫn, khóa luận xây dựng sáu nhóm đặc trưng như Bảng 3.4.

**Bảng 3.4.** Các nhóm đặc trưng và giới hạn sử dụng

| Nhóm | Đặc trưng chính | Giới hạn |
| --- | --- | --- |
| Thời gian | Ba thời lượng công đoạn | Chỉ tính khi đủ mốc và đúng thứ tự |
| Cấu trúc đơn | Số dòng sản phẩm, số sản phẩm, số người bán | Đơn nhiều người bán không quy được cho một người bán |
| Giá trị và thanh toán | Tổng thanh toán, giá hàng, phí vận chuyển, chênh lệch thanh toán với giá hàng và phí, loại thanh toán, số kỳ trả góp | Giá trị lớn không tự chứng minh sai sót hay gian lận |
| Lịch sử khách hàng | Số đơn trước đó, khoảng cách giữa các lần mua | Lịch sử lặp lại ít |
| Lịch sử người bán | Số đơn trước đó, tỷ lệ giao sau ngày dự kiến, đánh giá trước thời điểm đơn | Chỉ dùng lịch sử trước thời điểm đơn đang xét |
| Địa lý | Bang khách hàng, khoảng cách người bán–khách hàng, dải khoảng cách | Xấp xỉ đường thẳng; có kiểm tra chất lượng tọa độ |

Nguồn: Tác giả tổng hợp.

### 3.4.3. Đặc trưng hồi cứu và thông tin có sẵn tại thời điểm đánh giá

Một đặc trưng có trong dữ liệu chưa chắc đã có sẵn tại thời điểm cần cảnh báo. Ví dụ, đánh giá của khách hàng chỉ xuất hiện sau khi giao hàng, nên không thể dùng để cảnh báo một công đoạn trước đó. Khóa luận phân biệt hai loại:

- **Đặc trưng hồi cứu:** chỉ dùng cho phân tích sau khi đơn đã hoàn tất, như đánh giá và nội dung phàn nàn.
- **Đặc trưng có thể dùng tại thời điểm đánh giá:** thông tin đơn hàng, sản phẩm, người bán, địa lý và lịch sử trước thời điểm đó.

Bộ dữ liệu không ghi lại thời điểm thông tin sản phẩm, người bán và địa lý được cập nhật. Vì vậy, việc dùng các thông tin tĩnh này khi phát lại sự kiện dựa trên giả định chúng có sẵn từ lúc đặt hàng. Giả định này được ghi rõ ở mọi kết quả liên quan (xem mục 4.3).

Đặc trưng địa lý được xây dựng như sau. Mỗi tiền tố ZIP của khách hàng và người bán được gán một tọa độ đại diện, chọn từ chính các quan sát có trong nguồn. Khoảng cách được tính từ các tọa độ đại diện này và qua kiểm tra chất lượng. Đơn không đạt kiểm tra được giữ trạng thái không đủ điều kiện, không tự điền khoảng cách. Khoảng cách được chia thành các dải 0–50, 50–200, 200–500, 500–1.000, 1.000–2.000 và trên 2.000 km để dùng làm điều kiện bối cảnh.

## 3.5. Phân chia dữ liệu và kiểm soát rò rỉ thông tin

### 3.5.1. Chia theo thời gian

Tập dữ liệu chính được chia theo thời điểm đặt hàng, không xáo trộn ngẫu nhiên, như Bảng 3.5 và Hình 3.3. Mốc bắt đầu được tính và mốc kết thúc không được tính vào mỗi tập.

**Bảng 3.5.** Phân chia dữ liệu theo thời gian và số đơn dùng được cho phép tính thời gian

| Tập | Thời gian đặt hàng | Số đơn | Đủ bốn mốc trong dữ liệu | Hoàn tất sau mốc cắt | Đầu vào tính thời gian | Vai trò |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| Train | 03/2017 – 02/2018 | 5.480 | 5.385 | 307 | 5.078 | Xây dựng tham chiếu, huấn luyện mô hình đối chứng |
| Validation | 03/2018 – 05/2018 | 1.853 | 1.771 | 248 | 1.523 | So sánh phương pháp, lựa chọn cấu hình; phát lại sự kiện |
| Test | 06/2018 – 08/2018 | 1.691 | 1.595 | — | 1.595 | Đánh giá ứng viên đã cố định |

Nguồn: Tác giả tổng hợp từ dữ liệu Olist.

> [Vị trí chèn Hình 3.3]

**Hình 3.3.** Quy trình chuẩn bị dữ liệu và phân chia tập

Nguồn: Tác giả xây dựng.

### 3.5.2. Mốc cắt sự kiện

Mỗi tập train và validation có một mốc cắt sự kiện trùng với ngày bắt đầu của tập kế tiếp. Một đơn train hoàn tất sau mốc cắt vẫn thuộc tập train, nhưng không được đưa vào dữ liệu tính thời gian của train, vì kết quả của nó chưa được biết tại thời điểm cắt. Quy tắc tương tự áp dụng cho validation. Nhờ vậy, kết quả của các đơn hoàn tất muộn không thể ảnh hưởng đến việc xây dựng tham chiếu và lựa chọn phương pháp.

Quy tắc này có một đánh đổi: các đơn kéo dài gần mốc cắt bị thiếu đại diện trong dữ liệu tính thời gian. Số đơn bị loại được công bố trong Bảng 3.5 thay vì được coi là bình thường. Tập test không có mốc cắt riêng, vì nguồn không cung cấp ngày trích xuất dữ liệu; khóa luận không tự giả định một ngày trích xuất.

### 3.5.3. Các biện pháp kiểm soát rò rỉ

Chia theo thời gian không tự động loại bỏ mọi rò rỉ thông tin (Kapoor & Narayanan, 2023). Khóa luận áp dụng thêm các biện pháp sau:

- Tham chiếu, bộ chuẩn hóa, ngưỡng và mô hình đối chứng chỉ được fit trên tập train.
- Nhóm tham chiếu không bao gồm chính đơn đang được đánh giá.
- Lịch sử người bán và khách hàng chỉ tính từ các đơn trước thời điểm của đơn đang xét.
- Cấu hình được chọn trên validation, sau đó cố định trước khi chạy test, và không tinh chỉnh lại sau khi xem kết quả test.

Giới hạn còn lại: thông tin trạng thái, ngành hàng, người bán và khách hàng lấy từ bản dữ liệu cuối cùng, nên không chứng minh được chính xác những gì đã biết tại mỗi thời điểm trong một hệ thống vận hành thực. Người mua và người bán có thể xuất hiện ở nhiều giai đoạn, nên đây là phép chia theo thời gian ở cấp đơn hàng, không phải kiểm tra khả năng khái quát cho khách hàng hoặc người bán mới.

## 3.6. Xây dựng nhóm tham chiếu theo sản phẩm và bối cảnh

### 3.6.1. Thứ tự chọn nhóm

Với mỗi công đoạn của một đơn, nhóm tham chiếu được chọn theo thứ tự ưu tiên trong Bảng 3.6. Phương pháp dừng ở cấp đầu tiên đạt ít nhất 30 đơn độc lập theo mã đơn hàng.

**Bảng 3.6.** Thứ tự chọn nhóm tham chiếu

| Cấp | Điều kiện ghép nhóm | Phạm vi áp dụng |
| --- | --- | --- |
| 1 | Cùng sản phẩm, người bán, số dòng sản phẩm, bang khách hàng và dải khoảng cách | Đơn một sản phẩm, một người bán |
| 2 | Cùng sản phẩm, người bán, số dòng sản phẩm và dải khoảng cách | Đơn một sản phẩm, một người bán |
| 3 | Cùng sản phẩm, số dòng sản phẩm, bang khách hàng và dải khoảng cách | Đơn một sản phẩm, một người bán |
| 4 | Cùng sản phẩm, số dòng sản phẩm và dải khoảng cách | Đơn một sản phẩm, một người bán |
| 5 | Nhóm dự phòng cấp ngành có bối cảnh: bang khách hàng và dải khoảng cách đã kiểm tra chất lượng | Khi các cấp trên không đủ mẫu |
| — | Không có nhóm nào đạt điều kiện | Công đoạn ghi nhận là chưa đánh giá được |

Nguồn: Tác giả xây dựng.

### 3.6.2. Các quy tắc đi kèm

- **Một đơn là một quan sát.** Một đơn mua ba sản phẩm cùng mã không được tính thành ba lần thực hiện độc lập.
- **Đơn nhiều sản phẩm hoặc nhiều người bán** không được quy thời gian của cả đơn cho từng sản phẩm. Các đơn này chỉ dùng nhóm dự phòng cấp ngành, với trạng thái không quy được cho một sản phẩm cụ thể.
- **Ngưỡng 30 đơn** là quy ước hỗ trợ tối thiểu, không chứng minh độ chính xác thống kê. Với nhóm dưới 100 đơn, điểm có bước nhảy lớn (bước danh nghĩa 2.000/n điểm) và được gắn cờ độ phân giải đuôi hạn chế.
- **Công khai điều kiện đã bỏ.** Khi chuyển xuống cấp thấp hơn, các điều kiện đã bỏ được ghi lại. Chẳng hạn, khi bỏ điều kiện người bán, kết quả không được diễn giải là đã kiểm soát hiệu ứng người bán.

Trên tập test, trong 5.073 dòng công đoạn chỉ có 60 dòng (1,2%) dùng được nhóm theo sản phẩm. Có 4.509 dòng (88,9%) dùng nhóm dự phòng cấp ngành và 504 dòng (9,9%) chưa đánh giá được. Vì vậy, khóa luận không khẳng định đã xây dựng được tham chiếu riêng đáng tin cậy cho từng sản phẩm. Phần lớn đánh giá thực tế dựa trên nhóm ngành có xét bối cảnh.

## 3.7. Tính điểm và xác định cảnh báo công đoạn

### 3.7.1. Chênh lệch và điểm cảnh báo

Với thời lượng thực tế d của một công đoạn và nhóm tham chiếu đã chọn gồm n đơn có trung bình μ, khóa luận lưu riêng hai đại lượng.

Chênh lệch so với trung bình tham chiếu, tính bằng ngày:

$$ Δ = d − μ || (3.1)

Chênh lệch âm nghĩa là nhanh hơn tham chiếu. Chậm hơn trung bình không tự động là bất thường.

Điểm cảnh báo trên thang 0–100 dựa trên hàm phân phối thực nghiệm F(d), là tỷ lệ đơn trong nhóm tham chiếu có thời lượng không lớn hơn d:

$$ điểm = 100 × min(1, max(0, (F(d) − 0,95) / 0,05)) || (3.2)

Công thức (3.2) chỉ áp dụng khi d > μ và d > 0; nếu d ≤ μ hoặc d ≤ 0, điểm bằng 0. Theo công thức này, điểm chỉ lớn hơn 0 khi thời lượng vừa lớn hơn trung bình tham chiếu, vừa nằm trong 5% phần đuôi trên của nhóm. Khi thiếu mốc, thiếu tham chiếu hoặc không đạt điều kiện chất lượng, điểm để trống (giá trị thiếu), không gán bằng 0. Phương pháp này được khóa luận gọi là ứng viên product_context_stage_score_gt0.

### 3.7.2. Mức cảnh báo và điểm cấp đơn

Một công đoạn có cảnh báo khi điểm lớn hơn 0. Mức cảnh báo được chia như Bảng 3.7.

**Bảng 3.7.** Các mức cảnh báo theo điểm

| Khoảng điểm | Mức | Tương ứng F(d) khi d > μ |
| --- | --- | --- |
| 0 | Không cảnh báo | F(d) ≤ 0,95 |
| (0; 50] | Nhẹ | 0,95 < F(d) ≤ 0,975 |
| (50; 80] | Vừa | 0,975 < F(d) ≤ 0,99 |
| (80; 100] | Cao | F(d) > 0,99 |
| Trống | Chưa đủ dữ liệu | — |

Nguồn: Tác giả xây dựng.

Các mốc 50 và 80 là lựa chọn hiển thị, không phải ngưỡng tối ưu hay xác suất. Điểm cấp đơn lấy điểm cao nhất trong các công đoạn đã chấm, không lấy trung bình, để không làm mờ một công đoạn kéo dài. Điểm cấp đơn luôn đi kèm số công đoạn chấm được. Hình 3.4 tóm tắt luồng chọn nhóm tham chiếu và tính điểm.

> [Vị trí chèn Hình 3.4]

**Hình 3.4.** Luồng chọn nhóm tham chiếu và tính điểm cảnh báo công đoạn

Nguồn: Tác giả xây dựng.

### 3.7.3. Các phương pháp đối chứng

Để đánh giá phương pháp chính, khóa luận so sánh với các phương pháp đã trình bày ở mục 2.4:

- Quy tắc thống kê đơn biến: IQR với k ∈ {1,5; 2; 3} và điểm z hiệu chỉnh theo MAD với ngưỡng ∈ {3; 3,5; 4,5}, trên thời lượng gốc và log(1 + d); ngưỡng phân vị trên ∈ {0,95; 0,975; 0,99; 0,995}.
- Mô hình học máy: Isolation Forest, LOF và One-Class SVM. LOF và One-Class SVM dùng biến đổi log(1 + d) và bộ chuẩn hóa RobustScaler, fit trên tập train.

Lưới cấu hình được khai báo trước khi đánh giá. Đây không phải mọi cấu hình có thể có, nên kết quả không chứng minh được cấu hình tối ưu toàn cục. Kết quả so sánh được trình bày ở Chương 5.

## 3.8. Quy ước diễn giải kết quả

Để tránh diễn giải quá mức, khóa luận áp dụng các quy ước sau cho mọi kết quả.

- **Tách ba nội dung.** Đáp ứng ngày hẹn, hiệu suất công đoạn và trạng thái thiếu bằng chứng là ba chiều thông tin riêng. Giao trễ hẹn không đồng nghĩa với tắc nghẽn; thiếu dữ liệu không đồng nghĩa với bình thường.
- **Giữ cảnh báo khi giao sớm.** Một đơn giao trước ngày hẹn vẫn giữ cảnh báo công đoạn nếu có. Ngược lại, không ép phải tìm ra một công đoạn nhanh nếu số liệu không thể hiện điều đó.
- **Cơ sở ngày hẹn chưa xác minh.** Trạng thái giao sớm, đúng hoặc trễ hẹn được tính từ ngày giao thực tế so với ngày giao dự kiến gốc. Khóa luận không tính lại ngày hẹn và không suy diễn ngày hẹn được thiết lập ngẫu nhiên.
- **Gọi đúng tên kết luận.** Kết quả là "công đoạn kéo dài cần kiểm tra", không phải nguyên nhân tắc nghẽn đã chứng minh, không quy trách nhiệm cho người bán hay đơn vị vận chuyển.
- **Báo mẫu số.** Mọi tỷ lệ đều kèm số đơn đánh giá được và số đơn bị loại cùng lý do.

Tóm lại, Chương 3 đã trình bày dữ liệu, quy trình chuẩn bị dữ liệu và phương pháp phát hiện thời gian công đoạn bất thường dựa trên nhóm tham chiếu theo sản phẩm và bối cảnh. Chương 4 trình bày thiết kế hệ thống, gồm phát lại sự kiện và hệ ba tác tử giải thích cảnh báo.

@@REFS

Bose, R. P. J. C., Mans, R. S., & van der Aalst, W. M. P. (2013). Wanna improve process mining results? It's high time we consider data quality issues seriously. In *2013 IEEE Symposium on Computational Intelligence and Data Mining (CIDM)* (pp. 127–134). https://doi.org/10.1109/CIDM.2013.6597227

Kapoor, S., & Narayanan, A. (2023). Leakage and the reproducibility crisis in machine-learning-based science. *Patterns, 4*(9), Article 100804. https://doi.org/10.1016/j.patter.2023.100804

Olist. (2018). *Brazilian e-commerce public dataset by Olist* [Data set]. Kaggle. https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce

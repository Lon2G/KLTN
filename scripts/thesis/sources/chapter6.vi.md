# CHƯƠNG 6. KẾT LUẬN VÀ HƯỚNG PHÁT TRIỂN

## 6.1. Tổng kết kết quả đạt được theo mục tiêu nghiên cứu

Khóa luận hướng tới xây dựng và đánh giá một khung phân tích có nhận biết quy trình, tích hợp Multi-Agent AI. Khung phân tích nhằm phát hiện các công đoạn có thời gian xử lý bất thường và hỗ trợ giải thích các dấu hiệu liên quan đến điểm nghẽn tiềm năng trong quy trình hoàn tất đơn hàng thương mại điện tử. Bảng 6.1 đối chiếu năm mục tiêu cụ thể ở mục 1.3.2 với kết quả đạt được.

**Bảng 6.1.** Đối chiếu mục tiêu cụ thể với kết quả đạt được

| Mục tiêu | Kết quả đạt được | Mức độ hoàn thành |
| --- | --- | --- |
| 1. Xây dựng nhật ký sự kiện có truy vết từ dữ liệu Olist | Chuẩn hóa 9 bảng, giữ toàn bộ 99.441 đơn; sinh 392.856 sự kiện từ các mốc có thật; hồ sơ đối chiếu và mã băm cho mọi bước | Hoàn thành |
| 2. Xây dựng và đánh giá phương pháp phát hiện theo nhóm tham chiếu | Nhóm tham chiếu 5 cấp, điểm 0–100; so sánh 22 phương pháp, 121 cấu hình; chọn theo thời gian và đánh giá trên test | Hoàn thành; kết quả suy giảm trên test |
| 3. Phát lại sự kiện và chấm công đoạn gần thời gian thực | Phát lại 7.076 mốc của 1.853 đơn; trung vị 13,9 ms mỗi sự kiện; khớp tuyệt đối với chấm theo lô | Hoàn thành trong phạm vi phát lại lịch sử |
| 4. Triển khai hệ ba tác tử có kiểm chứng và quản lý chi phí | Ba tác tử với công cụ riêng, cổng kiểm tra bằng chương trình, vòng sửa có giới hạn, sổ ngân sách và biên nhận | Hoàn thành ở mức nguyên mẫu |
| 5. Đánh giá lớp giải thích bằng thực nghiệm đối chứng | Đối chứng quy tắc, một tác tử và ba tác tử trên 20 case, hai cấu hình, hai phiên bản giới hạn token; thiết kế chấm định tính và Kappa | Hoàn thành phần tiêu chí tự động; phần chấm định tính đang thực hiện |

Nguồn: Tác giả tổng hợp.

Về câu hỏi nghiên cứu thứ nhất, phương pháp tham chiếu theo sản phẩm và bối cảnh đạt F1 cao nhất ở ngưỡng cố định trên validation (0,678). Phương pháp được chọn theo tiêu chí F1 theo tháng. Trên tập test, kết quả suy giảm rõ (F1 0,210; ROC AUC 0,637), trong bối cảnh tỷ lệ đơn trễ hẹn giảm từ 10,6% xuống 2,3%. Phương pháp đánh giá được 90,1% số đơn test và ghi rõ lý do cho phần còn lại. Ưu điểm nổi bật là khả năng diễn giải và khả năng chỉ ra công đoạn kéo dài ngay cả ở đơn giao sớm: 58 đơn như vậy trên tập test. Do biến đích đối chiếu là giao trễ hẹn, kết quả không đo được độ chính xác phát hiện tắc nghẽn.

Về câu hỏi nghiên cứu thứ hai, khi đủ tài nguyên, hệ ba tác tử hoàn thành 20/20 case và nêu đủ cảnh báo ở 8/8 case có cảnh báo. Kết quả này ngang quy tắc cố định và nhỉnh hơn một tác tử, nhưng với chi phí khoảng 3,6 lần. Khi tài nguyên bị giới hạn, hệ ba tác tử kém hơn. Với các tiêu chí tự động, khóa luận chưa chứng minh được giá trị bổ sung rõ rệt của việc phối hợp nhiều tác tử. Đánh giá chất lượng giải thích bằng chấm định tính độc lập là bước cần hoàn tất để trả lời đầy đủ câu hỏi này.

Về câu hỏi nghiên cứu thứ ba, khung phân tích cập nhật trạng thái và chấm công đoạn ngay khi công đoạn hoàn tất, với độ trễ xử lý thấp. Có 95 đơn được cảnh báo trước khi giao hàng. Giới hạn chính là kết quả phụ thuộc vào giả định thông tin bối cảnh có sẵn từ lúc đặt hàng. Ngoài ra, lớp AI mất khoảng 17–19 giây mỗi case và chưa được tích hợp vào mọi sự kiện.

## 6.2. Đóng góp và khả năng ứng dụng trong doanh nghiệp

### 6.2.1. Đóng góp về phương pháp

- **Đánh giá thời gian ở cấp công đoạn theo bối cảnh.** Mỗi công đoạn được so với nhóm đơn tương đồng. Điều kiện đủ mẫu, quy tắc chuyển nhóm và độ bao phủ thực tế đều được công bố, kể cả khi phần lớn đánh giá phải dùng nhóm dự phòng.
- **Quy ước diễn giải ba chiều.** Đáp ứng ngày hẹn, hiệu suất công đoạn và trạng thái thiếu bằng chứng được tách riêng. Nhờ vậy, giao trễ hẹn không bị đồng nhất với tắc nghẽn, và thiếu dữ liệu không bị coi là bình thường.
- **Quy tắc xử lý chất lượng dữ liệu có lý lẽ.** Đơn có mốc đảo thứ tự được giữ và gắn cờ. Chỉ các khoảng thời gian dính mốc nghi ngờ bị vô hiệu hóa, và khoảng âm không bao giờ bị coi là chặng chậm.

### 6.2.2. Đóng góp về hệ thống

- **Nguyên mẫu ràng buộc đầu ra AI vào bằng chứng.** Tác tử chỉ chọn mục từ danh mục có nguồn, số liệu được chép từ bản ghi bằng chứng. Mọi mục được kiểm tra bằng chương trình và bởi một tác tử kiểm chứng trước khi công bố.
- **Cơ chế quản lý tài nguyên và tái lập.** Sổ ngân sách giữ chỗ theo chi phí xấu nhất và không gửi lại yêu cầu chưa rõ kết quả. Biên nhận của mọi yêu cầu được lưu, nên mọi đợt chạy có thể phát lại offline.
- **Giao diện BI local.** Giao diện đi từ tổng quan đến bằng chứng của từng đơn, với mẫu số và trạng thái chưa đánh giá được hiển thị ở mọi cấp.

### 6.2.3. Đóng góp về thực nghiệm

Khóa luận cung cấp kết quả trên dữ liệu thực tế, bao gồm cả các kết quả không thuận lợi: mức suy giảm từ validation sang test, các lượt tác tử bị dừng do giới hạn token, các lỗi dùng công cụ của mô hình và các lượt bỏ sót cảnh báo khi hết lượt gọi. Những kết quả này cho thấy giới hạn thực tế của việc áp dụng hệ đa tác tử. Chẳng hạn, giới hạn token và cách phân bổ lượt gọi ảnh hưởng trực tiếp đến độ hoàn thành, và hệ đa tác tử không tự động tốt hơn một quy tắc cố định được thiết kế cẩn thận.

### 6.2.4. Khả năng ứng dụng

Trong doanh nghiệp thương mại điện tử, khung phân tích có thể được áp dụng theo ba mức:

- **Theo dõi:** dùng điểm công đoạn và giao diện BI để thấy công đoạn, khu vực và người bán có tỷ lệ cảnh báo cao, kèm mẫu số. Mức này không cần mô hình ngôn ngữ.
- **Ưu tiên kiểm tra:** dùng danh sách đơn có cảnh báo, kể cả đơn giao sớm, để chọn đơn cần xem xét trước khi khách hàng phản ánh.
- **Hỗ trợ giải thích:** chỉ gọi lớp AI cho các đơn có cảnh báo, để tổng hợp bằng chứng, giới hạn và đề xuất kiểm tra. Mỗi case tốn khoảng 0,003 USD và 18 giây với cấu hình hiện tại.

Khi triển khai, doanh nghiệp cần xác định ưu tiên giữa cảnh báo nhầm và bỏ sót (mục 5.3.1). Doanh nghiệp cũng cần dùng dữ liệu có thời điểm cập nhật của thông tin bối cảnh, và xây dựng lại nhóm tham chiếu trên dữ liệu của mình.

## 6.3. Hạn chế của nghiên cứu

- **Không có nhãn nguyên nhân tắc nghẽn.** Phần phát hiện dùng trạng thái giao trễ hẹn làm biến đích gián tiếp. Khóa luận không đo được độ chính xác phát hiện tắc nghẽn và không xác nhận được nguyên nhân của các cảnh báo.
- **Cơ sở ngày hẹn và thời điểm có sẵn của thông tin chưa được xác minh.** Kết quả phát lại sự kiện phụ thuộc vào giả định thông tin bối cảnh có sẵn từ lúc đặt hàng.
- **Nhóm tham chiếu chủ yếu ở cấp ngành.** Trên tập test, chỉ 1,2% số dòng công đoạn dùng được nhóm theo sản phẩm. Ý tưởng tham chiếu theo sản phẩm chưa được kiểm chứng đầy đủ trên dữ liệu có ít mẫu cho mỗi mã sản phẩm.
- **Kết quả suy giảm trên test** và tập test không hoàn toàn chưa từng được xem xét, do việc chọn ngành hàng dựa trên khám phá toàn bộ lịch sử.
- **Mẫu đánh giá lớp AI nhỏ.** 20 case phát triển, mỗi phương án chạy một lần. Kết quả chưa phản ánh độ biến động giữa các lần chạy của mô hình, và phần chấm định tính chưa có kết quả tại thời điểm hoàn thành bản thảo.
- **Phạm vi dữ liệu hẹp.** Khóa luận chỉ thực nghiệm trên một ngành hàng của một sàn, giai đoạn 2017–2018.
- **Gần thời gian thực ở mức mô phỏng.** Hệ thống chưa cảnh báo được công đoạn đang diễn ra, và chưa được kiểm chứng trong môi trường vận hành với dữ liệu thời gian thực.

## 6.4. Hướng phát triển

Bảng 6.2 trình bày các hướng phát triển, sắp xếp theo mức độ cần thiết để khắc phục các hạn chế ở mục 6.3.

**Bảng 6.2.** Hướng phát triển của nghiên cứu

| Hướng | Vấn đề giải quyết | Điều kiện cần |
| --- | --- | --- |
| Hoàn tất chấm định tính và mở rộng mẫu đánh giá AI | Trả lời đầy đủ RQ2; đo độ biến động giữa các lần chạy | Người chấm độc lập; tập case mới chưa dùng trong phát triển; chạy lặp nhiều lần |
| Xây dựng nhãn kiểm chứng tắc nghẽn | Đo độ chính xác phát hiện đúng đối tượng nghiên cứu | Nhật ký kho, vận chuyển, lý do sự cố; chuyên gia gán nhãn theo tiêu chí độc lập với detector |
| Cảnh báo công đoạn đang diễn ra | Phát hiện sớm, trước khi công đoạn kết thúc | Theo dõi thời gian chờ theo đồng hồ; ngưỡng thời gian chờ theo bối cảnh |
| Triển khai thời gian thực | Kiểm chứng trong môi trường vận hành | Nguồn dữ liệu streaming; thời điểm cập nhật của thông tin bối cảnh; đo độ trễ toàn luồng |
| Giảm chi phí của hệ đa tác tử | Cân bằng chất lượng và chi phí | Chia nhỏ gói kiểm chứng; chỉ gọi AI cho đơn có cảnh báo; so sánh các cách phân bổ lượt gọi |
| Cải thiện nhóm tham chiếu | Tăng tỷ lệ tham chiếu theo sản phẩm | Gộp sản phẩm theo thuộc tính; mô hình phân cấp; dữ liệu nhiều năm hơn |
| Áp dụng cho ngành hàng, sàn khác | Kiểm tra khả năng khái quát | Dữ liệu mới có nguồn gốc rõ; xây dựng và đánh giá riêng |
| Tích hợp phản hồi của người quản lý | Học từ kết quả kiểm tra thực tế | Chức năng ghi nhận kết quả kiểm tra trên giao diện BI |

Nguồn: Tác giả đề xuất.

Hướng ưu tiên trước mắt là hoàn tất chấm định tính và mở rộng mẫu đánh giá lớp AI, vì đây là điều kiện để kết luận về giá trị bổ sung của hệ đa tác tử. Về lâu dài, hướng có ý nghĩa thực tiễn nhất là xây dựng nhãn kiểm chứng tắc nghẽn từ dữ liệu vận hành thật. Khi đó, khung phân tích có thể được đánh giá trực tiếp trên đúng đối tượng mà nó hướng tới.

Tóm lại, khóa luận đã xây dựng và đánh giá một khung phân tích kết hợp phát hiện thời gian công đoạn bất thường theo bối cảnh với lớp giải thích đa tác tử có ràng buộc bằng chứng, trên dữ liệu thương mại điện tử thực tế. Kết quả cho thấy khung phân tích có thể chỉ ra các công đoạn kéo dài cần xem xét, bao gồm cả ở những đơn đáp ứng ngày hẹn, và cung cấp giải thích có nguồn gốc kiểm chứng được. Đồng thời, kết quả cũng cho thấy rõ các giới hạn về dữ liệu, độ ổn định theo thời gian và chi phí. Đây là những điều kiện cần được giải quyết trước khi áp dụng khung phân tích trong môi trường vận hành thực tế.

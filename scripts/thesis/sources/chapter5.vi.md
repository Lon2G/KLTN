# CHƯƠNG 5. THỰC NGHIỆM VÀ ĐÁNH GIÁ

Chương này trình bày thiết kế thực nghiệm và kết quả đánh giá theo ba câu hỏi nghiên cứu. Mục 5.1 mô tả thiết kế thực nghiệm. Mục 5.2 và 5.3 trình bày kết quả so sánh các phương pháp phát hiện, độ ổn định theo thời gian và kết quả trên tập test (RQ1). Mục 5.4 trình bày kết quả phát lại sự kiện (RQ3). Mục 5.5 và 5.6 trình bày đối chứng lớp giải thích (RQ2). Mục 5.7 phân tích các trường hợp điển hình. Mục 5.8 thảo luận kết quả và các yếu tố ảnh hưởng đến độ tin cậy.

## 5.1. Thiết kế thực nghiệm

### 5.1.1. Câu hỏi, dữ liệu và tiêu chí

Bảng 5.1 tóm tắt các thực nghiệm, dữ liệu sử dụng và tiêu chí đánh giá.

**Bảng 5.1.** Tổng hợp thiết kế thực nghiệm

| Câu hỏi | Thực nghiệm | Dữ liệu | Tiêu chí |
| --- | --- | --- | --- |
| RQ1 | So sánh 22 phương pháp, 121 cấu hình cố định | Validation: 1.307 đơn đủ điều kiện, 138 đơn trễ hẹn | ROC AUC, Average Precision, Precision, Recall, F1 |
| RQ1 | Lựa chọn ứng viên theo thời gian, kiểm tra độ nhạy | Validation theo tháng 03–05/2018 | F1 theo tháng, F1 tháng kém nhất, F1 gộp |
| RQ1 | Đánh giá ứng viên đã cố định | Test: 1.523 đơn đủ điều kiện, 35 đơn trễ hẹn | Như trên, kèm độ bao phủ và các trường hợp bất đồng |
| RQ3 | Phát lại sự kiện và chấm công đoạn | 7.076 mốc của 1.853 đơn validation | Độ bao phủ, số cảnh báo, độ trễ xử lý, mức khớp với chấm theo lô |
| RQ2 | Đối chứng quy tắc cố định, một tác tử và ba tác tử | 20 case phát triển, hai cấu hình ngân sách, hai phiên bản giới hạn token | Tỷ lệ hoàn thành, tính đúng nguồn, độ đầy đủ cảnh báo, thời gian, chi phí; chấm định tính |

Nguồn: Tác giả tổng hợp.

Biến đích của phần phát hiện là trạng thái giao sau ngày dự kiến, tính theo ngày lịch. Như đã nêu ở mục 1.5.1 và 2.6.1, đây là biến đích đối chiếu gián tiếp. Các chỉ số phản ánh khả năng phân biệt nhóm giao trễ hẹn của điểm cảnh báo, không phải độ chính xác phát hiện tắc nghẽn.

### 5.1.2. Môi trường và nguyên tắc so sánh

Các thực nghiệm chạy trên máy tính cá nhân, dùng Python với pandas, scikit-learn và SQLite. Lớp giải thích dùng mô hình gpt-6-luna qua Responses API. Khóa luận áp dụng các nguyên tắc sau:

- Lưới cấu hình và quy tắc chọn được khai báo trước khi xem kết quả.
- Phương pháp được chọn trên validation, cố định, rồi đánh giá trên test đúng một lần, không tinh chỉnh lại sau khi xem kết quả test.
- Mọi lượt chạy đều được báo cáo, kể cả lượt thất bại hoặc bị dừng. Không gộp các chỉ số thành một điểm tổng để xếp hạng.
- Mọi tỷ lệ đều kèm mẫu số. Đơn không đủ điều kiện được báo riêng kèm lý do, không bị gán nhãn bình thường.

## 5.2. Kết quả so sánh các phương pháp phát hiện

Trên tập validation, nguồn có 1.739 đơn. Sau khi loại 314 đơn chưa đủ dữ liệu bối cảnh và 118 đơn ngoài phạm vi so sánh của các mô hình cũ, còn 1.307 đơn dùng chung cho mọi phương pháp, trong đó 138 đơn giao trễ hẹn (10,6%). Mỗi phương pháp cho một điểm cấp đơn hàng, được đánh giá trên cùng tập đơn này. Bảng 5.2 trình bày kết quả tốt nhất của từng nhóm phương pháp.

**Bảng 5.2.** Kết quả tốt nhất của từng nhóm phương pháp trên tập validation

| Nhóm phương pháp | Số phương pháp | Số cấu hình | ROC AUC tốt nhất | Average Precision tốt nhất | F1 tốt nhất (ngưỡng cố định) |
| --- | ---: | ---: | ---: | ---: | ---: |
| Phân vị trên | 4 | 4 | 0,920 | 0,742 | 0,639 |
| Isolation Forest | 4 | 32 | 0,904 | 0,507 | 0,559 |
| Tham chiếu theo sản phẩm và bối cảnh | 1 | 1 | 0,850 | 0,528 | 0,678 |
| LOF | 3 | 24 | 0,838 | 0,332 | 0,440 |
| One-Class SVM | 3 | 24 | 0,824 | 0,316 | 0,425 |
| IQR | 2 | 6 | 0,817 | 0,233 | 0,394 |
| Ensemble thứ hạng | 3 | 24 | 0,787 | 0,330 | 0,432 |
| MAD | 2 | 6 | 0,673 | 0,143 | 0,332 |

Nguồn: Tác giả tổng hợp từ kết quả thực nghiệm trên dữ liệu Olist.

> [Vị trí chèn Hình 5.1]

**Hình 5.1.** ROC AUC và Average Precision tốt nhất của từng nhóm phương pháp trên tập validation

Nguồn: Tác giả tổng hợp từ kết quả thực nghiệm trên dữ liệu Olist.

Kết quả cho thấy ba điểm:

- **Ngưỡng phân vị 95% (upper_q950)** có ROC AUC và Average Precision cao nhất, tức xếp hạng các đơn trễ hẹn tốt nhất trên tập validation.
- **Tham chiếu theo sản phẩm và bối cảnh** với ngưỡng điểm lớn hơn 0 có F1 cao nhất ở ngưỡng cố định (0,678). Phương pháp cũng có ưu điểm diễn giải, như đã trình bày ở mục 2.4.3.
- **Các mô hình học máy không giám sát** như LOF và One-Class SVM không vượt các quy tắc dựa trên phân vị trên tiêu chí này. Isolation Forest đạt ROC AUC 0,904 nhưng Average Precision thấp hơn đáng kể.

Không có phương pháp nào tốt nhất trên mọi tiêu chí. Vì vậy, hai ứng viên upper_q950 và product_context_stage_score_gt0 được đưa sang bước lựa chọn theo thời gian.

## 5.3. Độ ổn định theo thời gian và kết quả trên test

### 5.3.1. Lựa chọn ứng viên theo thời gian

Quy tắc chọn được khai báo trước: ưu tiên F1 trung bình của các tháng có cả đơn trễ và đơn không trễ, với trọng số mỗi tháng bằng nhau. Nếu hòa, xét F1 của tháng kém nhất, rồi đến F1 gộp. Ngưỡng của cả hai ứng viên được giữ nguyên, không tìm ngưỡng mới. Bảng 5.3 trình bày kết quả theo tháng.

**Bảng 5.3.** Kết quả theo tháng của hai ứng viên trên tập validation

| Tháng | Ứng viên | Số đơn | Trễ hẹn | Precision | Recall | F1 | ROC AUC |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 03/2018 | Phân vị 95% | 572 | 121 | 0,629 | 0,884 | 0,735 | 0,913 |
| 03/2018 | Tham chiếu theo bối cảnh | 572 | 121 | 0,752 | 0,752 | 0,752 | 0,847 |
| 04/2018 | Phân vị 95% | 479 | 17 | 0,222 | 0,824 | 0,350 | 0,888 |
| 04/2018 | Tham chiếu theo bối cảnh | 479 | 17 | 0,308 | 0,706 | 0,429 | 0,826 |
| 05/2018 | Cả hai | 256 | 0 | — | — | — | — |

Nguồn: Tác giả tổng hợp từ kết quả thực nghiệm trên dữ liệu Olist.

Tham chiếu theo bối cảnh có F1 trung bình tháng là 0,590, so với 0,543 của phân vị 95%. F1 tháng kém nhất là 0,429 so với 0,350, và F1 gộp là 0,678 so với 0,639. Vì vậy, ứng viên product_context_stage_score_gt0 được chọn. Hai ứng viên khác nhau chủ yếu ở cách đánh đổi lỗi:

- Tham chiếu theo bối cảnh cảnh báo nhầm ít hơn (63 so với 120 đơn không trễ).
- Đổi lại, nó bỏ sót nhiều hơn (35 so với 17 đơn trễ).

Phân tích chi phí giả định cho thấy, nếu một lần bỏ sót được coi là tốn hơn khoảng 3,17 lần một lần cảnh báo nhầm, phân vị 95% sẽ được ưu tiên. Lựa chọn hiện tại vì vậy phụ thuộc vào ưu tiên vận hành và nên được xác nhận với người dùng thực tế.

Các kiểm tra độ nhạy cho thấy lựa chọn tương đối ổn định nhưng không tuyệt đối. Khi bỏ lần lượt từng tuần, tham chiếu theo bối cảnh dẫn đầu 11/12 lượt; lựa chọn chỉ đảo khi bỏ tuần bắt đầu ngày 02/04/2018. Tháng 05/2018 không có đơn trễ hẹn, vì thời gian theo dõi ngắn (trung vị 15,7 ngày) và chỉ các đơn đã hoàn tất được giữ lại. Do đó, tháng này không được dùng để chọn và không được diễn giải là vận hành đã tốt lên.

### 5.3.2. Kết quả trên tập test

Ứng viên đã cố định được đánh giá trên tập test gồm 1.691 đơn (06–08/2018). Bảng 5.4 trình bày lý do của 168 đơn chưa đánh giá được.

**Bảng 5.4.** Lý do chưa đánh giá được trên tập test

| Lý do | Số đơn | Giải thích |
| --- | ---: | --- |
| Mâu thuẫn dữ liệu nguồn | 79 | Mốc thời gian đảo thứ tự, thời lượng âm |
| Địa lý không đạt kiểm tra chất lượng | 66 | Tọa độ đại diện của mã ZIP không đủ tin cậy |
| Chưa có kết quả giao hàng | 17 | Đơn đang vận chuyển, bị hủy hoặc chưa giao |
| Thiếu nhóm tham chiếu đủ mẫu | 6 | Không có nhóm nào đạt tối thiểu 30 đơn cùng bối cảnh |
| Tổng | 168 | 9,9% tập test |

Nguồn: Tác giả tổng hợp từ kết quả thực nghiệm trên dữ liệu Olist.

Còn lại 1.523 đơn đủ điều kiện, trong đó 35 đơn giao trễ hẹn (2,3%). Bảng 5.5 và Hình 5.2 so sánh kết quả trên validation và test.

**Bảng 5.5.** Kết quả của ứng viên đã cố định trên validation và test

| Chỉ số | Validation | Test |
| --- | ---: | ---: |
| Số đơn đủ điều kiện | 1.307 | 1.523 |
| Số đơn trễ hẹn (tỷ lệ) | 138 (10,6%) | 35 (2,3%) |
| Số cảnh báo | 166 | 70 |
| TP / FP / FN | 103 / 63 / 35 | 11 / 59 / 24 |
| Precision | 0,620 | 0,157 |
| Recall | 0,746 | 0,314 |
| F1 | 0,678 | 0,210 |
| ROC AUC | 0,850 | 0,637 |
| Average Precision | 0,528 | 0,072 |

Nguồn: Tác giả tổng hợp từ kết quả thực nghiệm trên dữ liệu Olist.

> [Vị trí chèn Hình 5.2]

**Hình 5.2.** So sánh kết quả của ứng viên đã cố định trên validation và test

Nguồn: Tác giả tổng hợp từ kết quả thực nghiệm trên dữ liệu Olist.

Kết quả suy giảm rõ rệt từ validation sang test. Khóa luận báo cáo đầy đủ mức suy giảm này thay vì chỉ trình bày kết quả validation. Có thể chỉ ra một số yếu tố liên quan, nhưng dữ liệu hiện có chưa đủ để xác định yếu tố nào là nguyên nhân chính:

- **Tỷ lệ đơn trễ hẹn giảm mạnh,** từ 10,6% xuống 2,3%. Average Precision phụ thuộc vào tỷ lệ nhóm dương: một điểm xếp hạng ngẫu nhiên có Average Precision xấp xỉ bằng tỷ lệ này (Saito & Rehmsmeier, 2015). Vì vậy, 0,072 trên test cần được so với mức nền 0,023, còn 0,528 trên validation cần được so với mức nền 0,106.
- **Phân phối dữ liệu thay đổi theo thời gian** (Gama et al., 2014). Tháng 06 và 07/2018 có rất ít đơn trễ (6 và 5 đơn), nên các chỉ số theo tháng biến động mạnh. Tháng 08/2018 có 24 đơn trễ, đạt Precision 0,28 và Recall 0,29.
- **Lựa chọn trên validation có thể làm kết quả validation lạc quan hơn,** vì ứng viên được chọn chính trên tập này.
- **Phần lớn công đoạn trên test dùng nhóm dự phòng cấp ngành:** 88,9% số dòng công đoạn. Chỉ 1,2% dùng nhóm theo sản phẩm.

### 5.3.3. Các trường hợp bất đồng giữa cảnh báo và giao hàng

Trên tập test có 83 đơn mà cảnh báo công đoạn và trạng thái giao hàng không thống nhất:

- **59 đơn có cảnh báo nhưng không trễ hẹn,** trong đó 58 đơn giao sớm và 1 đơn giao đúng ngày. Theo quy ước ở mục 3.8, các cảnh báo này được giữ. Chúng không tự động là cảnh báo sai, vì một công đoạn có thể kéo dài ngay cả khi ngày hẹn có khoảng dự phòng rộng.
- **24 đơn trễ hẹn nhưng không có cảnh báo.** Trong đó, 21 đơn có ít nhất một công đoạn dài hơn trung bình tham chiếu nhưng chưa vào 5% đuôi trên; 3 đơn có mọi công đoạn không vượt trung bình.

Trong 83 đơn này, 42 đơn có nội dung đánh giá của khách hàng, và 12 đơn có từ ngữ về chậm trễ hoặc không nhận được hàng. Thông tin này chỉ mang tính tham khảo, không phải xác nhận nguyên nhân. Trên toàn tập test, cảnh báo tập trung ở công đoạn đặt hàng đến duyệt đơn (45 công đoạn), sau đó là duyệt đơn đến bàn giao (20) và bàn giao đến giao khách (6). Theo mức cảnh báo, có 43 nhẹ, 13 vừa và 15 cao.

## 5.4. Kết quả phát lại sự kiện

Bộ phát lại xử lý 7.076 mốc thật của 1.853 đơn validation. Bảng 5.6 trình bày kết quả của hai kịch bản bối cảnh.

**Bảng 5.6.** Kết quả phát lại sự kiện và chấm công đoạn

| Chỉ số | Chỉ dùng bối cảnh đã xác minh | Giả định bối cảnh có sẵn lúc đặt hàng |
| --- | ---: | ---: |
| Dòng công đoạn | 5.559 | 5.559 |
| Công đoạn được chấm điểm | 0 | 4.865 |
| Đơn có ít nhất một công đoạn được chấm | 0 | 1.726 (93,1%) |
| Đơn chấm đủ ba công đoạn | 0 | 1.462 |
| Công đoạn có cảnh báo | 0 | 225 |
| Đơn có cảnh báo | 0 | 221 |
| Đơn được cảnh báo trước khi quan sát được mốc giao hàng | 0 | 95 |

Nguồn: Tác giả tổng hợp từ kết quả thực nghiệm trên dữ liệu Olist.

Kịch bản chỉ dùng bối cảnh đã xác minh không chấm được công đoạn nào, vì Olist không ghi thời điểm thông tin bối cảnh có sẵn. Kết quả này cho thấy giới hạn của dữ liệu, không có nghĩa là các đơn bình thường. Ở kịch bản giả định, 95 đơn được cảnh báo ở công đoạn đầu hoặc công đoạn giữa trước khi đơn được giao. Đây là các trường hợp mà, trong điều kiện giả định, người quản lý có thể biết sớm về công đoạn kéo dài.

Có 694 dòng công đoạn chưa chấm được. Lý do gồm: chưa quan sát mốc kết thúc (332), địa lý không đạt kiểm tra (179), mốc thời gian mâu thuẫn (153), thiếu nhóm tham chiếu đủ mẫu (24) và đơn đang giữ để rà soát (6). Trong số công đoạn được chấm, 97,8% dùng nhóm dự phòng cấp ngành.

**Tính nhất quán với chấm theo lô.** Với 4.233 công đoạn của 1.411 đơn có trong cả hai cách chấm, kết quả phát lại khớp tuyệt đối với chấm theo lô ở cả 11 trường: thời lượng, nhóm tham chiếu, số mẫu, trung bình, trung vị, chênh lệch, phân vị, điểm, mức và cờ cảnh báo. Đây là kiểm tra hồi quy số học, không phải đánh giá độ chính xác độc lập.

**Độ trễ xử lý.** Một lần chạy local không giãn cách thời gian xử lý 52 sự kiện mỗi giây. Thời gian cập nhật, chấm và ghi một sự kiện có trung vị 13,9 ms, phân vị 95 là 49,7 ms và lớn nhất 848,7 ms (lần tra cứu đầu tiên, khi bộ nhớ đệm còn trống). Số đo này không gồm thời gian nạp dữ liệu nguồn, độ trễ thu nhận, mạng, lớp AI và giao diện.

## 5.5. So sánh quy tắc, một tác tử và ba tác tử

### 5.5.1. Thiết lập

Đối chứng dùng 20 case phát triển, gồm 8 case có cảnh báo, 8 case đủ ba công đoạn nhưng dưới ngưỡng và 4 case chưa đánh giá đầy đủ. Mỗi case được giải thích bằng năm phương án: quy tắc cố định, một tác tử và ba tác tử, trong đó hai phương án AI chạy ở hai cấu hình ngân sách:

- **native:** ba tác tử tối đa 12 lượt gọi, một tác tử tối đa 4 lượt.
- **matched:** cả hai tối đa 4 lượt gọi.

Mọi phương án dùng chung dữ liệu, kết quả phát hiện, bộ công cụ và cổng kiểm tra. Đợt v3 dùng trần 6.000 token đầu vào. Sau khi phân tích các lượt bị dừng, đợt v4 chạy lại đúng thiết kế với trần 12.000 token (mục 4.6.2). Mỗi phương án chạy một lần cho mỗi case, không chạy lại khi lỗi và không chọn đầu ra tốt nhất. Cả hai đợt được phát lại offline và khớp toàn bộ với biên nhận đã lưu.

### 5.5.2. Kết quả

Bảng 5.7 và Hình 5.3 trình bày kết quả.

**Bảng 5.7.** Kết quả đối chứng lớp giải thích trên 20 case

| Phương án | Có đầu ra v3 | Có đầu ra v4 | Nêu đủ cảnh báo v3 (8 case) | Nêu đủ cảnh báo v4 (8 case) | Khớp nguồn | Phản hồi mô hình v4 | Chi phí v4 (USD) | Thời gian trung vị v4 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Quy tắc cố định | 20 | 20 | 8 | 8 | 100% | 0 | 0 | — |
| Một tác tử, native | 16 | 19 | 5 | 7 | 100% | 36 | 0,019 | 6,1 giây |
| Một tác tử, matched | 16 | 19 | 4 | 7 | 100% | 36 | 0,012 | 5,4 giây |
| Ba tác tử, native | 11 | 20 | 4 | 8 | 100% | 105 | 0,069 | 18,5 giây |
| Ba tác tử, matched | 10 | 18 | 6 | 6 | 100% | 80 | 0,043 | 16,7 giây |

Nguồn: Tác giả tổng hợp từ kết quả thực nghiệm.

> [Vị trí chèn Hình 5.3]

**Hình 5.3.** Số case có đầu ra công bố theo phương án và phiên bản giới hạn token

Nguồn: Tác giả tổng hợp từ kết quả thực nghiệm.

Với trần 6.000 token (v3), mọi lượt dừng đều do yêu cầu vượt trần và bị hủy trước suy luận. Trong đó, 29 yêu cầu bị hủy thuộc về Evidence Verifier, vì gói kiểm chứng chứa toàn bộ nhận định, bằng chứng và phần tính lại số liệu, có kích thước 6.550–6.730 token. Hệ ba tác tử vì vậy bị ảnh hưởng nhiều nhất: chỉ 11/20 case có đầu ra.

Với trần 12.000 token (v4), không còn lượt nào dừng do vượt trần. Yêu cầu lớn nhất là 8.374 token. Hệ ba tác tử ở cấu hình native đạt 20/20 case có đầu ra và nêu đủ cảnh báo ở cả 8/8 case có cảnh báo, bằng quy tắc cố định và cao hơn một tác tử (19/20 và 7/8). Khi bị giới hạn 4 lượt (matched), ba tác tử chỉ đạt 18/20 và 6/8. Nguyên nhân là ba vai trò phải chia nhau 4 lượt, nên không còn lượt cho vòng bổ sung cảnh báo bị bỏ sót.

Không có mục nào bị Evidence Verifier loại trong cả hai đợt. Tính đúng nguồn đạt 100% ở mọi phương án. Đây là kết quả của thiết kế ràng buộc đầu ra vào danh mục bằng chứng (mục 4.2.2): nó chứng minh ràng buộc hoạt động đúng, nhưng không chứng minh AI tạo giải thích tốt hơn quy tắc.

### 5.5.3. Chi phí của sự phối hợp

Ở cấu hình native của v4, hệ ba tác tử cần khoảng 2,9 lần số phản hồi mô hình (105 so với 36), 3,6 lần chi phí (0,069 so với 0,019 USD) và 3 lần thời gian (18,5 so với 6,1 giây) so với một tác tử. Chi phí tuyệt đối thấp: toàn bộ đợt v4 với 80 lượt tốn 0,143 USD. Tuy vậy, chi phí tăng tuyến tính theo số đơn cần giải thích. Do đó, khi triển khai, lớp AI nên chỉ được gọi cho các đơn có cảnh báo thay vì mọi đơn.

## 5.6. Đánh giá chất lượng giải thích và đề xuất kiểm tra

Các tiêu chí tự động ở mục 5.5 chưa trả lời được câu hỏi giải thích có hữu ích và có căn cứ hay không. Vì vậy, khóa luận thiết kế phần chấm định tính như sau.

- **Bộ tiêu chí:** sáu tiêu chí trên thang 1–5, cho phép NA. Gồm mức độ bằng chứng hỗ trợ nhận định, tính phù hợp của đề xuất kiểm tra, không bỏ sót quan sát quan trọng, nêu rõ phạm vi và giới hạn, không khẳng định nguyên nhân thiếu căn cứ, và mức hữu ích cho người quản lý.
- **Đối tượng chấm:** 100 đầu ra (20 case × 5 phương án) của đợt v4. Mọi đầu ra được trình bày theo cùng một định dạng tiếng Việt. Tên phương án được ẩn, thứ tự A–E được xáo riêng cho từng case, và bảng giải mã do người phụ trách giữ riêng.
- **Người chấm và mức đồng thuận:** hai người chấm độc lập. Mức đồng thuận tính bằng Cohen's kappa có trọng số bậc hai (Cohen, 1968), kèm khoảng bootstrap 95%. Nếu có từ ba người chấm, dùng Fleiss' kappa (Fleiss, 1971). Kappa chỉ phản ánh độ tin cậy của việc chấm, không chứng minh giải thích là đúng.

Tại thời điểm hoàn thành bản thảo, bộ tiêu chí đang được giảng viên hướng dẫn góp ý và phần chấm chưa có kết quả. Kết quả sẽ được bổ sung vào mục này khi hoàn tất.

## 5.7. Phân tích các trường hợp điển hình

**Trường hợp 1: giao sớm nhưng có công đoạn kéo dài.** Một đơn tháng 08/2018 giao đến bang BA được giao sớm hơn ngày hẹn 24 ngày. Tuy vậy, công đoạn đặt hàng đến duyệt đơn kéo dài 2,95 ngày, trong khi trung bình của nhóm tham chiếu (114 đơn, nhóm dự phòng cấp ngành có bối cảnh) là 0,29 ngày. Điểm của công đoạn là 100, mức cao. Nếu chỉ theo dõi ngày hẹn, trường hợp này không được phát hiện. Đây là một trong 58 đơn giao sớm có cảnh báo trên tập test.

**Trường hợp 2: vận chuyển kéo dài, trễ hẹn và có giải thích AI.** Một đơn tháng 03/2018 giao đến bang RJ có công đoạn bàn giao đến giao khách kéo dài 36,05 ngày, so với trung bình 12,23 ngày của nhóm tham chiếu 277 đơn. Điểm 49,5, mức nhẹ; đơn giao trễ hẹn 20 ngày. Giải thích của hệ ba tác tử (v4) gồm các nội dung sau:

- Nhận định cảnh báo ở công đoạn vận chuyển.
- Ghi chú các giới hạn: thời điểm có sẵn của bối cảnh chưa xác minh, cơ sở ngày hẹn chưa xác minh, còn yếu tố chưa kiểm soát trong phép so sánh.
- Đề xuất xem xét tuyến và đơn vị vận chuyển của đơn.

Đề xuất này là hướng xác minh có căn cứ, không khẳng định tuyến vận chuyển là nguyên nhân. Mức nhẹ dù thời lượng gấp khoảng ba lần trung bình cho thấy phân phối thời gian vận chuyển của nhóm có đuôi dài, nên điểm cần được đọc cùng số ngày chênh lệch.

**Trường hợp 3: mốc thời gian mâu thuẫn.** Một đơn tháng 07/2018 có mốc bàn giao vận chuyển (03/07/2018, 17:14) sớm hơn mốc duyệt đơn (05/07/2018, 16:33). Thời lượng công đoạn duyệt đơn đến bàn giao vì vậy là số âm. Theo quy tắc ở mục 4.3.2, mọi công đoạn của đơn tạm dừng đánh giá. Hệ thống không tự sửa thứ tự mốc để tạo ra điểm. Đây là một trong 79 đơn có mâu thuẫn dữ liệu nguồn trên tập test.

**Trường hợp 4: thiếu nhóm tham chiếu.** Một đơn giao đến bang PE ở dải khoảng cách trên 2.000 km có đủ dữ liệu, nhưng không có nhóm tham chiếu nào, kể cả nhóm dự phòng cấp ngành, đạt tối thiểu 30 đơn cùng bối cảnh. Đơn được ghi nhận là chưa đánh giá được thay vì bị so với một nhóm không tương đồng.

**Trường hợp 5: hành vi của tác tử.** Các lượt chạy ghi nhận ba kiểu hành vi cần lưu ý:

- **Gói kiểm chứng vượt trần token (v3):** yêu cầu của Evidence Verifier vượt trần nên lượt giải thích bị dừng. Đây là lý do nâng trần ở v4.
- **Dùng sai công cụ:** ở hai case của v4, tác tử đơn gọi công cụ thống kê nhóm cho một bằng chứng không phải phép so sánh nhóm. Công cụ từ chối và lượt giải thích kết thúc ở trạng thái không đủ kết luận.
- **Bỏ sót cảnh báo khi hết lượt:** ở cấu hình matched, có lượt ba tác tử bỏ sót công đoạn có cảnh báo và không còn lượt để bổ sung. Đầu ra được giữ với trạng thái chưa nêu đủ cảnh báo.

Cả ba trường hợp đều được hệ thống ghi nhận và báo cáo, không bị ẩn hay chạy lại.

## 5.8. Thảo luận kết quả và các yếu tố ảnh hưởng đến độ tin cậy

### 5.8.1. Trả lời các câu hỏi nghiên cứu

**RQ1.** Phương pháp tham chiếu theo sản phẩm và bối cảnh nhận diện công đoạn kéo dài bằng cách so sánh thời lượng với phần đuôi phân phối của nhóm đơn tương đồng.
- **Trên validation:** phương pháp đạt F1 cao nhất ở ngưỡng cố định (0,678) và được chọn theo tiêu chí F1 theo tháng. Phân vị 95% xếp hạng đơn trễ hẹn tốt hơn (ROC AUC 0,920 so với 0,850).
- **Trên test:** kết quả suy giảm rõ (F1 0,210; ROC AUC 0,637), trong bối cảnh tỷ lệ đơn trễ hẹn giảm mạnh và phân phối dữ liệu thay đổi.
- **Độ bao phủ:** 90,1% số đơn test đánh giá được; phần còn lại được ghi rõ lý do. Phần lớn đánh giá dựa trên nhóm dự phòng cấp ngành.
- **Ưu điểm chính:** khả năng diễn giải và khả năng phát hiện công đoạn kéo dài ngay cả ở đơn giao sớm (58 đơn trên test).

Vì đích đối chiếu là giao trễ hẹn, các kết quả này không đo được độ chính xác phát hiện tắc nghẽn.

**RQ2.**
- **Khi đủ tài nguyên** (v4, native), hệ ba tác tử hoàn thành 20/20 case và nêu đủ 8/8 cảnh báo, ngang quy tắc cố định và nhỉnh hơn một tác tử. Chênh lệch là 1 case về hoàn thành và 1 case về độ đầy đủ, quá nhỏ để khẳng định ưu thế tổng quát.
- **Khi tài nguyên bị giới hạn** (v3, hoặc cấu hình matched), hệ ba tác tử kém hơn, và chi phí của hệ luôn cao hơn khoảng 3,6 lần.
- **So với quy tắc cố định:** quy tắc đạt độ đầy đủ tối đa với chi phí bằng 0.

Vì vậy, với các tiêu chí tự động hiện có, khóa luận chưa chứng minh được giá trị bổ sung của việc phối hợp nhiều tác tử. Giá trị đó, nếu có, phải thể hiện ở chất lượng giải thích, tức phần chấm định tính ở mục 5.6.

**RQ3.** Khi phát lại sự kiện, khung phân tích cập nhật trạng thái và chấm công đoạn ngay khi công đoạn hoàn tất:
- Trung vị 13,9 ms cho mỗi sự kiện.
- Kết quả khớp tuyệt đối với chấm theo lô.
- 95 đơn được cảnh báo trước khi giao hàng.

Các giới hạn chính:
- Kết quả phụ thuộc vào giả định thông tin bối cảnh có sẵn từ lúc đặt hàng; không có giả định này thì không chấm được công đoạn nào.
- Chưa cảnh báo được công đoạn đang diễn ra.
- Lớp AI ba tác tử mất khoảng 17–19 giây mỗi case và chưa được tích hợp vào mọi sự kiện của luồng phát lại.

### 5.8.2. Các yếu tố ảnh hưởng đến độ tin cậy

- **Không có nhãn nguyên nhân.** Mọi chỉ số phát hiện dựa trên biến đích gián tiếp, và cơ sở xác lập ngày hẹn chưa được xác minh.
- **Tập test đã được xem xét một phần.** Việc chọn ngành hàng dựa trên khám phá toàn bộ lịch sử. Tập test được dùng một lần cho ứng viên đã cố định, nhưng không phải tập hoàn toàn chưa từng được xem xét.
- **Mẫu AI nhỏ và chạy một lần.** 20 case phát triển, mỗi phương án chạy một lần, nên chưa đo được độ biến động giữa các lần chạy của mô hình.
- **Thiếu mẫu ở cấp sản phẩm.** Tham chiếu theo sản phẩm chỉ áp dụng được cho khoảng 1–2% công đoạn.
- **Giả định về thời điểm có sẵn của thông tin.** Thông tin tĩnh lấy từ bản dữ liệu cuối cùng, nên không chứng minh được chính xác những gì đã biết tại từng thời điểm.

### 5.8.3. Khả năng tái lập

Mọi kết quả trong chương được tạo từ dữ liệu và mã nguồn có mã băm trong tệp manifest. Các đợt chạy AI có thể được phát lại offline từ biên nhận đã lưu, và việc phát lại khớp toàn bộ với kết quả gốc. Các bảng và hình trong chương được tính từ chính các tệp kết quả này, không nhập tay số liệu.

Tóm lại, Chương 5 đã trình bày kết quả của các thực nghiệm phát hiện, phát lại sự kiện và đối chứng lớp giải thích, cùng các trường hợp điển hình và giới hạn của kết quả. Chương 6 tổng kết khóa luận và đề xuất hướng phát triển.

@@REFS

Cohen, J. (1968). Weighted kappa: Nominal scale agreement provision for scaled disagreement or partial credit. *Psychological Bulletin, 70*(4), 213–220. https://doi.org/10.1037/h0026256

Fleiss, J. L. (1971). Measuring nominal scale agreement among many raters. *Psychological Bulletin, 76*(5), 378–382. https://doi.org/10.1037/h0031619

Gama, J., Žliobaitė, I., Bifet, A., Pechenizkiy, M., & Bouchachia, A. (2014). A survey on concept drift adaptation. *ACM Computing Surveys, 46*(4), Article 44. https://doi.org/10.1145/2523813

Saito, T., & Rehmsmeier, M. (2015). The precision-recall plot is more informative than the ROC plot when evaluating binary classifiers on imbalanced datasets. *PLOS ONE, 10*(3), Article e0118432. https://doi.org/10.1371/journal.pone.0118432

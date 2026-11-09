# Tiến độ và nội dung chuẩn bị viết khóa luận — 07/11/2026

Đã có chuỗi xử lý dữ liệu, thực nghiệm so sánh phương pháp, ứng viên chấm điểm công đoạn, đánh giá hồi cứu, phát lại sự kiện và prototype ba agent có gọi API thật. Hiện đang hoàn thiện độ tin cậy và kiểm chứng lớp giải thích AI. Chưa có dashboard local hoàn chỉnh, so sánh AI đủ quy mô hoặc đo độ trễ toàn hệ thống.

Cập nhật sau cặp thử API ngày 07/11: ba agent hoàn tất với 11 mục riêng biệt khớp nguồn và nêu đủ cờ cảnh báo vận chuyển; một agent vẫn dừng ở gói 6.024 token. Chi tiết ở `docs/agent_live_review_2026-11-07.md`.

Có thể bắt đầu viết phần dữ liệu, phương pháp, kiến trúc và thực nghiệm đã hoàn thành ngay. Không nên chờ mọi ý tưởng nghiên cứu được giải quyết mới viết. Bản này là sổ tay có nguồn đối chiếu để viết, không thay cho bản luận văn theo mẫu trường.

## 1. Dữ liệu và tiền xử lý

Đã xử lý 9 bảng Olist thật: orders, items, payments, reviews, customers, sellers, products, geolocation và bản dịch ngành hàng. Bản sạch giữ 99.441 đơn, 112.650 dòng item, 103.886 dòng payment và 99.224 dòng review. Chỉ bỏ 261.831 dòng tọa độ trùng hoàn toàn sau chuẩn hóa ở bản sạch; giữ 738.332 dòng tọa độ còn lại. Bản raw được giữ nguyên.

Đã chuẩn hóa kiểu dữ liệu, ID, chuỗi có cấu trúc, Unicode, ZIP 5 ký tự, số và thời gian; giữ nội dung review tự do. Không tự điền thời điểm, tiền hoặc tọa độ thiếu. Không tự xóa các đơn vì thời lượng dài. Một order ID xuất hiện ở nhiều item/payment/review là quan hệ một-nhiều, không đủ căn cứ coi là trùng đơn.

Có bảng đối soát số dòng, kiểm tra khóa/quan hệ, truy vết dòng nguồn và cờ vấn đề chất lượng. Nguồn vẫn có 2.980 đơn thiếu mốc và 1.382 đơn có thứ tự mốc đảo ngược; các nhóm có thể chồng lấn. Hệ thống giữ bằng chứng và áp dụng điều kiện sử dụng riêng cho từng phân tích. Vì vậy có thể viết “đã làm sạch cấu trúc và chuẩn hóa có truy vết”, chưa thể viết “dữ liệu sạch tuyệt đối về nghiệp vụ”.

Đã EDA quy mô ngành hàng, phân bố thời gian, trạng thái, biến thể quy trình, thanh toán, địa lý và seller. Phạm vi chính là `bed_bath_table`. Tập ngành đã chia theo thời gian gồm 9.024 đơn: train 5.480, validation 1.853, test 1.691. Các điều kiện đủ dữ liệu tiếp tục làm giảm mẫu dùng ở từng phép đánh giá; phải báo mẫu số riêng. Test đã được xem kết quả, không gọi đó là tập chưa từng tiếp xúc cho các lần tinh chỉnh tiếp theo.

Nguồn đối chiếu: `data/processed/olist_clean_v1/README.md`, `audit/table_reconciliation.csv`, `manifest.json`; `data/experiments/bed_bath_table_v1/split_summary.csv`; ghi chú tổng hợp `docs/progress_2026-10-28.md`.

## 2. Quy trình, đặc trưng và cách phát hiện

Bốn mốc quan sát được là đặt hàng, duyệt, bàn giao vận chuyển và giao khách. Từ đó tính ba công đoạn: đặt–duyệt; duyệt–bàn giao; bàn giao–giao khách. Không có dữ liệu để tách chính xác đóng gói, chờ kho hoặc từng chặng xe.

Đã xây dựng các nhóm đặc trưng thời gian, cấu trúc đơn, giá trị/cách thanh toán, lịch sử khách/seller và địa lý theo góp ý của giảng viên. Khoảng cách từ tọa độ là xấp xỉ đường thẳng, không phải quãng đường xe đi. Các đặc trưng hồi cứu như review hoặc thông tin chưa biết thời điểm sẵn có không được tự động xem là đầu vào trực tuyến.

Các phương pháp đã có trong code/thực nghiệm gồm Isolation Forest, LOF novelty, One-Class SVM; IQR, MAD, phân vị; tổng hợp điểm/thứ hạng; Gradient Boosting phân vị cho tham chiếu thời gian; điểm đuôi phân phối theo sản phẩm và bối cảnh. Đây là các nhánh thí nghiệm, không phải tất cả đang chạy đồng thời trong demo.

Trong benchmark, LOF/One-Class SVM dùng log1p và RobustScaler được fit trên phần fit. Isolation Forest có cấu hình raw/log1p. Chuẩn hóa nguồn và scaling mô hình là hai bước khác nhau; validation/test không được dùng để fit scaler hay ngưỡng.

Ứng viên hiện dùng để tích hợp là `product_context_stage_score_gt0`. Với thời lượng d, trung bình nhóm tham chiếu mu, F(d) là tỷ lệ thời lượng lịch sử không lớn hơn d, điểm bằng 0 nếu d <= mu hoặc d <= 0; ngoài ra là `clip(100 * (F(d) - 0.95) / 0.05, 0, 100)`. Thiếu bằng chứng giữ `null`. Điểm trên 0 tạo cảnh báo; (0,50] nhẹ, (50,80] vừa, (80,100] cao. Điểm này không phải xác suất.

Tham chiếu phải lấy từ lịch sử train hợp lệ, ít nhất 30 mẫu, không chứa đơn đang đánh giá. Ưu tiên nhóm sản phẩm/bối cảnh, dùng dự phòng cấp ngành có bối cảnh nếu không đủ điều kiện theo quy tắc cố định. Trên test, chỉ 60 dòng công đoạn dùng được nhóm cùng sản phẩm/bối cảnh, 4.509 dòng phải dự phòng. Không thể tuyên bố đã có ngưỡng đáng tin cậy riêng cho mọi mã sản phẩm.

Đầu ra tách riêng: chênh lệch thời lượng với tham chiếu; cờ vượt đuôi cảnh báo; giao sớm/đúng/trễ theo ngày hẹn. Giao sớm không xóa cảnh báo công đoạn. Không ép tìm công đoạn nhanh nếu số liệu không thể hiện. Trạng thái cơ sở ngày hẹn vẫn là chưa xác minh; không biết cơ sở không có nghĩa ngày hẹn được đặt ngẫu nhiên.

Nguồn: `docs/product_stage_scores.md`, `docs/process_performance_explanation_2026-10-19.md`, `scripts/03_anomaly_detection/build_product_stage_scores.py`.

## 3. Thực nghiệm đánh giá đã có

Đã so sánh 22 phương pháp/điểm thuộc 8 nhóm, 121 cấu hình cố định trên cùng 1.307 đơn validation đủ điều kiện; có phân tích độ ổn định theo giai đoạn. Hai ứng viên nổi bật là `upper_q950` và `product_context_stage_score_gt0`. Không có ứng viên thắng mọi tiêu chí; lựa chọn theo F1 theo tháng có một trường hợp đảo khi bỏ tuần bắt đầu 02/04/2018.

Ứng viên theo sản phẩm/bối cảnh được giữ nguyên để đánh giá test gốc. Kết quả đọc từ snapshot đã lưu:

| Chỉ số | Validation so sánh | Test đủ điều kiện |
| --- | ---: | ---: |
| Số đơn | 1.307 | 1.523 |
| Đơn trễ ngày lịch | 138 | 35 |
| Precision | 0,6205 | 0,1571 |
| Recall | 0,7464 | 0,3143 |
| F1 | 0,6776 | 0,2095 |
| ROC AUC | 0,8498 | 0,6373 |
| Average precision | 0,5277 | 0,0719 |

Đích đối chiếu là giao sau ngày dự kiến, không phải tắc nghẽn được xác nhận. Vì vậy các số liệu không chứng minh accuracy phát hiện tắc nghẽn. Cảnh báo công đoạn trên một đơn giao sớm có thể vẫn có ý nghĩa theo mục tiêu đề tài. Tỷ lệ trễ, cửa sổ quan sát và độ bao phủ giữa hai tập cũng khác nhau; cần phân tích mức suy giảm, không chỉ chọn con số đẹp ở validation.

Test có tổng 1.691 đơn, 168 đơn không đủ điều kiện cho phép đánh giá trên. Không điền nhãn bình thường cho số đơn bị loại. Không chỉnh ngưỡng trên test cũ rồi gọi đó là kiểm chứng độc lập.

Đã phân tích 83 đơn bất đồng giữa cảnh báo công đoạn và giao trễ, đối chiếu review và nhóm bối cảnh để tìm dấu hiệu cần điều tra. Review và từ khóa phàn nàn chưa phải nguyên nhân tắc nghẽn đã xác minh. Bộ 120 đơn đã có audit kỹ thuật, chú giải theo quy tắc không thay thế nhãn độc lập. Không đánh giá mô hình bằng chính quy tắc đã tạo nhãn.

Nguồn: `data/experiments/olist_delivery_discrimination_v1/summary.json`, `olist_temporal_candidate_selection_v1/summary.json`, `olist_selected_holdout_v1/summary.json`, `olist_holdout_process_evidence_v1/summary.json`.

## 4. Phát lại sự kiện và ba agent

Luồng backend hiện có:

```text
Olist raw -> chuẩn hóa + kiểm tra chất lượng -> phạm vi ngành + chia thời gian
 -> tham chiếu/so sánh detector -> chấm công đoạn theo mốc quan sát
 -> công cụ lấy bằng chứng -> Process Analyst -> Context Analyst
 -> Evidence Verifier + kiểm tra bằng code -> kết quả có nguồn và giới hạn
```

Các luồng batch và replay đã có artifact; lớp AI đã thử ở pilot, chưa được gọi trên mọi sự kiện của toàn luồng. Dashboard là phần cần tích hợp tiếp.

Replay đã xử lý 7.076 mốc thật của 1.853 đơn validation, lưu trạng thái và lịch sử thay đổi, kiểm tra chống xử lý trùng và phục hồi checkpoint. Bộ chấm sự kiện phân biệt hai kịch bản:

- Chỉ dùng bối cảnh có thời điểm sẵn có đã xác minh: chưa đủ cơ sở chấm điểm bằng bối cảnh tĩnh Olist.
- Giả định bối cảnh tĩnh đã sẵn có lúc mua: chấm được 4.865 dòng công đoạn, 1.726 đơn có ít nhất một điểm; 225 công đoạn cảnh báo thuộc 221 đơn. Có 95 đơn được cảnh báo công đoạn đã hoàn tất trước khi quan sát giao khách.

Các số này không chứng minh cảnh báo ngay trong một công đoạn chưa kết thúc hoặc hệ thống vận hành production real-time. Thực nghiệm cảnh báo đang chờ bàn giao đã có thiết kế, chưa có kết quả triển khai được xác nhận.

Ba vai trò AI đã có:

| Agent | Nhiệm vụ |
| --- | --- |
| Process Analyst | Đọc thời lượng, ngưỡng và trạng thái từng công đoạn; chọn nhận định có nguồn. |
| Context Analyst | Dùng công cụ so sánh nhóm tham chiếu, xem điều kiện bối cảnh và mức đủ mẫu; giữ phần chưa biết. |
| Evidence Verifier | Kiểm tra các nhận định, ghi chú và đề xuất kiểm tra; chấp nhận, yêu cầu sửa hoặc loại bỏ. |

Bộ điều phối bằng code không được tính thêm thành agent. Agent không tự thay detector/ngưỡng, tự tạo nhãn hoặc khẳng định nhân quả. Đã có API thật trên 6 case phân biệt qua nhiều phiên bản. Số 62 phản hồi đã quyết toán là số lượt của các thử nghiệm lịch sử, không phải 62 đơn và không phải một phép so sánh đồng nhất.

Lần ba-agent gần nhất hoàn tất 6 lượt với 12 mục khớp nguồn nhưng bỏ sót cờ vận chuyển trong phần nhận định. Nhánh một-agent dừng vì token. Ngày 07/11 đã xử lý offline bằng phiên bản mới: giảm nội dung lặp, đánh dấu phần giải thích chưa đủ và thêm chu kỳ sửa có giới hạn. 125 kiểm thử đạt, chi phí mới 0 USD. Sau đó đã chạy đúng cặp thử được duyệt: ba agent hoàn tất và nêu đủ cảnh báo, một agent nhận hai phản hồi rồi bị chặn ở 6.024 token. Lượt kiểm chứng sau chạy có 131 kiểm thử đạt. Chưa có pilot mở rộng của phiên bản mới.

Nguồn: `docs/event_stage_scoring.md`, `data/experiments/olist_event_stage_scoring_v1/summary.json`, `docs/multi_agent_integration_2026-10-30.md`, `docs/agent_live_compact_2026-11-06.md`, `docs/agent_review_2026-11-07.md`.

## 5. Phần đã có thể viết ngay

| Phần luận văn | Nội dung có bằng chứng hiện có | Lưu ý khi viết |
| --- | --- | --- |
| Bài toán và phạm vi | Thời gian xử lý bất thường, điểm nghẽn tiềm năng, hỗ trợ quản lý theo quy trình; Olist/bed_bath_table | Giải thích phạm vi near real-time thông qua replay và giới hạn nguồn. |
| Dữ liệu và tiền xử lý | 9 bảng, grain/khóa/quan hệ, EDA, làm sạch, truy vết, điều kiện sử dụng | Đưa số giữ/loại và lý do; không khẳng định sạch tuyệt đối. |
| Phương pháp | Ba công đoạn, đặc trưng, chia thời gian, tham chiếu, công thức điểm, cách chọn ứng viên | Phân biệt điểm, cảnh báo, ngày hẹn và nhãn kiểm chứng. |
| Thiết kế hệ thống | Replay, lưu trạng thái, công cụ bằng chứng, ba agent, bộ kiểm tra, ngân sách | Phân biệt phần đã chạy và thiết kế chưa triển khai. |
| Thực nghiệm đã hoàn thành | So sánh detector, độ ổn định, test hồi cứu, replay, các lượt API thật và lỗi tìm thấy | Báo cả kết quả giảm trên test và lượt chạy bị chặn. |
| Thảo luận | Thiếu nhãn/nhật ký nguyên nhân, nhóm sản phẩm ít mẫu, giả định thời điểm bối cảnh | Không biến hạn chế thành kết quả đã giải quyết. |

Chương dashboard/demo và bảng so sánh AI cuối cùng còn để cập nhật. Nếu trường có mẫu chính thức, cần dùng mẫu đó để đặt tên chương và định dạng; chưa thấy yêu cầu mẫu cụ thể trong phạm vi đang làm.

## 6. Những việc cần hoàn tất và những hướng có thể để nghiên cứu tiếp

Thử nghiệm live có giới hạn của phiên bản sửa đã hoàn tất theo phạm vi duyệt, với một nhánh thành công và một nhánh bị chặn. Các hạng mục còn cần hoàn tất cho bản demo và luận văn: ổn định vòng gọi công cụ; pilot AI có kế hoạch và bảng so sánh công bằng; dashboard local đọc được các kết quả thật, nhóm cảnh báo và mở chi tiết bằng chứng; kịch bản demo chạy lại được; tổng hợp kết quả, giới hạn và sao lưu artifact. So sánh cần báo đồng thời hoàn thành/độ đầy đủ/khớp nguồn/thời gian/chi phí, không tự đặt ba-agent thắng.

Có thể trình bày thành hướng nghiên cứu nếu chưa có dữ liệu hoặc thời gian:

| Hướng | Lý do chưa thể kết luận | Cần thêm gì |
| --- | --- | --- |
| Xác nhận nguyên nhân và hiệu quả can thiệp | Olist không ghi đầy đủ nhật ký xử lý/kho/vận chuyển | Log tác nghiệp, lý do sự cố, đối soát nghiệp vụ, kết quả sau can thiệp. |
| Accuracy/AUC cho tắc nghẽn xác nhận | Chưa có nhãn độc lập đúng đối tượng nghiên cứu | Bộ kiểm chứng có tiêu chí và nguồn bằng chứng độc lập với luật detector. |
| Real-time production và SLA | Hiện là replay lịch sử, bối cảnh có giả định | Nguồn streaming thật, thời điểm ingestion/update, thử tải và độ trễ toàn luồng. |
| Cảnh báo khi công đoạn còn chờ | Thiết kế đã có, chưa chạy/đánh giá đầy đủ | Đồng hồ kiểm tra, điều kiện đang chờ, đo thời gian can thiệp và khối lượng cảnh báo. |
| Áp dụng sang sàn/ngành khác | Phân phối dữ liệu và nghiệp vụ khác, chưa có kiểm chứng ngoài Olist | Nguồn mới có provenance, train/calibrate riêng và đánh giá riêng. |
| Khẳng định lợi ích ba agent vượt một agent | Pilot hiện nhỏ, nhiều lần là sửa lỗi trên cùng case | Đối chứng cùng bằng chứng/ngân sách, mẫu định trước, độ ổn định qua nhiều lượt. |

Không nên đưa toàn bộ Multi-Agent hoặc việc kiểm chứng đóng góp chính vào hướng tương lai rồi vẫn kết luận đã chứng minh tiêu đề đề tài. Nếu mẫu AI nhỏ hoặc không chứng minh ưu thế, trình bày đúng là prototype và kết quả thăm dò. Không cần giải quyết nhân quả hoặc mọi sàn để hoàn thành phạm vi đã thống nhất.

## 7. Lịch đề xuất đến hạn 30/11

Đây là kế hoạch có điều kiện, không phải cam kết đã đạt mốc. Còn khoảng 23 ngày tính từ 07/11.

| Mốc dự kiến | Đầu ra ưu tiên |
| --- | --- |
| 07–10/11 | Bắt đầu viết dữ liệu/phương pháp/kiến trúc; duyệt và thử phiên bản AI đã sửa trong hạn mức nhỏ. |
| 11–16/11 | Hoàn thiện demo local tối thiểu; thực hiện pilot/đối chứng theo phạm vi được duyệt; lưu bảng kết quả. |
| 17–21/11 | Chốt phạm vi thực nghiệm, đo luồng demo, hoàn thành bản luận văn có đầy đủ các chương. |
| 22–26/11 | Nhận góp ý, chỉnh luận văn, hình/bảng/tài liệu tham khảo; chuẩn bị slide và video dự phòng. |
| 27–29/11 | Kiểm tra chạy lại, sao lưu, chuẩn hóa hồ sơ nộp; tránh mở thêm nhánh nghiên cứu lớn. |
| 30/11 | Hạn nộp người dùng đã cung cấp. |

Nếu thử live còn vướng, ưu tiên trình bày lỗi, điều kiện dừng và kết quả đạt được; giữ dashboard thể hiện cảnh báo và bằng chứng từ backend, đồng thời công khai trạng thái giải thích AI chưa đầy đủ. Không che lỗi bằng kết quả sinh giả. Quy mô pilot/bản demo cần được chốt sớm để lịch viết không tiếp tục phụ thuộc việc tối ưu mở rộng.

Cặp thử nêu trong kế hoạch đã được duyệt và thực hiện, chi phí suy luận 0,004518515 USD. Bước đề xuất mới là thiết kế và kiểm tra offline việc quản lý vòng công cụ khi dữ kiện đã có, để dành chỗ cho kết luận trước khi lịch sử vượt giới hạn. Chưa thực hiện thêm lần API nào ngoài cặp đã duyệt. Không cần import thêm dữ liệu cho bước kỹ thuật này; nguồn bổ sung chỉ cần khi muốn xác nhận nguyên nhân hoặc kiểm chứng ở môi trường thực tế.

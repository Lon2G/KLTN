# CHƯƠNG 4. THIẾT KẾ VÀ TRIỂN KHAI HỆ THỐNG TÍCH HỢP MULTI-AGENT AI

Chương này trình bày thiết kế và triển khai nguyên mẫu của khóa luận. Mục 4.1 xác định yêu cầu chức năng và kiến trúc tổng thể. Mục 4.2 trình bày pipeline xử lý và cách quản lý bằng chứng. Mục 4.3 trình bày cơ chế phát lại sự kiện và quản lý trạng thái đơn hàng. Mục 4.4 và 4.5 trình bày thiết kế ba tác tử cùng cơ chế phối hợp, kiểm chứng. Mục 4.6 trình bày cách quản lý API, token, chi phí và lỗi. Mục 4.7 trình bày giao diện BI local.

## 4.1. Yêu cầu chức năng và kiến trúc tổng thể

### 4.1.1. Yêu cầu chức năng

Từ mục tiêu ở Chương 1 và phương pháp ở Chương 3, nguyên mẫu cần đáp ứng các yêu cầu trong Bảng 4.1.

**Bảng 4.1.** Yêu cầu chức năng của nguyên mẫu

| Mã | Yêu cầu | Thành phần đáp ứng |
| --- | --- | --- |
| YC1 | Nạp và chuẩn hóa dữ liệu nguồn, lưu dấu vết xử lý | Lớp dữ liệu chuẩn hóa |
| YC2 | Chấm điểm từng công đoạn theo nhóm tham chiếu đã cố định | Bộ phát hiện và tài nguyên đã cố định |
| YC3 | Cập nhật trạng thái đơn theo từng sự kiện, chấm công đoạn ngay khi hoàn tất | Bộ phát lại sự kiện và quản lý trạng thái |
| YC4 | Truy xuất bằng chứng có nguồn cho từng nhận định | Lớp công cụ và bằng chứng |
| YC5 | Tạo giải thích, ghi nhận giới hạn và đề xuất kiểm tra có căn cứ | Bộ điều phối và ba tác tử |
| YC6 | Kiểm chứng đầu ra trước khi công bố | Kiểm tra bằng chương trình và Evidence Verifier |
| YC7 | Giới hạn lượt gọi, token và chi phí; lưu biên nhận | Sổ ngân sách và provider có kiểm soát |
| YC8 | Tổng hợp cảnh báo và truy xuất đến từng đơn | Giao diện BI local |

Nguồn: Tác giả xây dựng.

Ngoài yêu cầu chức năng, nguyên mẫu tuân theo ba ràng buộc chung. Thứ nhất, lớp AI không được thay đổi kết quả phát hiện, ngưỡng hay dữ liệu nguồn. Thứ hai, mọi kết quả phải tái lập được từ dữ liệu và mã nguồn đã lưu. Thứ ba, các trạng thái thiếu dữ liệu, bị dừng hoặc thất bại được lưu và báo cáo, không bị loại bỏ.

### 4.1.2. Kiến trúc tổng thể

Hình 4.1 trình bày kiến trúc của nguyên mẫu, gồm sáu lớp:

- **Lớp dữ liệu chuẩn hóa:** các bảng nguồn, nhật ký sự kiện, thông tin bối cảnh và cờ chất lượng (Chương 3).
- **Lớp phát triển và lựa chọn phương pháp:** fit trên train, chọn trên validation, đánh giá trên test. Kết quả là các tài nguyên đã cố định: nhóm tham chiếu, công thức điểm, điều kiện đánh giá, cấu hình kèm mã băm và phiên bản.
- **Lớp phát lại và chấm công đoạn:** đọc sự kiện theo thời gian, cập nhật trạng thái, áp dụng bộ phát hiện đã cố định.
- **Lớp công cụ và bằng chứng:** cung cấp trạng thái quan sát, tính lại số liệu, kiểm tra ràng buộc thời gian và so sánh nhóm tương đồng.
- **Lớp giải thích:** bộ điều phối và ba tác tử, chịu ràng buộc về lượt gọi, token và ngân sách.
- **Lớp trình bày:** kết quả kèm dấu vết kiểm chứng, và giao diện BI local.

> [Vị trí chèn Hình 4.1]

**Hình 4.1.** Kiến trúc tổng thể của nguyên mẫu

Nguồn: Tác giả xây dựng.

Bộ điều phối là chương trình viết bằng code, không phải một tác tử thứ tư. Nguyên mẫu được viết bằng Python. Phần phát hiện dùng pandas, NumPy và scikit-learn (Pedregosa et al., 2011), phần khám phá quy trình dùng PM4Py (Berti et al., 2019), trạng thái và sổ ngân sách lưu bằng SQLite, giao diện BI là trang HTML tĩnh. Các tác tử gọi mô hình gpt-6-luna qua Responses API của OpenAI.

## 4.2. Pipeline xử lý và quản lý bằng chứng

### 4.2.1. Luồng xử lý

Luồng xử lý của nguyên mẫu đi theo trình tự: dữ liệu Olist gốc, chuẩn hóa và kiểm tra chất lượng, chọn ngành và chia tập theo thời gian, xây dựng tham chiếu và so sánh phương pháp, chấm công đoạn theo mốc quan sát, truy xuất bằng chứng, giải thích bằng tác tử, rồi kiểm chứng và công bố. Mỗi bước đọc đầu ra đã lưu của bước trước, kiểm tra mã băm, rồi ghi kết quả vào một thư mục mới. Thư mục kết quả đã tồn tại không bị ghi đè; khi thay đổi phương pháp, bước đó được chạy thành một phiên bản mới.

### 4.2.2. Mã bằng chứng và truy vết

Mỗi kết quả do công cụ trả về là một bản ghi bằng chứng (evidence record). Bản ghi gồm loại bằng chứng, công đoạn, dữ liệu, chữ ký nguồn và các giới hạn đi kèm. Mã bằng chứng là giá trị băm SHA-256 của nội dung bản ghi. Vì vậy, cùng một nội dung luôn có cùng mã, và mọi thay đổi nội dung đều tạo ra mã khác.

Tác tử không tự viết số liệu. Từ mỗi bản ghi bằng chứng, chương trình tạo một danh mục nhận định (claim catalog), trong đó mỗi mục ứng với một trường dữ liệu cụ thể của bản ghi. Tác tử chỉ chọn mã mục trong danh mục. Giá trị hiển thị cho người dùng được chép trực tiếp từ bản ghi bằng chứng, không do mô hình sinh ra. Tương tự, ghi chú về giới hạn và đề xuất kiểm tra được chọn từ danh mục siêu dữ liệu (metadata catalog). Danh mục này chỉ tạo ra mục khi bằng chứng đáp ứng điều kiện tương ứng, ví dụ chỉ có ghi chú "không đủ đơn tương đồng" khi phép so sánh nhóm thực sự thiếu mẫu.

Bảng 4.2 trình bày các hồ sơ được lưu để kiểm chứng và tái lập.

**Bảng 4.2.** Các hồ sơ phục vụ truy vết và tái lập

| Hồ sơ | Nội dung | Mục đích |
| --- | --- | --- |
| manifest.json | Mã băm của dữ liệu đầu vào, mã nguồn và kết quả | Phát hiện mọi thay đổi so với lần chạy gốc |
| Hồ sơ đối chiếu dữ liệu | Vị trí bản ghi nguồn, lý do và hành động | Truy vết từng thao tác chuẩn hóa |
| plan.json | Phạm vi, giới hạn, giá và mã băm mã nguồn trước khi chạy | Khai báo trước khi gọi API |
| requests.jsonl | Toàn bộ yêu cầu gửi tới mô hình | Phát lại và kiểm tra nội dung đã gửi |
| Biên nhận | Phản hồi gốc, số token và chi phí của từng yêu cầu | Đối chiếu chi phí và đầu ra |
| Bản ghi bằng chứng | Dữ liệu công cụ trả về kèm mã bằng chứng | Kiểm tra từng nhận định được công bố |

Nguồn: Tác giả xây dựng.

Nhờ các hồ sơ này, mỗi đợt chạy AI có thể được phát lại hoàn toàn offline. Chương trình đọc lại các yêu cầu và phản hồi đã lưu, chạy lại toàn bộ luồng, rồi so sánh từng trường kết quả với bản đã lưu mà không gọi API mới.

## 4.3. Phát lại sự kiện và quản lý trạng thái đơn hàng

### 4.3.1. Hợp đồng thời gian

Bộ phát lại đọc các mốc thật của Olist theo thứ tự thời gian. Các sự kiện được sắp xếp theo thời điểm ghi nhận, sau đó theo mã đơn hàng, rồi theo thứ tự hoạt động. Thứ tự hoạt động chỉ dùng để giữ thứ tự ổn định khi hai mốc trùng thời điểm, không phải bằng chứng về quan hệ nhân quả. Phát lại không mô phỏng khoảng chờ thực tế giữa các sự kiện, mà giả định mỗi sự kiện có sẵn ngay tại thời điểm được ghi nhận, vì Olist không cung cấp độ trễ thu nhận thực tế.

Phạm vi phát lại là tập validation: 1.853 đơn với 7.076 mốc trong khoảng [01/03/2018, 01/06/2018). Tại mốc cắt, bộ phát lại dừng mà không tạo sự kiện đóng, hủy hay giao hàng giả. Một mốc chưa quan sát có thể là bị thiếu hoặc chỉ là xảy ra muộn hơn; bộ xử lý không phân biệt được hai trường hợp này và không được biết về sự kiện tương lai. Mỗi sự kiện gửi đi chỉ gồm phiên bản lược đồ, mã sự kiện, mã đơn, hoạt động và thời điểm. Trạng thái cuối của đơn, ngày giao dự kiến, thanh toán và đánh giá không được đưa vào luồng sự kiện.

### 4.3.2. Trạng thái đơn và chấm công đoạn

Mỗi sự kiện chỉ cập nhật trạng thái của đơn tương ứng. Trạng thái ghi nhận các mốc đã quan sát và chưa quan sát, các cặp mốc đảo thứ tự, các cặp trùng thời điểm và ba thời lượng công đoạn. Sau mỗi sự kiện, ba công đoạn của đơn được đánh giá lại theo các điều kiện sau:

- Công đoạn chỉ được chấm khi đã quan sát được cả hai mốc đầu, cuối và thời lượng dương. Hai công đoạn đầu không cần chờ mốc giao hàng.
- Nếu đơn có bất kỳ cặp mốc đảo thứ tự nào đang hiển thị, mọi công đoạn của đơn đều tạm dừng đánh giá.
- Mốc thiếu là chưa biết, không phải 0. Thời lượng âm được giữ như bằng chứng mâu thuẫn, không dùng để tính điểm.

Khi dữ liệu mới làm lộ ra mâu thuẫn, điểm hiện hành của đơn có thể bị rút lại, nhưng lịch sử thay đổi vẫn được lưu.

### 4.3.3. Hai kịch bản về thông tin bối cảnh

Như đã nêu ở mục 3.4.3, Olist không ghi thời điểm thông tin sản phẩm, người bán và địa lý được biết đến. Vì vậy, bộ chấm luôn tạo hai kịch bản riêng và không gộp kết quả:

- **Chỉ dùng bối cảnh đã xác minh:** không dùng thông tin bối cảnh chưa xác minh được thời điểm có sẵn. Ở kịch bản này, không công đoạn nào chấm được điểm.
- **Giả định bối cảnh có sẵn từ lúc đặt hàng:** giả định thông tin trong bản dữ liệu đã có và không đổi kể từ mốc đặt hàng. Kết quả chấm được 4.865 công đoạn của 1.726 đơn. Có 225 công đoạn cảnh báo, thuộc 221 đơn.

Mọi kết quả của kịch bản thứ hai đều được gắn nhãn là dựa trên giả định. Kết quả "0 công đoạn có điểm" ở kịch bản thứ nhất không có nghĩa là các đơn bình thường, mà là chưa chứng minh được điều kiện sử dụng dữ liệu. Hình 4.2 trình bày luồng phát lại và chấm công đoạn.

> [Vị trí chèn Hình 4.2]

**Hình 4.2.** Luồng phát lại sự kiện và chấm công đoạn

Nguồn: Tác giả xây dựng.

### 4.3.4. Lưu trữ và phục hồi

Trạng thái được lưu bằng SQLite. Mỗi sự kiện và trạng thái cập nhật được ghi trong cùng một giao dịch, nên không có trường hợp ghi được sự kiện mà thiếu trạng thái hoặc ngược lại. Khi chạy lại cùng khoảng thời gian, các sự kiện đã xử lý được nhận diện qua mã sự kiện và không làm phát sinh thêm cập nhật. Cơ chế này giúp hệ thống phục hồi từ điểm lưu (checkpoint) mà không xử lý trùng. Sự kiện có phiên bản không khớp, bản ghi mâu thuẫn hoặc trường lạ đều bị từ chối.

## 4.4. Thiết kế các tác tử

### 4.4.1. Vai trò, công cụ và đầu ra

Hệ thống gồm ba tác tử với vai trò, công cụ và quyền truy cập bằng chứng khác nhau, như Bảng 4.3.

**Bảng 4.3.** Vai trò, công cụ và đầu ra của ba tác tử

| Tác tử | Nhiệm vụ | Công cụ được phép | Đầu ra |
| --- | --- | --- | --- |
| Process Analyst | Phân tích thời lượng, ngưỡng và trạng thái từng công đoạn | get_observed_state, get_frozen_stage_assessment, get_reference_membership | Nhận định, ghi chú và đề xuất kiểm tra về công đoạn |
| Context Analyst | Đối chiếu bối cảnh và nhóm tương đồng, giữ phần chưa biết | get_available_context, compare_declared_peer_groups, get_group_support_and_coverage | Nhận định, ghi chú và đề xuất kiểm tra về bối cảnh |
| Evidence Verifier | Kiểm chứng từng mục do hai tác tử trên đề xuất | resolve_evidence, recompute_reported_metrics, check_temporal_and_comparison_contract | Quyết định chấp nhận, yêu cầu sửa hoặc loại bỏ cho từng mục |

Nguồn: Tác giả xây dựng.

Mỗi tác tử chỉ được gọi các công cụ của vai trò mình và chỉ thấy bằng chứng đã được cấp hoặc tự truy xuất. Context Analyst nhận thêm danh sách nhận định của Process Analyst và các công đoạn đang có cảnh báo. Các phép so sánh nhóm phải theo một trong các kế hoạch đã khai báo trước, như cùng sản phẩm hoặc cùng ngành, để tác tử không tự tạo nhóm so sánh tùy ý.

### 4.4.2. Ràng buộc đầu ra

Đầu ra của mỗi tác tử là một lời gọi hàm có lược đồ chặt (strict function schema). Process Analyst và Context Analyst nộp kết quả qua hàm submit_analysis, gồm ba danh sách mã: claim_ids, unknown_ids và check_ids. Evidence Verifier nộp qua hàm submit_verdict, gồm quyết định accept, revise hoặc reject kèm lý do cho từng mục. Phản hồi không đúng lược đồ, gọi công cụ ngoài vai trò hoặc chọn mã không có trong danh mục đều bị từ chối, và tác tử được yêu cầu sửa trong giới hạn lượt còn lại.

Chỉ dẫn hệ thống yêu cầu các tác tử không khẳng định nguyên nhân, không tạo nhãn và không thay đổi kết quả phát hiện. Vì đầu ra chỉ gồm các mã chọn từ danh mục, ràng buộc này còn được bảo đảm bằng cấu trúc của hệ thống, không chỉ dựa vào chỉ dẫn.

### 4.4.3. Phương án đối chứng

Để đánh giá giá trị của việc phối hợp nhiều tác tử, nguyên mẫu triển khai thêm hai phương án dùng chung bộ công cụ, dữ liệu và cổng kiểm tra:

- **Quy tắc cố định:** chọn nhận định theo thứ tự ưu tiên định trước, không gọi mô hình.
- **Một tác tử:** một mô hình được dùng mọi công cụ, tự chọn nhận định cho cả hai phần quy trình và bối cảnh, không có Evidence Verifier.

## 4.5. Cơ chế phối hợp và kiểm chứng

### 4.5.1. Trình tự phối hợp

Bộ điều phối thực hiện một trình tự cố định, như Hình 4.3:

1. Kiểm tra trước khi gọi AI: ràng buộc thời gian, đơn đang xét không thuộc tập train, và số liệu cố định tính lại phải khớp. Nếu không đạt, dừng với trạng thái không đủ kết luận.
2. Process Analyst phân tích, tối đa 3 lượt gọi, lượt cuối buộc phải nộp kết quả.
3. Context Analyst phân tích với kết quả của Process Analyst, tối đa 3 lượt gọi.
4. Mọi mục đề xuất qua cổng kiểm tra bằng chương trình (code gate), đối chiếu giá trị với bản ghi bằng chứng.
5. Evidence Verifier đưa ra quyết định cho từng mục.
6. Nếu có mục cần sửa, chạy tối đa một vòng sửa: tác tử liên quan sửa, rồi Verifier chấm lại.
7. Chỉ công bố mục vừa qua cổng kiểm tra vừa được Verifier chấp nhận. Mục bị loại được lưu riêng.

> [Vị trí chèn Hình 4.3]

**Hình 4.3.** Trình tự phối hợp ba tác tử và kiểm chứng đầu ra

Nguồn: Tác giả xây dựng.

### 4.5.2. Kiểm tra độ đầy đủ cảnh báo

Trong các lần chạy thử, có trường hợp giải thích hợp lệ nhưng bỏ sót công đoạn đang có cảnh báo. Vì vậy, sau khi công bố, chương trình kiểm tra xem mọi công đoạn có cảnh báo đã được nêu bằng một nhận định đúng nguồn hay chưa. Nếu còn thiếu và còn lượt gọi, hệ thống chạy một vòng bổ sung: Process Analyst bổ sung, sau đó Evidence Verifier kiểm chứng lại. Nếu vòng bổ sung không thành công, đầu ra trước đó được giữ nguyên với trạng thái chưa nêu đủ cảnh báo, để người dùng thấy rõ phần còn thiếu.

### 4.5.3. Trạng thái đầu ra

Mỗi lượt giải thích kết thúc ở một trong các trạng thái trong Bảng 4.4. Trạng thái của lớp giải thích không làm thay đổi kết quả phát hiện. Khi giải thích thất bại, điểm và cảnh báo của công đoạn vẫn được giữ.

**Bảng 4.4.** Các trạng thái đầu ra của lớp giải thích

| Trạng thái | Ý nghĩa |
| --- | --- |
| evidence_checked | Có mục được công bố, đã qua cổng kiểm tra và Evidence Verifier |
| evidence_checked_with_rejections | Như trên, có mục bị loại và được lưu riêng |
| code_checked | Có mục được công bố, chỉ qua cổng kiểm tra bằng chương trình (quy tắc cố định, một tác tử) |
| incomplete_warning_coverage | Có đầu ra nhưng chưa nêu đủ công đoạn có cảnh báo |
| inconclusive | Không đủ kết luận: vi phạm ràng buộc hoặc không có mục nào được chấp nhận |
| unavailable_budget | Dừng do giới hạn lượt gọi, token hoặc ngân sách |
| unavailable_api | Dừng do API không khả dụng |

Nguồn: Tác giả xây dựng.

## 4.6. Quản lý API, token, chi phí và lỗi

### 4.6.1. Sổ ngân sách

Mọi lời gọi mô hình đi qua một provider có kiểm soát và một sổ ngân sách lưu bằng SQLite. Quy trình cho mỗi yêu cầu như sau:

1. Kiểm tra hợp đồng yêu cầu: chỉ dùng hàm cục bộ có lược đồ chặt, không dùng công cụ trả phí của nhà cung cấp, không lưu hội thoại phía nhà cung cấp, mức suy luận cố định.
2. Giữ chỗ ngân sách theo chi phí xấu nhất: số token đầu vào tối đa nhân giá đầu vào cao nhất, cộng số token đầu ra tối đa nhân giá đầu ra.
3. Đếm token chính thức của yêu cầu. Nếu vượt trần, yêu cầu bị hủy trước khi gửi suy luận và khoản giữ chỗ được giải phóng.
4. Đánh dấu "đã gửi" trước khi gửi, rồi gửi yêu cầu.
5. Lưu biên nhận phản hồi, sau đó quyết toán theo số token thực tế.

Yêu cầu đã gửi nhưng không có biên nhận vẫn giữ khoản giữ chỗ và không bao giờ được gửi lại, vì không thể chứng minh nó chưa phát sinh chi phí. Nếu chi phí thực tế vượt khoản giữ chỗ, sổ ngân sách bị khóa và không nhận yêu cầu mới. Hệ thống không tự động gửi lại khi lỗi, không đổi sang mô hình khác và không chọn phản hồi tốt nhất trong nhiều lần gọi.

### 4.6.2. Các giới hạn

Bảng 4.5 trình bày các giới hạn được áp dụng.

**Bảng 4.5.** Các giới hạn tài nguyên của lớp giải thích

| Giới hạn | Giá trị |
| --- | --- |
| Token đầu vào mỗi yêu cầu | 6.000 (chính sách v1); 12.000 (chính sách v2) |
| Token đầu ra mỗi yêu cầu | 1.200 |
| Số lượt gọi mỗi lượt giải thích | Ba tác tử: 12 (native) hoặc 4 (matched); một tác tử: 4 |
| Số lượt gọi mỗi vai trò | 4, gồm cả lượt sửa và bổ sung |
| Số case phân biệt | 20 |
| Hạn mức chi phí | 5 USD (sổ v1); 2 USD (sổ v2) |

Nguồn: Tác giả xây dựng.

Chính sách v1 đặt trần 6.000 token đầu vào. Ở đợt đối chứng đầu tiên, nhiều lượt bị dừng vì yêu cầu của Evidence Verifier vượt trần (Chương 5). Do đó, chính sách v2 nâng trần lên 12.000 token. Sổ ngân sách được thiết kế để từ chối mọi thay đổi chính sách trên cùng một sổ, nhằm tránh xóa số liệu chi tiêu đã tích lũy. Vì vậy, chính sách v2 dùng một sổ ngân sách riêng, còn sổ v1 giữ nguyên. Chi phí của toàn dự án là tổng của hai sổ.

### 4.6.3. Quản lý vòng gọi công cụ

Ngoài giới hạn token, nguyên mẫu có một lớp quản lý vòng gọi công cụ, đặt giữa bộ điều phối và provider. Lớp này buộc tác tử nộp kết quả, bằng cách chỉ cung cấp hàm nộp kết quả, khi rơi vào một trong các trường hợp sau:

- Tác tử gọi lại một công cụ với đúng tham số đã gọi.
- Lần gọi công cụ trước không đem lại bằng chứng mới.
- Cần giữ lượt cho các vai trò phía sau trong giới hạn lượt gọi.
- Yêu cầu bị từ chối trước suy luận vì vượt trần token. Khi đó, yêu cầu được gửi lại đúng một lần dưới dạng yêu cầu nộp kết quả, với mã yêu cầu mới.

Lớp này không sửa bằng chứng, danh mục hay kết quả phát hiện, và mọi quyết định của nó đều được ghi lại.

### 4.6.4. Lượt bị gián đoạn kỹ thuật

Theo quy tắc chung của mọi đợt chạy, một lượt bị gián đoạn do lỗi kỹ thuật không liên quan đến phương án được chạy lại một lần với mã lượt chạy riêng. Lượt gốc vẫn được lưu để kiểm chứng.

## 4.7. Giao diện BI local và tình huống sử dụng

### 4.7.1. Thiết kế luồng khai thác

Giao diện BI được thiết kế theo luồng đi từ tổng quát đến chi tiết (Hình 4.4): tổng quan quản lý, nhóm cần kiểm tra, danh sách đơn, rồi bằng chứng của từng đơn. Ở mọi cấp, các tỷ lệ đều kèm mẫu số và số đơn chưa đánh giá được. Khi so sánh theo người bán, giao diện chỉ tính các đơn có một người bán để không quy một đơn cho nhiều người bán. Nhóm dưới 30 đơn đánh giá được chỉ được đánh dấu để tham khảo.

> [Vị trí chèn Hình 4.4]

**Hình 4.4.** Luồng khai thác của giao diện BI

Nguồn: Tác giả xây dựng.

### 4.7.2. Triển khai

Giao diện là một trang HTML tĩnh, được dựng bằng script Python từ các kết quả đã lưu. Trang mở trực tiếp bằng trình duyệt, không cần cài đặt hay kết nối mạng, không gọi mô hình và không chấm lại điểm. Giao diện có hai tập dữ liệu:

- **Test hồi cứu (06–08/2018):** 1.691 đơn được chấm bằng ứng viên đã cố định.
- **Validation phát lại sự kiện (03–05/2018):** 1.853 đơn ở kịch bản giả định bối cảnh. Các mốc sau mốc cắt bị ẩn để không lộ thông tin tương lai.

Giao diện có thanh điều hướng bên trái với sáu màn hình: Tổng quan, Công đoạn, Nhóm cần xem xét, Danh sách đơn, Giải thích AI, và Cách đọc và giới hạn. Phía trên có ô tìm mã đơn và các bộ lọc theo tháng, công đoạn, bang khách hàng và trạng thái giao hàng. Bộ lọc áp dụng chung cho mọi màn hình.

Màn hình tổng quan (Hình 4.5) gồm bốn thẻ chỉ số: đơn đánh giá được, đơn có cảnh báo, đơn giao sớm hoặc đúng hẹn có cảnh báo, và đơn trễ hẹn. Mỗi thẻ có biểu đồ nhỏ theo tháng và mẫu số đi kèm. Bên dưới là biểu đồ diễn biến theo tháng, có thể chuyển giữa số đơn cảnh báo, tỷ lệ cảnh báo và số đơn trễ hẹn. Ngoài ra còn biểu đồ tròn về trạng thái đánh giá, thanh tỷ lệ cảnh báo theo công đoạn và bảng chéo giữa cảnh báo công đoạn với kết quả giao hàng. Bảng chéo cho thấy hai tiêu chí khác nhau. Ví dụ, trên tập test có 58 đơn giao sớm vẫn có cảnh báo công đoạn. Người dùng bấm vào một ô của bảng chéo hoặc một dòng của bảng nhóm để mở danh sách đơn tương ứng. Màn hình Công đoạn cho biết tỷ lệ dùng từng loại nhóm tham chiếu. Trên tập validation, 97,8% công đoạn đánh giá được dùng nhóm dự phòng cấp ngành.

> [Vị trí chèn Hình 4.5]

**Hình 4.5.** Màn hình tổng quan của giao diện BI local

Nguồn: Tác giả xây dựng từ kết quả thực nghiệm trên dữ liệu Olist.

### 4.7.3. Tình huống sử dụng

Một người quản lý vận hành có thể dùng giao diện theo bốn bước:

1. Xem tổng quan để biết số đơn có cảnh báo và công đoạn nào có nhiều cảnh báo nhất.
2. Chọn một bang hoặc người bán có tỷ lệ cảnh báo cao, kèm theo số đơn của nhóm.
3. Mở danh sách đơn của nhóm, sắp xếp theo điểm cao nhất.
4. Mở chi tiết một đơn trong ngăn bên phải để xem bằng chứng (Hình 4.6): các mốc thời gian, thời lượng từng công đoạn so với nhóm tham chiếu, điểm và mức cảnh báo, trạng thái giao hàng, và giải thích của hệ ba tác tử nếu có.

Phần giải thích chỉ có cho 20 đơn đã chạy qua lớp AI trong đợt đối chứng. Lớp AI chưa được gọi cho mọi đơn trong luồng phát lại. Các đề xuất kiểm tra trong giải thích, như xem xét tuyến vận chuyển, là hướng xác minh tiếp theo có căn cứ, không phải kết luận về nguyên nhân.

> [Vị trí chèn Hình 4.6]

**Hình 4.6.** Màn hình chi tiết bằng chứng của một đơn hàng

Nguồn: Tác giả xây dựng từ kết quả thực nghiệm trên dữ liệu Olist.

Nguyên mẫu hiện có giới hạn sau: giao diện chưa kết nối trực tiếp với luồng phát lại để tự cập nhật theo thời gian thực, và chưa có chức năng ghi nhận kết quả kiểm tra của người quản lý. Đây là phần cần phát triển tiếp nếu triển khai trong môi trường vận hành.

Tóm lại, Chương 4 đã trình bày kiến trúc, pipeline quản lý bằng chứng, cơ chế phát lại sự kiện, thiết kế và cơ chế phối hợp của ba tác tử, cách quản lý tài nguyên API và giao diện BI local. Chương 5 trình bày thiết kế thực nghiệm và kết quả đánh giá.

@@REFS

Berti, A., van Zelst, S. J., & van der Aalst, W. M. P. (2019). *Process mining for Python (PM4Py): Bridging the gap between process- and data science* (arXiv:1905.06169). arXiv. https://doi.org/10.48550/arXiv.1905.06169

Pedregosa, F., Varoquaux, G., Gramfort, A., Michel, V., Thirion, B., Grisel, O., Blondel, M., Prettenhofer, P., Weiss, R., Dubourg, V., Vanderplas, J., Passos, A., Cournapeau, D., Brucher, M., Perrot, M., & Duchesnay, É. (2011). Scikit-learn: Machine learning in Python. *Journal of Machine Learning Research, 12*, 2825–2830.

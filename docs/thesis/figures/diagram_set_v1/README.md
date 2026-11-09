# Bộ 11 sơ đồ tiếng Việt cho khóa luận

Ngày lập: 09/11/2026. Trạng thái: **bản vẽ chờ người dùng duyệt nội dung**. Các sơ đồ phản ánh code, hồ sơ và kết quả hiện có; không phải thực nghiệm mới. Chưa dịch sang tiếng Anh và chưa gán số hình chính thức theo chương.

Mở [thư viện xem toàn bộ hình](index.html) trong trình duyệt hoặc xem [ảnh tổng quan](contact_sheet.vi.png). Trang HTML chỉ là thư viện hình tĩnh, không phải dashboard của đề tài.

## Các sơ đồ và vị trí đề xuất

Các số 01–11 dưới đây là mã file, không phải số hình của báo cáo.

| Mã | Vị trí | Nội dung và chú thích đề xuất | Bản xem |
| --- | --- | --- | --- |
| 01 | Chương 1 | Quy trình nghiên cứu của đề tài, phân biệt kết quả đã triển khai và phần dự kiến. | [SVG](01_research_overview.vi.svg) · [PNG](01_research_overview.vi.png) |
| 02 | Chương 2 | Khung khái niệm liên kết biểu diễn quy trình, phát hiện bất thường, Multi-Agent và BI. | [SVG](02_conceptual_framework.vi.svg) · [PNG](02_conceptual_framework.vi.png) |
| 03 | Chương 3 | Quan hệ giữa chín bảng nguồn Olist và lớp tham chiếu địa lý dẫn xuất. | [SVG](03_olist_data_relationships.vi.svg) · [PNG](03_olist_data_relationships.vi.png) |
| 04 | Chương 3 | Ánh xạ bốn mốc Olist thành ba công đoạn; ngày giao dự kiến được đối chiếu độc lập. | [SVG](04_order_milestones_and_stages.vi.svg) · [PNG](04_order_milestones_and_stages.vi.png) |
| 05 | Chương 3 | Luồng chuẩn hóa, kiểm tra chất lượng, chọn phạm vi ngành hàng và phân chia theo thời gian. | [SVG](05_data_preparation_and_split.vi.svg) · [PNG](05_data_preparation_and_split.vi.png) |
| 06 | Chương 3 | Lựa chọn nhóm tham chiếu, tính chênh lệch và điểm; tách biệt điều kiện đánh giá với trễ hẹn. | [SVG](06_reference_and_warning.vi.svg) · [PNG](06_reference_and_warning.vi.png) |
| 07 | Chương 4 | Kiến trúc các thành phần đã triển khai và lớp BI local dự kiến. | [SVG](07_system_architecture.vi.svg) · [PNG](07_system_architecture.vi.png) |
| 08 | Chương 4 | Luồng ba agent với kiểm tra bằng code, vòng sửa nhận định và vòng bổ sung cảnh báo bị bỏ sót. | [SVG](08_multi_agent_coordination.vi.svg) · [PNG](08_multi_agent_coordination.vi.png) |
| 09 | Chương 4 | Phát lại sự kiện lịch sử với hai kịch bản về thời điểm có sẵn của thông tin bối cảnh. | [SVG](09_event_replay.vi.svg) · [PNG](09_event_replay.vi.png) |
| 10 | Chương 4 | Thiết kế luồng BI từ tổng quan đến nhóm, đơn hàng và bằng chứng phục vụ kiểm tra. | [SVG](10_bi_drilldown_design.vi.svg) · [PNG](10_bi_drilldown_design.vi.png) |
| 11 | Chương 5 | Hai nhánh đánh giá phương pháp phát hiện và đóng góp của Multi-Agent, với giới hạn của từng loại bằng chứng. | [SVG](11_experiment_design.vi.svg) · [PNG](11_experiment_design.vi.png) |

Nguồn chú thích chung: **Tác giả xây dựng từ thiết kế, mã nguồn và hồ sơ thực nghiệm của đề tài KLTN-Lon2G.** Sơ đồ quan hệ dữ liệu được đối chiếu với cấu trúc nguồn Olist trong snapshot chuẩn hóa. Khi chèn vào báo cáo, vẫn cần trích dẫn bộ dữ liệu Olist và tài liệu lý thuyết liên quan tại phần nội dung; bảng nguồn kỹ thuật không thay thế trích dẫn APA.

## Quy ước đọc và phạm vi bằng chứng

- Khung xanh/nét liền: thành phần đã có triển khai hoặc kết quả, không có nghĩa toàn bộ hệ thống đã hoàn thiện.
- Khung vàng/nét đứt: phần dự kiến/chưa hoàn tất, đặc biệt BI và đánh giá định tính/Kappa.
- Khung đỏ: không đủ điều kiện đánh giá hoặc trạng thái không có giải thích khả dụng; không phải nhãn gian lận.
- Khung xám: ghi chú, hạn chế hoặc ràng buộc; khung xanh lá: đầu ra.
- Sơ đồ 02 là khung khái niệm, không phải thuật toán khám phá quy trình đã được thực nghiệm hoặc chứng minh quan hệ nhân quả.
- Sơ đồ 03 là sơ đồ các quan hệ phục vụ phân tích, không phải lược đồ SQL tuyên bố mọi ràng buộc khóa đã được áp đặt. Tên bảng được rút gọn từ tên CSV Olist. Nhiều dòng items/payments/reviews cần tổng hợp đúng cấp để tránh nhân bản đơn. ZIP không duy nhất ở bảng tọa độ nguồn.
- Sơ đồ 04: ba thời lượng thuộc cấp đơn, không phải thời gian nội tại từng item. Không suy diễn cơ sở thiết lập ngày hẹn.
- Sơ đồ 05: dùng phân chia đang được các thực nghiệm sử dụng: 5.480/1.853/1.691 đơn. File kế hoạch 80/20 có ghi lại các số này trong `old_split_counts`; phần chia 80/20 đề xuất trong file đó **chưa được kích hoạt**, không được nhầm với số trên hình. Số đơn đủ điều kiện còn phụ thuộc từng phép đánh giá; test đã được xem kết quả.
- Sơ đồ 06: nhóm sản phẩm được thử theo thứ tự điều kiện có trong code; khi thiếu hỗ trợ, dùng nhóm dự phòng ngành theo điều kiện sẵn có. Tối thiểu 30 đơn là quy ước, không phải chứng minh thống kê. Giữ độc lập điểm/cờ công đoạn, chênh lệch thời gian, độ bao phủ và trạng thái trễ hẹn. `clip(x; 0; 1)` chặn x trong [0, 1].
- Sơ đồ 07: mô tả thành phần và quan hệ dữ liệu, không khẳng định luồng replay → AI → dashboard đã chạy đầu-cuối trong sản xuất.
- Sơ đồ 08: bộ điều phối là code; ba agent không trao đổi tự do. Code gate kiểm tra cả nhận định, ghi chú và đề xuất. Vòng sửa nhận định trong coordinator và vòng bổ sung bao phủ trong review_v2 là hai cơ chế riêng, dùng chung giới hạn tài nguyên. Các đường ngoài dẫn đến dải trạng thái dừng chỉ là cách rút gọn nhánh lỗi có thể phát sinh ở bước tương ứng; không giới hạn lỗi API/ngân sách vào riêng Verifier. Phần sửa bao phủ thất bại giữ đầu ra trước đó và trạng thái chưa đầy đủ. Mục “không đủ kết luận” cũng bao gồm trường hợp không có mục nào được chấp nhận. Kết quả detector không bị xóa khi giải thích thất bại.
- Sơ đồ 09: 7.076 sự kiện/1.853 đơn là phạm vi phát lại. Chỉ kịch bản giả định bối cảnh có sẵn từ khi đặt mới chấm được 4.865 công đoạn/1.726 đơn. Không chấm thời lượng của công đoạn đang diễn ra, không gộp hai kịch bản, không dùng tương lai để dựng giả đầu vào tại thời điểm xét.
- Sơ đồ 10: toàn bộ tương tác dashboard là thiết kế dự kiến; hiện có các bảng đầu vào và hồ sơ bằng chứng, chưa có dashboard local hoàn chỉnh.
- Sơ đồ 11: bộ so sánh đã chuẩn bị còn 80 lượt AI chờ chạy; các pilot riêng đã có kết quả nhưng chưa đủ chọn phương án AI tốt nhất. Chỉ số đối chiếu giao trễ hẹn không phải độ chính xác tắc nghẽn được xác nhận. Chưa có kết quả chấm độc lập/Kappa; việc chọn loại Kappa phụ thuộc thiết kế chấm thực tế.

## Bản draw.io để chỉnh sửa (từ 09/11/2026)

Thư mục [drawio/](drawio/) chứa 11 file `.drawio`, mỗi hình một file, cùng tên gốc với PNG/SVG. Đây là bản nên dùng khi chỉnh tay: khung, đường nối, nhãn mũi tên, tiêu đề và ghi chú đều là đối tượng sửa được; đường nối gắn với khung nên kéo khung thì mũi tên đi theo. Mở bằng draw.io desktop, app.diagrams.net hoặc extension VS Code "Draw.io Integration"; xuất PNG 300 dpi hoặc SVG từ draw.io khi chèn vào Word.

File `.drawio` được tạo từ `.layout.json` bằng [export_drawio.py](export_drawio.py) và đã xem thử bằng trình xem chính thức của draw.io. **Sau khi đã sửa tay file `.drawio`, không chạy lại `export_drawio.py`**, vì script sẽ ghi đè bản sửa tay. Khi đó, file `.drawio` trở thành nguồn chính, và PNG/SVG cần xuất lại từ draw.io.

Thay đổi so với bản đầu cùng ngày:
- 03: thêm quan hệ 1 – 1 / 1 – n trên mọi liên kết (đã kiểm tra trên `data/raw`: mỗi `customer_id` một đơn; `customer_unique_id` tối đa 17 đơn; tối đa 29 dòng thanh toán/đơn; `review_id` có thể gắn tối đa 3 đơn).
- 06: hiện đủ nhãn "Không đạt", "Không đủ nhóm sản phẩm", "Đạt nhóm sản phẩm"; đường vào "Chưa đánh giá được" đi thẳng.
- 08: chuyển sang bố cục dọc khổ A4, luồng chính bên trái, nhánh dừng/sửa/bổ sung bên phải; nội dung không đổi.

## File chỉnh sửa và cách dựng lại

Mỗi sơ đồ có bốn file cùng tên gốc:

- `.png`: ảnh 300 dpi để chèn vào Word.
- `.svg`: vector giữ sắc nét; chữ chuyển thành đường nét để không lỗi font tiếng Việt trên máy khác.
- `.mmd`: Mermaid chỉnh sửa được, mô tả nội dung và quan hệ. Mermaid tự sắp bố cục nên không giống hoàn toàn bản xuất thủ công.
- `.layout.json`: bản lưu nội dung, tọa độ, đường nối và nguồn của hình.

Nguồn chính của bộ ảnh là [render_diagrams.py](render_diagrams.py). Để sửa ảnh đã xuất, sửa hàm `definitions()` trong script rồi chạy lại. Sửa riêng `.mmd` hoặc `.layout.json` không tự cập nhật ảnh PNG/SVG.

Chạy từ thư mục gốc project:

```sh
.venv/bin/python -B docs/thesis/figures/diagram_set_v1/render_diagrams.py
```

Script dùng Matplotlib và Pillow trong môi trường hiện có. Font chính là Times New Roman, dự phòng DejaVu Serif nếu không có; mọi ký tự phải có trong font trước khi xuất. Script kiểm tra chữ nằm trong khung, không đè chữ khác, không tràn canvas và đường nối không xuyên khối không liên quan. Không chạy pipeline, không đọc key, không gọi API; chỉ ghi lại bộ hình trong thư mục này.

[manifest.json](manifest.json) lưu kích thước, nguồn đối chiếu và SHA-256 từng nguồn cùng phiên bản mã dựng hình. Khi code hoặc thực nghiệm thay đổi, cần rà soát lại tính đúng của hình trước khi dùng; chạy lại script không tự hiểu và cập nhật mọi thay đổi nghiệp vụ.

## Dùng trong báo cáo

Ưu tiên SVG nếu Word hỗ trợ; dùng PNG 300 dpi khi cần. Giữ nguyên tỷ lệ khung hình. Cả 11 sơ đồ có bố cục dọc, vừa bề rộng nội dung trang A4. Không ép toàn bộ sơ đồ phức tạp thành hình nhỏ khiến chữ khó đọc.

Chỉ gán số Hình 1.1, 3.1,... bằng caption của Word sau khi chốt vị trí. Chưa tự chèn bộ hình này vào Chương 1 hoặc tạo danh mục hình của báo cáo. Sơ đồ tổng quan cũ và bản Word hiện có được giữ nguyên để người dùng duyệt trước khi thay.

Bộ này gồm **11 sơ đồ**, chưa gồm các biểu đồ số liệu Chương 5. Những biểu đồ đó phải dựng từ kết quả thật ở bước được duyệt tiếp theo; không dùng số giả để lấp chỗ trống.

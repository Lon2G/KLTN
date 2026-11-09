# Bộ 11 sơ đồ nằm ngang — định dạng icon và số vàng xanh

Ngày dựng: 09/11/2026. Bản nháp chờ người dùng duyệt; giữ tiếng Việt. Dựa trên nội dung `diagram_set_v1`, bố cục và phong cách của graphical abstract được người dùng chọn. Không ghi đè bộ hình cũ, không sửa Word, dữ liệu hoặc chạy thực nghiệm/API.

## Mở để chỉnh sửa

**Mở `diagram_set_landscape_v2.vi.drawio` trong diagrams.net / draw.io.** File chứa 11 trang, chuyển trang bằng các tab bên dưới. Mỗi hình cũng có file riêng trong thư mục `drawio/`.

- Khung, chữ, số thứ tự, đường nối và nhãn điều kiện đều là đối tượng chỉnh sửa được; hình không bị gộp thành một ảnh nền.
- Chữ và icon nằm trong khung cha: di chuyển khung sẽ kéo các thành phần đi cùng. Mũi tên có `source`/`target` gắn với khung.
- Nhấp chọn thành phần bên trong để sửa chữ; nhấp đúp vào chữ để nhập nội dung. Khi thay bố cục lớn, cần kiểm tra lại các điểm gấp khúc và vị trí nhãn mũi tên.
- Icon là SVG vector nhúng, có thể di chuyển, thay hoặc đổi kích thước trong draw.io. Sửa đường nét chi tiết của icon trong mã SVG / `build_landscape.py`.
- Nền tổng quan được khóa để tránh kéo nhầm; các khung nội dung không bị khóa.
- Sau khi sửa tay, lưu thành tên riêng. Không chạy lại script dựng bộ hình lên bản đã sửa tay vì script sinh lại các file cùng tên.

Ảnh PNG có metadata 300 dpi, SVG giữ vector. Trang `index.html` là thư viện xem hình cục bộ, không phải dashboard nghiên cứu.

## Danh sách

| Trang | Nội dung | Chương đề xuất |
| --- | --- | --- |
| 01 | Quy trình nghiên cứu | 1 |
| 02 | Khung khái niệm | 2 |
| 03 | Quan hệ dữ liệu Olist | 3 |
| 04 | Bốn mốc và ba công đoạn | 3 |
| 05 | Chuẩn bị dữ liệu và chia tập | 3 |
| 06 | Nhóm tham chiếu và cảnh báo | 3 |
| 07 | Kiến trúc hệ thống | 4 |
| 08 | Phối hợp ba agent và kiểm chứng | 4 |
| 09 | Phát lại sự kiện | 4 |
| 10 | Luồng khai thác BI local | 4 |
| 11 | Thiết kế thực nghiệm | 5 |

Các số vàng là mã khối để trao đổi trong một hình; đặc biệt trong sơ đồ quan hệ dữ liệu/khung khái niệm, chúng không biểu thị thứ tự thực thi. Chưa gán số Hình 1.1, 3.1,... hoặc chèn vào khóa luận.

## Những gì được giữ và cập nhật

Toàn bộ nút và các cặp nối có hướng trong bộ v1 được giữ. Nhãn điều kiện, tên trường, công thức, số liệu chia tập và số liệu phát lại được giữ nguyên. Khung trắng/xanh nhạt dùng cho phần đã có, xanh lá cho đầu ra, vàng nét đứt cho phần chưa hoàn tất, đỏ nhạt cho nhánh chưa đủ điều kiện. Màu và nét vẫn có ý nghĩa nghiệp vụ; số vàng không thay thế quy ước này.

Một số ghi chú của bản v1 đã lỗi thời. Bản mới cập nhật theo hồ sơ và code ngày 09/11/2026, với thay đổi chi tiết ghi ở `manifest.json`:

- Hình 01, 02, 07 và 10 ghi nhận đã có dashboard local từ kết quả lưu sẵn; không khẳng định triển khai trực tuyến đầu-cuối hoặc hiệu quả quản trị đã được đánh giá.
- Hình 01 và 11 ghi nhận đợt đối chứng đã chạy 80 lượt AI trên 20 case phát triển. **80 lượt đã chạy không có nghĩa 80 lượt đều có kết luận thành công**, cũng không chứng minh Multi-Agent tốt hơn quy tắc/một agent.
- Hình 10 chỉ mô tả chức năng thực sự có trong bản local: bộ lọc hiện có, nhóm bang/seller đủ điều kiện, đơn, số liệu công đoạn, tham chiếu và giải thích đã lưu. Không giữ tuyên bố đã có bộ lọc đa ngành hoặc mọi chức năng của thiết kế cũ.
- Đánh giá định tính và Kappa vẫn chưa hoàn tất. Không tạo nhãn tắc nghẽn được xác nhận, không đổi phạm vi near real-time từ phát lại lịch sử sang vận hành thực tế.

Nguồn trạng thái: `docs/remaining_work_2026-11-09.md`, `docs/agent_comparison_v3_results_2026-11-09.md`, `scripts/06_dashboard/build_dashboard.py`, `scripts/06_dashboard/template.html`. Hash nguồn và hash từng layout v1 được lưu trong manifest.

## Dựng lại và kiểm tra

1. Chạy `build_landscape.py` với Python có Pillow và font Arial: sinh SVG, layout và file draw.io.
2. Chạy `rasterize.mjs` với Node có Sharp: sinh PNG và preview. Khi dùng runtime riêng, đặt `KLTN_NODE_PACKAGE_ROOT` thành đường dẫn `package.json` của runtime.
3. Chạy `package_and_validate.py`: kiểm tra file, lập thư viện, ảnh tổng quan và gói ZIP.

Nguồn dựng dùng icon vector của `../graphical_abstract_landscape_v1/render_graphical_abstract.py` và bổ sung icon nghiệp vụ. Icon biểu đồ chỉ là ký hiệu chức năng; không chứa số liệu thực nghiệm giả. Các kiểm tra gồm: giữ đồ thị gốc, đủ nút/đường nối, chữ nằm trong khung, đường không xuyên khung không liên quan, nhãn điều kiện không đè khung, XML hợp lệ và mũi tên gắn đúng khung.

Chú thích nguồn đề xuất: **Tác giả xây dựng từ mã nguồn và hồ sơ thực nghiệm của đề tài KLTN-Lon2G.** Đây là nguồn kỹ thuật cho hình; không thay thế trích dẫn dữ liệu Olist và tài liệu học thuật trong nội dung khóa luận.

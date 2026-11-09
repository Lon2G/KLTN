# Graphical abstract — bố cục ngang

Bản nháp tiếng Anh ngày 09/11/2026, được vẽ theo hình người dùng cung cấp: khung bo góc, số thứ tự vàng xanh, icon gắn với từng nghiệp vụ. Sáu khối chính bố trí từ trái sang phải; 21 ô chi tiết giữ cách trình bày của hình tham khảo.

## File sử dụng

| File | Mục đích |
| --- | --- |
| `graphical_abstract_landscape.en.png` | Ảnh 5.400 × 2.920 px, metadata 300 dpi; chèn vào Word hoặc slide |
| `graphical_abstract_landscape.en.svg` | Vector, chữ và icon có thể chỉnh sửa; phóng to không vỡ nét |
| `graphical_abstract_landscape.en.drawio` | Mở trong diagrams.net; mỗi ô được gom thành một group; chữ, nền, số và icon là các thành phần riêng |
| `graphical_abstract_landscape.en.preview.png` | Ảnh xem nhanh rộng 2.160 px |
| `render_graphical_abstract.py` | Nội dung, bố cục và mã icon vector gốc |
| `rasterize.mjs` | Xuất PNG từ SVG bằng Sharp |
| `graphical_abstract_landscape.en.sources.json` | Nguồn đối chiếu, hash, phạm vi kết luận |
| `graphical_abstract_landscape.en.layout.json` | Dữ liệu hình học để dựng lại/chỉnh sửa |

Các icon là nét vector tự vẽ, không sử dụng hình thương hiệu hoặc tài sản icon bên ngoài. Icon biểu đồ là ký hiệu chức năng, không biểu diễn kết quả thực nghiệm hay dữ liệu giả.

Trong diagrams.net, chữ chỉnh được bằng cách chọn thành phần trong group hoặc ungroup. Icon được nhúng SVG để giữ chất lượng; các đường nét chi tiết chỉnh được trong bản SVG nguồn. Mũi tên có các điểm gấp khúc cố định để tránh đè lên nội dung; nếu đổi bố cục, cần chỉnh lại các điểm nối.

## Nội dung và phạm vi

- Nguồn là dữ liệu Olist lịch sử, ngành `bed_bath_table`.
- Ba công đoạn: đặt hàng → duyệt; duyệt → bàn giao; bàn giao → giao hàng. Ngày giao dự kiến được đối chiếu riêng.
- Huấn luyện/xây tham chiếu bằng train, lựa chọn trên validation, cố định cấu hình trước đánh giá test. Đây là mô tả vai trò tập; không khẳng định test chưa từng được xem trong lịch sử nghiên cứu.
- Luồng hiện tại dùng detector/tham chiếu cố định; có nhóm dự phòng và trạng thái chưa đánh giá được. Điểm không phải xác suất.
- Phát lại lịch sử chấm công đoạn đã đủ mốc; chưa chứng minh triển khai trực tuyến tại doanh nghiệp.
- Bộ điều phối bằng code; ba agent là Process Analyst, Context Analyst, Evidence Verifier. Kiểm tra bằng code và vòng sửa có giới hạn được gom trong khối kiểm chứng để hình không quá chi tiết.
- Nguyên nhân tắc nghẽn chưa được xác nhận. “Verified Explanations” chỉ các nhận định qua cơ chế kiểm tra nguồn/quy tắc hiện có, không khẳng định tính đúng đắn nhân quả hay hiệu quả quản trị đã được chứng minh.
- Dashboard local trình bày kết quả đã lưu. Đường nối trên hình biểu thị quan hệ dữ liệu/chức năng, không phải bằng chứng đã chạy một dịch vụ tự động đầu-cuối.
- Hai đường nét đứt thể hiện sử dụng lại nhật ký sự kiện và chuyển kết quả cảnh báo đến BI kể cả khi lớp giải thích AI không khả dụng.

Không gọi API, không thay dữ liệu thí nghiệm, không sửa detector hoặc Chương 1. Bản gốc người dùng và các hình đã có được giữ nguyên. Chờ người dùng duyệt hình; chưa chèn vào bản thảo.

## Dựng lại

Chạy `render_graphical_abstract.py` bằng Python có Pillow và font Arial; sau đó chạy `rasterize.mjs` bằng Node có Sharp. Nếu Sharp nằm trong runtime riêng, đặt `KLTN_NODE_PACKAGE_ROOT` là đường dẫn `package.json` của runtime trước khi chạy. Script Python kiểm tra độ rộng từng dòng chữ. PNG lớn và preview được xuất từ cùng một SVG.

Chú thích gợi ý: **Overview of the process-aware anomaly detection framework integrating multi-agent AI.** Nguồn: tác giả tổng hợp từ triển khai KLTN-Lon2G.

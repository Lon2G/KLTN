# Sơ đồ và hình minh họa của khóa luận

Thư mục này dành cho sơ đồ nghiên cứu, kiến trúc, luồng xử lý và biểu đồ thực nghiệm đi kèm các phần nội dung đã được duyệt.

## Bộ 11 sơ đồ nằm ngang, có icon và bản draw.io

Theo yêu cầu định dạng lại `diagram_set_v1`, [bộ landscape v2](diagram_set_landscape_v2/README.md) có 11 sơ đồ tiếng Việt với khung bo góc, số vàng xanh, icon nghiệp vụ, PNG/SVG và **một file draw.io chung 11 trang** cùng 11 file riêng. Có thư viện `index.html` và ZIP. Bản v1 giữ nguyên; các ghi chú trạng thái cũ về BI và đối chứng AI được cập nhật có nguồn trong manifest v2. Chưa chèn bộ mới vào báo cáo.

## Graphical abstract nằm ngang

Đã tạo [bản ngang theo hình tham khảo của người dùng](graphical_abstract_landscape_v1/README.md): sáu khối, 21 ô, icon vector theo nghiệp vụ, nhãn tiếng Anh. Có PNG 5.400 × 2.920 px, SVG và diagrams.net chỉnh sửa được. Đây là bản nháp ngày 09/11/2026 chờ duyệt, chưa thay thế hoặc chèn vào hình nào trong Chương 1. Nguồn và giới hạn kết luận được lưu riêng cùng hình.

## Bộ 11 sơ đồ ngày 09/11/2026

Đã dựng bộ sơ đồ tiếng Việt theo phương án được người dùng duyệt: quy trình nghiên cứu, khung khái niệm, quan hệ dữ liệu, ba công đoạn, chuẩn bị/chia tập, nhóm tham chiếu, kiến trúc, phối hợp agent, replay, thiết kế BI và thiết kế thực nghiệm.

Xem [thư viện hình](diagram_set_v1/index.html) hoặc [hướng dẫn và chú thích từng hình](diagram_set_v1/README.md). Mỗi sơ đồ có SVG, PNG 300 dpi, Mermaid và bố cục JSON; script Python dựng lại toàn bộ. Nội dung hình chờ người dùng duyệt; chưa dịch, chưa chèn vào Word và chưa đánh số hình chính thức. Các biểu đồ số liệu Chương 5 chưa thuộc bộ này. Bản sơ đồ tổng quan ngày 08/11 bên dưới vẫn được giữ nguyên.

Mỗi hình cần có:

- Mã nguồn chỉnh sửa được (`.mmd`, `.dot` hoặc script tạo biểu đồ).
- Bản xuất SVG/PDF phù hợp chèn vào báo cáo; PNG nếu cần.
- Nhãn và chú thích phù hợp với phiên bản ngôn ngữ của phần nội dung.
- Nguồn số liệu hoặc file code làm căn cứ; ghi rõ phần nào mới là thiết kế đề xuất.

## Sơ đồ quy trình nghiên cứu tiếng Việt

Được tạo theo yêu cầu ngày 08/11/2026 và chèn thành Hình 1.1 trong Chương 1. Người dùng đã duyệt Chương 1 tiếng Việt và yêu cầu dịch ngày 09/11/2026; bản tiếng Anh được lưu riêng bên dưới.

| File | Mục đích |
| --- | --- |
| `research_framework.vi.svg` | Bản vector để phóng to hoặc chèn vào trình soạn thảo hỗ trợ SVG |
| `research_framework.vi.png` | Ảnh 300 dpi, 2.220 × 3.360 pixel để chèn vào báo cáo |
| `research_framework.vi.mmd` | Mã Mermaid chỉnh sửa được, mô tả các bước và quan hệ |
| `render_research_framework.py` | Dựng hình bằng Matplotlib, đồng thời sinh bản Mermaid từ cùng nội dung |
| `research_framework.vi.sources.json` | Thời điểm hiện trạng, trạng thái bản nháp, font và các nguồn đối chiếu kèm hash |

Chú thích đề xuất: **Sơ đồ quy trình nghiên cứu của đề tài.** Nguồn: tổng hợp từ thiết kế và triển khai trong project KLTN-Lon2G. Được đánh số Hình 1.1 trong Chương 1 đã duyệt.

Sơ đồ phù hợp phần phương pháp nghiên cứu hoặc giới thiệu quy trình nghiên cứu. Lưu riêng để chèn vào phần đó, không chèn vào Lời cam kết. Đây là sơ đồ tổng quan nghiên cứu, không phải sơ đồ trình tự mọi lệnh thực thi trong repository.

Nét liền biểu thị bước đã có triển khai hoặc kết quả; không đồng nghĩa toàn bộ bước đã hoàn thiện. Nét đứt biểu thị phần dự kiến/chưa hoàn tất. Bước Multi-Agent và đối chứng AI có chú thích về phạm vi thử nhóm nhỏ. Đánh giá Kappa, hoàn thiện giao diện và tổng kết toàn bộ nghiên cứu vẫn là phần chưa hoàn tất. Mũi tên từ bước phát hiện sang khung Multi-Agent biểu thị việc sử dụng ứng viên đã cố định.

Nội dung đối chiếu từ các snapshot và tài liệu có thật được liệt kê trong file `.sources.json`. Phần Kappa dựa trên email giảng viên do người dùng cung cấp; cách tổ chức người chấm chưa được chốt. Sơ đồ không khẳng định đã xác nhận nguyên nhân tắc nghẽn, đánh giá AI diện rộng hoặc vận hành real-time trong sản xuất.

Tạo lại các bản xuất từ thư mục gốc repository:

```sh
env MPLCONFIGDIR=/tmp/kltn-thesis-mpl XDG_CACHE_HOME=/tmp/kltn-thesis-cache .venv/bin/python -B docs/thesis/figures/render_research_framework.py
```

Script kiểm tra văn bản nằm trong khung và không chồng tiêu đề/nội dung trước khi xuất. Font chính là Times New Roman trên máy hiện tại, dùng DejaVu Serif nếu máy khác không có font này. SVG lưu đường nét glyph để giữ khả năng hiển thị tiếng Việt. Script chỉ ghi các file sơ đồ, không thay dữ liệu hoặc kết quả thực nghiệm.

Muốn đổi nội dung, chỉnh `NODES`/`EDGES` và các điểm nối trong script rồi dựng lại. Sửa riêng bản Mermaid sẽ không tự cập nhật ảnh SVG/PNG do Matplotlib xuất.

## Bản tiếng Anh của Hình 1.1

`research_framework.en.png` (2.220 × 3.360 pixel, 300 dpi) và `research_framework.en.svg` đã được tạo từ nội dung hình tiếng Việt được duyệt. Bản PNG được chèn vào `../chapter1_overview.en.docx`; sơ đồ được đặt cuối mục 1.5 để tránh khoảng trắng lớn khi dàn trang bản dịch. Câu dẫn chiếu vẫn nằm ở mục 1.5.1.

Nguồn chỉnh sửa: `research_framework.en.mmd` và `render_research_framework_en.py`. File `research_framework.en.sources.json` lưu hash hình tiếng Việt và hồ sơ nguồn tương ứng. Hiện trạng trong hình vẫn là ngày 08/11/2026; ngày dịch là 09/11/2026, không phải ngày thực nghiệm mới. Bản dịch không thay các hình tiếng Việt hoặc bộ 11 sơ đồ đang chờ duyệt riêng.

Script tiếng Anh sử dụng Pillow và Times New Roman để dựng PNG/SVG từ cùng nội dung, đồng thời kiểm tra độ rộng chữ và khoảng cách giữa tiêu đề với nội dung. Chạy bằng Python có Pillow, ví dụ runtime đã dùng cho tài liệu trên máy này:

```sh
/Users/huynhquoclong/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 docs/thesis/figures/render_research_framework_en.py
```

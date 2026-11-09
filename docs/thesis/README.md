# Quy ước biên soạn khóa luận

Ghi nhận theo yêu cầu của Huỳnh Quốc Long ngày 08/11/2026.

## Quy trình ngôn ngữ và duyệt nội dung

1. Viết nội dung bằng tiếng Việt trước để người dùng đọc, kiểm tra và góp ý.
2. Chỉ dịch phần nội dung đã được người dùng duyệt sang tiếng Anh. Việc duyệt một phần không tự động là duyệt các phần khác.
3. Giữ bản tiếng Việt đã duyệt và bản tiếng Anh thành hai file riêng, cùng tên gốc, hậu tố `.vi.docx` và `.en.docx` đối với các chương; `.vi.md` và `.en.md` đối với phần mở đầu đã có.
4. Bản dịch giữ đúng nội dung, số liệu, phạm vi và mức độ khẳng định của bản tiếng Việt. Không bổ sung kết quả, nguồn tham khảo hoặc diễn giải mạnh hơn bản gốc.
5. Nếu phát hiện nội dung đã duyệt có sai sót, trình bày vấn đề và đề xuất sửa bản tiếng Việt trước; không âm thầm thay đổi nội dung trong bản dịch.

## Văn phong và tính trung thực học thuật

- Dùng văn phong học thuật phù hợp khóa luận tốt nghiệp: rõ ràng, có cấu trúc, thuật ngữ nhất quán, lập luận dựa trên bằng chứng.
- Phần lời cảm ơn có thể dùng giọng văn cá nhân trang trọng. Các chương nghiên cứu sử dụng cách diễn đạt khách quan.
- Phân biệt kết quả đã thực hiện, thiết kế dự kiến và hướng nghiên cứu tiếp theo.
- Không dùng dữ liệu nghiệp vụ giả, nhãn giả hoặc số liệu kiểm thử phần mềm để trình bày như kết quả thực nghiệm AI thật.
- Giữ đúng các giới hạn đã xác định: dấu hiệu thời gian xử lý bất thường và điểm nghẽn tiềm năng không mặc nhiên là nguyên nhân được xác nhận; điểm cảnh báo không phải xác suất.
- Nội dung, hình và bảng sử dụng nguồn bên ngoài cần trích dẫn APA. Không tự tạo tài liệu tham khảo.

## Sơ đồ nghiên cứu và hình minh họa

- Khi biên soạn/dịch các phần phương pháp, kiến trúc và thực nghiệm, bổ sung sơ đồ phù hợp với nội dung đã được duyệt.
- Sơ đồ phải bám theo code, dữ liệu và trạng thái triển khai thực tế. Phần mới đề xuất cần được phân biệt rõ trong hình và chú thích.
- Lưu trong `docs/thesis/figures/`, gồm mã nguồn có thể chỉnh sửa và bản xuất dùng trong báo cáo, ưu tiên SVG hoặc PDF; thêm PNG khi cần.
- Sơ đồ quy trình/kiến trúc có thể lưu nguồn Mermaid (`.mmd`) hoặc Graphviz (`.dot`). Biểu đồ thực nghiệm lưu script tạo hình và đường dẫn đến nguồn số liệu thật.
- Giữ nhãn tiếng Việt và nhãn tiếng Anh ở các phiên bản tương ứng; kiểm tra nhãn, mũi tên, chú thích, số liệu và khả năng đọc sau khi xuất hình.
- Đánh số hình theo chương sau khi vị trí trong bố cục được chốt. Không chèn sơ đồ nghiên cứu vào phần lời cảm ơn.

## Phạm vi trình bày

Người dùng tự làm bìa, mục lục và các danh mục. Không tự tạo các phần này. Lời cảm ơn và Lời cam kết được soạn theo các yêu cầu riêng của người dùng.

Theo yêu cầu ngày 08/11/2026, các chương khóa luận được viết trực tiếp thành file Word (`.docx`), không tạo bản Markdown. Định dạng theo hướng dẫn của Khoa HTTT ngày 20/12/2024: A4, Times New Roman 13, giãn dòng 1,5, số trang ở giữa chân trang, không ghi thông tin khác ở header/footer; đánh số chương/mục dạng 1, 1.1, 1.1.1; hình/bảng/công thức đánh theo chương (ví dụ 3.1); tiêu đề dùng Heading 1–3 để tạo mục lục tự động; trích dẫn APA. Lề trái 3 cm, phải/trên/dưới 2 cm là lựa chọn tạm vì hướng dẫn không quy định lề.

## Nội dung hiện có

| Phần | Tiếng Việt | Tiếng Anh | Trạng thái |
| --- | --- | --- | --- |
| Lời cảm ơn | `acknowledgements.vi.md` | `acknowledgements.en.md` | Tiếng Việt đã được duyệt; tiếng Anh được dịch từ bản đã duyệt |
| Lời cam kết | `commitment.vi.md` | `commitment.en.md` | Tiếng Việt đã được duyệt; tiếng Anh được dịch từ bản đã duyệt |
| Chương 1. Tổng quan đề tài | `chapter1_overview.vi.docx` | `chapter1_overview.en.docx` | Người dùng đã duyệt bản tiếng Việt và yêu cầu dịch ngày 09/11/2026. Đã dịch toàn bộ nội dung, hai bảng và Hình 1.1; giữ nguyên số liệu, phạm vi kết luận và 12 nguồn tham khảo. Bản tiếng Việt được giữ nguyên |
| Chương 2. Cơ sở lý thuyết và nghiên cứu liên quan | `chapter2_theory.vi.docx` | Chưa dịch | Bản nháp tiếng Việt chờ người dùng duyệt; 53 tài liệu APA ở cuối file, DOI/arXiv đã đối chiếu với Crossref/DataCite ngày 09/11/2026 |
| Chương 3. Dữ liệu và phương pháp phát hiện bất thường | `chapter3_data_method.vi.docx` | Chưa dịch | Bản nháp tiếng Việt chờ duyệt; nguồn `scripts/thesis/sources/chapter3.vi.md` |
| Chương 4. Thiết kế và triển khai hệ thống | `chapter4_system.vi.docx` | Chưa dịch | Bản nháp tiếng Việt chờ duyệt; nguồn `scripts/thesis/sources/chapter4.vi.md`; Hình 4.5–4.6 chụp từ dashboard |
| Chương 5. Thực nghiệm và đánh giá | `chapter5_experiments.vi.docx` | Chưa dịch | Bản nháp tiếng Việt chờ duyệt; mục 5.6 chờ kết quả chấm định tính; Hình 5.1–5.3 từ `figures/chapter5/make_charts.py` |
| Chương 6. Kết luận và hướng phát triển | `chapter6_conclusion.vi.docx` | Chưa dịch | Bản nháp tiếng Việt chờ duyệt; cập nhật RQ2 khi có kết quả chấm định tính |
| Sơ đồ quy trình nghiên cứu | `figures/research_framework.vi.svg` và `.png`; nguồn `.mmd` và script Python | `figures/research_framework.en.svg`, `.png`, `.mmd`; `render_research_framework_en.py` | Hình 1.1 đã được dịch và chèn vào bản Word tiếng Anh. Nội dung và hiện trạng 08/11/2026 được giữ theo hình tiếng Việt; bản dịch không bổ sung kết quả thực nghiệm |
| Bộ 11 sơ đồ cho Chương 1–5 | `figures/diagram_set_v1/`; mở `index.html` để xem | Chưa dịch nhãn | Đã dựng ngày 09/11/2026; chờ duyệt nội dung. Có SVG/PNG, mã Mermaid/Python và nguồn đối chiếu. Chưa thay hình trong Word; BI/Kappa được đánh dấu dự kiến |

Chương 1 đã có bản Word; các chương 2–6 chưa được tạo trong thư mục này. Xem `figures/README.md` để biết cách dựng lại sơ đồ và nguồn đối chiếu.

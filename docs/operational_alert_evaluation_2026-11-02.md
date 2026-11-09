# Gói A · Quy ước đánh giá cảnh báo trong quy trình giao hàng

Ngày 02/11/2026, chủ nhiệm khóa luận KLTN-Lon2G đã duyệt hai quyết định thiết kế cho demo local trên Olist, ngành `bed_bath_table`.

1. Hệ thống lưu bằng chứng theo đơn và công đoạn. Người quản lý theo dõi đầu việc theo nhóm, mở được từng đơn và có góc nhìn xu hướng theo tuần.
2. Hệ thống đánh giá cả ba công đoạn khi nhận mốc kết thúc. Phần cảnh báo trong lúc đang chờ được đưa vào phạm vi **thử nghiệm riêng cho chặng duyệt đơn → bàn giao vận chuyển**.

## Đối tượng theo dõi

| Đối tượng | Người quản lý cần biết gì? | Bằng chứng và cách dùng |
| --- | --- | --- |
| Một đơn, một công đoạn | Đã chờ hoặc đã xử lý bao lâu? | Hiện mốc đã quan sát, thời điểm đánh giá, thời lượng, loại tham chiếu, số đơn tham chiếu, độ chênh và lý do cảnh báo hoặc chưa đánh giá được. |
| Đầu việc đang mở | Hôm nay ai có thể kiểm tra việc gì? | Gom các đơn cùng công đoạn, cùng đầu mối có thể đối soát và cùng khoảng thời gian. Hiện số đơn phân biệt bị ảnh hưởng trên tổng số đơn đủ điều kiện của nhóm, số đơn thiếu bằng chứng và hạn gần nhất nếu xác định được. Một đơn khẩn vẫn có thể tạo đầu việc riêng. |
| Xu hướng của một nhóm | Một công đoạn có kéo dài qua nhiều tuần không? | Xem toàn bộ đơn đủ điều kiện, gồm cả đơn dưới ngưỡng. Báo số đơn, trung vị thời lượng, mức bao phủ và tỷ lệ vượt hạn trên những đơn đo được kết quả đó. So với nhóm có sản phẩm, độ phức tạp đơn và điều kiện thực hiện tương đồng. Khi đánh giá riêng một seller, nhóm đối chứng cần nằm ngoài seller đó. |
| Kết quả với khách | Đơn giao sớm, đúng hay trễ ngày hẹn? | So ngày lịch giao thực tế với ngày dự kiến. Giữ trường “chưa xác minh được cơ sở ngày hẹn”. Một đơn giao sớm vẫn có thể có công đoạn cần kiểm tra. |

Khi có thêm đơn phù hợp, cập nhật đầu việc đang mở. Khi quan sát được bàn giao, kết thúc lượt theo dõi đang chờ của đơn tương ứng, lưu kết quả công đoạn và giữ lịch sử. Chỉ đóng đầu việc nhóm khi các đơn cần xử lý trong nhóm đã được giải quyết. Đầu mối đối soát là người có thể kiểm tra sự việc. Việc xác nhận nguyên nhân và trách nhiệm cần bằng chứng nghiệp vụ riêng.

## Hai thời điểm phát cảnh báo

**Khi công đoạn kết thúc.** Replay hiện có dùng các mốc đặt hàng, duyệt, bàn giao và giao khách để tính thời lượng từng chặng đã hoàn tất. Giữ riêng chế độ chỉ dùng bối cảnh có tính sẵn có đã xác minh và chế độ giả định bối cảnh tĩnh đã sẵn có lúc đặt hàng. [Quy ước chấm điểm theo sự kiện](../templates/event_stage_scoring_v1/protocol.json)

**Trong lúc đang chờ bàn giao.** Ở mỗi thời điểm kiểm tra của đồng hồ phát lại, chỉ xét đơn đã quan sát sự kiện duyệt và chưa quan sát sự kiện bàn giao. Thời gian đã chờ bằng thời điểm kiểm tra trừ thời điểm duyệt. Chỉ dùng tham chiếu và bối cảnh hợp lệ tại thời điểm đó. Ghi rõ “chưa ghi nhận bàn giao”. Đồng hồ phát lại cho biết lúc hệ thống kiểm tra, còn nhật ký sự kiện lưu các mốc Olist thực sự ghi nhận. Trường hợp thiếu mốc, xung đột hoặc thiếu tham chiếu cần có trạng thái riêng.

Đánh giá **vượt thời gian tham chiếu** và **vượt hạn bàn giao** riêng. Với `shipping_limit_date`, cần đối chiếu đúng mặt hàng, seller, phạm vi của lần bàn giao và thời điểm hạn được biết. Trường hợp có nhiều mặt hàng, nhiều hạn hoặc đầu mối chưa rõ phải nêu giới hạn quy kết. Khi chỉ biết hạn qua hồ sơ cuối kỳ, dùng hạn đó cho đối chiếu hồi cứu hoặc ghi rõ giả định nếu thử trong replay.

Đơn Olist `af4b4e5e7cc290c42e0935df9ff3479c` minh họa sự khác nhau. Đơn được duyệt ngày 10/03/2018, hạn bàn giao của mặt hàng là 27/03, mốc bàn giao ghi nhận ngày 29/03. Replay đã chấm thời lượng duyệt–bàn giao **19,23 ngày** với biên tham chiếu **7,27 ngày** dưới giả định bối cảnh tĩnh. Khách nhận ngày 13/04, sớm hơn ngày hẹn 18/04. Ngày 18/03 có thể dùng làm ví dụ về lúc kiểm tra khi đang chờ. Đó là tình huống cho thiết kế thử nghiệm, kết quả đã lưu hiện chỉ xác nhận cảnh báo khi công đoạn kết thúc. [Kết quả replay hiện hành](../docs/event_stage_scoring.md)

## Tiêu chí cho lần thử nghiệm sau

| Tiêu chí | Cách đo và mẫu số cần báo |
| --- | --- |
| Thời gian còn có thể can thiệp | Với mỗi đơn đã cảnh báo khi đang chờ, lấy lần cảnh báo đầu tiên. Khi quan sát được bàn giao, tính thời điểm bàn giao trừ thời điểm cảnh báo. Báo trung vị, số đơn đo được và số đơn vẫn chưa quan sát bàn giao ở cuối cửa sổ. Khi có hạn hợp lệ, tính thêm hạn bàn giao trừ thời điểm cảnh báo. Giá trị dương là còn thời gian trước hạn. |
| Khối lượng việc mỗi ngày | Trên cùng tập đơn, luồng sự kiện và cửa sổ quan sát, báo số đơn mới phát cảnh báo, số đầu việc nhóm mở mới, số đầu việc được cập nhật, số còn mở cuối ngày và số đơn quá hạn còn chờ. Đếm riêng cảnh báo mới và lần nhắc lại. Đối chiếu danh sách trước và sau khi gom để mọi đơn cần xử lý đều có thể được tìm thấy. |
| Bằng chứng để hành động | Từ mỗi đầu việc phải mở được các đơn liên quan và thấy công đoạn, mốc đã biết, căn cứ ưu tiên, giới hạn dữ liệu và hành động đề nghị. Khi rà soát, ghi số đầu việc đạt từng tiêu chí trên tổng số đã xem, cùng các trường hợp thiếu căn cứ. |

Khi phân tích xu hướng, báo riêng các đơn còn đang chờ ở thời điểm cắt vì trung vị trên công đoạn đã hoàn tất có thể bỏ sót chính các đơn chờ lâu. Một nhóm liên tục chậm hơn nhóm đối chứng là dấu hiệu để điều tra. Đánh giá hiệu quả thực tế của can thiệp cần thêm nhật ký xử lý và kết quả vận hành tại doanh nghiệp.

## Dữ liệu, hiện trạng và điểm duyệt tiếp theo

- Sử dụng cách chia theo thời gian đã có với 5.480 đơn train, 1.853 validation và 1.691 test. Bộ test gốc đã được đánh giá. Các thử nghiệm phát triển và lựa chọn quy tắc tiếp theo dùng phạm vi phát triển. [Thiết kế so sánh và giới hạn đánh giá](delivery_discrimination.md)
- Phần chấm sau khi kết thúc đã có kết quả replay. Cảnh báo đang chờ, màn hình đầu việc nhóm và góc nhìn xu hướng là các hạng mục đã duyệt **về thiết kế**. Các tham số như nhịp kiểm tra, cửa sổ gom nhóm, mức nâng ưu tiên và tiêu chí đủ bằng chứng cho xu hướng sẽ được trình cùng gói triển khai trước khi chạy.
- Pilot 20 đơn đã chuẩn bị để kiểm tra lời giải thích của ba agent. Mẫu này có mục đích riêng với phép đo khối lượng đầu việc theo ngày trên luồng replay. Lần chạy API cho một đơn và phần code thử cảnh báo đang chờ đều cần đề xuất và duyệt phạm vi riêng. [Hướng dẫn tích hợp ba agent](multi_agent_integration_2026-10-30.md)

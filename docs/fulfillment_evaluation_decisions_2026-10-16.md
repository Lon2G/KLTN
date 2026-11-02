# Quy ước cảnh báo và đánh giá đã thống nhất

## Trạng thái và phạm vi

Ngày ghi nhận: 16/10/2026. Người dùng đã đồng ý cả năm phương án được đề xuất
trong cuộc trao đổi, với xác nhận: "tôi đồng ý các câu trả lời bạn đề xuất".

Đây là quyết định nghiệp vụ của người dùng cho khóa luận KLTN-Lon2G, áp dụng
cho hướng phát triển tiếp theo trên dữ liệu Olist thật, ngành `bed_bath_table`.
Không suy ra rằng giảng viên đã phê duyệt bản quy ước kỹ thuật này.

Tài liệu này ghi nhận quyết định, đối chiếu hiện trạng và thiết kế các bước
thực nghiệm tiếp theo. Nó không tự kích hoạt quy trình gán nhãn, cập nhật
thuật toán, thay ngưỡng, chia lại dữ liệu hoặc chọn mô hình. Các kết quả và
quy ước đã đóng phiên bản vẫn giữ nguyên để tái lập lịch sử.

## Năm quyết định đã chốt

| Mã | Quyết định | Giới hạn diễn giải |
| --- | --- | --- |
| Q1 | Không có thông tin về cách đặt ngày hẹn thì ghi "Chưa xác minh được cơ sở ngày hẹn, cần rà soát". | Không biết cơ sở không có nghĩa là ngày hẹn được đặt ngẫu nhiên. Ngày hẹn có công thức cũng chưa bảo đảm được hiệu chỉnh phù hợp. |
| Q2 | Chọn B: công đoạn phải vượt ngưỡng cảnh báo phù hợp với nhóm tương đồng, không chỉ dài hơn trung vị. | Ghi "Công đoạn kéo dài cần kiểm tra"; không tự xác nhận nguyên nhân, trách nhiệm hay độ chính xác của ngưỡng. |
| Q3 | Khi có kế hoạch công đoạn đã xác minh thì so thực tế với kế hoạch; nếu không có thì dùng tham chiếu thống kê phù hợp. | Ghi rõ loại tham chiếu. Không ép đơn giao sớm phải có công đoạn nhanh; vẫn giữ cảnh báo công đoạn chậm nếu cả đơn giao sớm. |
| Q4 | Chọn A: hệ thống đề xuất rà soát cách đặt hẹn và cung cấp bằng chứng. | Chưa xây chức năng tự tính ngày hẹn mới; không sửa ngày hẹn lịch sử để xóa dấu trễ. |
| Q5 | Chuẩn bị bằng chứng của đơn thật cho người rà soát đánh giá theo tiêu chí đã thống nhất. Giữ riêng trường hợp chưa xác định. | Không dùng nhãn do AI hoặc chính ngưỡng sinh ra làm kiểm chứng độc lập. Chưa đủ bằng chứng thì chỉ chọn cấu hình vận hành thử, không tuyên bố chính xác nhất. |

## Ba phần đầu ra phải tách riêng

1. **Đáp ứng ngày hẹn:** giao sớm, đúng ngày, giao trễ hoặc chưa đánh giá được.
   Giao sau ngày hẹn là sự không đáp ứng mốc hẹn quan sát được, chưa chỉ ra
   nguyên nhân hoặc chứng minh riêng một công đoạn gây trễ.
2. **Hiệu suất công đoạn:** thời lượng thực tế, loại tham chiếu, thời lượng
   tham chiếu, mức chênh lệch, ngưỡng và cờ cảnh báo. Nhanh/chậm hơn trung vị
   là mô tả; vượt ngưỡng cảnh báo là một quyết định riêng.
3. **Cơ sở ngày hẹn và hành động:** nguồn xác minh cơ sở đặt hẹn nếu có,
   phần còn thiếu, bằng chứng gợi ý cần rà soát và đề xuất kiểm tra.
   Không đồng nhất thiếu tài liệu về ngày hẹn với bằng chứng ngày hẹn sai.

Một đơn có thể đồng thời giao sớm và có công đoạn kéo dài. Các kết luận về
ngày hẹn, công đoạn, chất lượng dữ liệu và nguyên nhân không loại trừ nhau.
Không ghép chúng thành một nhãn "bình thường toàn diện" hoặc "lỗi đã xác nhận".

## Quy tắc so sánh thời gian

- Giữ cách so ngày lịch thực tế với ngày hẹn đã dùng trong pipeline. Ngày
  dự kiến không được biến thành sự kiện thực tế hoặc deadline chính xác đến
  giây nếu dữ liệu chỉ hỗ trợ ý nghĩa ngày lịch.
- Ba khoảng thời gian quan sát hiện có là đặt hàng đến duyệt, duyệt đến
  bàn giao vận chuyển, và bàn giao đến giao khách. Không thêm mốc đóng gói
  hay diễn giải toàn bộ khoảng duyệt-bàn giao thành thời gian đóng gói.
- Kế hoạch công đoạn chỉ được dùng khi có nguồn, phạm vi áp dụng và thông
  tin thời điểm biết kế hoạch. Không chia ngày hẹn cuối thành ba khoảng giả.
- Với tham chiếu thống kê, sử dụng các nhóm train và điều kiện đủ dữ liệu
  đã khai báo. Không dùng chính đơn đang đánh giá làm tham chiếu cho nó.
- Chênh lệch mô tả = thời lượng thực tế - thời lượng tham chiếu. Giá trị âm
  là nhanh hơn, dương là chậm hơn; không tự chuyển dấu của chênh lệch thành
  nhãn bất thường. Số bằng nhau được báo là bằng tham chiếu.
- Không cộng chênh lệch với các trung vị riêng để giải thích chính xác số
  ngày giao sớm/trễ so với ngày hẹn. Đó là các chuẩn so sánh khác nhau, và
  các nhóm tham chiếu từng công đoạn có thể khác nhau.
- Mốc thiếu, mâu thuẫn, ngoài cửa sổ quan sát hoặc thiếu nhóm tham chiếu
  phải giữ lý do chưa đánh giá được. Không thay bằng 0 hoặc nhãn bình thường.
- Thời lượng ghi nhận bằng 0 giữ là ghi chú về hai mốc bằng nhau, không tự
  dùng làm bằng chứng hiệu suất tốt hoặc lỗi nhập liệu.

Ngưỡng ECDF trên 0,95 hiện tại và ghi chú đuôi dưới 0,05 vẫn là quy ước
phát triển tạm thời của phiên bản cũ. Việc người dùng chốt Q2 không có nghĩa
là xác nhận hai ngưỡng này tối ưu, phê duyệt ngưỡng mới hoặc chấp nhận một
xác suất bất thường. Thứ hạng thực nghiệm không phải xác suất bất thường.

## Đối chiếu code hiện tại

| Thành phần | Đã có | Còn thiếu so với quyết định mới |
| --- | --- | --- |
| [Hiệu suất giao hàng](../scripts/03_anomaly_detection/build_delivery_performance.py) | So ngày lịch, giữ các trạng thái sớm/đúng/trễ và hạn chế chất lượng dữ liệu. | Chưa có bằng chứng về cơ chế tạo ngày hẹn; cần thể hiện trạng thái chưa xác minh, không suy diễn ngẫu nhiên. |
| [Dấu hiệu công đoạn](../scripts/03_anomaly_detection/build_process_signals.py) | Ba công đoạn, nhóm train, trung vị, ngưỡng, cờ dài/ngắn và phần chưa chấm được. Đơn giao sớm vẫn có thể mang cờ dài. | Chưa có đầu ra riêng, nhất quán cho chênh lệch nhanh/chậm so trung vị và phần tóm tắt giao sớm không có công đoạn nhanh. Không có kế hoạch gốc đầy đủ cho từng công đoạn. |
| [So sánh detector](../scripts/03_anomaly_detection/compare_signal_detectors.py) | So 120 cấu hình thuộc 7 nhóm phương pháp trên cùng phạm vi đủ điều kiện; giữ các trường hợp bất đồng. | Kết quả là đối chiếu quy tắc, chưa phải precision/recall độc lập hoặc lựa chọn tối ưu. |
| [Phân tích bối cảnh](../scripts/03_anomaly_detection/analyze_bottleneck_context.py) | Nhóm bối cảnh, mẫu số, đối chiếu có trọng số và đề xuất có điều kiện. | Cần kiểm tra độ ổn định theo cửa sổ quan sát và giới hạn các đơn chưa hoàn tất. Chưa xác minh nguyên nhân hoặc hiệu quả can thiệp. |
| [Rà soát có cấu trúc](../scripts/03_anomaly_detection/prepare_hierarchical_review.py) | Bằng chứng và ghi chú có người thực hiện, phạm vi, lý do và lịch sử chỉnh sửa. | Chưa có bộ tham chiếu độc lập hoàn tất. Pilot cũ không tự có thêm bằng chứng địa lý hoặc quyền đánh giá toàn bộ phạm vi mới. |

Các hợp đồng tham khảo hiện có:
[process signals](../templates/process_signals_v1/protocol.json),
[tiêu chí bằng chứng](../templates/evidence_criteria_v1/protocol.json),
[hướng dẫn rà soát](../templates/hierarchical_review_v1/README.md).
Không sửa các file này tại chỗ: hash của chúng được ràng buộc với kết quả cũ.

## Thiết kế thực nghiệm tiếp theo

Đây là kế hoạch kỹ thuật, không phải thực nghiệm đã chạy hoặc mọi tham số
đánh giá đã được người dùng/giảng viên phê duyệt.

| Câu hỏi | Đối chiếu | Bằng chứng và điều kiện kết luận |
| --- | --- | --- |
| Cảnh báo có nhất quán với dữ liệu và quy ước không? | Tính lại ngày lịch, khoảng thời gian, nhóm tham chiếu và ranh giới quyết định trên nguồn thật. | Kiểm thử phần mềm và truy vết dữ liệu. Test đạt chỉ chứng minh tính nhất quán của code, không phải độ chính xác nghiệp vụ. |
| Xét công đoạn/bối cảnh có giúp phát hiện tốt hơn không? | Ngưỡng chung, tham chiếu theo bối cảnh và các detector hiện có, trên cùng mục tiêu và cùng tập đủ điều kiện. | Phân biệt mức khớp quy tắc với đánh giá độc lập. Precision/recall/F1 chỉ áp dụng khi bộ tham chiếu đủ căn cứ; báo cả phần chưa xác định, bao phủ và số cảnh báo. |
| Nhóm khu vực/thời điểm nổi bật có ổn định không? | Các cửa sổ thời gian và thời gian theo dõi phù hợp, giữ định nghĩa công đoạn và bối cảnh. | Báo số đơn, tỷ lệ, phần khớp và mức bất định. Không xem một chênh lệch dương là bằng chứng nguyên nhân hoặc tác dụng can thiệp. |
| Multi-agent có tạo thêm giá trị không? | Không agent, một agent và nhiều agent trên cùng bằng chứng và bộ phát hiện. | Kiểm tra số liệu, dẫn chứng, kết luận thiếu căn cứ, thời gian và chi phí. Chưa có thí nghiệm này. |
| Hệ thống có giám sát gần thời gian thực được không? | Replay sự kiện thật và kiểm tra đơn đang chờ, không sử dụng thông tin tương lai. | Đo độ trễ toàn pipeline, thời điểm cảnh báo và giả định về thời điểm biết dữ liệu. Replay không phải triển khai live; không tạo sự kiện thanh toán có timestamp giả. |

## Những việc không tự động được thông qua

- Không suy ra nhãn cho bất kỳ đơn nào từ việc người dùng đồng ý quy ước.
- Không coi nhãn do AI đề xuất hoặc người đọc lại nguyên cờ mô hình xác nhận
  là bộ tham chiếu độc lập. Phải ghi nhận nguồn đánh giá và mức đã tiếp xúc
  kết quả mô hình; không tuyên bố rà soát mù khi người rà soát đã thấy kết quả.
- Không gán toàn bộ phạm vi địa lý/seller cho pilot chỉ có bằng chứng thời
  gian, thanh toán và nội dung đơn hàng. Kiểm tra sự phù hợp trước khi tái dùng.
- Không dùng cùng bộ đã phục vụ chọn mô hình để tuyên bố đánh giá cuối độc
  lập. Rà soát lịch sử sử dụng dữ liệu trước khi kích hoạt kế hoạch 80/20.
- Quy mô mẫu, người thực hiện thực tế, chỉ số chính, cách xử lý trọng số lấy
  mẫu và quy tắc chọn mô hình vẫn phải được cụ thể hóa trước phép đánh giá.
  Q5 chấp thuận nguyên tắc kiểm chứng, không xác nhận các bước đó đã hoàn tất.
- Không chuyển hướng chính thành dự báo ngày giao; không tự tính lại ngày
  hẹn, tạo quan sát giả, train lại hoặc đưa dữ liệu sang nhà cung cấp AI.

## Bước triển khai gần nhất

Ưu tiên bổ sung một phiên bản đầu ra diễn giải dựa trên các bảng dấu hiệu đã
đóng băng: cơ sở ngày hẹn chưa xác minh; loại tham chiếu từng công đoạn;
chênh lệch nhanh/chậm; trường hợp giao sớm vẫn chậm hoặc chưa đủ bằng chứng.
Giữ nguyên quyết định cảnh báo và ngưỡng trong bước này, không triển khai
lại phần tính thời lượng/tham chiếu đã có. Đầu ra mới phải ghi riêng, có
nguồn truy vết và kiểm thử trên dữ liệu thật; không ghi đè kết quả cũ.

Sau đó kiểm tra phạm vi bộ rà soát, chuẩn bị những bằng chứng còn thiếu và
ghi nhận đánh giá thực sự trước khi chọn mô hình bằng độ chính xác. Không
cần import thêm dữ liệu để chuẩn hóa phần đầu ra hiện có; muốn xác nhận
nguyên nhân hoặc kế hoạch công đoạn phải có nguồn vận hành tương ứng.

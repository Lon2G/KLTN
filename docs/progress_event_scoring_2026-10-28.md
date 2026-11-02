# Tiến độ tích hợp chấm điểm theo sự kiện, 28/10/2026

## Phần được triển khai

Nối bộ chấm điểm công đoạn với bộ phát lại sự kiện Olist đã xây ở lượt trước.
Giữ nguyên dữ liệu, tham chiếu train, công thức điểm, ngưỡng và các kết quả cũ.
Không train lại, gán nhãn, gọi dịch vụ AI, làm dashboard hay triển khai agent.

Luồng hiện tại:

```text
Sự kiện thật đến
    -> cập nhật các mốc đã quan sát của đơn
    -> kiểm tra công đoạn nào đã đủ hai đầu mút
    -> kiểm tra mâu thuẫn, bối cảnh và độ đủ tham chiếu
    -> tính điểm bằng công thức đã đóng băng
    -> lưu điểm, lý do chưa chấm và nhật ký thay đổi
```

Trước đây, phép so sánh chính thường yêu cầu đơn hoàn tất. Lớp mới cho phép
chấm chặng đặt-duyệt ngay khi đã duyệt và chặng duyệt-bàn giao ngay khi đã bàn
giao, không cần biết trước ngày giao cho khách. Chặng vận chuyển vẫn chỉ được
chấm khi đã ghi nhận giao hàng. Chưa có cảnh báo công đoạn đang chờ quá lâu.

## Giới hạn dữ liệu và hai chế độ

Olist không ghi thời điểm biết/cập nhật product, seller, thông tin khách hàng
và bản đồ ZIP. Không thể tự tuyên bố chúng chắc chắn đã biết lúc đặt hàng.

| Chế độ | Cách làm | Kết quả có thể kết luận |
| --- | --- | --- |
| Chỉ dùng bối cảnh có thời điểm xác minh | Chưa dùng bối cảnh không có lịch sử thời điểm | Chưa đủ điều kiện chấm điểm theo mô hình bối cảnh hiện tại |
| Giả định bối cảnh tĩnh đã biết khi đặt đơn | Dùng giá trị thật trong snapshot sau khi thấy mốc Purchased; ghi rõ giả định | Kiểm chứng kỹ thuật của luồng xử lý theo giả định, không chứng minh chạy live |

Không tạo sự kiện hoặc timestamp "bối cảnh đến" giả. Không đưa trạng thái cuối,
ngày hẹn, ngày giao tương lai, review hay khoản thanh toán vào đầu vào bối cảnh.
Giả định còn áp dụng cho bản đồ địa lý và bối cảnh lịch sử dùng làm tham chiếu;
đó không phải dữ liệu có lịch sử phiên bản được nguồn xác minh.

Hai chế độ phải đọc riêng. **Không có điểm trong chế độ nghiêm ngặt không có
nghĩa là không có bất thường**, mà là chưa chứng minh được điều kiện dùng dữ liệu.

## Kết quả trên dữ liệu thật

Vẫn dùng 1.853 đơn validation và 7.076 mốc sự kiện trước 01/06/2018. Giữ hai
đơn pilot đang bảo lưu trong danh sách trạng thái nhưng không chấm điểm chúng.
Không đọc/chấm lại test gốc để chọn công thức hoặc điều chỉnh ngưỡng.

Kết quả của **chế độ giả định bối cảnh**:

| Nội dung | Số lượng |
| --- | ---: |
| Đơn có ít nhất một công đoạn được chấm | 1.726 |
| Đơn chấm đủ ba công đoạn | 1.462 |
| Đơn chấm được một phần | 264 |
| Đơn chưa có công đoạn nào được chấm | 127 |
| Công đoạn được chấm | 4.865 |
| Công đoạn có cảnh báo đuôi dài | 225 |
| Đơn có ít nhất một cảnh báo | 221 |
| Đơn đã nhận cảnh báo trước khi quan sát giao hàng | 95 |

225 cảnh báo công đoạn gồm 66 ở đặt-duyệt, 29 ở duyệt-bàn giao và 130 ở vận
chuyển-giao hàng. 95 cảnh báo trước giao thuộc hai công đoạn đầu. Đây là cảnh
báo về công đoạn đã hoàn tất; không phải 95 đơn được dự báo chính xác sẽ trễ.
221 đơn có tín hiệu cũng không phải 221 đơn được xác nhận tắc nghẽn.

Trong 5.559 dòng công đoạn mỗi chế độ, phần chưa chấm của chế độ giả định:

| Lý do chính của dòng công đoạn | Số dòng |
| --- | ---: |
| Chưa quan sát đủ đầu mút | 332 |
| Đơn có mâu thuẫn thời gian đã quan sát | 153 |
| Bối cảnh địa lý không đạt điều kiện | 179 |
| Đơn thuộc diện bảo lưu rà soát | 6 |
| Nhóm tham chiếu chưa đủ hỗ trợ | 24 |

Đây là **số dòng công đoạn**, không phải số đơn. 153 dòng mâu thuẫn tương ứng
51 đơn, mỗi đơn ba công đoạn bị tạm ngưng chấm theo chính sách thận trọng.
Các lý do có thứ tự ưu tiên; không được hiểu mỗi dòng chỉ có duy nhất một vấn đề.

Chế độ nghiêm ngặt có 0 dòng được chấm: 5.068 dòng dừng ở điều kiện chưa xác
minh thời điểm biết bối cảnh; các dòng còn lại dừng ở thiếu đầu mút, mâu thuẫn
hoặc bảo lưu. Đây là phần giới hạn được thể hiện công khai, không bị giấu đi.

## Công thức và tham chiếu được giữ nguyên

Chọn nhóm từ lịch sử train, không thêm đơn đang phát lại vào nhóm. Giữ ngưỡng
tối thiểu 30 đơn, điều kiện khớp khoảng cách và thứ tự tìm nhóm theo sản phẩm,
seller, số item, khu vực, khoảng cách; thiếu nhóm sản phẩm thì dùng phương án
dự phòng cấp ngành có bối cảnh như trước, không tự bỏ yếu tố địa lý.

Gọi d là thời lượng, mu là trung bình nhóm, F(d) là tỷ lệ giá trị tham chiếu
không lớn hơn d. Giữ công thức:

```text
Nếu d <= mu hoặc d <= 0: score = 0
Nếu không: score = max(0, min(100, 100 * (F(d) - 0.95) / 0.05))
Cờ kéo dài = score > 0
Chênh lệch mô tả = d - mu
```

Điểm là mức chú ý theo đuôi phân phối, không phải xác suất tắc nghẽn. Chênh lệch
dương không tự trở thành cảnh báo. Chưa đủ bằng chứng giữ điểm trống, không
thay bằng 0. Đơn có điểm một phần nhưng chưa có cảnh báo vẫn mang trạng thái
chưa đánh giá đầy đủ, không bị gán bình thường toàn đơn.

Trong 4.865 dòng được chấm, 105 dùng nhóm sản phẩm/bối cảnh, 4.760 dùng nhóm
dự phòng cấp ngành/bối cảnh. Vẫn chưa thể nói mỗi mã sản phẩm có ngưỡng riêng
đủ tin cậy. Tham chiếu train là dữ liệu đã quan sát, không phải bộ được chứng
nhận toàn bình thường.

Đã đối chiếu **4.233 dòng công đoạn của 1.411 đơn từng được chấm** với bản cũ:
khớp chính xác thời lượng, mã nhóm, số mẫu, trung bình/trung vị, chênh lệch,
thứ hạng, điểm, mức độ và cờ cảnh báo. Việc lập chỉ mục nhóm lịch sử chỉ để
giảm lọc lặp, không thay thành viên nhóm hoặc cách tính.

Lớp mới chấm thêm 632 dòng so với 4.233 dòng đối chiếu. Phạm vi/cửa sổ và
điều kiện chấm từng công đoạn khác điều kiện hoàn tất đơn cũ, nên không dùng
con số này để tuyên bố tăng accuracy. Không có AUC/F1 mới trong bước này.

632 dòng bổ sung gồm: 372 dòng trước đây không qua điều kiện hoàn tất toàn
đơn; 85 dòng có đầu mút chưa quan sát tại cutoff cũ nhưng đã xuất hiện trong
cửa sổ replay hiện tại; 175 dòng thuộc các đơn ngoài bảng công đoạn cũ. Không
được mô tả toàn bộ phần tăng này là lợi ích của chấm sớm theo sự kiện.

## Ví dụ cảnh báo thật

Đơn `22329698dd5a16cc6e8025805e3f881e` đặt lúc 03/03/2018 15:11:03,
được duyệt lúc 06/03/2018 03:49:11. Ngay khi phát lại tới sự kiện duyệt:

- Thời gian đặt-duyệt: 2,52648 ngày.
- Tham chiếu cấp ngành có bối cảnh: 1.252 đơn, trung bình 0,36230 ngày.
- Chênh lệch so trung bình: +2,16418 ngày.
- Ranh giới đuôi tham chiếu: 1,81366 ngày; vẫn áp dụng đầy đủ công thức điểm.
- Thứ hạng thực nghiệm: 97,2843; điểm chú ý: 45,6869/100, mức nhẹ theo quy ước.
- Tại lúc ghi nhận cảnh báo chưa quan sát sự kiện giao hàng.

Đây là kết quả đọc từ nhật ký dữ liệu thật trong chế độ giả định bối cảnh.
45,6869 không có nghĩa xác suất đơn bị tắc nghẽn là 45,6869%. "Nhẹ" là mức
điểm trong phân phối tham chiếu, không phải mức thiệt hại kinh doanh xác minh.

## Nhật ký và an toàn xử lý

- Event, trạng thái quan sát, điểm hiện tại và nhật ký điểm cùng lưu trong
  một transaction SQLite. Nếu chấm điểm lỗi, không lưu nửa kết quả.
- Nhận lại cùng event không phát thêm cảnh báo. Có thể mở lại checkpoint
  cùng hợp đồng và phát lại từ đầu mà không đếm trùng.
- Đổi tham chiếu/bối cảnh/quy tắc khi nối tiếp checkpoint sẽ bị từ chối.
- Nếu dữ liệu đến sau làm lộ mâu thuẫn, điểm cũ bị rút khỏi trạng thái hiện
  tại, nhưng bản ghi đã từng tính vẫn được giữ cùng lý do thay đổi.
- Lượt dữ liệu thật này không phát sinh việc rút điểm; chức năng rút điểm
  được kiểm thử bằng lỗi cố ý chỉ trong test, không chèn vào thực nghiệm.
- Không gán nguyên nhân, không dùng cơ sở ngày hẹn chưa xác minh để thay
  quyết định công đoạn, không tự xác nhận đơn đúng hay sai về nghiệp vụ.

Nhật ký mới có 28.972 bản ghi thay đổi kết quả cho cả hai chế độ; đây không
phải số sự kiện nguồn hoặc số đơn hàng. Sự kiện nguồn vẫn là 7.076.

## Thời gian thực thi

Một lượt chạy local đã đo vòng nhận event, cập nhật trạng thái, chấm cả hai
chế độ và commit SQLite mất **135,743 giây**, khoảng **52,13 sự kiện/giây**.
Thời gian mỗi lần xử lý: p50 **13,94 ms**, p95 **49,66 ms**, lớn nhất
**848,74 ms**. Có tính chi phí tra nhóm lần đầu, chưa có bộ nhớ đệm được làm
nóng trước khi vào vòng event.

Không bao gồm tải/kiểm tra nguồn, đối chiếu bản cũ, xuất file, agent, mạng,
giao diện hay gửi cảnh báo. Lượt cũ chỉ cập nhật trạng thái, lượt này còn
chấm điểm và lưu thêm nhật ký, nên không so trực tiếp hai tốc độ để kết luận
mô hình tốt/xấu hoặc hệ thống đã đạt SLA near real-time. Đây là một lượt đo
kỹ thuật, chưa phải thử tải nhiều mức hay đo độ trễ toàn hệ thống.

## File và cách xem

- [Lớp chấm điểm và lưu trạng thái](../scripts/04_streaming/score_event_stages.py).
- [Lệnh chạy và kiểm chứng](../scripts/04_streaming/run_stage_scoring_replay.py).
- [Hợp đồng thực nghiệm](../templates/event_stage_scoring_v1/protocol.json).
- [Kiểm thử](../tests/test_event_stage_scoring.py).
- [Tài liệu kỹ thuật](event_stage_scoring.md).
- [Tóm tắt số liệu](../data/experiments/olist_event_stage_scoring_v1/summary.json).
- [Điểm từng đơn](../data/experiments/olist_event_stage_scoring_v1/order_scores.csv).
- [Điểm từng công đoạn](../data/experiments/olist_event_stage_scoring_v1/stage_scores.csv).
- [Nhật ký từng thay đổi](../data/experiments/olist_event_stage_scoring_v1/stage_decisions.jsonl).
- [Kết quả khớp bản cũ](../data/experiments/olist_event_stage_scoring_v1/parity_summary.json).

Khi đọc CSV, chọn đúng cột `mode`: mỗi đơn xuất hiện ở cả hai kịch bản,
không cộng chúng thành hai đơn khác nhau. Khi đọc nhật ký, một công đoạn
có thể có nhiều lần cập nhật; chỉ `stage_scores.csv` là trạng thái cuối.

## Kiểm chứng hoàn tất

- **137 kiểm thử liên quan đều đạt**: 28 kiểm thử mới, 26 kiểm thử bộ phát lại
  và 83 kiểm thử so sánh/chọn ứng viên/đánh giá/bằng chứng đã có. Lượt này mất
  631,330 giây; đây là thời gian test suite, không phải độ trễ xử lý một đơn.
- Kiểm thử mới đã tái lập toàn bộ luồng chấm điểm: 7.076 event, 11.118 dòng
  kết quả cuối của hai chế độ và 28.972 bản ghi thay đổi. Khớp SQLite, JSON,
  CSV, nhóm tham chiếu và 4.233 dòng chấm cũ. Không lấy lại điểm cũ để giả làm
  kết quả chạy theo event.
- Kiểm tra đủ các trường hợp: chưa có dữ liệu tương lai, thiếu bối cảnh,
  nhóm ít mẫu, lỗi địa lý, trùng event, chạy lại checkpoint, lỗi giữa lúc
  ghi dữ liệu, rút điểm khi lộ mâu thuẫn, không ghi đè và giữ hai đơn bảo lưu.
- Đối chiếu SHA-256 trước/sau: **1.039 file có sẵn không thay đổi, không xóa
  file cũ**. Chỉ thêm 6 file code/cấu hình/test/tài liệu và 14 file snapshot.
- `git diff --check`, kiểm tra cú pháp Python và whitespace của file mới đạt.

Đây không phải toàn bộ test suite của repository, không phải chứng nhận
accuracy nghiệp vụ. Không commit/push trong lượt này. Dữ liệu/mô hình/output
vẫn nằm ngoài Git theo cấu hình hiện có và cần sao lưu riêng.

## Tiến trình và bước tiếp theo

Đã tiến từ "phát lại và lưu trạng thái" sang **"phát lại, chấm công đoạn đã
hoàn tất và lưu dấu vết quyết định"**, dưới hợp đồng bối cảnh được công bố.

Chưa hoàn thành Multi-Agent, cảnh báo công đoạn đang chờ, dashboard và đánh
giá độ trễ toàn pipeline. Chưa chốt mô hình tối ưu; số liệu test cũ không đổi.

Gói tiếp theo cần duyệt: thiết kế và triển khai tối thiểu lớp phối hợp agent
trên các đầu ra đã có, xác định nhiệm vụ, giao tiếp, bằng chứng và kiểm tra
kết luận; đồng thời chốt đối chứng không agent/một agent/nhiều agent. Không
chỉ đổi tên các hàm thành agent. Việc dùng LLM/provider/API, gửi dữ liệu và
chi phí phải được chốt riêng trước khi gọi dịch vụ.

Chưa cần import thêm dữ liệu để tiếp tục phần tích hợp local. Muốn gỡ giả
định thời điểm biết bối cảnh cần nguồn có lịch sử ghi nhận/cập nhật; train
thêm không khắc phục được thông tin mà Olist không ghi.

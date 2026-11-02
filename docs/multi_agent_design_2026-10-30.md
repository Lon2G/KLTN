# Thiết kế Multi-Agent — quyết định ngày 30/10/2026

## Phạm vi và tình trạng

Hệ thống chính vẫn là Multi-Agent AI cho phân tích thời gian xử lý bất thường
và điểm nghẽn tiềm năng trong quy trình hoàn tất đơn hàng. Dùng dữ liệu thật
Olist, ngành `bed_bath_table`, demo trên máy người dùng. Một agent và không
agent chỉ là các đối chứng thực nghiệm; không thay phạm vi khóa luận.

Gói này hoàn thành **thiết kế có thể kiểm tra**, bộ ghi sổ ngân sách API cục bộ
và kiểm tra tính toàn vẹn các kết quả có sẵn. Chưa chạy agent/LLM, chưa dựng UI,
chưa thực hiện phép so sánh địa lý mới. Không sinh đơn, timestamp, nguyên nhân,
nhãn hay kết quả đánh giá giả. Các bài thử sổ chi phí chỉ dùng số tiền/token
kiểm thử trong SQLite tạm, không phải quan sát nghiên cứu.

Hợp đồng chi tiết: [protocol.json](../templates/multi_agent_v1/protocol.json).

## Quyết định của người dùng và cách áp dụng

| Quyết định | Thiết kế áp dụng |
| --- | --- |
| Đầu ra đủ gồm công đoạn, mức chậm, nhóm liên quan, bằng chứng thiếu và việc cần kiểm tra | Giữ nguyên năm nội dung; mỗi nhận định có tham chiếu bằng chứng |
| Có vấn đề phải hiện lên dashboard; chưa rõ và thiếu dữ liệu là các trường hợp khác nhau | Tách các trục trạng thái, không ép một nhãn duy nhất |
| Tìm công đoạn và nhóm đang có vấn đề | Luôn kiểm tra các nhóm đã định nghĩa; cho phép kết quả chưa đủ so sánh hoặc không thấy tập trung |
| Đối chiếu đơn tương đồng và các yếu tố khác trước khi nghi địa lý | Kiểm tra theo công đoạn, công bố yếu tố đã xét/chưa xét và mức bao phủ |
| Cảnh báo trước, giải thích sau; nhanh và đáng tin cậy | Đo riêng độ trễ phát hiện, giải thích và hiển thị; thông báo nếu AI chưa hoàn tất |
| Có thể dùng API; ngân sách tổng 200.000 đồng | Trần gọi mô hình dự kiến 150.000 đồng, dự phòng 50.000 đồng; pilot 20 đơn thật |
| Người dùng có thể rà soát bản giải thích | Chuẩn bị rubric về đúng số liệu, bằng chứng, mức khẳng định và tính hữu ích; không đổi thành nhãn tắc nghẽn đã xác nhận |

Các giới hạn 12 yêu cầu/case, 4 yêu cầu/vai trò/case, token và một vòng sửa
là lựa chọn kỹ thuật ban đầu để giới hạn chi phí; chưa phải tham số tối ưu
được thực nghiệm xác nhận. Đổi chúng phải tạo phiên bản, ghi lý do và giữ
nguyên việc hạch toán chi phí đã phát sinh.

## Đối chiếu trước khi nêu giả thuyết địa lý

“Loại trừ yếu tố khác” được triển khai thành **kiểm tra/kiểm soát những yếu tố
quan sát được**, không tuyên bố loại trừ mọi nguyên nhân. Các yếu tố như tồn
kho, nhân sự, hãng vận chuyển, thời tiết và tắc đường chưa có bằng chứng xác
nhận trong dữ liệu đang dùng. Không đưa chúng vào như sự kiện đã xảy ra.

Trình tự:

1. Kiểm tra mốc thời gian, khả năng sử dụng dữ liệu và phạm vi quan sát.
2. Xác định công đoạn có dấu hiệu, thời lượng, nhóm tham chiếu và độ chênh.
3. Xem xét các yếu tố quan sát được, phù hợp với công đoạn đó.
4. Đối chiếu các nhóm theo kế hoạch đã khai báo trước; ghi lại mọi lần so sánh.
5. Nếu có bằng chứng, báo mối liên hệ địa lý và đề xuất việc kiểm tra thêm.
6. Nếu không đủ bằng chứng, giữ phát hiện cấp đơn và ghi rõ phần chưa biết.

| Công đoạn | Nội dung cần xét | Giới hạn |
| --- | --- | --- |
| Đặt hàng → duyệt | Trình tự mốc, thời điểm, bối cảnh đơn; thanh toán chỉ khi chứng minh được thời điểm biết dữ liệu hoặc trong phân tích hồi cứu | Không quy chậm duyệt thành vấn đề khoảng cách vận chuyển |
| Duyệt → bàn giao | Sản phẩm/ngành, số lượng, seller có thể quy thuộc, thời điểm bắt đầu | Địa chỉ nhận không chứng minh thiếu hàng hoặc chậm chuẩn bị; đơn nhiều seller không tự quy lỗi cho một seller |
| Bàn giao → giao khách | Sản phẩm/ngành, số lượng, seller/điểm gửi, thời điểm, chất lượng tọa độ và tuyến/nhóm khoảng cách quan sát được | Khoảng cách ZIP là xấp xỉ, không phải đường đi thực; khối lượng/kích thước cần kiểm tra nguồn, mức thiếu và khả năng sử dụng trước khi bổ sung |

Phải phân biệt câu hỏi nghiên cứu của mỗi phép đối chiếu:

- **Khu vực nhận có chậm hơn các khu vực khác ngoài khác biệt về khoảng cách?**
  Có thể so trong các dải khoảng cách tương đồng cùng các yếu tố còn lại.
- **Khoảng cách có liên hệ với thời lượng không?** Không ghép chính xác theo
  khoảng cách rồi tuyên bố đã đo được tác động của khoảng cách; cần thiết kế
  phép so khác, ghép các yếu tố không phải biến đang xét.

Địa lý vẫn có thể được dùng từ đầu để chọn tham chiếu phù hợp cho detector.
Yêu cầu mới là không vội **quy nguyên nhân** cho địa lý, không phải bỏ địa lý
ra khỏi detector rồi thêm lại cuối cùng. Cờ detector vốn đã điều kiện hóa theo
địa lý phản ánh cảnh báo so với chuẩn bối cảnh, không đo tổng tác động địa lý.

Không ghép nhóm bằng chính duration, điểm bất thường, kết quả giao muộn, review
sau giao hoặc trạng thái cuối. Khi phát lại sự kiện, nhóm lịch sử không được
lấy thông tin chưa biết tại thời điểm đang đánh giá. Tổng hợp cả tháng chỉ
có sau cuối tháng thuộc phân tích hồi cứu, không đưa ngược vào cảnh báo đầu tháng.

Mỗi phép so phải công bố mẫu số tổng/đủ điều kiện/ghép được, tỷ lệ cảnh báo,
độ chênh thời gian, mức bao phủ, yếu tố đã kiểm soát, yếu tố còn thiếu và tập
đơn đối chiếu. Không xem số cảnh báo tuyệt đối là bằng chứng seller có vấn đề.
Không cố mở rộng nhóm cho đến khi thu được kết quả dương.

Code bối cảnh cũ có đối chiếu theo một số trường và các điều kiện đủ mẫu,
nhưng chưa thực hiện toàn bộ danh sách trên. Ngưỡng đủ mẫu cũ là quy ước báo
cáo, chưa phải xác suất hay bảo đảm thống kê. Gói này không thay code/đầu ra
cũ và không tuyên bố đã chạy phép loại trừ mới.

## Trạng thái dashboard

Một đơn có thể đồng thời có cảnh báo ở một công đoạn và thiếu dữ liệu cho
một phân tích khác. Vì vậy các trục chất lượng dữ liệu, quan sát công đoạn,
kết quả detector, phân tích nhóm và giải thích được lưu độc lập.

- `warning`: giữ điểm và cờ gốc, dẫn công đoạn/tham chiếu; agent không tự xóa.
- `unassessed`: số và cờ không biết là `null`, kèm lý do; không hiển thị bình thường.
- `endpoint_not_yet_observed`: chỉ biết chưa quan sát mốc; chưa thể xác định
  là dữ liệu bị thiếu hay công đoạn vẫn đang chạy.
- `insufficient_comparable_orders`: có thông tin nhóm nhưng chưa có đủ đối chiếu.
- `missing_context`: thiếu trường bối cảnh cần cho một phép phân tích cụ thể.
- `inconclusive`: đã xử lý nhưng chưa có nhận định được bằng chứng hỗ trợ đủ.
- `unavailable_budget`/`unavailable_api`: trạng thái vận hành, không làm mất
  cảnh báo detector đã có.

Giữ cảnh báo công đoạn chậm kể cả đơn giao sớm. Không buộc tìm công đoạn nhanh.
Cơ sở ngày hẹn vẫn chưa xác minh. Các chế độ sử dụng bối cảnh tĩnh theo giả định
và chỉ dùng dữ liệu có thời điểm khả dụng đã xác minh phải được tách biệt.

Chú ý: detector hiện tại có thể chưa chấm được khi thiếu địa lý. Quy ước UI
không tự cấp quyền tạo một score mới bỏ qua địa lý; cần phiên bản detector
riêng nếu muốn thay chính sách đó.

## Ba agent và cơ chế phối hợp

1. **Phân tích quy trình:** chọn công đoạn cần kiểm tra, gọi công cụ đọc trạng
   thái/điểm/tham chiếu; đưa bằng chứng và câu hỏi cho agent bối cảnh.
2. **Phân tích bối cảnh:** chọn trong các phép đối chiếu đã khai báo; sử dụng
   công cụ tính toán, công bố yếu tố chưa kiểm soát và đề xuất kiểm tra.
3. **Kiểm chứng bằng chứng:** kiểm tra nguồn, số liệu, thời điểm và mức khẳng
   định; yêu cầu sửa hoặc đưa kết quả chưa xác định.

Scheduler bằng code quản lý yêu cầu và ngân sách; không được tính là agent
AI thứ tư. Ba vai trò có thể dùng cùng một mô hình nền nhưng cần cấu hình,
trạng thái nhiệm vụ và trao đổi riêng; không chỉ đổi tên ba hàm tính toán.

Thông điệp có mã case ẩn danh, vai trò, phiên bản đánh giá, thời điểm quan sát,
mã bằng chứng, nhận định, câu hỏi và thông điệp được trả lời. Bốn loại nhận
định: sự kiện quan sát, liên hệ mô tả, giả thuyết điều tra, phần chưa biết.
Số liệu do công cụ tính, không do mô hình bịa hoặc ước đoán.

Kiểm tra số liệu/nguồn bằng code chạy trước đánh giá ngôn ngữ. Agent kiểm
chứng không phải đáp án chuẩn độc lập; nhiều agent dùng cùng mô hình vẫn có
thể mắc cùng lỗi. Cho phép tối đa một vòng sửa và trả kết quả chưa xác định
khi hết ngân sách hoặc vẫn thiếu bằng chứng. Các tool call, lượt tiếp tục và
retry đều tính vào giới hạn 12 request/case, 4 request/vai trò/case cho một
lượt phân tích. `run_key` định danh cấu hình/lần lặp đã khai báo, không đổi
run key để né giới hạn retry. Case key phải ổn định qua các lần lặp.

Không cho agent sửa ngưỡng, tự train, sửa dữ liệu, tạo nhãn kiểm chứng, gửi
thông báo cho seller hoặc tự thực hiện biện pháp vận hành.

## Chi phí và bộ ghi sổ đã triển khai

Mã: [api_budget.py](../scripts/05_multi_agent/api_budget.py).
Chính sách: [budget.json](../templates/multi_agent_v1/budget.json).

Giá tham khảo đã kiểm tra ngày 30/10/2026, Standard, context ngắn:

| Mô hình | Input USD/triệu token | Output USD/triệu token |
| --- | ---: | ---: |
| GPT-6 Luna | 0,10 | 0,50 |
| GPT-6.1 Terra | 1,00 | 5,00 |

Nguồn chính thức: [Luna](https://developers.openai.com/api/docs/models/gpt-6-luna),
[Terra](https://developers.openai.com/api/docs/models/gpt-6.1-terra),
[token suy luận](https://developers.openai.com/api/docs/guides/reasoning).
Không bảo đảm tài khoản đã có quyền truy cập hoặc giá không đổi. Kiểm tra
lại trước lần gửi đầu tiên; đây chưa phải lựa chọn theo chất lượng thực nghiệm.

Với giả định tổng 12.000 input + 3.000 output tính phí cho ba agent/một đơn,
Luna là 0,0027 USD, Terra 0,027 USD. Quy đổi kế hoạch 30.000 đồng/USD tương
ứng 81 và 810 đồng/đơn; 20 đơn lần lượt 1.620 và 16.200 đồng. Đây là dự toán,
không phải token/tốc độ/chi phí đã đo. Không nhân lại ba lần vì đã cộng cả
ba agent. Chạy lặp, đối chứng, phản hồi công cụ và lịch sử hội thoại làm tăng
tổng token. Token suy luận tính trong output.

Giới hạn gọi mô hình: 150.000/30.000 = **5 USD**; 50.000 đồng dự phòng thuế,
phí, quy đổi và phần phát sinh. 30.000 đồng/USD là mức lập kế hoạch, không
phải tỷ giá ngân hàng. Số tiền nạp tài khoản ban đầu có thể khác tiền tiêu
thụ thực tế; kiểm tra màn hình thanh toán trước khi nạp, không tự nạp thêm.

Bộ ghi sổ SQLite hiện có:

- Dùng số nguyên nano-USD, không làm tròn các khoản nhỏ thành 0.
- Giữ trước chi phí tối đa của từng request, gồm cả giới hạn output/suy luận
  và mức input cao hơn khi ghi cache 30 phút.
- Request ID gắn fingerprint payload; gọi lại cùng ID không tạo quyền gửi lần nữa.
- Ghi trạng thái `in_flight` trước khi gửi; mất mạng hoặc crash giữ khoản đã
  dự phòng. Chỉ giải phóng nếu biết chắc chưa gửi.
- Nhập usage thực để quyết toán; receipt trùng không tính hai lần. Usage vượt
  mức dự phòng vẫn được ghi đầy đủ và khóa các lần gọi tiếp.
- Chặn vượt trần, model ngoài danh sách, token quá giới hạn, quá 20 case pilot,
  quá số lượt mỗi case/vai trò; kiểm tra đồng thời nhiều kết nối bằng transaction.
- Mở lại DB không xóa chi phí; policy thay đổi bị từ chối để tránh reset ngân sách.

Trần tiền là cho tổng quá trình pilot, đối chứng và chạy lại, không phải mỗi
lần chạy được cấp thêm 150.000 đồng. Giới hạn 20 case khác nhau cũng dùng
chung; mỗi run/case có giới hạn lượt riêng. API sender tương lai phải dùng
chung `data/runtime/multi_agent_budget.sqlite`; thư viện hiện cho chọn đường
dẫn để kiểm thử, nên ràng buộc đường dẫn/case/run vẫn cần ở lớp tích hợp.

**Đây chưa phải bảo đảm trần hóa đơn của nhà cung cấp.** Chưa có API sender:
sender sau này phải đếm/bound toàn bộ payload, lấy đủ usage, thực thi quy trình
reserve → mark_sent → gửi → settle, giữ unknown khi timeout. Không hỗ trợ giá
Fast, context dài, cache 12 giờ hay công cụ hosted tính phí trong pilot này.
Tính toán và truy vấn dữ liệu chạy local; chỉ gửi bằng chứng tối thiểu, không
gửi toàn bộ CSV, tọa độ chính xác, review tự do hoặc khóa bí mật.

## Pilot và đánh giá

Chưa chọn hoặc gửi 20 đơn. Sau khi xử lý thiếu mã nguồn, chọn thật theo quy
tắc tái lập trên tập phát triển: có cảnh báo, không cảnh báo và chưa đánh giá
được. Giữ các case đang bảo lưu. Không gọi tập đã dùng chọn mô hình là test
độc lập mới. Người rà soát thấy bằng chứng/điểm thì phải ghi rõ, không gọi là mù.

So hệ thống chính với không agent, một agent và bỏ riêng agent kiểm chứng.
Giữ cùng bằng chứng được phép biết, detector, công cụ; báo cả chi phí thực
và so sánh với ngân sách xử lý tương đương. Không bỏ các lần lỗi/abstain.
Đo đúng số liệu, nguồn dẫn, kết luận thiếu căn cứ, dùng tương lai, mức biết
từ chối, bao phủ giải thích, độ hữu ích do người dùng rà soát, độ ổn định,
độ trễ p50/p95 và token/chi phí.

Pilot 20 đơn phục vụ kỹ thuật và sửa thiết kế, không đủ kết luận khái quát.
AUC detector giữ nguyên nếu score giữ nguyên. AI rà soát lại cờ detector
không tạo ground truth tắc nghẽn độc lập.

Demo có thể dùng kết quả lưu lại nhưng phải ghi rõ cache. Các lần đo độ ổn
định cần thực sự gọi lại mô hình; không lấy tốc độ đọc cache làm độ trễ AI.

## Kết quả kiểm tra nền tảng hiện tại

Báo cáo đầy đủ: [audit JSON](multi_agent_readiness_2026-10-30.json).

- Replay sự kiện: 12 output được kê trong manifest, 5 source và 3 file triển
  khai đều khớp hash đã lưu.
- Snapshot chấm điểm sự kiện **có tồn tại**. 13 output và 76 source khớp hash.
  7/10 file triển khai khớp, 3 file đang thiếu:
  `scripts/04_streaming/run_stage_scoring_replay.py`,
  `scripts/04_streaming/score_event_stages.py`,
  `templates/event_stage_scoring_v1/protocol.json`.
- Test `test_event_stage_scoring.py` và hai tài liệu của gói đó cũng chưa có.
- Git chỉ ghi nhận worktree hiện tại; không tìm được lịch sử các script thiếu
  trong các ref local đã kiểm tra. Chưa xác định vì sao file không còn.

Hash khớp xác nhận file hiện tại phù hợp với manifest đã lưu; không thay thế
việc chạy lại thuật toán hoặc chứng minh đúng nghiệp vụ. Không thể coi ba file
thiếu đã được phục hồi chỉ bằng cách chép kết quả hoặc sửa hash manifest.

Phần chuẩn bị multi-agent tiến triển được, nhưng pilot API chưa sẵn sàng.
Trước khi nối vào luồng sự kiện cần phục hồi nguồn gốc nếu có backup; nếu
không, tái triển khai thành **phiên bản mới**, đối chiếu với kết quả cũ trên
cùng phạm vi và giữ nguyên mọi snapshot lịch sử.

## Lệnh kiểm tra không tốn API

```sh
.venv/bin/python -B -m unittest discover -s tests -p 'test_multi_agent_*.py' -v
.venv/bin/python -B scripts/05_multi_agent/audit_readiness.py
.venv/bin/python -B scripts/05_multi_agent/audit_readiness.py --require-ready
```

Lệnh cuối dự kiến thoát mã 2 khi còn điều kiện chưa đáp ứng; đó là cơ chế
chặn, không phải một lần gọi API thất bại. Không có API client trong gói này.

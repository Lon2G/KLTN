# Kiểm chứng ghi chú và đề xuất kiểm tra — bản v3, 04/11/2026

Đã hoàn thành gói sửa offline theo yêu cầu của người dùng. Agent phải chọn ghi chú và đề xuất có bằng chứng, có phạm vi công đoạn hoặc trường dữ liệu. Verifier phải kiểm tra các mục này cùng với nhận định số liệu. Đầu ra cuối không còn hợp nhất trực tiếp các chuỗi ghi chú chưa kiểm chứng của analyst.

77 kiểm thử đạt, không lỗi hoặc bỏ qua. Đã dựng lại lời gọi công cụ và đối chiếu 39 phản hồi API đã lưu của sáu case v2, gồm lượt một đơn trước đó và nhóm năm đơn mới. Cơ chế mới bác bỏ ghi chú thiếu mốc sai ở cả bốn case index 2, 8, 9, 16. Không gọi API, không đọc key để gửi yêu cầu, không phát sinh chi phí.

Đây là kiểm chứng bộ quy tắc trên dữ liệu và lỗi đã biết. Chưa chứng minh phản hồi AI mới sẽ tốt hơn, chưa đánh giá accuracy phát hiện tắc nghẽn và chưa có nhãn độc lập.

## 1. Điều kiện cho từng ghi chú

| Tình huống | Điều kiện được phép ghi | Thông tin đi kèm |
|---|---|---|
| Chưa quan sát mốc công đoạn | `start_timestamp` hoặc `end_timestamp` thực sự là `null` tại thời điểm đánh giá | Tên công đoạn, đúng trường chưa quan sát và trạng thái quan sát |
| Mốc thời gian mâu thuẫn | Khoảng thời gian ghi nhận của công đoạn âm | Hai timestamp, số giây có dấu và công đoạn liên quan |
| Ít mẫu so sánh | Phép so sánh đã đếm mẫu và số lượng thấp hơn mức tối thiểu | Số mẫu, ngưỡng, kế hoạch so sánh và công đoạn |
| Độ bao phủ nhóm đối chiếu thấp | Tỷ lệ dữ liệu ghép được thấp hơn yêu cầu của nhóm | Tỷ lệ, số mẫu hai phía và mức bao phủ tối thiểu |
| Chưa thực hiện được so sánh | Công cụ trả về công đoạn chưa đánh giá được hoặc một điều kiện tiên quyết chưa đạt | Trạng thái và kế hoạch so sánh; không tự gán số mẫu là 0 |
| Thiếu trường bối cảnh | Trường cần thiết được cung cấp nhưng có giá trị trống trong chế độ cho phép dùng bối cảnh | Tên trường và giá trị nguồn; không chấp nhận ghi chung “thiếu dữ liệu” |
| Chưa xác minh thời điểm sẵn có của bối cảnh | Nguồn đánh dấu chưa xác minh hoặc đang dùng giả định tĩnh | Cơ sở và chế độ đánh giá |
| Yếu tố chưa được kiểm soát | Yếu tố nằm trong danh sách giới hạn đã khai báo của công cụ | Tên yếu tố; không diễn giải thành chắc chắn thiếu trong toàn bộ raw data |
| Biến chưa có trong hợp đồng sự kiện | Ví dụ payment/review chưa được cung cấp cho công cụ tại thời điểm này | Ghi rõ phạm vi event contract, không kết luận bảng raw không tồn tại |

“Chưa quan sát” không khẳng định rằng nguồn vĩnh viễn thiếu bản ghi. Tại một sự kiện sớm, mốc bàn giao/giao hàng có thể chưa xuất hiện. Khi đã ghi nhận đủ mốc nhưng sai thứ tự, phải ghi nhận mâu thuẫn.

Ví dụ thực tế của index 16: dữ liệu ghi bàn giao lúc 17:32:27 nhưng duyệt lúc 17:36:01 ngày 24/04/2018. Chênh lệch là -214 giây. V3 cho phép ghi `timeline_conflict` ở công đoạn chuẩn bị/bàn giao; không cho phép đổi thành thiếu mốc. Duration không hợp lệ và điểm đánh giá vẫn giữ `null` theo detector hiện có.

Ví dụ khác từ dữ liệu phát triển thật: một phép so sánh vùng có 48 mẫu phía mục tiêu và 58 mẫu đối chiếu, đều đạt yêu cầu 30. Tuy nhiên độ bao phủ là 48/75 và 58/78, thấp hơn 80%. V3 ghi nhận độ bao phủ thấp, không gọi đó là thiếu số lượng mẫu. Các yêu cầu 30 và 80% được lấy từ cấu hình hiện hành, không điều chỉnh để đạt kết quả mong muốn.

## 2. Điều kiện cho đề xuất kiểm tra

| Đề xuất | Bằng chứng cần có trong phạm vi vai trò |
|---|---|
| Rà timestamp nguồn | Mốc chưa quan sát, khoảng thời gian âm hoặc cảnh báo công đoạn đã tính |
| Kiểm tra chuẩn bị hàng/seller | Cảnh báo tại công đoạn chuẩn bị và bàn giao |
| Kiểm tra tuyến vận chuyển | Cảnh báo tại chặng vận chuyển |
| Thu thập thêm lịch sử tương đồng | Số mẫu hoặc độ bao phủ của phép so sánh thực sự chưa đạt yêu cầu |
| Kiểm tra khối lượng/kích thước | Có cảnh báo công đoạn và công cụ công khai yếu tố này chưa được kiểm soát |
| Kiểm tra tải công việc/tồn kho | Cảnh báo chuẩn bị hàng và hai yếu tố này nằm trong giới hạn chưa kiểm soát |
| Xác minh thời điểm có dữ liệu bối cảnh | Nguồn công khai chưa xác minh thời điểm sẵn có |
| Tìm hiểu cách đặt ngày hẹn | Nguồn công khai chưa biết phương pháp đặt ngày hẹn |

Đây là điều kiện để một đề xuất phù hợp với bằng chứng trong hệ thống hiện tại. Nó không chứng minh nguyên nhân, không bảo đảm đây là bước điều tra tối ưu và không xác nhận hiệu quả của một giải pháp. Một doanh nghiệp vẫn có thể kiểm tra định kỳ ngoài phạm vi các cảnh báo này.

Hai case dưới ngưỡng không còn có lựa chọn đề xuất điều tra seller/tuyến vận chuyển chỉ vì chúng là đơn giao hàng. Case xung đột thời gian được hướng tới rà timestamp; không tự quy kết vấn đề nghiệp vụ cho seller hoặc carrier. Một cảnh báo công đoạn vẫn đủ điều kiện đề xuất kiểm tra công đoạn đó, kể cả khi đơn giao sớm: quy tắc không dùng giao sớm để xóa cảnh báo.

## 3. Thay đổi trong luồng Multi-Agent

1. Công cụ local cung cấp dữ liệu và danh sách các lựa chọn có bằng chứng cho từng vai trò. Mỗi lựa chọn gắn với case, thời điểm, chế độ, trường/công đoạn, evidence ID và phiên bản quy tắc.
2. Analyst chọn `claim_ids`, `unknown_ids`, `check_ids`. Agent không tự viết một mã ghi chú chung hoặc tự điền giá trị nguồn. Giới hạn mỗi analyst vẫn là ba nhận định số liệu; bổ sung tối đa ba ghi chú và hai đề xuất kiểm tra.
3. Code kiểm tra lựa chọn có nằm trong bằng chứng vai trò đã thấy hay không, thuộc đúng loại hay không và điều kiện nguồn còn đúng hay không. ID từ case, vai trò hoặc thời điểm khác bị từ chối. Ghi chú trùng ID cũng bị từ chối.
4. Context Analyst nhận các đánh giá công đoạn cùng bối cảnh. Điều này cho phép kiểm tra một đề xuất có liên quan đến cảnh báo nào mà không chỉ dựa vào lời tóm tắt của Process Analyst. Chỉ lựa chọn mới được bổ sung vào lịch sử công cụ để giảm thông tin lặp.
5. Verifier nhận cả nhận định số liệu, ghi chú và đề xuất kiểm tra, cùng bằng chứng. Phải có đúng một quyết định cho từng mục; không được chỉ xác nhận số liệu rồi bỏ qua ghi chú. Verifier không được chấp nhận mục đã bị code bác.
6. Chỉ các mục đạt kiểm tra code và được Verifier chấp nhận mới vào `verified_notes` hoặc `verified_recommended_checks`. Mục bị bác nằm ở `rejected_metadata`. Các danh sách mã ngắn `unknowns`/`recommended_checks` được tạo từ những mục đã duyệt này; giao diện nên dùng bản đầy đủ để hiển thị phạm vi và bằng chứng.

Giới hạn “chưa xác minh nguyên nhân” luôn được hệ thống công khai dưới dạng giới hạn phương pháp, không giả làm kết luận mới của AI. `metadata_verification_complete` cho biết vòng kiểm chứng đã hoàn tất. Nếu có mục được xuất bản và mục bị từ chối, trạng thái là `evidence_checked_with_rejections`. Khi kiểm chứng lỗi hoặc thiếu quyết định, phần giải thích không được xuất bản; detector vẫn được giữ.

Phản hồi gốc, lỗi hợp đồng và vòng sửa có giới hạn vẫn được lưu. Không âm thầm đổi ghi chú sai thành ghi chú đúng rồi tính đó là kết quả của mô hình. Giới hạn yêu cầu theo vai trò, số token và sổ ngân sách chung không thay đổi. Độ lớn input và độ trễ API thật của v3 chưa được đo; provider vẫn đếm token và từ chối trước inference nếu vượt giới hạn.

## 4. Kết quả kiểm tra phản hồi cũ

Sáu case gồm index 0, 1, 2, 8, 9, 16 trong pilot v2. Kiểm tra bằng nguồn dữ liệu thật và biên nhận đã lưu, có chặn HTTP và phương thức gọi API thật.

| Hạng mục | Kết quả |
|---|---:|
| Phản hồi mô hình cũ đối chiếu với biên nhận | 39 |
| Nhận định scalar cũ vẫn khớp nguồn | 34 |
| Ghi chú/đề xuất cũ được rà ở từng vai trò | 91 |
| Có lựa chọn bằng chứng phù hợp đã hiện diện trong vai trò | 42 |
| Giới hạn nguyên nhân do hệ thống tiếp tục công khai | 11 |
| Không đạt điều kiện hỗ trợ cho vai trò gửi theo chính sách mới | 26 |
| Cần cung cấp bằng chứng thêm vào gói của vai trò | 9 |
| Ghi “thiếu bối cảnh” nhưng không chỉ rõ trường | 3 |
| Ghi chú thiếu mốc sai đã được chặn | Cả 4 case đã biết |
| Submission analyst v2 không còn lọt qua schema v3 | 12/12 |
| Yêu cầu API mới / chi phí mới | 0 / 0 USD |

91 mục là các mục do hai analyst gửi, có lặp mã giữa vai trò, không phải 91 đơn hay 91 nhãn kiểm chứng độc lập. Nhóm 26 bao gồm đề xuất chưa đạt chính sách kích hoạt hoặc chưa có bằng chứng trong phạm vi vai trò; không được diễn giải thành 26 kết luận nghiệp vụ chắc chắn sai. Kết quả có lựa chọn bằng chứng phù hợp cũng không có nghĩa mô hình đã thực sự chọn ID mới hoặc Verifier đã duyệt nó.

Các submission v2 dùng chuỗi ghi chú, không dùng ID metadata, nên bị schema v3 từ chối. Chương trình không chuyển chúng thành phản hồi v3 giả. Nó dựng lại bằng chứng từ lời gọi công cụ cũ, kiểm tra lại từng mục, giữ bản gốc và xuất một báo cáo rà soát riêng. Mẫu đã được dùng phát triển bản sửa nên không phải tập kiểm tra độc lập.

## 5. Kiểm thử, bảo toàn và vị trí file

Bộ kiểm thử có 77 test đạt: 60 test trước đó và 17 test metadata mới. Các test bao gồm thiếu mốc tại sự kiện sớm thật, mâu thuẫn timestamp thật, thiếu mẫu, đủ mẫu nhưng thiếu độ bao phủ, không cho mượn ID từ case/vai trò/thời điểm khác, không cho Verifier bỏ qua metadata, và không để mục bị bác lọt vào danh sách xuất bản. Các phản hồi dùng để tiêm lỗi giao thức được đánh dấu test-only; không tạo dòng dữ liệu kinh doanh hoặc nhãn nghiên cứu giả.

42 file được bảo vệ giữ nguyên hash, gồm dữ liệu raw, các đầu vào/đầu ra đã đóng băng, biên nhận và ledger. Bản mã v2 được lưu trước khi sửa. Ledger vẫn có 51 yêu cầu đã quyết toán, tổng 0,015692540 USD, ngân sách local còn 4,984307460 USD như trước gói này; không đối chiếu lại Billing.

Đã tạo pilot v3 offline trên cùng 20 case. Các file `pilot.json`, `evidence_packets.json` và mẫu rà soát giống v2 hoàn toàn. 60 công đoạn được tính lại, gồm 52 có điểm và tám chưa đánh giá; 60 kiểm tra chế độ chỉ dùng thời điểm sẵn có đã xác minh vẫn không tự sinh điểm từ giả định. Không thay ngưỡng, dữ liệu huấn luyện hoặc mô hình API.

Các file chính:

- `scripts/05_multi_agent/metadata_contracts.py`: điều kiện bằng chứng, phạm vi ghi chú và điều kiện đề xuất kiểm tra.
- `scripts/05_multi_agent/agent_contracts.py`: schema và lựa chọn ID của hợp đồng v3.
- `scripts/05_multi_agent/coordinator.py`: đưa metadata qua Verifier, giữ mục bị bác và chỉ xuất mục được chấp nhận.
- `scripts/05_multi_agent/replay_saved_metadata.py`: kiểm tra phản hồi v2 đã lưu mà không gọi API.
- `tests/test_multi_agent_metadata.py`: 17 test mới.
- `docs/multi_agent_metadata_validation_2026-11-04.json`: kết quả 77 test, hash mã nguồn và test.
- `docs/multi_agent_metadata_preservation_2026-11-04.json`: kiểm tra bảo toàn dữ liệu và ledger.
- `data/experiments/olist_multi_agent_metadata_regression_v3/`: báo cáo rà soát từng case và manifest.
- `data/experiments/olist_multi_agent_tools_v3/`: pilot v3 offline.
- `data/experiments/olist_multi_agent_implementation_v2/`: bản sao mã v2 và danh sách hash cần bảo vệ.

Lệnh kiểm tra không gọi API:

```sh
.venv/bin/python -B -m unittest discover -s tests -p 'test_multi_agent*.py'
```

Lệnh xuất báo cáo phải dùng một thư mục mới; thư mục kết quả hiện có không bị ghi đè:

```sh
.venv/bin/python -B scripts/05_multi_agent/replay_saved_metadata.py --output-dir data/experiments/olist_multi_agent_metadata_regression_v3_repeat
```

Kiểm tra bảo toàn trong script trên gắn với ledger tại lúc sửa. Nếu về sau được duyệt gọi thêm API làm ledger thay đổi, script sẽ dừng để yêu cầu kiểm tra lịch sử thay đổi, không âm thầm coi nó là không đổi. Các manifest v2 vẫn mô tả mã v2; mã đang hoạt động là v3 và không được dùng để tuyên bố khớp hash triển khai v2. Có thể đối chiếu mã cũ trong thư mục archive.

Snapshot dữ liệu thực nghiệm bị Git bỏ qua và cần sao lưu local. Chưa commit hoặc push trong gói này.

## 6. Phần chưa hoàn thành

Chưa có phản hồi API v3 mới, chưa đánh giá chất lượng giải thích độc lập, chưa so sánh ba agent với một agent/không agent, chưa tích hợp dashboard. Mọi lượt API mới cần người dùng duyệt phạm vi và chi phí riêng. Công việc hiện tại dừng đúng ở gói sửa và kiểm tra offline đã được yêu cầu.

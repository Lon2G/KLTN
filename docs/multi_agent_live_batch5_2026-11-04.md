# Thử API Multi-Agent v2 trên năm đơn Olist — 04/11/2026

Đã chạy đúng năm đơn được người dùng duyệt, không chạy lại hoặc gọi thêm đơn. Cả năm lượt hoàn tất qua API thật; 28 nhận định có cấu trúc khớp nguồn khi dựng lại bằng công cụ local. Tuy nhiên, bốn đơn có ghi chú sai rằng chưa quan sát được mốc công đoạn. Vì vậy chưa thể kết luận toàn bộ phần giải thích đạt yêu cầu, dù chương trình trả `evidence_checked` cho cả năm lượt.

Không đổi detector, ngưỡng, dữ liệu, mô hình, prompt hay mã nguồn agent trong gói này. Không tạo dữ liệu giả hoặc nhãn tắc nghẽn. Các kết quả gốc, kể cả ghi chú sai, được giữ nguyên.

## Phạm vi và điều kiện thực nghiệm

- Năm đơn thật thuộc `bed_bath_table`, lấy từ tập validation trong pilot đã đóng băng: index 1, 2, 8, 9, 16. Chọn trước khi xem phản hồi AI, theo thứ tự có sẵn trong từng nhóm.
- Hai đơn có cảnh báo, hai đơn hoàn tất dưới ngưỡng, một đơn chưa đánh giá được do xung đột thời gian. Đây là mẫu kiểm tra kỹ thuật theo tình huống, không đại diện cho tỷ lệ bất thường toàn dữ liệu.
- `gpt-6-luna`, ba vai trò Process Analyst, Context Analyst, Evidence Verifier; run key `multi-agent-pilot-v2`.
- Tối đa 12 yêu cầu sinh phản hồi mỗi đơn; 6.000 input và 1.200 output tokens mỗi yêu cầu. Không tự đổi mô hình, tăng giới hạn, chạy lại lỗi hoặc nạp thêm tiền.
- Dùng sổ ngân sách chung 5 USD, dự trù tối đa 0,081 USD cho nhóm. Key chỉ được nạp cục bộ, HTTPS có xác minh chứng chỉ.
- Chế độ `assumed_static_context_at_purchase`: thời điểm sẵn có của bối cảnh tĩnh là giả định công khai, chưa được nguồn xác minh. Cơ sở đặt ngày hẹn vẫn chưa xác minh.

## Kết quả từng lượt

| Index | Mã đơn Olist | Nhóm | Lượt mô hình | Nhận định xuất bản | Thời gian toàn lượt, giây | Chi phí USD |
|---:|---|---|---:|---:|---:|---:|
| 1 | `8d0b6018a2f4e690127e0a2ea6cb4ee7` | Cảnh báo | 7 | 6 | 22,347 | 0,001977935 |
| 2 | `32df72b9f8dee31b9b3b8b8e31f2ba1d` | Cảnh báo | 6 | 6 | 15,550 | 0,001831365 |
| 8 | `fc6dbce689351adc115bf0cc85884892` | Dưới ngưỡng | 7 | 4 | 15,193 | 0,001845015 |
| 9 | `b8b8d73455ada889914390847d5e90b8` | Dưới ngưỡng | 5 | 6 | 12,652 | 0,001892080 |
| 16 | `e8a9b36dea36cc9c246000b8c7cca976` | Xung đột thời gian | 8 | 6 | 20,389 | 0,002088620 |

Tổng 33 yêu cầu sinh phản hồi và 33 lần đếm token trước yêu cầu, 28 nhận định xuất bản, không có nhận định bị loại ở nhánh scalar claim hoặc lỗi schema trong lượt này. Input lớn nhất là 4.595 tokens, output lớn nhất 193 tokens. Trung bình toàn lượt 17,226 giây; riêng Coordinator trung bình 15,761 giây. Thời gian gồm gọi dịch vụ, đếm token và xử lý local, không phải độ trễ riêng detector hoặc cam kết near real-time trong sản xuất.

## Những gì detector và agent thể hiện được

| Index | Quan sát từ dữ liệu và tham chiếu đã khai báo |
|---:|---|
| 1 | Vận chuyển 33,944 ngày, so với trung bình tham chiếu 16,615 ngày và biên cảnh báo 29,702 ngày. Có cảnh báo chặng vận chuyển; score 57,895/100. |
| 2 | Vận chuyển 22,276 ngày, so với trung bình 9,890 ngày và biên 18,280 ngày. Có cảnh báo chặng vận chuyển; score 71,014/100. |
| 8 | Cả ba công đoạn dưới ngưỡng. Vận chuyển 8,168 ngày, ngắn hơn trung bình tham chiếu 4,066 ngày. |
| 9 | Cả ba công đoạn dưới ngưỡng. Duyệt đơn 1,421 ngày, dài hơn trung bình 1,046 ngày nhưng vẫn dưới biên 1,809 ngày. Ví dụ này cho thấy “dài hơn trung bình” khác với “vượt ngưỡng cảnh báo”. |
| 16 | Nguồn ghi bàn giao lúc 17:32:27 và duyệt lúc 17:36:01 ngày 24/04/2018: bàn giao sớm hơn duyệt 214 giây. Công đoạn chuẩn bị có duration âm; hệ thống không sửa thành 0 hoặc tự điền thời gian. Score và cờ cảnh báo của cả đơn giữ `null` do xung đột timeline. |

Các tham chiếu của 12 công đoạn được chấm điểm đều ở mức `category_context_fallback`, không phải đủ mẫu cùng một mã sản phẩm. Score là điểm theo phân vị, không phải xác suất tắc nghẽn. Những giá trị trong bảng được đọc từ detector đóng băng, không phải tất cả đều do AI lựa chọn đưa vào nhận định.

Context Analyst đã gọi so sánh cùng sản phẩm trên cả năm đơn. Với bốn đơn có thể đánh giá, số mẫu ghép được lần lượt là 1, 0, 0, 3, đều dưới mức tối thiểu 30. Với đơn xung đột thời gian, công cụ dừng ở `stage_unassessed`, chưa đếm mẫu. Không có phép so sánh nào đủ hỗ trợ kết luận về nguyên nhân hoặc địa lý.

Việc hai đơn dưới ngưỡng không bị đổi thành cảnh báo chỉ chứng minh giữ nguyên đầu ra detector trong lượt này. Không thể gọi đây là hai đơn “bình thường đã được xác nhận” hoặc dùng chúng tính specificity/accuracy độc lập.

## Sai sót mới phát hiện và lý do

**Bốn đơn index 2, 8, 9, 16 có ghi chú `stage_endpoint_not_observed` không đúng với bằng chứng.** Cả bốn mốc đặt hàng, duyệt, bàn giao và giao hàng đều hiện diện trong dữ liệu quan sát. Riêng index 16 có thứ tự thời gian mâu thuẫn; điều đó không đồng nghĩa thiếu mốc.

Index 16 còn ghi `insufficient_comparable_orders` dù công cụ dừng vì công đoạn chưa đánh giá được, chưa tính số mẫu tương đồng. Đây là nhận định chưa có bằng chứng về quy mô mẫu, cần phân biệt với bốn đơn thực sự đếm được ít hơn 30 mẫu.

Index 9 và 16 ghi `missing_context` chung chung. Bối cảnh cần thiết cho bộ công cụ hiện có vẫn hiện diện dưới giả định sẵn có, trong khi các yếu tố chưa kiểm soát cũng tồn tại. Do ghi chú không chỉ rõ thiếu trường nào, lượt rà soát đánh dấu “mơ hồ”, không quy chụp rằng mọi nhận định thiếu bối cảnh đều sai.

Nguyên nhân kỹ thuật có thể xác định trong code: `claim_ids` được kiểm tra bằng evidence ID, kiểu dữ liệu và giá trị nguồn; nhưng `unknowns` và `recommended_checks` hiện chỉ được kiểm tra thuộc danh sách mã hợp lệ. Verifier nhận gói kiểm chứng scalar claims, còn các ghi chú cuối cùng được hợp nhất từ hai analyst mà chưa qua điều kiện bằng chứng riêng. Vì vậy không có lỗi schema vẫn có thể có lỗi nội dung. Các đề xuất kiểm tra hiện cũng chưa được chứng minh tối ưu hoặc có ích cho từng tình huống.

Kết quả `evidence_checked` gốc được giữ để kiểm toán. Báo cáo bổ sung `all_output_evidence_supported=false`, không sửa lịch sử thành thành công hoàn toàn. Bốn trường hợp sai ghi chú trên năm đơn không phải ước lượng tỷ lệ lỗi chung của mô hình; mẫu quá nhỏ và được lấy theo nhóm tình huống.

## Kiểm chứng và chi phí

- Dựng lại toàn bộ bằng chứng từ nguồn và phát lại 33 phản hồi đã lưu bằng Coordinator hiện hành, với HTTP/API bị chặn. Detector, messages, lựa chọn claim, verdict, ghi chú và đầu ra xuất bản khớp kết quả thật.
- Tính lại 15 đánh giá công đoạn và đối chiếu 15 khoảng thời gian trực tiếp với timestamp raw. Có 14 khoảng dương và một khoảng âm; không thay giá trị âm bằng dữ liệu giả.
- 28 nhận định scalar khớp chính xác kiểu và giá trị nguồn. Các giá trị được code sao chép từ bằng chứng do AI chọn, nên đây không phải 28 dự đoán độc lập đúng của AI.
- 21 file được bảo vệ ngoài ledger giữ nguyên hash, gồm chín CSV raw. Manifest pilot và 11 file triển khai/cấu hình vẫn khớp; không thay mã sau bộ 60 kiểm thử offline trước đó.
- 18 biên nhận cũ giữ nguyên; ledger chỉ bổ sung 33 yêu cầu đã quyết toán, tổng 51. Không có khoản giữ chỗ chưa quyết toán hoặc vượt giới hạn.
- Biên nhận mới được xuất thành snapshot độc lập để lịch sử không phụ thuộc việc ledger tiếp tục được bổ sung sau này. Lệnh `--verify` kiểm tra bằng snapshot, không cần gọi API.

Chi phí nhóm là **0,009635015 USD**, khoảng **289 đồng** theo tỷ giá dự trù 30.000 VND/USD. Tổng tất cả lượt đã ghi trong ledger là **0,015692540 USD**; ngân sách local còn **4,984307460 USD**. Đây là tính từ usage, chưa đối chiếu giao diện Billing/hóa đơn; không bao gồm thuế, phí hoặc chênh lệch tỷ giá thanh toán.

Đơn giá được đối chiếu ngày 04/11/2026 theo [trang mô hình OpenAI](https://developers.openai.com/api/docs/models/gpt-6-luna): mỗi triệu tokens, input 0,10 USD, cached input 0,01 USD, cache write 0,125 USD và output 0,50 USD. Tính riêng từng loại usage cho từng biên nhận rồi cộng, khớp ledger.

## Dữ liệu lưu và cách kiểm tra lại

Snapshot: `data/experiments/olist_multi_agent_live_v2_batch5_2026-11-04/`.

- `approved_plan.json`: năm case, giới hạn chi và số tiền đã dùng trước lượt chạy.
- `execution.jsonl`: sự kiện bắt đầu/kết thúc theo thứ tự, thời lượng đo và kết quả trả về.
- `provider_receipts.json`: 33 phản hồi nguyên gốc, usage, dữ liệu hạch toán và kiểm tra bảo toàn 18 biên nhận cũ.
- `audit_summary.json`: số liệu từng case, bằng chứng kiểm tra và lỗi ghi chú phát hiện được.
- `audit_receipts.py`: chương trình kiểm chứng offline của thực nghiệm này.
- `manifest.json`: hash nguồn, kết quả, mã kiểm chứng và triển khai đã đóng băng.

```sh
.venv/bin/python -B data/experiments/olist_multi_agent_live_v2_batch5_2026-11-04/audit_receipts.py --verify
```

Snapshot dữ liệu bị Git bỏ qua; cần giữ bản sao lưu local cùng các snapshot cũ. Báo cáo và README có thể theo dõi bằng Git. Gói này chưa commit/push.

## Gói sửa đề xuất, chưa triển khai

Trước khi chạy thêm API hoặc xây phần hiển thị giải thích, bổ sung kiểm chứng bằng chứng cho toàn bộ ghi chú và đề xuất kiểm tra. Phân biệt rõ: thiếu mốc, mốc mâu thuẫn, thiếu mẫu đã đếm, chưa thể thực hiện so sánh, và thiếu trường bối cảnh cụ thể. Verifier cần nhìn thấy các mục này; khi mâu thuẫn, giữ đầu ra gốc và ghi nhận phần bị từ chối thay vì lặng lẽ sửa thành đúng.

Đề xuất thực hiện một gói offline: thiết kế hợp đồng mới, kiểm thử các lỗi vừa quan sát, rồi phát lại sáu case v2 đã lưu để kiểm tra khả năng chặn lỗi mà vẫn giữ bất định hợp lệ. Giữ nguyên detector và mọi kết quả v2; tạo phiên bản mới khi thay code. Gói offline không dùng thêm tiền API, nhưng cũng không chứng minh mô hình sẽ sinh phản hồi mới tốt hơn. Mọi lượt API phiên bản mới cần một đề xuất và phê duyệt riêng.

Chưa chốt chất lượng Multi-Agent, chưa so sánh với một agent/không agent, chưa có ground truth tắc nghẽn, chưa tích hợp dashboard và chưa chứng minh near real-time thực tế.

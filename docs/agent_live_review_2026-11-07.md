# Cặp thử API thật sau tối ưu và kiểm tra độ đầy đủ — 07/11/2026

Đã thực hiện đúng một cặp thử được duyệt trên cùng đơn phát triển `32df72b9f8dee31b9b3b8b8e31f2ba1d`, case key `olist-04029aa370b3a937565a`, ngành `bed_bath_table`. Ba agent hoàn tất và lần này nêu rõ cảnh báo vận chuyển. Một agent nhận hai phản hồi nhưng chưa xuất bản kết luận vì yêu cầu kế tiếp vượt trần token. Giữ nguyên cả kết quả thành công và bị chặn.

## Phạm vi và ngân sách

Dùng `gpt-6-luna`, định dạng `evidence_catalog_v2`, bộ kiểm tra `warning_coverage_v1`, nguồn/tham chiếu/điểm detector đã đóng băng và cùng mốc quan sát 31/05/2018 16:40:52. Chế độ vẫn là `assumed_static_context_at_purchase`; tính sẵn có của bối cảnh là giả định được công bố.

Giới hạn được duyệt: 6.000 token đầu vào, 1.200 token đầu ra mỗi yêu cầu; tối đa 4 lượt một agent, 12 lượt ba agent; tối đa 16 lượt tổng, dự trù 0,0216 USD. Mỗi vai trò có tối đa 4 lượt, gồm lượt sửa. Không tăng trần, thay mô hình, chạy lại hoặc chuyển sang đơn khác trong thử nghiệm.

Giá Standard đối chiếu ngày 07/11/2026: input 0,10 USD, cached input 0,01 USD, cache write 30 phút 0,125 USD và output 0,50 USD trên một triệu token. Giá khớp policy hiện có; không sửa ledger/policy lịch sử. [Nguồn OpenAI](https://developers.openai.com/api/docs/pricing).

Lệnh chạy mới `scripts/06_agent_evaluation/live/run_review_pilot.py` kiểm tra snapshot, tạo plan trước khi gọi, lưu ledger trước chạy, ghi từng payload và kết quả đếm token, áp giới hạn trên cả cặp, lưu biên nhận. Thư mục đã tồn tại sẽ chặn chạy lại. API key được đọc local và không ghi vào log.

## Kết quả

| Cấu hình | Phản hồi suy luận thật | Đầu vào/đầu ra đã ghi nhận | Thời gian đến khi hoàn tất/dừng | Chi phí suy luận USD | Trạng thái |
| --- | ---: | ---: | ---: | ---: | --- |
| Ba agent | 7 | 37.093 / 657 | 21,22 giây | 0,003077415 | Hoàn tất, cờ vận chuyển được nêu và kiểm chứng |
| Một agent | 2 | 11.186 / 86 | 4,15 giây | 0,001441100 | Chưa có kết luận; gói thứ ba 6.024 token bị chặn |

Tổng: 9 phản hồi suy luận, 1 yêu cầu hủy trước suy luận, chi phí 0,004518515 USD, khoản giữ chỗ còn lại 0. Có 12 lượt đếm token: 2 phép kiểm tra lịch sử trước chạy và 10 phép đếm gắn với các yêu cầu của thử nghiệm.

Tổng ledger sau thử: 71 yêu cầu đã quyết toán, 4 yêu cầu hủy trước suy luận, 6 case phân biệt; chi tiêu lũy kế 0,026095675 USD, còn 4,973904325 USD trong hạn mức local 5 USD. Đây là tính toán từ biên nhận usage và giá; đối chiếu số dư/hóa đơn nhà cung cấp thực hiện tại Billing.

Thời gian của một agent là thời gian đến khi dừng, không phải thời gian tạo một kết luận thành công. Không so hai con số thời gian này để kết luận một agent nhanh hơn về cùng đầu ra.

## Ba agent đã làm gì?

Process Analyst dùng một lượt và chọn ba nhận định cho vận chuyển: có cờ thời lượng bất thường; thời lượng 22,2756 ngày; dài hơn trung bình tham chiếu 12,3857 ngày. Nguồn tham chiếu kèm theo có 69 đơn, trung bình 9,8899 ngày, biên đuôi 18,2797 ngày, điểm 71,0145/100, mức vừa. Các số này được tính từ detector và công cụ nguồn; AI chọn nội dung có căn cứ để trình bày.

Context Analyst dùng ba lượt: lấy bối cảnh, so sánh nhóm cùng sản phẩm ở công đoạn vận chuyển, rồi chọn nhận định/ghi chú/đề xuất. Nhóm cùng sản phẩm ghép theo điều kiện của phép so sánh có 0 mẫu, không đủ mức tối thiểu 30. Điều này khác với nhóm dự phòng cấp ngành/bối cảnh gồm 69 đơn đang dùng để chấm cảnh báo. Không diễn giải thành không tồn tại đơn cùng sản phẩm trong toàn bộ Olist.

Evidence Verifier dùng ba lượt: kiểm tra hợp đồng thời gian/chia tập, tính lại số liệu vận chuyển, rồi đưa quyết định cho từng mục. Không có lỗi định dạng hoặc mục bị loại trong lượt này. Chu kỳ sửa do thiếu cảnh báo không phải kích hoạt vì nhận định ban đầu đã nêu cờ đúng.

Đầu ra có 12 mục công bố: 5 nhận định, 3 ghi chú và 4 đề xuất kiểm tra. Hai agent cùng đề nghị kiểm tra tuyến vận chuyển nên có một nội dung trùng. Tính theo mục riêng biệt là **11/11 mục khớp nguồn**; cờ cảnh báo được nêu **1/1**. Không tính mục trùng để nâng chỉ số.

Các ghi chú giữ rõ cơ sở ngày hẹn chưa xác minh, thời điểm bối cảnh có sẵn chưa xác minh và thiếu mẫu cùng sản phẩm. Đề xuất xem tuyến vận chuyển/đối soát timestamp không khẳng định tuyến vận chuyển là nguyên nhân. Đề xuất bổ sung khối lượng/kích thước là bổ sung vào bằng chứng so sánh hiện dùng, không phải kết luận toàn bộ Olist thiếu các cột này.

## Vì sao một agent vẫn bị chặn?

Kiểm tra trước chạy cho thấy gói lịch sử từng gây lỗi đã giảm từ 6.050 xuống **5.793 token**, nằm trong giới hạn. Tuy nhiên, lịch sử mới do các lựa chọn công cụ của mô hình tạo ra khác lịch sử cũ.

1. Yêu cầu đầu 5.295 token: mô hình chọn `compare_declared_peer_groups` cho vận chuyển và nhóm cùng sản phẩm.
2. Yêu cầu thứ hai 5.891 token: mô hình chọn `get_group_support_and_coverage` cho chính evidence ID đã trả về.
3. Công cụ trả lại cùng bản bằng chứng; không có bằng chứng nghiệp vụ mới. Dù dữ liệu lặp đã được dẫn lại bằng mã, lịch sử gọi công cụ vẫn dài hơn. Yêu cầu tiếp theo đạt **6.024 token**, vượt trần 24 token, nên bị hủy trước suy luận.

Không có phản hồi suy luận thứ ba, không có kết luận cuối và không gọi lượt sửa sau khi nhánh đã dừng vì ngân sách token. Kết quả này cho thấy cần quản lý vòng hỏi công cụ và thời điểm chuyển sang kết luận, ngoài việc rút gọn cách biểu diễn dữ liệu.

Gói lớn nhất của ba agent là 5.977 token, còn 23 token so với trần. Do đó chưa đủ cơ sở chạy rộng pilot mà không kiểm tra thêm độ bền với lịch sử công cụ dài hơn.

## Kiểm chứng và lưu trữ

Đã dựng lại hai luồng offline từ biên nhận thật, đối chiếu chính xác 10 yêu cầu gửi/chuẩn bị gửi, các phản hồi gốc, detector, trạng thái, ghi chú, nhận định và số lượt. Ledger lịch sử trước thử giữ nguyên; chỉ thêm các yêu cầu được duyệt. Cả 10 phép đếm gắn với yêu cầu được kiểm tra đúng fingerprint; các lượt đã suy luận có input usage khớp số đếm, lượt bị hủy có số đếm vượt trần.

131 kiểm thử liên quan đạt, gồm 6 kiểm thử mới về phạm vi case/role/model, tối đa 16 lượt và 4 lượt/vai trò, chặn lặp, chi phí kết hợp, token và thay đổi policy. Lượt kiểm chứng sau chạy có 0 HTTP/API mới, ledger không thay đổi. Đây là kiểm thử kỹ thuật; không phải 131 phép kiểm chứng nhãn tắc nghẽn.

- Snapshot: `data/experiments/olist_agent_live_review_v2_case2/`.
- Kết quả tổng: `summary.json`; gói và số token: `requests.jsonl`, `token_counts.jsonl`.
- Biên nhận: `three_agents_receipts.json`, `single_agent_receipts.json`, `all_receipts.json`.
- Kế hoạch/nguồn: `plan.json`, `budget_before.sqlite`, `manifest.json`.
- Kết quả hồi quy: `docs/agent_live_review_validation_2026-11-07.json`.

```sh
.venv/bin/python -B scripts/06_agent_evaluation/live/run_review_pilot.py --verify
```

Lệnh trên chỉ kiểm chứng offline. Lệnh `--live` từ chối chạy lại vì snapshot đã tồn tại.

## Ý nghĩa với khóa luận và bước đề xuất

Đã có ví dụ API thật cho thấy hệ ba agent chọn đúng công đoạn cảnh báo, nêu số liệu đối chiếu, truy vấn bối cảnh và kiểm chứng bằng chứng. Có thể trình bày diễn tiến này trong phần thiết kế/kiểm thử prototype, cùng kết quả lỗi được phát hiện và cải tiến qua các phiên bản.

Đây vẫn chỉ là một đơn phát triển đã xem nhiều lần, chưa có lặp để đo biến động mô hình. Đối chứng dùng giới hạn 4 lượt so với 12 lượt cho ba agent; không phải phép tách riêng ảnh hưởng của số agent với ngân sách bằng nhau. Chưa kết luận multi-agent tốt hơn tổng quát, chưa đo ích lợi quản lý độc lập hoặc độ chính xác tắc nghẽn. 11/11 mục khớp nguồn là kiểm tra tính nhất quán của đầu ra có kiểm soát, không phải 100% accuracy nghiệp vụ.

Đề xuất kế tiếp là gói offline quản lý vòng công cụ: nhận biết yêu cầu thông tin đã có, tiết kiệm lượt còn lại và ưu tiên trả kết luận có đủ bằng chứng trước khi lịch sử vượt giới hạn; kiểm tra công bằng trên cả hai cấu hình. Giữ nguyên kết quả thử này. Cần duyệt thiết kế và kiểm tra offline trước mọi lần API tiếp theo. Song song, có thể viết ngay phần dữ liệu, phương pháp, kiến trúc và case study đã có; chưa cần import thêm data cho bước kỹ thuật này.

"""Deterministic Vietnamese rendering of structured explanation outputs for human review.

Agents select typed items (claims, notes, follow-up checks); they do not write prose.
This renderer turns each selected item into one fixed Vietnamese sentence so that every
arm is shown in exactly the same style. Unknown codes are shown verbatim, never guessed.
"""
import html

STAGES = {'approval': 'Đặt hàng → duyệt đơn', 'preparation_handoff': 'Duyệt đơn → bàn giao vận chuyển',
          'transport': 'Bàn giao → giao khách', None: 'Toàn đơn'}

FIELDS = {
    'long_stage_warning': 'Cảnh báo công đoạn kéo dài',
    'duration_days': 'Thời lượng công đoạn (ngày)',
    'delay_score_0_100': 'Điểm cảnh báo (0–100, không phải xác suất)',
    'difference_from_reference_mean_days': 'Chênh lệch so với trung bình tham chiếu (ngày)',
    'selected_reference_mean_days': 'Trung bình nhóm tham chiếu (ngày)',
    'selected_reference_n': 'Số đơn trong nhóm tham chiếu',
    'selected_reference_scope': 'Loại nhóm tham chiếu',
    'upper_tail_boundary_days': 'Ngưỡng đuôi cảnh báo (ngày)',
    'assessment_status': 'Trạng thái đánh giá',
    'promise_basis_status': 'Cơ sở ngày hẹn',
    'observation_clock': 'Thời điểm quan sát',
    'status': 'Trạng thái',
    'n': 'Số đơn so sánh', 'mean_days': 'Trung bình nhóm so sánh (ngày)', 'median_days': 'Trung vị (ngày)',
    'training_only_before_assessment': 'Chỉ dùng lịch sử train trước thời điểm đánh giá',
    'context_availability_source_verified': 'Thời điểm có sẵn của bối cảnh đã được nguồn xác minh',
    'single_product_available': 'Đơn chỉ có một sản phẩm', 'single_seller_available': 'Đơn chỉ có một người bán',
    'payment_and_review_features': 'Đặc trưng thanh toán/đánh giá',
    'difference_from_peer_mean_days': 'Chênh lệch so với nhóm so sánh (ngày)', 'difference_days': 'Chênh lệch (ngày)',
    'target_n': 'Số đơn nhóm đích', 'control_n': 'Số đơn nhóm đối chứng',
    'target_coverage': 'Độ bao phủ nhóm đích', 'control_coverage': 'Độ bao phủ nhóm đối chứng', 'direction': 'Chiều chênh lệch',
}

NOTES = {
    'context_availability_unverified': 'Chưa xác minh thông tin bối cảnh (sản phẩm, người bán, địa lý) có sẵn tại thời điểm đặt hàng.',
    'promise_basis_unverified': 'Chưa xác minh phương pháp xác lập ngày giao dự kiến.',
    'insufficient_comparable_orders': 'Không đủ đơn tương đồng để so sánh (dưới mức tối thiểu).',
    'insufficient_comparison_coverage': 'Độ bao phủ của phép so sánh không đạt điều kiện.',
    'stage_endpoint_not_observed': 'Chưa quan sát được mốc đầu hoặc mốc cuối của công đoạn.',
    'timeline_conflict': 'Các mốc thời gian mâu thuẫn (mốc sau sớm hơn mốc trước).',
    'uncontrolled_factor': 'Còn yếu tố chưa được kiểm soát trong phép so sánh.',
    'attribution_unavailable': 'Không quy được công đoạn cho một người bán/sản phẩm cụ thể.',
    'feature_not_in_event_contract': 'Đặc trưng này không nằm trong dữ liệu được phép dùng tại thời điểm sự kiện.',
    'missing_context': 'Thiếu thông tin bối cảnh.',
    'comparison_not_performed_stage_unassessed': 'Chưa so sánh vì công đoạn chưa đánh giá được.',
}

CHECKS = {
    'verify_context_availability': 'Xác minh thời điểm có sẵn của thông tin bối cảnh.',
    'inspect_source_timestamps': 'Kiểm tra lại các mốc thời gian gốc của công đoạn.',
    'obtain_promise_method': 'Thu thập phương pháp xác lập ngày giao dự kiến.',
    'collect_more_comparable_history': 'Bổ sung lịch sử đơn tương đồng trước khi so sánh.',
    'review_carrier_route': 'Xem xét tuyến/đơn vị vận chuyển của đơn.',
    'review_seller_preparation': 'Xem xét khâu chuẩn bị hàng của người bán.',
    'collect_weight_dimensions': 'Bổ sung khối lượng/kích thước vào bằng chứng so sánh.',
    'collect_workload_stock': 'Thu thập dữ liệu khối lượng công việc/tồn kho.',
}

VALUES = {True: 'Có', False: 'Không', None: 'Chưa xác định',
          'category_context_fallback': 'Nhóm dự phòng cấp ngành có bối cảnh', 'product_context': 'Nhóm theo sản phẩm và bối cảnh',
          'assessed_under_context_assumption': 'Đã đánh giá (theo giả định bối cảnh)',
          'unverified': 'Chưa xác minh', 'insufficient_support': 'Không đủ mẫu',
          'available_under_explicit_assumption': 'Có sẵn theo giả định đã công bố (chưa xác minh)'}


def value_vi(v):
    if isinstance(v, bool) or v is None:
        return VALUES[v]
    if isinstance(v, float):
        return f'{v:,.2f}'.replace(',', ' ').replace('.', ',')
    return VALUES.get(v, str(v))


KIND_STATUS = {'peer_comparison': 'Kết quả so sánh với nhóm tương đồng', 'reference_membership': 'Nhóm tham chiếu',
               'available_context': 'Thông tin bối cảnh', 'frozen_stage_assessment': 'Đánh giá công đoạn'}
CASE_WIDE = {'context_availability_unverified', 'promise_basis_unverified', 'verify_context_availability',
             'obtain_promise_method'}


def claim_vi(c, kinds=None):
    stage = STAGES.get(c.get('stage'), c.get('stage'))
    field = FIELDS.get(c['field'], c['field'])
    if c['field'] == 'status' and kinds and kinds.get(c.get('evidence_id')) in KIND_STATUS:
        field = KIND_STATUS[kinds[c['evidence_id']]]
    if c.get('claim_type') == 'unknown':
        return f'[{stage}] {field}: chưa xác định từ bằng chứng hiện có.'
    return f'[{stage}] {field}: {value_vi(c.get("value"))}.'


def scope(m):
    return STAGES[None] if m['code'] in CASE_WIDE else STAGES.get(m.get('stage'), m.get('stage'))


def note_vi(m):
    return f'[{scope(m)}] ' + NOTES.get(m['code'], m['code'])


def check_vi(m):
    return f'[{scope(m)}] ' + CHECKS.get(m['code'], m['code'])


def output_sections(result):
    """Published content only, de-duplicated by rendered sentence (same rule for every arm)."""
    if result is None:
        return None
    seen = set()
    kinds = {e.get('evidence_id'): e.get('kind') for e in result.get('evidence', [])}

    def unique(lines):
        out = []
        for line in lines:
            if line not in seen:
                seen.add(line)
                out.append(line)
        return out
    return {'status': result.get('explanation_status'),
            'claims': unique(claim_vi(c, kinds) for c in result.get('published_claims', [])),
            'notes': unique(note_vi(m) for m in result.get('verified_notes', [])),
            'checks': unique(check_vi(m) for m in result.get('verified_recommended_checks', []))}


STATUS_VI = {'code_checked': 'Đã kiểm tra bằng code', 'evidence_checked': 'Đã kiểm chứng bằng chứng',
             'evidence_checked_with_rejections': 'Đã kiểm chứng, có mục bị loại',
             'incomplete_warning_coverage': 'Chưa nêu đủ công đoạn được cảnh báo',
             'inconclusive': 'Không đủ kết luận', 'unavailable_budget': 'Dừng do giới hạn tài nguyên',
             'unavailable_api': 'API không khả dụng'}


def detector_rows(detector):
    rows = []
    for r in detector:
        d = r['data']
        rows.append({'stage': STAGES.get(r['stage'], r['stage']), 'status': value_vi(d.get('assessment_status')),
                     'duration': value_vi(d.get('duration_days')),
                     'reference_mean': value_vi(d.get('selected_reference_mean_days')),
                     'reference_n': value_vi(d.get('selected_reference_n')),
                     'score': value_vi(d.get('delay_score_0_100')), 'warning': value_vi(d.get('long_stage_warning'))})
    return rows


def esc(s):
    return html.escape(str(s))

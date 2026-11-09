"""English twin of render_vi.py: same codes, same de-duplication, one fixed sentence per item.
Used by the bilingual dashboard. Unknown codes are shown verbatim, never guessed."""
STAGES = {'approval': 'Purchase → approval', 'preparation_handoff': 'Approval → carrier handoff',
          'transport': 'Handoff → customer delivery', None: 'Whole order'}

FIELDS = {
    'long_stage_warning': 'Long-stage warning', 'duration_days': 'Stage duration (days)',
    'delay_score_0_100': 'Warning score (0–100, not a probability)',
    'difference_from_reference_mean_days': 'Difference from reference mean (days)',
    'selected_reference_mean_days': 'Reference group mean (days)', 'selected_reference_n': 'Orders in reference group',
    'selected_reference_scope': 'Reference group type', 'upper_tail_boundary_days': 'Tail warning boundary (days)',
    'assessment_status': 'Assessment status', 'promise_basis_status': 'Promised-date basis',
    'observation_clock': 'Observation time', 'status': 'Status', 'n': 'Orders compared',
    'mean_days': 'Comparison group mean (days)', 'median_days': 'Median (days)',
    'training_only_before_assessment': 'Uses only training history before assessment',
    'context_availability_source_verified': 'Context availability time verified by source',
    'single_product_available': 'Order has a single product', 'single_seller_available': 'Order has a single seller',
    'payment_and_review_features': 'Payment/review features',
    'difference_from_peer_mean_days': 'Difference from peer group mean (days)', 'difference_days': 'Difference (days)',
    'target_n': 'Target group orders', 'control_n': 'Control group orders',
    'target_coverage': 'Target group coverage', 'control_coverage': 'Control group coverage', 'direction': 'Direction of difference',
}

NOTES = {
    'context_availability_unverified': 'It is not verified that context (product, seller, geography) was available at purchase time.',
    'promise_basis_unverified': 'The method used to set the promised delivery date is not verified.',
    'insufficient_comparable_orders': 'Not enough comparable orders to compare (below the minimum).',
    'insufficient_comparison_coverage': 'Comparison coverage does not meet the requirement.',
    'stage_endpoint_not_observed': 'The start or end milestone of the stage has not been observed.',
    'timeline_conflict': 'Milestones conflict (a later milestone is earlier than a previous one).',
    'uncontrolled_factor': 'Some factors are not controlled in the comparison.',
    'attribution_unavailable': 'The stage cannot be attributed to a single seller/product.',
    'feature_not_in_event_contract': 'This feature is not part of the data allowed at event time.',
    'missing_context': 'Context information is missing.',
    'comparison_not_performed_stage_unassessed': 'No comparison because the stage could not be assessed.',
}

CHECKS = {
    'verify_context_availability': 'Verify when the context information became available.',
    'inspect_source_timestamps': 'Re-check the original timestamps of the stage.',
    'obtain_promise_method': 'Obtain the method used to set the promised delivery date.',
    'collect_more_comparable_history': 'Collect more comparable order history before comparing.',
    'review_carrier_route': "Review the order's carrier/route.",
    'review_seller_preparation': "Review the seller's preparation step.",
    'collect_weight_dimensions': 'Add weight/dimensions to the comparison evidence.',
    'collect_workload_stock': 'Collect workload/stock data.',
}

VALUES = {True: 'Yes', False: 'No', None: 'Unknown',
          'category_context_fallback': 'Category-level fallback with context', 'product_context': 'Product and context group',
          'assessed_under_context_assumption': 'Assessed (under context assumption)',
          'unverified': 'Unverified', 'insufficient_support': 'Insufficient support',
          'available_under_explicit_assumption': 'Available under a declared assumption (unverified)'}

KIND_STATUS = {'peer_comparison': 'Peer group comparison result', 'reference_membership': 'Reference group',
               'available_context': 'Context information', 'frozen_stage_assessment': 'Stage assessment'}
CASE_WIDE = {'context_availability_unverified', 'promise_basis_unverified', 'verify_context_availability',
             'obtain_promise_method'}

STATUS_EN = {'code_checked': 'Checked by code', 'evidence_checked': 'Evidence verified',
             'evidence_checked_with_rejections': 'Verified, some items rejected',
             'incomplete_warning_coverage': 'Not all warned stages reported',
             'inconclusive': 'Inconclusive', 'unavailable_budget': 'Stopped by resource limit',
             'unavailable_api': 'API unavailable'}


def value_en(v):
    if isinstance(v, bool) or v is None:
        return VALUES[v]
    if isinstance(v, float):
        return f'{v:,.2f}'
    return VALUES.get(v, str(v))


def claim_en(c, kinds=None):
    stage = STAGES.get(c.get('stage'), c.get('stage'))
    field = FIELDS.get(c['field'], c['field'])
    if c['field'] == 'status' and kinds and kinds.get(c.get('evidence_id')) in KIND_STATUS:
        field = KIND_STATUS[kinds[c['evidence_id']]]
    if c.get('claim_type') == 'unknown':
        return f'[{stage}] {field}: not determined from available evidence.'
    return f'[{stage}] {field}: {value_en(c.get("value"))}.'


def scope(m):
    return STAGES[None] if m['code'] in CASE_WIDE else STAGES.get(m.get('stage'), m.get('stage'))


def output_sections(result):
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
            'claims': unique(claim_en(c, kinds) for c in result.get('published_claims', [])),
            'notes': unique(f'[{scope(m)}] ' + NOTES.get(m['code'], m['code']) for m in result.get('verified_notes', [])),
            'checks': unique(f'[{scope(m)}] ' + CHECKS.get(m['code'], m['code']) for m in result.get('verified_recommended_checks', []))}

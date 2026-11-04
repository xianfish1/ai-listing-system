from services.api import storage, title_selection, title_skill_presets


def rule_for(audience):
    return next(p['rule'] for p in title_skill_presets.list_presets() if p['scope']['audience'] == audience)


def context_for(audience):
    rule = storage._normalize_title_skill(rule_for(audience))
    rule.update(id='test', enabled=True, is_default=True)
    return title_selection.build_context(
        {'title_category': 'tshirt', 'title_audience': audience}, [rule], [], lambda _: [])


def test_children_presets_preserve_latest_order_and_source():
    boys = rule_for('boys')
    girls = rule_for('girls')
    assert boys['operator_template']['patterns'] == ['{style} damit {theme} {core} ootd short-sleeve clothes printed 2-12 years old']
    assert girls['operator_template']['patterns'] == ['Cute {theme} {core} Printed Cotton Short-Sleeve Tops korean OOTD 2-12 years old']
    assert boys['operator_template']['style_words'] == ['Cartoon', 'Anime']
    assert girls['operator_template']['companion_words'] == ['Princess', 'white', 'toddler', 'kuromi', 'Casual']
    for audience in ('boys', 'girls'):
        context = context_for(audience)
        assert '2026-11-02' in context['skill']['operator_template']['source']
        assert context['skill']['title_max_chars'] == 100
        prompt = title_selection.prompt_context(context)
        assert 'Preserve the relative order' in prompt
        assert 'COD' in prompt
        assert '2-12 years old' in prompt


def test_unconfirmed_material_and_age_are_reference_warnings_not_facts():
    context = context_for('girls')
    audit = title_selection.validate_title('Cute Cat T-Shirt for Kids Girl Printed Cotton 2-12 years old', context)
    assert audit['status'] == 'REVIEW'
    assert any('Cotton' in w and '待确认' in w for w in audit['warnings'])
    assert any('2-12 years old' in w and '待确认' in w for w in audit['warnings'])
    assert 'cotton' not in context['facts']
    assert title_selection.validate_title('Cute Cat T-Shirt for Kids Girl 100% Cotton', context)['status'] == 'BLOCKED'


def test_template_keeps_hard_limits_and_gender_gate():
    context = context_for('boys')
    assert title_selection.validate_title('Cartoon Cat t shirt for kids boy ' + 'x' * 100, context)['status'] == 'BLOCKED'
    assert title_selection.validate_title('Cute Cat T-Shirt for Kids Girl', context)['status'] == 'BLOCKED'
    audit = title_selection.validate_title('Cartoon Cat t shirt for kids boy printed', context)
    assert '2-12 years old' in audit['omitted_terms']


def test_reference_terms_roundtrip_and_standard_mode_stays_strict():
    rule = storage._normalize_title_skill(rule_for('girls'))
    assert rule['operator_template']['reference_terms'] == ['Cotton', '2-12 years old']
    context = context_for('girls')
    context['skill']['generation_mode'] = 'standard'
    assert title_selection.validate_title('Cat T-Shirt for Kids Girl Cotton', context)['status'] == 'BLOCKED'

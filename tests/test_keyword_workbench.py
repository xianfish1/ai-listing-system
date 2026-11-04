from __future__ import annotations

import base64
import io
import json
from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook

from services.api import settings, storage, keyword_workbench as kw


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(settings, 'STORAGE_DIR', tmp_path / 'storage')
    monkeypatch.setattr(settings, 'EXPORT_DIR', tmp_path / 'exports')
    monkeypatch.setattr(settings, 'STORE_FILE', tmp_path / 'store.json')
    monkeypatch.setattr(settings, 'STORAGE_BACKEND', 'json')
    storage.save_store(storage._empty_store())


def payload(**changes):
    library = next((item for item in kw.list_libraries() if item['name'] == 'PH test'), None)
    result = {'library_id': (library or kw.create_library({'name': 'PH test'}))['id'],
              'text': '关键词\t搜索量\t增长率\nmen tshirt\t123\t2.5%\nkids t shirt\t\t-1%',
              'source': 'PH export', 'source_type': 'measured', 'data_date': '2026-10-01',
              'scope_categories': ['tshirt'], 'scope_audiences': ['all']}
    result.update(changes)
    return result


def imported(**changes):
    preview = kw.preview_import(payload(**changes))
    assert preview['errors'] == []
    return kw.confirm_import({'preview_id': preview['preview_id']})['batch']


def test_import_preserves_dates_null_metrics_and_scopes():
    batch = imported()
    rows = kw.rows_for_batch(batch['id'])
    assert batch['data_date'] == '2026-10-01'
    assert batch['uploaded_at'] != batch['data_date']
    assert rows[0]['category'] == 'tshirt'
    assert rows[0]['audience'] == 'men'
    assert rows[1]['audience'] == 'kids'
    assert rows[1]['search_volume'] is None
    assert rows[0]['growth_rate_percent'] == 2.5
    assert rows[0]['raw_metrics']['搜索量'] == '123'


def test_duplicate_import_and_same_phrase_across_dates():
    p = payload()
    one = kw.confirm_import({'preview_id': kw.preview_import(p)['preview_id']})
    again_preview = kw.preview_import(p)
    assert again_preview['duplicate_batch_id'] == one['batch']['id']
    again = kw.confirm_import({'preview_id': again_preview['preview_id']})
    assert again['duplicate'] is True
    # A different dated export retains historical records.
    p.update(text=p['text'].replace('123', '124'), data_date='2026-10-02')
    two = kw.confirm_import({'preview_id': kw.preview_import(p)['preview_id']})
    assert two['batch']['id'] != one['batch']['id']
    assert len(kw.query_rows({'library_id': p['library_id']})['items']) == 4


def test_header_discovery_validation_and_preview_immutability():
    p = payload(source='', data_date='')
    initial = kw.preview_import(p)
    assert initial['headers'] == ['关键词', '搜索量', '增长率']
    assert initial['errors']
    with pytest.raises(ValueError):
        kw.confirm_import({'preview_id': initial['preview_id']})
    p.update(source='PH sheet', data_date='2026-10-01')
    valid = kw.preview_import(p)
    saved = kw.confirm_import({'preview_id': valid['preview_id'], 'data_date': '2099-01-01'})
    assert saved['batch']['data_date'] == '2026-10-01'


def test_dedupe_bad_numeric_and_pending_classification():
    p = payload(text='关键词\t搜索量\n Men  Tshirt \t12\nmen tshirt\t12\nspecial phrase\t9')
    preview = kw.preview_import(p)
    assert preview['total'] == 2
    rows = kw.rows_for_batch(kw.confirm_import({'preview_id': preview['preview_id']})['batch']['id'])
    assert rows[1]['status'] == 'pending'
    assert rows[1]['audience'] == 'unknown'
    bad = kw.preview_import({**p, 'text': '关键词\t搜索量\nmen tshirt\tbanana'})
    assert bad['errors'][0]['row'] == 2
    with pytest.raises(ValueError):
        kw.confirm_import({'preview_id': bad['preview_id']})


def test_xlsx_mapping_percentages_and_formula_rejection():
    book = Workbook()
    sheet = book.active
    sheet.append(['Phrase custom', 'Quantity', 'Growth'])
    sheet.append(['men tshirt', 100, .025])
    sheet['C2'].number_format = '0.0%'
    output = io.BytesIO()
    book.save(output)
    p = payload(text='', file_name='keywords.xlsx', file_base64=base64.b64encode(output.getvalue()).decode(),
                mapping={'word': 'Phrase custom', 'search_volume': 'Quantity', 'growth_rate_percent': 'Growth'})
    preview = kw.preview_import(p)
    assert preview['errors'] == []
    assert preview['rows'][0]['growth_rate_percent'] == 2.5
    sheet['B2'] = '=10*10'
    output = io.BytesIO()
    book.save(output)
    p['file_base64'] = base64.b64encode(output.getvalue()).decode()
    with pytest.raises(ValueError, match='公式'):
        kw.preview_import(p)


def test_query_bulk_edit_revision_and_safe_export():
    batch = imported()
    page = kw.query_rows({'batch_id': batch['id'], 'sort': 'search_volume', 'direction': 'desc', 'page_size': 1})
    assert page['total'] == 2
    assert page['items'][0]['word'] == 'men tshirt'
    row = page['items'][0]
    kw.update_rows({'ids': [row['id']], 'changes': {'search_volume': 321, 'notes': '=HYPERLINK("x")', 'status': 'disabled'}})
    assert storage.load_store()['keyword_revisions'][0]['before']['search_volume'] == 123
    assert kw.query_rows({'status': 'disabled'})['total'] == 1
    exported = load_workbook(io.BytesIO(kw.export_rows({'batch_id': batch['id']})))
    values = list(exported.active.values)
    assert len(values) == 3
    assert all(cell.data_type != 'f' for row in exported.active for cell in row)
    assert kw.rows_for_batch(batch['id'])[0]['raw_metrics']['搜索量'] == '123'


def test_update_atomic_and_reject_unknown_ids():
    batch = imported()
    row = kw.rows_for_batch(batch['id'])[0]
    with pytest.raises(ValueError):
        kw.update_rows({'ids': [row['id'], 'missing'], 'changes': {'status': 'disabled'}})
    assert kw.rows_for_batch(batch['id'])[0]['status'] == 'enabled'
    with pytest.raises(ValueError):
        kw.update_rows({'ids': [row['id']], 'changes': {'search_volume': -2}})


def test_initialize_backup_legacy_seed_idempotent(tmp_path):
    with storage.locked_store() as (data, save):
        data['hot_words'] = [{'id': 'legacy', 'word': 'tshirt for men', 'enabled': True}]
        save(data)
    first = kw.initialize()
    second = kw.initialize()
    assert first['initialized'] is True
    assert second['initialized'] is False
    backup = Path(first['backup_path'])
    assert json.loads(backup.read_text(encoding='utf-8'))['hot_words'][0]['id'] == 'legacy'
    seed = next(b for b in kw.list_batches() if b['data_date'] == '2026-09-20')
    assert seed['row_count'] == 1161
    legacy = next(b for b in kw.list_batches() if b['data_date'] is None)
    assert kw.rows_for_batch(legacy['id'])[0]['search_volume'] is None
    assert storage.load_store()['hot_words'][0]['id'] == 'legacy'
    ordered = kw.query_rows({'batch_id': seed['id'], 'sort': 'search_volume', 'direction': 'desc', 'page_size': 10})
    assert ordered['total'] == 1161
    assert ordered['items'][0]['search_volume'] == 173169
    assert kw.query_rows({'batch_id': seed['id'], 'audience': 'kids'})['total'] > 0
    assert kw.query_rows({'batch_id': seed['id'], 'page': 24, 'page_size': 50})['total'] == 1161
    assert len(kw.query_rows({'batch_id': seed['id'], 'page': 24, 'page_size': 50})['items']) == 11
    book = load_workbook(io.BytesIO(kw.export_rows({'batch_id': seed['id']})), read_only=True)
    assert book.active.max_row == 1162
    book.close()


def test_batch_enable_update_and_generation_history():
    batch = imported()
    assert kw.update_batch(batch['id'], {'enabled': False})['enabled'] is False
    assert kw.read_batch(batch['id'])['enabled'] is False
    audit = kw.record_generation({'product_id': 'p1', 'batch_id': batch['id'], 'title': 'Men Tshirt', 'rule_version': 2})
    assert kw.list_generations('p1')[0]['id'] == audit['id']


def test_expired_preview_cannot_commit():
    preview = kw.preview_import(payload())
    with storage.locked_store() as (data, save):
        data['keyword_import_previews'][0]['expires_at'] = '2000-01-01T00:00:00+00:00'
        save(data)
    with pytest.raises(ValueError, match='过期'):
        kw.confirm_import({'preview_id': preview['preview_id']})
    assert kw.list_batches() == []


def test_date_range_validation_and_metrics_do_not_claim_manual_evidence():
    invalid = kw.preview_import(payload(period_start='2026-10-02', period_end='2026-10-01'))
    assert any('开始日期' in error['message'] for error in invalid['errors'])
    bad_date = kw.preview_import(payload(data_date='October 1'))
    assert any('YYYY-MM-DD' in error['message'] for error in bad_date['errors'])
    batch = imported(source_type='manual')
    assert kw.rows_for_batch(batch['id'])[0]['evidence_level'] == 'MANUAL'


def test_null_metrics_sort_last_both_directions_and_filter_exports():
    batch = imported()
    for direction in ['asc', 'desc']:
        rows = kw.query_rows({'batch_id': batch['id'], 'sort': 'search_volume', 'direction': direction})['items']
        assert rows[-1]['search_volume'] is None
    book = load_workbook(io.BytesIO(kw.export_rows({'batch_id': batch['id'], 'audience': 'kids'})))
    assert book.active.max_row == 2
    assert book.active['A2'].value == 'kids t shirt'


@pytest.mark.parametrize(
    ("word", "expected"),
    [
        ("tshirt for boys", "boys"),
        ("boys graphic tee", "boys"),
        ("男童T恤", "boys"),
        ("tshirt for girls", "girls"),
        ("girls dress", "girls"),
        ("女童T恤", "girls"),
        ("kids tshirt", "kids"),
        ("tshirt for men", "men"),
        ("女装 T 恤", "women"),
    ],
)
def test_infer_audience_separates_boys_and_girls(word: str, expected: str) -> None:
    assert kw.infer_audience(word) == expected

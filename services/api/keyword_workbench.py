"""Dated PH keyword libraries, reviewable imports and immutable source metrics.

No background collection occurs here. Import previews are persisted for one hour;
confirmation consumes exactly the validated preview rather than client-sent rows.
"""
from __future__ import annotations

import base64
import binascii
import csv
import hashlib
import io
import json
import math
import re
import uuid
import zipfile
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from openpyxl import Workbook, load_workbook

from services.api import settings, storage

CATEGORIES = frozenset({'tshirt', 'set', 'hoodie', 'dress', 'other', 'all'})
# 主口径 boys/girls/men（男童/女童/男装）；kids/women/adult 兼容历史关键词。
AUDIENCES = frozenset({'boys', 'girls', 'men', 'kids', 'women', 'adult', 'all', 'unknown'})
SOURCE_TYPES = frozenset({'official', 'measured', 'manual', 'competitor', 'template'})
STATUSES = frozenset({'enabled', 'disabled', 'pending'})
METRICS = ('search_volume', 'avg_30d_search_volume', 'growth_rate_percent', 'product_count', 'supply_demand_ratio_percent')
ROW_FIELDS = ('word', 'category', 'audience', *METRICS, 'status', 'notes')
ALIASES = {
    'word': ['关键词', '搜索词', '热词', 'keyword', 'keywords', 'word', 'phrase'],
    'category': ['类目', '品类', '分类', 'category'],
    'audience': ['人群', '适用人群', 'audience', 'target_audience'],
    'search_volume': ['搜索量', '搜索次数', 'search_volume', 'metric_value'],
    'avg_30d_search_volume': ['30天日均', '30天日均搜索量', '近30天日均搜索量', '30天平均搜索量', 'avg_30d_search_volume'],
    'growth_rate_percent': ['增长率', '搜索增长率', '搜索量增长率', 'growth_rate_percent'],
    'product_count': ['商品数', '商品数量', 'product_count'],
    'supply_demand_ratio_percent': ['供需比', '供需比率', 'supply_demand_ratio_percent'],
    'status': ['使用状态', '状态', 'status'],
    'notes': ['备注', 'notes'],
}
SEED_PATH = Path(__file__).with_name('fixtures') / 'title_keywords_seed.json'
MAX_UPLOAD_BYTES = 12 * 1024 * 1024
MAX_ROWS = 50_000
MAX_COLUMNS = 150


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _id() -> str:
    return str(uuid.uuid4())


def _phrase(value: Any) -> str:
    return re.sub(r'\s+', ' ', str(value or '')).strip()


def _date(value: Any, label: str, required: bool = False) -> str | None:
    value = str(value or '').strip()
    if not value:
        if required:
            raise ValueError(f'请填写{label}')
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise ValueError(f'{label}格式须为 YYYY-MM-DD') from None
    if parsed.isoformat() != value:
        raise ValueError(f'{label}格式须为 YYYY-MM-DD')
    return value


def _number(value: Any, field: str) -> float | int | None:
    if value is None or str(value).strip() in {'', '-', '—', 'N/A', 'null'}:
        return None
    if isinstance(value, bool):
        raise ValueError(f'{field}必须是数字')
    try:
        number = float(str(value).strip().replace(',', '').replace('%', ''))
    except (ValueError, TypeError):
        raise ValueError(f'{field}必须是数字') from None
    if not math.isfinite(number) or (field != 'growth_rate_percent' and number < 0):
        raise ValueError(f'{field}必须为有效数字，除增长率外不得为负数')
    return int(number) if number.is_integer() else number


def _scope(payload: dict, key: str, allowed: frozenset[str]) -> list[str]:
    values = payload.get(key, ['all'])
    if not isinstance(values, list) or not values or any(value not in allowed for value in values):
        raise ValueError(f'{key}范围无效')
    return list(dict.fromkeys(values))


def infer_category(word: str, original: str = '') -> str:
    text = f'{word} {original}'.casefold()
    if re.search(r'\b(hoodie|hoodies|sweatshirt)\b|连帽|卫衣', text):
        return 'hoodie'
    if re.search(r'\b(dress|dresses)\b|连衣裙', text):
        return 'dress'
    if re.search(r'\b(set|sets|terno)\b|套装', word.casefold()):
        return 'set'
    if re.search(r'\b(t[ -]?shirts?|tees?)\b|t恤', text):
        return 'tshirt'
    return 'other'


def infer_audience(word: str) -> str:
    text = word.casefold()
    # 先分男童/女童，再落到泛童装：'boy'/'girl' 都是童装，但规则要能各选各的。
    if re.search(r'\bboys?\b|男童', text):
        return 'boys'
    if re.search(r'\bgirls?\b|女童', text):
        return 'girls'
    if re.search(r'\b(kids?|baby|babies|children|toddler)\b|童|儿童|婴', text):
        return 'kids'
    if re.search(r'\b(women|woman|ladies|female)\b|女装|女士', text):
        return 'women'
    if re.search(r'\b(men|man|mens|male)\b|男装|男士', text):
        return 'men'
    if re.search(r'\b(adults?|unisex)\b|成人', text):
        return 'adult'
    return 'unknown'


def _normalize_row(raw: dict, source_type: str, scope_categories: list[str], scope_audiences: list[str]) -> dict:
    word = _phrase(raw.get('word'))
    if not word or len(word) > 160:
        raise ValueError('关键词不能为空，且最多 160 个字符')
    category = str(raw.get('category') or '').strip()
    category_aliases = {'T恤': 'tshirt', 'T-shirt': 'tshirt', '套装': 'set', '卫衣': 'hoodie', '连衣裙': 'dress', '通用': 'all'}
    category = category_aliases.get(category, category)
    if category not in CATEGORIES:
        category = infer_category(word, category)
        if category == 'other' and len(scope_categories) == 1 and scope_categories[0] not in {'all', 'other'}:
            category = scope_categories[0]
    audience = str(raw.get('audience') or '').strip()
    audience = {'男童': 'boys', '女童': 'girls', '童装': 'kids', '男装': 'men', '女装': 'women', '成人': 'adult', '通用': 'all', '待分类': 'unknown'}.get(audience, audience)
    if audience not in AUDIENCES:
        audience = infer_audience(word)
        if audience == 'unknown' and len(scope_audiences) == 1 and scope_audiences[0] not in {'all', 'unknown'}:
            audience = scope_audiences[0]
    status = str(raw.get('status') or 'enabled').strip()
    status = {'启用': 'enabled', '停用': 'disabled', '待分类': 'pending'}.get(status, status)
    if status not in STATUSES:
        raise ValueError('使用状态无效')
    if audience == 'unknown' or category == 'other':
        status = 'pending' if status != 'disabled' else status
    return {
        'id': _id(), 'word': word, 'category': category, 'audience': audience,
        **{field: _number(raw.get(field), field) for field in METRICS},
        'status': status, 'notes': str(raw.get('notes') or '').strip()[:2000],
        'evidence_level': {'official': 'PH_OFFICIAL', 'measured': 'PH_MEASURED', 'manual': 'MANUAL',
                           'competitor': 'PH_COMPETITOR', 'template': 'TEMPLATE_ONLY'}[source_type],
    }


def list_libraries() -> list[dict]:
    return deepcopy(storage.load_store().get('keyword_libraries', []))


def create_library(payload: dict) -> dict:
    name = _phrase(payload.get('name'))
    if not name or len(name) > 100:
        raise ValueError('词库名称不能为空且最多 100 个字符')
    with storage.locked_store(split_keys=()) as (data, save):
        libraries = data.setdefault('keyword_libraries', [])
        if any(row['name'].casefold() == name.casefold() for row in libraries):
            raise ValueError('词库名称已存在')
        item = {'id': _id(), 'name': name, 'market': 'PH', 'created_at': _now()}
        libraries.append(item)
        save(data)
    return deepcopy(item)


def list_batches(library_id: str = '') -> list[dict]:
    result = [deepcopy(row) for row in storage.load_store().get('keyword_batches', [])
              if not library_id or row['library_id'] == library_id]
    return sorted(result, key=lambda row: (row.get('data_date') or '', row.get('uploaded_at') or ''), reverse=True)


def read_batch(batch_id: str) -> dict | None:
    return next((row for row in list_batches() if row['id'] == batch_id), None)


def rows_for_batch(batch_id: str) -> list[dict]:
    return [deepcopy(row) for row in storage.load_store().get('keyword_records', []) if row['batch_id'] == batch_id]


def update_batch(batch_id: str, payload: dict) -> dict | None:
    if not isinstance(payload.get('enabled'), bool):
        raise ValueError('批次启用状态必须为布尔值')
    with storage.locked_store(split_keys=()) as (data, save):
        batch = next((row for row in data.get('keyword_batches', []) if row['id'] == batch_id), None)
        if not batch:
            return None
        batch['enabled'] = payload['enabled']
        save(data)
    return deepcopy(batch)


def _read_upload(payload: dict) -> tuple[list[str], list[dict], str]:
    encoded = payload.get('file_base64')
    if encoded:
        if not isinstance(encoded, str) or len(encoded) > MAX_UPLOAD_BYTES * 1.4:
            raise ValueError('文件过大，最多 12 MB')
        try:
            content = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            raise ValueError('上传文件编码无效') from None
        if len(content) > MAX_UPLOAD_BYTES:
            raise ValueError('文件过大，最多 12 MB')
        file_name = str(payload.get('file_name') or '')
        if file_name.casefold().endswith('.xlsx'):
            try:
                with zipfile.ZipFile(io.BytesIO(content)) as archive:
                    if sum(info.file_size for info in archive.infolist()) > 100 * 1024 * 1024:
                        raise ValueError('Excel 解压后过大')
                book = load_workbook(io.BytesIO(content), read_only=True, data_only=False)
                sheet = book.active
                if sheet.max_row > MAX_ROWS + 1 or sheet.max_column > MAX_COLUMNS:
                    raise ValueError('Excel 最多 50,000 行、150 列')
                iterator = iter(sheet.rows)
                first = next(iterator, ())
                headers = [_phrase(cell.value) or f'列{index + 1}' for index, cell in enumerate(first)]
                rows = []
                for row_number, cells in enumerate(iterator, 2):
                    row = {}
                    for header, cell in zip(headers, cells):
                        if cell.data_type == 'f':
                            raise ValueError(f'第 {row_number} 行含公式，请粘贴为值后导入')
                        value = cell.value
                        if isinstance(value, (datetime, date)):
                            value = value.isoformat()
                        if isinstance(value, (int, float)) and '%' in cell.number_format:
                            value = f'{value * 100:g}%'
                        row[header] = value
                    if any(value not in ('', None) for value in row.values()):
                        rows.append(row)
                book.close()
            except (zipfile.BadZipFile, KeyError, OSError) as exc:
                raise ValueError('无法读取 Excel 文件，请使用 .xlsx 格式') from exc
            return headers, rows, hashlib.sha256(content).hexdigest()
        if not file_name.casefold().endswith(('.csv', '.tsv', '.txt', '.json')):
            raise ValueError('支持 .xlsx、.csv、.tsv 文件；旧版 .xls 请另存为 .xlsx')
        try:
            text = content.decode('utf-8-sig')
        except UnicodeDecodeError:
            raise ValueError('文本文件须使用 UTF-8 编码') from None
    else:
        text = str(payload.get('text') or '').lstrip('\ufeff')
        content = text.encode('utf-8')
    if not text.strip():
        raise ValueError('请上传文件或粘贴表格内容')
    if len(content) > MAX_UPLOAD_BYTES:
        raise ValueError('内容过大，最多 12 MB')
    if str(payload.get('file_name') or '').casefold().endswith('.json'):
        try:
            decoded = json.loads(text)
            rows = decoded.get('records', []) if isinstance(decoded, dict) else decoded
            if not isinstance(rows, list) or not rows or any(not isinstance(row, dict) for row in rows):
                raise ValueError('JSON 必须包含 records 记录列表')
        except json.JSONDecodeError:
            raise ValueError('JSON 文件内容无效') from None
        headers = list(dict.fromkeys(key for row in rows for key in row))
    else:
        dialect = '\t' if '\t' in text.splitlines()[0] else ','
        parsed = list(csv.reader(io.StringIO(text), delimiter=dialect))
        headers = [_phrase(value) or f'列{index + 1}' for index, value in enumerate(parsed[0])]
        rows = [dict(zip(headers, values)) for values in parsed[1:] if any(v.strip() for v in values)]
    if len(rows) > MAX_ROWS or len(headers) > MAX_COLUMNS:
        raise ValueError('每次最多 50,000 行、150 列')
    if len(set(headers)) != len(headers):
        raise ValueError('列标题重复，请修改为唯一名称')
    return headers, rows, hashlib.sha256(content).hexdigest()


def _auto_mapping(headers: list[str]) -> dict:
    normalized = {re.sub(r'[\s_%（）()]', '', header).casefold(): header for header in headers}
    result = {}
    for field, aliases in ALIASES.items():
        for alias in aliases:
            match = normalized.get(re.sub(r'[\s_%（）()]', '', alias).casefold())
            if match is not None:
                result[field] = match
                break
    return result


def preview_import(payload: dict) -> dict:
    headers, originals, file_hash = _read_upload(payload)
    if len(set(headers)) != len(headers):
        raise ValueError('列标题重复，请修改为唯一名称')
    mapping = _auto_mapping(headers)
    if payload.get('mapping') is not None:
        if not isinstance(payload['mapping'], dict):
            raise ValueError('列映射格式无效')
        mapping.update(payload['mapping'])
    errors = []
    for field, header in mapping.items():
        if field not in ROW_FIELDS or (header and header not in headers):
            errors.append({'row': 0, 'message': f'列映射无效：{field}'})
    if not mapping.get('word'):
        errors.append({'row': 0, 'message': '请选择关键词对应的列'})
    source_type = str(payload.get('source_type') or 'manual')
    library_id = str(payload.get('library_id') or '')
    source = str(payload.get('source') or '').strip()
    metadata = {'library_id': library_id, 'source': source, 'source_type': source_type,
                'name': str(payload.get('name') or payload.get('file_name') or '粘贴导入')[:200],
                'file_name': str(payload.get('file_name') or ''), 'file_hash': file_hash, 'market': 'PH'}
    if not any(row['id'] == library_id for row in list_libraries()):
        errors.append({'row': 0, 'message': '请选择有效词库'})
    if not source:
        errors.append({'row': 0, 'message': '请填写数据来源'})
    if source_type not in SOURCE_TYPES:
        errors.append({'row': 0, 'message': '数据来源类型无效'})
    for field, label in [('data_date', '数据日期'), ('period_start', '统计开始日期'), ('period_end', '统计结束日期')]:
        try:
            metadata[field] = _date(payload.get(field), label, required=field == 'data_date')
        except ValueError as exc:
            errors.append({'row': 0, 'message': str(exc)})
    if metadata.get('period_start') and metadata.get('period_end') and metadata['period_start'] > metadata['period_end']:
        errors.append({'row': 0, 'message': '统计开始日期不能晚于结束日期'})
    for key, allowed in [('scope_categories', CATEGORIES), ('scope_audiences', AUDIENCES - {'unknown'})]:
        try:
            metadata[key] = _scope(payload, key, allowed)
        except ValueError as exc:
            errors.append({'row': 0, 'message': str(exc)})
            metadata[key] = ['all']
    rows, seen, skipped = [], set(), 0
    for line, original in enumerate(originals, 2):
        raw = {field: original.get(header) for field, header in mapping.items() if field in ROW_FIELDS and header}
        try:
            row = _normalize_row(raw, source_type if source_type in SOURCE_TYPES else 'manual', metadata['scope_categories'], metadata['scope_audiences'])
        except ValueError as exc:
            errors.append({'row': line, 'message': str(exc)})
            continue
        key = row['word'].casefold()
        if key in seen:
            skipped += 1
            continue
        seen.add(key)
        row['raw_metrics'] = deepcopy(original)
        rows.append(row)
    if not rows:
        errors.append({'row': 0, 'message': '没有可导入的关键词'})
    duplicate = next((b['id'] for b in list_batches(library_id) if b.get('file_hash') == file_hash), None)
    preview_id = _id()
    now = _now()
    preview = {'id': preview_id, 'metadata': metadata, 'rows': rows, 'errors': errors,
               'created_at': now, 'expires_at': (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()}
    with storage.locked_store(split_keys=()) as (data, save):
        previews = [p for p in data.get('keyword_import_previews', []) if p['expires_at'] > now]
        previews.append(preview)
        data['keyword_import_previews'] = previews[-20:]
        save(data)
    return {'preview_id': preview_id, 'headers': headers, 'mapping': mapping, 'rows': deepcopy(rows[:50]),
            'errors': errors, 'total': len(rows), 'skipped': skipped, 'duplicate_batch_id': duplicate,
            'expires_at': preview['expires_at']}


def confirm_import(payload: dict) -> dict:
    with storage.locked_store(split_keys=()) as (data, save):
        preview = next((p for p in data.get('keyword_import_previews', []) if p['id'] == payload.get('preview_id')), None)
        if not preview or preview['expires_at'] < _now():
            raise ValueError('导入预览已过期，请重新预览')
        if preview['errors']:
            raise ValueError('请修正导入错误后重新预览')
        meta = preview['metadata']
        batches = data.setdefault('keyword_batches', [])
        duplicate = next((b for b in batches if b['library_id'] == meta['library_id'] and b.get('file_hash') == meta['file_hash']), None)
        if duplicate:
            return {'batch': deepcopy(duplicate), 'duplicate': True}
        batch = {**deepcopy(meta), 'id': _id(), 'uploaded_at': _now(), 'enabled': True, 'row_count': len(preview['rows'])}
        batches.append(batch)
        data.setdefault('keyword_records', []).extend({**deepcopy(row), 'batch_id': batch['id']} for row in preview['rows'])
        preview['confirmed_batch_id'] = batch['id']
        save(data)
    return {'batch': deepcopy(batch), 'duplicate': False}


def _filtered_rows(params: dict) -> list[dict]:
    data = storage.load_store()
    batches = {b['id']: b for b in data.get('keyword_batches', [])}
    search = str(params.get('search') or '').casefold().strip()
    result = []
    for row in data.get('keyword_records', []):
        batch = batches.get(row['batch_id'], {})
        if params.get('batch_id') and row['batch_id'] != params['batch_id']:
            continue
        if params.get('library_id') and batch.get('library_id') != params['library_id']:
            continue
        if any(params.get(field) and params[field] != row.get(field) for field in ('category', 'audience', 'status')):
            continue
        if search and search not in (row['word'] + ' ' + row.get('notes', '')).casefold():
            continue
        result.append({**deepcopy(row), 'data_date': batch.get('data_date'), 'source': batch.get('source', ''),
                       'source_type': batch.get('source_type', 'manual'), 'library_id': batch.get('library_id'),
                       'period_start': batch.get('period_start'), 'period_end': batch.get('period_end')})
    field = str(params.get('sort') or 'word')
    if field not in {*ROW_FIELDS, 'data_date', 'source'}:
        raise ValueError('排序字段无效')
    descending = str(params.get('direction') or 'asc').lower() == 'desc'
    present = [row for row in result if row.get(field) is not None]
    missing = [row for row in result if row.get(field) is None]
    present.sort(key=lambda row: row[field] if field in METRICS else str(row[field]).casefold(), reverse=descending)
    return present + missing


def query_rows(params: dict | None = None) -> dict:
    params = params or {}
    try:
        page, page_size = max(1, int(params.get('page') or 1)), min(200, max(1, int(params.get('page_size') or 50)))
    except (ValueError, TypeError):
        raise ValueError('分页参数必须是整数') from None
    rows = _filtered_rows(params)
    start = (page - 1) * page_size
    return {'items': rows[start:start + page_size], 'total': len(rows), 'page': page, 'page_size': page_size}


def update_rows(payload: dict) -> dict:
    ids = payload.get('ids')
    changes = payload.get('changes')
    if not isinstance(ids, list) or not ids or any(not isinstance(v, str) for v in ids):
        raise ValueError('请选择要修改的关键词')
    if not isinstance(changes, dict) or not changes or any(k not in ROW_FIELDS for k in changes):
        raise ValueError('修改字段无效')
    if len(ids) > 5000:
        raise ValueError('每次最多修改 5,000 条')
    with storage.locked_store(split_keys=()) as (data, save):
        lookup = {row['id']: row for row in data.get('keyword_records', [])}
        if any(row_id not in lookup for row_id in ids):
            raise ValueError('部分关键词不存在，请刷新后重试')
        updates = []
        for row_id in dict.fromkeys(ids):
            old = lookup[row_id]
            new = {**old, **changes}
            if new.get('category') not in CATEGORIES or new.get('audience') not in AUDIENCES or new.get('status') not in STATUSES:
                raise ValueError('品类、人群或状态无效')
            if new['status'] == 'enabled' and (new['audience'] == 'unknown' or new['category'] == 'other'):
                raise ValueError('请先明确品类和人群，再启用关键词')
            new['word'] = _phrase(new.get('word'))
            if not new['word'] or len(new['word']) > 160:
                raise ValueError('关键词不能为空且最多 160 个字符')
            new['notes'] = str(new.get('notes') or '').strip()[:2000]
            for metric in METRICS:
                new[metric] = _number(new.get(metric), metric)
            updates.append((old, new))
        resulting = {**lookup, **{new['id']: new for _, new in updates}}
        unique = set()
        for row in resulting.values():
            key = (row['batch_id'], row['word'].casefold())
            if key in unique:
                raise ValueError('同一批次内关键词不能重复')
            unique.add(key)
        revisions = data.setdefault('keyword_revisions', [])
        for old, new in updates:
            revisions.append({'id': _id(), 'row_id': old['id'], 'changed_at': _now(),
                              'before': {key: old.get(key) for key in changes}, 'after': {key: new.get(key) for key in changes}})
            old.update(new)
        save(data)
    return {'updated': len(updates), 'items': [deepcopy(new) for _, new in updates]}


def export_rows(params: dict | None = None) -> bytes:
    rows = _filtered_rows(params or {})
    book = Workbook()
    sheet = book.active
    sheet.title = '关键词'
    fields = ['word', 'category', 'audience', *METRICS, 'data_date', 'source', 'status', 'notes', 'period_start', 'period_end']
    sheet.append(['关键词', '品类', '人群', '搜索量', '30天日均', '增长率', '商品数', '供需比', '数据日期', '来源', '使用状态', '备注', '统计开始日期', '统计结束日期'])
    for row in rows:
        values = []
        for field in fields:
            value = row.get(field)
            if isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@')):
                value = "'" + value
            values.append(value)
        sheet.append(values)
    sheet.freeze_panes = 'B2'
    sheet.auto_filter.ref = sheet.dimensions
    sheet.column_dimensions['A'].width = 38
    output = io.BytesIO()
    book.save(output)
    return output.getvalue()


def record_generation(payload: dict) -> dict:
    item = {**deepcopy(payload), 'id': _id(), 'created_at': _now()}
    # title_generation_history 已拆成独立集合：必须显式点名，否则部分加载的 data 里
    # 没有它，setdefault 只会造出一条孤记录、save 还把它静默吞掉。
    with storage.locked_store(split_keys=('title_generation_history',)) as (data, save):
        data.setdefault('title_generation_history', []).append(item)
        save(data)
    return deepcopy(item)


def list_generations(product_id: str = '') -> list[dict]:
    return [deepcopy(row) for row in reversed(storage.load_store().get('title_generation_history', []))
            if not product_id or row.get('product_id') == product_id]


def initialize() -> dict:
    """One-time import without changing original rules or deleting legacy data."""
    if storage.load_store().get('keyword_workbench_version', 0) >= 1:
        return {'initialized': False}
    with storage.locked_store() as (data, save):
        if data.get('keyword_workbench_version', 0) >= 1:
            return {'initialized': False}
        backup_dir = settings.STORE_FILE.parent / 'backups'
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup = backup_dir / f'pre-keyword-workbench-{datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")}.json'
        backup.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        libraries = data.setdefault('keyword_libraries', [])
        batches = data.setdefault('keyword_batches', [])
        records = data.setdefault('keyword_records', [])
        seed_library = {'id': 'ph-keywords', 'name': '菲律宾服装关键词', 'market': 'PH', 'created_at': _now()}
        if not any(lib['id'] == seed_library['id'] for lib in libraries):
            libraries.append(seed_library)
        if SEED_PATH.is_file():
            seed = json.loads(SEED_PATH.read_text(encoding='utf-8'))
            batch = {'id': 'ph-keywords-20260920', 'library_id': seed_library['id'], 'name': 'Shopee标题 · 2026-09-20 历史快照',
                     'data_date': seed['collected_at'], 'source': seed.get('source_file', ''), 'source_type': 'measured',
                     'market': 'PH', 'period_start': None, 'period_end': None, 'uploaded_at': _now(), 'enabled': True,
                     'scope_categories': ['all'], 'scope_audiences': ['all'], 'file_hash': hashlib.sha256(SEED_PATH.read_bytes()).hexdigest(),
                     'row_count': len(seed['records'])}
            if not any(item['id'] == batch['id'] for item in batches):
                batches.append(batch)
                for original in seed['records']:
                    row = _normalize_row({**original, 'word': original['phrase'], 'search_volume': original.get('metric_value'), 'status': 'enabled'}, 'measured', ['all'], ['all'])
                    row.update(batch_id=batch['id'], raw_metrics=deepcopy(original))
                    records.append(row)
        legacy = data.get('hot_words', [])
        if legacy:
            lib = {'id': 'legacy-manual-keywords', 'name': '历史手动词库', 'market': 'PH', 'created_at': _now()}
            libraries.append(lib)
            batch = {'id': 'legacy-manual-batch', 'library_id': lib['id'], 'name': '历史手动热词 · 日期未知', 'market': 'PH',
                     'data_date': None, 'period_start': None, 'period_end': None, 'source': '旧版 Skills 热词列表',
                     'source_type': 'manual', 'uploaded_at': _now(), 'enabled': True, 'scope_categories': ['all'],
                     'scope_audiences': ['all'], 'row_count': 0}
            seen = set()
            for original in legacy:
                word = _phrase(original.get('word'))
                if not word or word.casefold() in seen:
                    continue
                seen.add(word.casefold())
                row = _normalize_row({'word': word, 'notes': original.get('tag', ''), 'status': 'enabled' if original.get('enabled', True) else 'disabled'}, 'manual', ['all'], ['all'])
                row.update(batch_id=batch['id'], raw_metrics=deepcopy(original), legacy_id=original.get('id'))
                records.append(row)
                batch['row_count'] += 1
            batches.append(batch)
        data['keyword_workbench_version'] = 1
        data['keyword_workbench_backup'] = str(backup)
        save(data)
    return {'initialized': True, 'backup_path': str(backup), 'libraries': len(libraries), 'batches': len(batches)}

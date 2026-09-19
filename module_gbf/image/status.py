import os
import re
import shutil

import requests

from module_huiji.danteng_downloader import Downloader
from module_huiji.danteng_lib import load_json, log, save_json
from ..util import get_skip_list
from config import DATA_PATH, GBF_CDN_URL, IMAGE_PATH, IMAGE_NEW_PATH, IMAGE_STATUS_PATH


STATUS_INDEX_VERSION = 1
STATUS_INDEX_PATH = os.path.join(DATA_PATH, 'status_index.json')
STATUS_ID_MIN = 0
STATUS_ID_MAX = 9999
STATUS_BLOCK_SIZE = 250
STATUS_BOOTSTRAP_MISS_LIMIT = 50
STATUS_INCREMENTAL_MISS_LIMIT = 25
STATUS_INCREMENTAL_LOOKBACK = 20
STATUS_RECENT_RECHECK_COUNT = 5
STATUS_MIN_FILE_SIZE = 200
STATUS_PROBE_TIMEOUT = 10

# GBF 的状态图标除无后缀版本外，还存在多种历史/特殊后缀。
# 这些是目前游戏资源中已知的命名形式。这里仅保留命名规律，运行时不再读取 GBFAL。
STATUS_SUFFIXES_EXTENDED = [
    '', '_1', '_2', '_10', '_11', '_101', '_110', '_111', '_20', '_30',
    '1', '_1_1', '_2_1', '_0_10', '_1_10', '_1_20', '_2_10',
    '1_1', '2_1', '3_1',
]
STATUS_SUFFIXES_LEGACY = [
    suffix for suffix in STATUS_SUFFIXES_EXTENDED
    if suffix not in {'1', '1_1', '2_1', '3_1'}
]


def _get_retry_times(cfg):
    retry_times = 5
    if 'IMAGE' in cfg and 'retry' in cfg['IMAGE']:
        try:
            retry_times = int(cfg['IMAGE']['retry'])
        except (TypeError, ValueError):
            pass
    return max(1, retry_times)


def _suffixes_for_id(status_id):
    if status_id >= 1000:
        return STATUS_SUFFIXES_EXTENDED
    return STATUS_SUFFIXES_LEGACY


def _sort_suffixes(status_id, suffixes):
    order = {suffix: i for i, suffix in enumerate(_suffixes_for_id(status_id))}
    return sorted(set(suffixes), key=lambda suffix: (order.get(suffix, 999), suffix))


def _load_status_index():
    try:
        data = load_json(STATUS_INDEX_PATH)
    except Exception as exc:
        log('读取状态图标索引失败，将重新建立：%s' % exc)
        return {}

    if not isinstance(data, dict):
        return {}

    # v1 使用 {"version": 1, "icons": {...}}；同时兼容早期直接保存的 ID -> suffixes 结构。
    icons = data.get('icons') if isinstance(data.get('icons'), dict) else data
    result = {}
    for raw_id, raw_suffixes in icons.items():
        if not str(raw_id).isdigit() or not isinstance(raw_suffixes, list):
            continue
        status_id = int(raw_id)
        if status_id < STATUS_ID_MIN or status_id > STATUS_ID_MAX:
            continue
        valid_suffixes = [
            str(suffix) for suffix in raw_suffixes
            if str(suffix) in _suffixes_for_id(status_id)
        ]
        if valid_suffixes:
            result[str(status_id)] = _sort_suffixes(status_id, valid_suffixes)
    return result


def _save_status_index(index):
    ordered = {}
    for key in sorted(index.keys(), key=lambda value: int(value)):
        status_id = int(key)
        suffixes = _sort_suffixes(status_id, index[key])
        if suffixes:
            ordered[str(status_id)] = suffixes
    save_json({
        'version': STATUS_INDEX_VERSION,
        'icons': ordered,
    }, STATUS_INDEX_PATH, indent=2)


def _parse_status_filename(filename):
    match = re.match(r'^status_(.+)\.png$', filename, flags=re.IGNORECASE)
    if match is None:
        return None

    stem = match.group(1)
    # ID 当前为 0~9999。个别后缀没有下划线，因此必须从最多 4 位 ID 开始尝试拆分。
    for width in range(min(4, len(stem)), 0, -1):
        id_part = stem[:width]
        suffix = stem[width:]
        if not id_part.isdigit():
            continue
        status_id = int(id_part)
        if status_id < STATUS_ID_MIN or status_id > STATUS_ID_MAX:
            continue
        if suffix in _suffixes_for_id(status_id):
            return status_id, suffix
    return None


def _merge_existing_files(index):
    status_dir = os.path.join(IMAGE_PATH, IMAGE_STATUS_PATH)
    if not os.path.isdir(status_dir):
        return 0

    added = 0
    for filename in os.listdir(status_dir):
        parsed = _parse_status_filename(filename)
        if parsed is None:
            continue
        status_id, suffix = parsed
        key = str(status_id)
        known = set(index.get(key, []))
        if suffix not in known:
            known.add(suffix)
            index[key] = _sort_suffixes(status_id, known)
            added += 1
    return added


def _response_size(response):
    try:
        return int(response.headers.get('Content-Length', 0))
    except (TypeError, ValueError):
        return 0


def _probe_url(session, url, retry_times):
    """Return True if a real icon exists, False for a confirmed miss, None for network uncertainty."""
    attempts = min(max(1, retry_times), 2)
    last_state = None

    for _ in range(attempts):
        response = None
        try:
            response = session.head(
                url,
                timeout=STATUS_PROBE_TIMEOUT,
                allow_redirects=True,
                verify=False,
            )
            status_code = response.status_code
            if status_code == 404:
                return False
            if status_code == 200:
                size = _response_size(response)
                if size > 0:
                    return size >= STATUS_MIN_FILE_SIZE

                # 极少数环境下 HEAD 不返回长度；只读取前 200 字节确认不是空白占位文件。
                response.close()
                response = session.get(
                    url,
                    headers={'Range': 'bytes=0-%d' % (STATUS_MIN_FILE_SIZE - 1)},
                    timeout=STATUS_PROBE_TIMEOUT,
                    stream=True,
                    verify=False,
                )
                if response.status_code not in (200, 206):
                    if response.status_code == 404:
                        return False
                    last_state = None
                    continue
                size = _response_size(response)
                if size > 0 and response.status_code == 200 and size < STATUS_MIN_FILE_SIZE:
                    return False
                content = next(response.iter_content(chunk_size=STATUS_MIN_FILE_SIZE), b'')
                return len(content) >= STATUS_MIN_FILE_SIZE

            # 5xx/限流等不当成“文件不存在”，避免因为临时网络问题漏掉 ID。
            last_state = None
        except requests.RequestException:
            last_state = None
        finally:
            if response is not None:
                response.close()

    return last_state


def _probe_status_filename(session, base_url, filename, retry_times, skip_list):
    url = base_url + filename
    if url in skip_list:
        return False
    return _probe_url(session, url, retry_times)


def _find_first_variant(session, base_url, status_id, retry_times, skip_list):
    uncertain = False
    for suffix in _suffixes_for_id(status_id):
        filename = 'status_%s%s.png' % (status_id, suffix)
        exists = _probe_status_filename(session, base_url, filename, retry_times, skip_list)
        if exists is True:
            return suffix, uncertain
        if exists is None:
            uncertain = True
    return None, uncertain


def _discover_missing_variants(session, base_url, status_id, known_suffixes, retry_times, skip_list):
    known = set(known_suffixes)
    added = 0
    uncertain = False

    for suffix in _suffixes_for_id(status_id):
        if suffix in known:
            continue
        filename = 'status_%s%s.png' % (status_id, suffix)
        exists = _probe_status_filename(session, base_url, filename, retry_times, skip_list)
        if exists is True:
            known.add(suffix)
            added += 1
        elif exists is None:
            uncertain = True

    return _sort_suffixes(status_id, known), added, uncertain


def _scan_status_index(index, cfg, retry_times, skip_list, bootstrap=False):
    """Discover status IDs directly from the official GBF CDN and update the local index."""
    base_url = cfg['base_url'] + 'ui/icon/status/x64/'
    new_ids = 0
    new_variants = 0
    had_network_error = False

    if bootstrap:
        log('未找到本地状态图标索引，将直接扫描 GBF 官方 CDN；首次运行可能需要较长时间。')
    else:
        log('已读取本地状态图标索引：%d 个状态 ID。' % len(index))

    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) VajraGo/1.0',
        'Accept': 'image/avif,image/webp,image/apng,image/*,*/*;q=0.8',
        'Connection': 'keep-alive',
    })
    requests.packages.urllib3.disable_warnings()

    try:
        for block_start in range(STATUS_ID_MIN, STATUS_ID_MAX + 1, STATUS_BLOCK_SIZE):
            block_end = min(block_start + STATUS_BLOCK_SIZE - 1, STATUS_ID_MAX)
            known_ids = sorted(
                int(key) for key in index.keys()
                if key.isdigit() and block_start <= int(key) <= block_end
            )
            last_known = known_ids[-1] if known_ids else block_start - 1
            miss_limit = STATUS_BOOTSTRAP_MISS_LIMIT if (bootstrap or not known_ids) else STATUS_INCREMENTAL_MISS_LIMIT

            if bootstrap or not known_ids:
                scan_start = block_start
            else:
                scan_start = max(block_start, last_known - STATUS_INCREMENTAL_LOOKBACK)

            consecutive_misses = 0
            consecutive_errors = 0
            block_changed = False

            for status_id in range(scan_start, block_end + 1):
                key = str(status_id)
                if key in index:
                    consecutive_misses = 0
                    consecutive_errors = 0
                    continue

                first_suffix, uncertain = _find_first_variant(
                    session, base_url, status_id, retry_times, skip_list
                )
                if first_suffix is not None:
                    suffixes, extra_added, extra_uncertain = _discover_missing_variants(
                        session,
                        base_url,
                        status_id,
                        [first_suffix],
                        retry_times,
                        skip_list,
                    )
                    index[key] = suffixes
                    new_ids += 1
                    new_variants += 1 + extra_added
                    block_changed = True
                    consecutive_misses = 0
                    consecutive_errors = 0
                    had_network_error = had_network_error or extra_uncertain
                    log('发现状态图标 ID %d，共 %d 个文件变体。' % (status_id, len(suffixes)))
                    continue

                if uncertain:
                    had_network_error = True
                    consecutive_errors += 1
                    # 连续多个 ID 都无法确定时更可能是网络/CDN异常，停止当前分段，避免误判和长时间卡住。
                    if consecutive_errors >= 3:
                        log('状态图标扫描在 ID %d 附近连续遇到网络异常，暂时停止当前分段。' % status_id)
                        break
                    continue

                consecutive_errors = 0
                consecutive_misses += 1
                # 已有索引时必须至少扫描到该分段当前最高已知 ID；之后连续 miss 达阈值即可停止。
                if status_id > last_known and consecutive_misses >= miss_limit:
                    break

            # 已有索引时，额外复查每个 250 ID 分段中较新的若干 ID，捕获后来新增的特殊后缀。
            current_known_ids = sorted(
                int(key) for key in index.keys()
                if key.isdigit() and block_start <= int(key) <= block_end
            )
            if not bootstrap and current_known_ids:
                for status_id in current_known_ids[-STATUS_RECENT_RECHECK_COUNT:]:
                    key = str(status_id)
                    suffixes, added, uncertain = _discover_missing_variants(
                        session,
                        base_url,
                        status_id,
                        index[key],
                        retry_times,
                        skip_list,
                    )
                    if added > 0:
                        index[key] = suffixes
                        new_variants += added
                        block_changed = True
                        log('状态图标 ID %d 新发现 %d 个文件变体。' % (status_id, added))
                    had_network_error = had_network_error or uncertain

            # 首次扫描可能持续较久，分段保存，意外中断后下次可以从本地索引继续。
            if block_changed or bootstrap:
                _save_status_index(index)
    finally:
        session.close()

    _save_status_index(index)
    if not index:
        log('未能从 GBF 官方 CDN 建立状态图标索引，请检查网络连接后重试。')
        return False
    log('状态图标索引更新完成：新增 %d 个 ID、%d 个文件变体。' % (new_ids, new_variants))
    if had_network_error:
        log('扫描期间有部分请求遇到网络异常；已发现的数据已保存，下次运行会继续检查。')
    return True


def _iter_indexed_filenames(index):
    for key in sorted(index.keys(), key=lambda value: int(value)):
        status_id = int(key)
        for suffix in _sort_suffixes(status_id, index[key]):
            yield 'status_%s%s.png' % (status_id, suffix)


def status(cfg):
    """Discover and download battle status/buff icons without external indexes."""
    save_to_new = False
    if 'IMAGE' in cfg and cfg['IMAGE'].get('new', '').lower() == 'yes':
        save_to_new = True

    retry_times = _get_retry_times(cfg)
    skip_list = set(get_skip_list(include_log=False))

    index = _load_status_index()
    bootstrap = len(index) == 0
    imported = _merge_existing_files(index)
    if imported > 0:
        log('从 IMAGE/status/ 补充了 %d 条本地状态图标索引记录。' % imported)
        _save_status_index(index)

    if not _scan_status_index(index, cfg, retry_times, skip_list, bootstrap=bootstrap):
        return False

    downloader = Downloader()
    downloader.set_try_count(retry_times)

    primary_base_url = cfg['base_url'] + 'ui/icon/status/x64/'
    english_base_url = f'{GBF_CDN_URL}/assets_en/img/sp/ui/icon/status/x64/'

    indexed_count = 0
    queued_count = 0
    for source_filename in _iter_indexed_filenames(index):
        indexed_count += 1
        source_url = primary_base_url + source_filename
        fallback_url = english_base_url + source_filename

        if source_url in skip_list or fallback_url in skip_list:
            continue

        save_path = os.path.join(IMAGE_PATH, IMAGE_STATUS_PATH, source_filename)
        save_new_path = os.path.join(IMAGE_PATH, IMAGE_NEW_PATH, source_filename)

        if os.path.exists(save_path):
            if save_to_new and not os.path.exists(save_new_path):
                os.makedirs(os.path.dirname(save_new_path), exist_ok=True)
                shutil.copy2(save_path, save_new_path)
            continue

        save_list = [save_path]
        if save_to_new and not os.path.exists(save_new_path):
            save_list.append(save_new_path)

        downloader.download_multi_copies(
            source_url,
            save_list,
            fallback_url=fallback_url,
            write_skip_log=False,
        )
        queued_count += 1

    log('本地状态图标索引共 %d 个文件，本次需要下载 %d 个。' % (indexed_count, queued_count))
    downloader.wait_threads()
    return True

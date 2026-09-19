import os
import re
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from module_huiji.danteng_downloader import Downloader
from module_huiji.danteng_lib import load_json, log, save_json
from ..util import get_skip_list
from .status_seed import STATUS_SEED
from config import DATA_PATH, GBF_CDN_URL, IMAGE_PATH, IMAGE_NEW_PATH, IMAGE_STATUS_PATH


STATUS_INDEX_VERSION = 2
STATUS_INDEX_PATH = os.path.join(DATA_PATH, 'status_index.json')
STATUS_ID_MIN = 0
STATUS_ID_MAX = 10999
STATUS_SCAN_RANGES = [
    (0, 999),
    (1000, 2999),
    (3000, 3999),
    (4000, 4999),
    (5000, 5999),
    (6000, 7999),
    (8000, 8999),
    (9000, 9999),
    (10000, 10999),
]
STATUS_INCREMENTAL_LOOKBACK = 24
STATUS_FRONTIER_MISS_LIMIT = 25
STATUS_DEFAULT_SCAN_WORKERS = 16
STATUS_MIN_FILE_SIZE = 200
STATUS_PROBE_TIMEOUT = 10

# 这组后缀只用于“发现一个新的状态 ID 是否存在”。
# 其规则来自 GBFAL updater 的 search_buff；历史完整数据已经内置在 status_seed.py，
# 运行时不再访问 GBFAL。
STATUS_DISCOVERY_SUFFIXES_EXTENDED = [
    '', '_1', '_2', '_10', '_11', '_101', '_110', '_111', '_20', '_30',
    '1', '_01', '3', '_1_1', '_2_1', '_0_10', '_1_10', '_1_20', '_2_10',
    '1_1', '2_1', '3_1',
]
STATUS_DISCOVERY_SUFFIXES_LEGACY = [
    suffix for suffix in STATUS_DISCOVERY_SUFFIXES_EXTENDED
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


def _get_scan_workers(cfg):
    workers = STATUS_DEFAULT_SCAN_WORKERS
    if 'IMAGE' in cfg and 'status_threads' in cfg['IMAGE']:
        try:
            workers = int(cfg['IMAGE']['status_threads'])
        except (TypeError, ValueError):
            pass
    return min(32, max(2, workers))


def _suffix_sort_key(suffix):
    if suffix == '':
        return (0,)
    parts = re.split(r'(\d+)', suffix)
    key = [1]
    for part in parts:
        if part == '':
            continue
        if part.isdigit():
            key.append((0, int(part)))
        else:
            key.append((1, part))
    return tuple(key)


def _normalize_suffixes(suffixes):
    result = []
    seen = set()
    for suffix in suffixes:
        suffix = str(suffix)
        if suffix in seen:
            continue
        seen.add(suffix)
        result.append(suffix)
    result.sort(key=_suffix_sort_key)
    return result


def _seed_index():
    return {
        str(status_id): _normalize_suffixes(suffixes)
        for status_id, suffixes in STATUS_SEED.items()
    }


def _load_local_index():
    try:
        data = load_json(STATUS_INDEX_PATH)
    except Exception as exc:
        log('读取本地状态图标索引失败，将使用内置历史索引：%s' % exc)
        return {}

    if not isinstance(data, dict):
        return {}

    icons = data.get('icons') if isinstance(data.get('icons'), dict) else data
    result = {}
    for raw_id, raw_suffixes in icons.items():
        if not str(raw_id).isdigit() or not isinstance(raw_suffixes, list):
            continue
        status_id = int(raw_id)
        if status_id < STATUS_ID_MIN or status_id > STATUS_ID_MAX:
            continue
        suffixes = _normalize_suffixes(raw_suffixes)
        if suffixes:
            result[str(status_id)] = suffixes
    return result


def _merge_index(target, source):
    added_ids = 0
    added_files = 0
    for raw_id, suffixes in source.items():
        key = str(int(raw_id))
        before = set(target.get(key, []))
        after = before | set(str(suffix) for suffix in suffixes)
        if key not in target:
            added_ids += 1
        added_files += len(after - before)
        target[key] = _normalize_suffixes(after)
    return added_ids, added_files


def _save_status_index(index):
    ordered = {}
    for key in sorted(index.keys(), key=lambda value: int(value)):
        suffixes = _normalize_suffixes(index[key])
        if suffixes:
            ordered[str(int(key))] = suffixes
    save_json({
        'version': STATUS_INDEX_VERSION,
        'icons': ordered,
    }, STATUS_INDEX_PATH, indent=2)


def _count_index_files(index):
    return sum(len(suffixes) for suffixes in index.values())


def _merge_existing_files(index):
    """Use already-downloaded files to supplement known IDs without guessing historical data."""
    status_dir = os.path.join(IMAGE_PATH, IMAGE_STATUS_PATH)
    if not os.path.isdir(status_dir):
        return 0

    added = 0
    known_ids = set(index.keys())
    for filename in os.listdir(status_dir):
        match = re.match(r'^status_(.+)\.png$', filename, flags=re.IGNORECASE)
        if match is None:
            continue
        stem = match.group(1)

        # Prefer the longest ID that is already known in the bundled/local index.
        parsed = None
        for width in range(min(5, len(stem)), 0, -1):
            id_part = stem[:width]
            if not id_part.isdigit():
                continue
            key = str(int(id_part))
            if key in known_ids:
                parsed = (key, stem[width:])
                break
        if parsed is None:
            continue

        key, suffix = parsed
        known = set(index.get(key, []))
        if suffix not in known:
            known.add(suffix)
            index[key] = _normalize_suffixes(known)
            added += 1
    return added


def _new_probe_session():
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) VajraGo/1.0',
        'Accept': 'image/avif,image/webp,image/apng,image/*,*/*;q=0.8',
        'Connection': 'keep-alive',
    })
    return session


def _response_size(response):
    try:
        return int(response.headers.get('Content-Length', 0))
    except (TypeError, ValueError):
        return 0


def _probe_url(session, url, retry_times):
    """True=real icon, False=confirmed missing/placeholder, None=network uncertainty."""
    attempts = min(max(1, retry_times), 2)

    for _ in range(attempts):
        response = None
        try:
            response = session.head(
                url,
                timeout=STATUS_PROBE_TIMEOUT,
                allow_redirects=True,
                verify=False,
            )
            if response.status_code == 404:
                return False
            if response.status_code == 200:
                size = _response_size(response)
                if size > 0:
                    return size >= STATUS_MIN_FILE_SIZE

                response.close()
                response = session.get(
                    url,
                    headers={'Range': 'bytes=0-%d' % (STATUS_MIN_FILE_SIZE - 1)},
                    timeout=STATUS_PROBE_TIMEOUT,
                    stream=True,
                    verify=False,
                )
                if response.status_code == 404:
                    return False
                if response.status_code not in (200, 206):
                    continue

                size = _response_size(response)
                if size > 0 and response.status_code == 200 and size < STATUS_MIN_FILE_SIZE:
                    return False
                content = next(response.iter_content(chunk_size=STATUS_MIN_FILE_SIZE), b'')
                return len(content) >= STATUS_MIN_FILE_SIZE
        except requests.RequestException:
            pass
        finally:
            if response is not None:
                response.close()

    return None


def _probe_suffix(session, base_url, status_id, suffix, retry_times, skip_list):
    filename = 'status_%s%s.png' % (status_id, suffix)
    url = base_url + filename
    if url in skip_list:
        return False
    return _probe_url(session, url, retry_times)


def _discovery_suffixes(status_id):
    if status_id >= 1000:
        return STATUS_DISCOVERY_SUFFIXES_EXTENDED
    return STATUS_DISCOVERY_SUFFIXES_LEGACY


def _find_first_variant(session, base_url, status_id, retry_times, skip_list):
    uncertain = False
    for suffix in _discovery_suffixes(status_id):
        exists = _probe_suffix(
            session, base_url, status_id, suffix, retry_times, skip_list
        )
        if exists is True:
            return suffix, uncertain
        if exists is None:
            uncertain = True
    return None, uncertain


def _scan_variant_mode(mode, base_url, status_id, known_suffixes, retry_times, skip_list):
    """Probe one variation family. The stopping rules mirror GBFAL's updater logic."""
    found = set()
    uncertain = False
    known = set(known_suffixes)
    session = _new_probe_session()

    def check(suffix):
        nonlocal uncertain
        if suffix in known or suffix in found:
            return True
        exists = _probe_suffix(
            session, base_url, status_id, suffix, retry_times, skip_list
        )
        if exists is True:
            found.add(suffix)
            return True
        if exists is None:
            uncertain = True
        return False

    try:
        if mode == 0:
            check('')

        elif mode == 1:
            err = 0
            n = 0
            while err < 3 and n < 10:
                suffix = '_' + str(n)
                if check(suffix):
                    err = 0
                else:
                    err += 1
                n += 1

        elif mode == 2:
            if status_id < 1000:
                return found, uncertain
            err = 0
            n = 0
            while err < 5 and n < 100:
                suffix = str(n)
                if check(suffix):
                    err = 0
                else:
                    err += 1
                n += 1

        elif mode == 3:
            err_limit = 10 if status_id in (3000, 1008) else 4
            for x in range(1, 10):
                n = 10 * x
                end = n + 10
                err = 0
                while err < err_limit and n < end:
                    suffix = '_' + str(n)
                    if check(suffix):
                        err = 0
                    else:
                        err += 1
                    n += 1

        elif mode == 4:
            for x in range(1, 8):
                n = 0
                err = 0
                while err < 3 and n < 100:
                    suffix = '_' + str(x) + str(n).zfill(2)
                    if check(suffix):
                        err = 0
                    else:
                        err += 1
                        if err == 3 and n < 10:
                            n = 9
                            err = 0
                    n += 1
            check('_110')

        elif mode == 5:
            base_limit = 22 if status_id in (6579, 6967) else 10
            err_limit = 6 if status_id == 1019 else 4
            for x in range(base_limit):
                n = 0
                err = 0
                while err < err_limit and n < 200:
                    suffix = '_' + str(x) + '_' + str(n)
                    if check(suffix):
                        err = 0
                    else:
                        err += 1
                    n += 1

        elif mode == 6:
            if status_id < 1000:
                return found, uncertain
            for x in range(10):
                n = 0
                err = 0
                while err < 4 and n < 100:
                    suffix = str(x) + '_' + str(n)
                    if check(suffix):
                        err = 0
                    else:
                        err += 1
                    n += 1
    finally:
        session.close()

    return found, uncertain


def _discover_all_variants(base_url, status_id, initial_suffixes, retry_times, skip_list):
    known = set(initial_suffixes)
    uncertain = False

    with ThreadPoolExecutor(max_workers=7) as executor:
        futures = [
            executor.submit(
                _scan_variant_mode,
                mode,
                base_url,
                status_id,
                known,
                retry_times,
                skip_list,
            )
            for mode in range(7)
        ]
        for future in as_completed(futures):
            try:
                found, mode_uncertain = future.result()
                known.update(found)
                uncertain = uncertain or mode_uncertain
            except Exception:
                uncertain = True

    return _normalize_suffixes(known), uncertain


def _scan_block(block_start, block_end, index, base_url, retry_times, skip_list):
    """Scan only the frontier/lookback of one 250-ID block."""
    known_ids = sorted(
        int(key) for key in index.keys()
        if key.isdigit() and block_start <= int(key) <= block_end
    )
    last_known = known_ids[-1] if known_ids else block_start - 1

    if known_ids:
        scan_start = max(block_start, last_known - STATUS_INCREMENTAL_LOOKBACK)
    else:
        scan_start = block_start

    discoveries = {}
    had_network_error = False
    consecutive_misses = 0
    consecutive_errors = 0
    frontier = last_known
    session = _new_probe_session()

    try:
        for status_id in range(scan_start, block_end + 1):
            key = str(status_id)
            if key in index or key in discoveries:
                consecutive_misses = 0
                consecutive_errors = 0
                continue

            first_suffix, uncertain = _find_first_variant(
                session, base_url, status_id, retry_times, skip_list
            )
            if first_suffix is not None:
                suffixes, variant_uncertain = _discover_all_variants(
                    base_url,
                    status_id,
                    [first_suffix],
                    retry_times,
                    skip_list,
                )
                discoveries[key] = suffixes
                frontier = max(frontier, status_id)
                consecutive_misses = 0
                consecutive_errors = 0
                had_network_error = had_network_error or variant_uncertain
                continue

            if uncertain:
                had_network_error = True
                consecutive_errors += 1
                if consecutive_errors >= 3:
                    break
                continue

            consecutive_errors = 0
            consecutive_misses += 1

            # Lookback 区间不能提前终止；越过原有最高 ID 后才按连续 miss 收敛。
            if status_id > last_known and consecutive_misses >= STATUS_FRONTIER_MISS_LIMIT:
                break
    finally:
        session.close()

    return block_start, discoveries, had_network_error, frontier


def _scan_incremental(index, cfg, retry_times, skip_list):
    base_url = cfg['base_url'] + 'ui/icon/status/x64/'
    workers = _get_scan_workers(cfg)
    requests.packages.urllib3.disable_warnings()

    # 状态 ID 本身按用途分成几个大区段。历史数据已经由内置快照覆盖，
    # 日常只扫描每个区段的最新前沿和少量回看范围，不再逐个扫完整 0~9999。
    blocks = list(STATUS_SCAN_RANGES)

    log('开始增量扫描 GBF 官方 CDN：%d 个分段，%d 线程。' % (len(blocks), workers))

    new_ids = 0
    new_files = 0
    had_network_error = False
    snapshot = {
        key: list(value)
        for key, value in index.items()
    }

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [
            executor.submit(
                _scan_block,
                block_start,
                block_end,
                snapshot,
                base_url,
                retry_times,
                skip_list,
            )
            for block_start, block_end in blocks
        ]

        for future in as_completed(futures):
            try:
                _, discoveries, network_error, _ = future.result()
            except Exception as exc:
                had_network_error = True
                log('状态图标分段扫描异常：%s' % exc)
                continue

            had_network_error = had_network_error or network_error
            for key in sorted(discoveries.keys(), key=lambda value: int(value)):
                suffixes = discoveries[key]
                before = set(index.get(key, []))
                after = before | set(suffixes)
                if key not in index:
                    new_ids += 1
                    log('发现新的状态图标 ID %s，共 %d 个文件变体。' % (key, len(after)))
                new_files += len(after - before)
                index[key] = _normalize_suffixes(after)

    _save_status_index(index)
    log('状态图标增量扫描完成：新增 %d 个 ID、%d 个文件。' % (new_ids, new_files))
    if had_network_error:
        log('扫描期间部分请求遇到网络异常；已发现的数据已保存，下次运行会继续检查。')
    return True


def _iter_indexed_filenames(index):
    for key in sorted(index.keys(), key=lambda value: int(value)):
        status_id = int(key)
        for suffix in _normalize_suffixes(index[key]):
            yield 'status_%s%s.png' % (status_id, suffix)


def status(cfg):
    """Download battle status/buff icons using a bundled historical snapshot plus CDN discovery."""
    save_to_new = False
    if 'IMAGE' in cfg and cfg['IMAGE'].get('new', '').lower() == 'yes':
        save_to_new = True

    retry_times = _get_retry_times(cfg)
    skip_list = set(get_skip_list(include_log=False))

    # 先以内置 GBFAL 快照作为历史基线，再叠加本地后续发现。
    index = _seed_index()
    seed_ids = len(index)
    seed_files = _count_index_files(index)

    local_index = _load_local_index()
    _, local_added_files = _merge_index(index, local_index)

    existing_added = _merge_existing_files(index)
    _save_status_index(index)

    log(
        '已载入内置状态图标历史索引：%d 个 ID、%d 个文件（GBFAL 快照 2026-09-19）。'
        % (seed_ids, seed_files)
    )
    if local_added_files > 0:
        log('从本地状态索引合并了 %d 个后续发现文件。' % local_added_files)
    if existing_added > 0:
        log('从 IMAGE/status/ 补充了 %d 个文件记录。' % existing_added)

    # GBFAL 只作为打包时的一次性历史快照；运行时只访问 GBF 官方 CDN。
    _scan_incremental(index, cfg, retry_times, skip_list)

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

    log('状态图标索引共 %d 个文件，本次需要下载 %d 个。' % (indexed_count, queued_count))
    downloader.wait_threads()
    return True

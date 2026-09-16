import os
import time

import requests

from module_huiji.danteng_downloader import Downloader
from module_huiji.danteng_lib import log
from ..util import get_skip_list
from config import GBF_CDN_URL, IMAGE_PATH, IMAGE_NEW_PATH, IMAGE_STATUS_PATH


GBFAL_DATA_URLS = [
    'https://raw.githubusercontent.com/MizaGBF/GBFAL/main/json/data.json',
    'https://mizagbf.github.io/GBFAL/json/data.json',
]


def _get_retry_times(cfg):
    retry_times = 5
    if 'IMAGE' in cfg and 'retry' in cfg['IMAGE']:
        try:
            retry_times = int(cfg['IMAGE']['retry'])
        except (TypeError, ValueError):
            pass
    return max(1, retry_times)


def _load_gbfal_buffs(retry_times):
    last_error = None
    # data.json is fairly large; do not retry forever if GitHub is unavailable.
    metadata_retry_times = min(retry_times, 3)

    for data_url in GBFAL_DATA_URLS:
        for attempt in range(1, metadata_retry_times + 1):
            try:
                response = requests.get(data_url, timeout=60)
                response.raise_for_status()
                data = response.json()
                buffs = data.get('buffs')
                if not isinstance(buffs, dict):
                    raise ValueError('GBFAL data.json 中不存在有效的 buffs 索引')
                return buffs
            except Exception as exc:
                last_error = exc
                if attempt < metadata_retry_times:
                    time.sleep(attempt)

    log('无法读取 GBFAL 状态图标索引：%s' % last_error)
    return None


def _iter_status_filenames(buffs):
    """Yield unique CDN filenames recorded by GBFAL's buffs index."""
    seen = set()

    for buff_id, buff_info in buffs.items():
        if buff_info == 0 or not isinstance(buff_info, list) or len(buff_info) < 2:
            continue

        prefixes = buff_info[0]
        suffixes = buff_info[1]
        if not isinstance(prefixes, list):
            prefixes = [prefixes]
        if not isinstance(suffixes, list):
            suffixes = [suffixes]

        for prefix in prefixes:
            if prefix is None:
                continue
            prefix = str(prefix).strip()
            if not prefix:
                # GBFAL normally stores the filename prefix here. Fall back to
                # the numeric key only for malformed/older entries.
                try:
                    prefix = str(int(buff_id))
                except (TypeError, ValueError):
                    continue

            for suffix in suffixes:
                if suffix is None:
                    continue
                suffix = str(suffix)
                filename = 'status_%s%s.png' % (prefix, suffix)
                if filename in seen:
                    continue
                seen.add(filename)
                yield filename


def status(cfg):
    """Download battle status/buff icons using GBFAL's maintained index."""
    save_to_new = False
    if 'IMAGE' in cfg and cfg['IMAGE'].get('new', '').lower() == 'yes':
        save_to_new = True

    retry_times = _get_retry_times(cfg)
    buffs = _load_gbfal_buffs(retry_times)
    if buffs is None:
        return False

    # These filenames are speculative/updatable metadata. Do not use the
    # runtime 404 cache, otherwise a temporarily missing icon can be skipped
    # permanently on later runs.
    skip_list = set(get_skip_list(include_log=False))

    downloader = Downloader()
    downloader.set_try_count(retry_times)

    primary_base_url = cfg['base_url'] + 'ui/icon/status/x64/'
    english_base_url = f'{GBF_CDN_URL}/assets_en/img/sp/ui/icon/status/x64/'

    indexed_count = 0
    queued_count = 0
    for source_filename in _iter_status_filenames(buffs):
        indexed_count += 1
        source_url = primary_base_url + source_filename
        fallback_url = english_base_url + source_filename

        if source_url in skip_list or fallback_url in skip_list:
            continue

        save_path = os.path.join(IMAGE_PATH, IMAGE_STATUS_PATH, source_filename)
        if os.path.exists(save_path):
            continue

        save_list = [save_path]
        if save_to_new:
            save_new_path = os.path.join(IMAGE_PATH, IMAGE_NEW_PATH, source_filename)
            save_list.append(save_new_path)

        downloader.download_multi_copies(
            source_url,
            save_list,
            fallback_url=fallback_url,
            write_skip_log=False,
        )
        queued_count += 1

    log('GBFAL 状态图标索引共 %d 个文件，本次需要下载 %d 个。' % (indexed_count, queued_count))
    downloader.wait_threads()
    return True

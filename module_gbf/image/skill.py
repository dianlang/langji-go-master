from module_huiji.danteng_downloader import Downloader
from module_huiji.danteng_lib import log
from ..util import get_skip_list
import os
import shutil
from config import IMAGE_PATH, IMAGE_NEW_PATH, IMAGE_SKILL_PATH

DOWNLOAD_TYPE = 'skill'


def skill(cfg):
    # 开关
    save_to_new = False
    retry_times = 5
    if 'IMAGE' in cfg:
        if 'new' in cfg['IMAGE'] and cfg['IMAGE']['new'].lower() == 'yes':
            save_to_new = True
        if 'retry' in cfg['IMAGE']:
            try:
                retry_times = int(cfg['IMAGE']['retry'])
            except:
                pass

    # 获取范围
    start = 0
    end = 0
    if 'IMAGE' in cfg:
        if 'sk_start' in cfg['IMAGE']:
            try:
                start = int(cfg['IMAGE']['sk_start'])
            except:
                pass
        if 'sk_end' in cfg['IMAGE']:
            try:
                end = int(cfg['IMAGE']['sk_end'])
            except:
                pass

    if start <= 0 or end <= 0 or start > end:
        log('抓取技能图标的ID范围有误，请检查')
        return False

    # 技能图标不能使用运行时 skip_log：不存在的 ID/变体未来可能被补上。
    # 这里只保留手工维护的 skip.txt，并用 set 加速大量 URL 的查找。
    skip_list = set(get_skip_list(include_log=False))

    downloader = Downloader()
    downloader.set_try_count(retry_times)

    # https://prd-game-a-granbluefantasy.akamaized.net/assets/img/sp/
    # ui/icon/ability/m/17.png
    # ui/icon/ability/m/17_1.png
    skill_base_url = cfg['base_url'] + 'ui/icon/ability/m/'

    # 技能图标既有无后缀 ID.png，也有 ID_1.png ~ ID_5.png。
    suffixes = [''] + ['_%s' % sk_type for sk_type in range(1, 6)]

    for sk_id in range(start, end + 1):
        for suffix in suffixes:
            source_filename = '%s%s.png' % (sk_id, suffix)
            save_filename = 'SK_' + source_filename
            save_path = os.path.join(IMAGE_PATH, IMAGE_SKILL_PATH, save_filename)
            save_new_path = os.path.join(IMAGE_PATH, IMAGE_NEW_PATH, save_filename)

            # 主目录已有文件时无需重新下载；如果 new=yes，则补齐 new 目录。
            if os.path.exists(save_path):
                if save_to_new and not os.path.exists(save_new_path):
                    os.makedirs(os.path.dirname(save_new_path), exist_ok=True)
                    shutil.copy2(save_path, save_new_path)
                continue

            skill_icon_url = skill_base_url + source_filename
            if skill_icon_url in skip_list:
                continue

            save_list = [save_path]
            if save_to_new and not os.path.exists(save_new_path):
                save_list.append(save_new_path)

            # 技能 ID/变体的 404 不写入全局 skip_log，保证以后仍会重试。
            downloader.download_multi_copies(
                skill_icon_url,
                save_list,
                write_skip_log=False,
            )

    downloader.wait_threads()
    return True

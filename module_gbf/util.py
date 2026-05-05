from module_huiji.danteng_lib import read_file
from config import SKIP_LIST_PATH, SKIP_LOG
from urllib.parse import urlsplit, urlunsplit


def _expand_skip_url_variants(url):
    variants = []
    parts = urlsplit(url)
    if parts.scheme not in ['http', 'https'] or parts.netloc == '':
        return [url]

    host_map = {
        'game-a.granbluefantasy.jp': [
            'game-a.granbluefantasy.jp',
            'prd-game-a-granbluefantasy.akamaized.net',
        ],
        'prd-game-a-granbluefantasy.akamaized.net': [
            'prd-game-a-granbluefantasy.akamaized.net',
            'game-a.granbluefantasy.jp',
        ],
    }
    candidate_hosts = host_map.get(parts.netloc, [parts.netloc])

    for scheme in ['http', 'https']:
        for host in candidate_hosts:
            variants.append(urlunsplit((scheme, host, parts.path, parts.query, parts.fragment)))
    return variants


def _read_skip_lines(path):
    content, result = read_file(path)
    if not result:
        return []
    result_list = []
    for line in content.split('\n'):
        url = line.strip()
        if not url:
            continue
        # skip_log 里可能有说明文字，仅保留 URL
        if not (url.startswith('http://') or url.startswith('https://')):
            continue
        result_list.extend(_expand_skip_url_variants(url))
    return result_list


# 获取图片跳过的文件地址
def get_skip_list():
    skip_urls = []
    skip_urls.extend(_read_skip_lines(SKIP_LIST_PATH))
    skip_urls.extend(_read_skip_lines(SKIP_LOG))

    # 去重并保持顺序
    dedup_urls = list(dict.fromkeys(skip_urls))
    return dedup_urls

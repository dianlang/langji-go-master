import requests
import os
from module_huiji.threading_danteng import ObjectDanteng, ThreadDanteng
from module_huiji.danteng_lib import check_folder
from config import SKIP_LOG


class Downloader(ObjectDanteng):
    # 复制此段作为初始化函数
    def __init__(self, title='DOWNLOADER'):
        super().__init__()
        requests.packages.urllib3.disable_warnings()
        self._log_title = title
        self._threads = []
        self._max_threads_number = 10
        self._try_count = 50
        self._block_size = 1024 * 1024 * 5  # 默认5M一个块

    def set_try_count(self, number):
        self._try_count = max(1, int(number))

    def set_block_size(self, size):
        self._block_size = size

    # 尝试下载
    def download(self, url, path, filename, headers=None, log_handle=None, fallback_url=None, fallback_urls=None, write_skip_log=True):
        return self.download_multi_copies(
            url,
            [os.path.join(path, filename)],
            headers=headers,
            log_handle=log_handle,
            fallback_url=fallback_url,
            fallback_urls=fallback_urls,
            write_skip_log=write_skip_log,
        )

    # 多保存目标下载
    def download_multi_copies(self, url, save_list, headers=None, log_handle=None, fallback_url=None, fallback_urls=None, write_skip_log=True):
        if len(save_list) == 0:
            return False
        filename = os.path.split(save_list[0])[1]
        url_fallbacks = []
        if fallback_url:
            url_fallbacks.append(fallback_url)
        if fallback_urls:
            if isinstance(fallback_urls, (list, tuple)):
                url_fallbacks.extend(fallback_urls)
            else:
                url_fallbacks.append(fallback_urls)
        # 去重并避免把当前 URL 再塞回备用列表。
        url_fallbacks = [
            item for item in dict.fromkeys(url_fallbacks)
            if item and item != url
        ]

        args = {
            'url': url,
            'fallback_urls': url_fallbacks,
            'filename': filename,
            'save_list': save_list,
            'block_size': self._block_size,
            'segment': False,
            'headers': headers or {},
            'log_handle': log_handle,
            'write_skip_log': write_skip_log,
        }
        self._que_in.put(args)
        self._start_thread()
        return True

    # 分块下载
    def segment_download(self, url, filename, s_index, s_total):
        args = {
            'url': url,
            'filename': filename,
            's_index': s_index,
            's_total': s_total,
            'block_size': self._block_size,
            'segment': True,
        }
        self._que_in.put(args)
        self._start_thread()
        return True

    # 调用不同的类来解决问题
    # 复制此函数修改
    def _thread_do(self):
        return DownloaderThread(self._que_in, self._que_out, self._try_count)


# 同时下载文件的线程
class DownloaderThread(ThreadDanteng):
    def __init__(self, que_in, que_out, try_count):
        self._try_count = try_count
        super().__init__(que_in, que_out)

    # 覆盖此函数
    def _exec(self, args):
        if not args['segment']:
            self._head(args)
        else:
            self._segment_download(args)

    def _switch_to_fallback(self, args, reason):
        fallbacks = args.get('fallback_urls') or []
        while fallbacks:
            next_url = fallbacks.pop(0)
            if not next_url or next_url == args.get('url'):
                continue
            args['url'] = next_url
            self._log('<%s>%s，切换备用URL...' % (args['filename'], reason))
            return True
        return False

    def _write_skip_log(self, args):
        if not args.get('write_skip_log', True):
            return
        if args.get('log_handle') is not None:
            args['log_handle'].write(args['url'] + '\n')
            args['log_handle'].flush()
            return
        with open(SKIP_LOG, 'a', encoding='utf-8') as skip_log_f:
            skip_log_f.write(args['url'] + '\n')

    def _head(self, args):
        headers = args.get('headers', {})
        count = 0
        response = None
        while True:
            count += 1
            try:
                # 这里只探测状态码和 Content-Length。旧实现使用普通 GET，
                # requests 会先把整个文件读入内存，随后 _download() 又下载一遍。
                response = requests.get(
                    args['url'],
                    headers=headers,
                    timeout=30,
                    verify=False,
                    stream=True,
                )
                if response.status_code == 200:
                    break
                elif response.status_code == 404:
                    response.close()
                    if self._switch_to_fallback(args, '文件获取失败（404）'):
                        count = 0
                        continue
                    self._write_skip_log(args)
                    self._log('<%s>文件不存在（404），已跳过！' % args['filename'])
                    return False
                else:
                    status_code = response.status_code
                    response.close()
                    if self._switch_to_fallback(args, '返回HTTP %s' % status_code):
                        count = 0
                        continue
                    if count < self._try_count:
                        self._log('<%s>下载时返回HTTP %s，第%d次重试！' % (args['filename'], status_code, count))
                    else:
                        self._log('<%s>下载时连续返回HTTP %s，已跳过！' % (args['filename'], status_code))
                        return False
            except Exception:  # 超时/连接错误
                if response is not None:
                    response.close()
                    response = None
                if self._switch_to_fallback(args, '连接失败'):
                    count = 0
                    continue
                if count < self._try_count:
                    self._log('<%s>下载时，连接超时%d次，正在重试！' % (args['filename'], count))
                else:
                    self._log('<%s>下载时，连接超时%d次，已跳过！' % (args['filename'], count))
                    return False

        try:
            file_size = int(response.headers.get('Content-Length'))
        except (TypeError, ValueError):
            file_size = 0
        finally:
            response.close()

        if file_size < args['block_size'] or args['block_size'] < 0:
            download_response = self._download(args)
            if not download_response['stat']:
                self._log('<%s>下载失败！（%s）' % (args['filename'], download_response['msg']))
                return False
            for save_path in args['save_list']:
                check_folder(save_path, 1)
                with open(save_path, 'wb') as file:
                    file.write(download_response['content'])

            self._log('文件<%s>下载成功！' % args['filename'])
            return True

        block_num = (file_size + args['block_size'] - 1) // args['block_size']
        self._log('文件<%s>大小：%s，分为%d块进行下载' % (args['filename'], get_size_desc(file_size), block_num))
        segment_downloader = Downloader(title=args['filename'])
        segment_downloader.set_thread_number(10)
        segment_downloader.set_try_count(self._try_count)
        segment_downloader.set_block_size(args['block_size'])
        for i in range(block_num):
            segment_downloader.segment_download(args['url'], args['filename'], i, block_num)
        segment_downloader.wait_threads()
        segment_data = segment_downloader.get_result()
        segment_data.sort(key=lambda s: s['s_index'])

        # 任意一个分块失败都不能继续写文件，否则会生成一个“看起来下载成功”的损坏文件。
        received_indexes = [s['s_index'] for s in segment_data]
        if received_indexes != list(range(block_num)):
            missing_indexes = sorted(set(range(block_num)) - set(received_indexes))
            self._log('文件<%s>分块不完整，缺少块：%s，已放弃保存！' % (
                args['filename'], ','.join(str(i + 1) for i in missing_indexes)
            ))
            return False

        content = b''.join([s['content'] for s in segment_data])
        if file_size > 0 and len(content) != file_size:
            self._log('文件<%s>分块合并后大小异常（预期%d，实际%d），已放弃保存！' % (
                args['filename'], file_size, len(content)
            ))
            return False

        self._log('开始保存文件<%s>...' % args['filename'])
        for save_path in args['save_list']:
            check_folder(save_path, 1)
            with open(save_path, 'wb') as file:
                file.write(content)

        self._log('文件<%s>下载成功！' % args['filename'])
        return True

    def _download(self, args):
        headers = args.get('headers', {})
        count = 0
        while True:
            count += 1
            try:
                response = requests.get(args['url'], headers=headers, timeout=30, verify=False)
                # 分块请求必须得到 206。若服务端忽略 Range 返回 200，继续拼接会把
                # 多份完整文件串在一起，因此直接判定失败。
                if args.get('segment'):
                    if response.status_code == 206:
                        break
                    if response.status_code == 200:
                        return {'stat': False, 'msg': '服务器忽略 Range 请求（HTTP 200）'}
                elif response.status_code in [200, 206]:
                    break

                if response.status_code == 404:
                    response.close()
                    if self._switch_to_fallback(args, '文件获取失败（404）'):
                        count = 0
                        continue
                    return {'stat': False, 'msg': '404 目标不存在'}

                status_code = response.status_code
                response.close()
                if self._switch_to_fallback(args, '返回HTTP %s' % status_code):
                    count = 0
                    continue
                if count < self._try_count:
                    self._log('<%s>下载时返回HTTP %s，第%d次重试！' % (args['filename'], status_code, count))
                else:
                    return {'stat': False, 'msg': 'HTTP %s' % status_code}
            except Exception:  # 超时/连接错误
                if self._switch_to_fallback(args, '连接失败'):
                    count = 0
                    continue
                if count < self._try_count:
                    self._log('<%s>下载时，连接超时%d次，正在重试！' % (args['filename'], count))
                else:
                    self._log('<%s>下载时，连接超时%d次，已跳过！' % (args['filename'], count))
                    return {'stat': False, 'msg': '连接超时'}
        return {'stat': True, 'content': response.content}

    def _segment_download(self, args):
        start, end = get_start_and_end(args)
        args['headers'] = {'Range': 'Bytes=%s-%s' % (start, end), 'Accept-Encoding': '*', 'Accept-Ranges': 'bytes'}
        download_response = self._download(args)
        if not download_response['stat']:
            self._log('<%s>第%d/%d块下载失败！（%s）' % (args['filename'], args['s_index'] + 1, args['s_total'], download_response['msg']))
            return False
        self._que_out.put({
            's_index': args['s_index'],
            'content': download_response['content'],
        })
        self._log('<%s>第%d/%d(%.2f%%)块下载完成！' % (
        args['filename'], args['s_index'] + 1, args['s_total'], self._que_out.qsize() / args['s_total']*100))


_SIZE_UNIT = {
    1: '',
    2: 'KB',
    3: 'MB',
    4: 'GB',
}


def get_size_desc(size):
    for i in range(1, 5):
        if size < 1024:
            if size == int(size):
                return '%d%s' % (size, _SIZE_UNIT[i])
            else:
                return '%.2f%s' % (size, _SIZE_UNIT[i])
        size /= 1024
    return '%.2fTB' % size


def get_start_and_end(args):
    if args['s_index'] == args['s_total'] - 1:
        return args['s_index'] * args['block_size'], ''
    else:
        return args['s_index'] * args['block_size'], (args['s_index']+1) * args['block_size'] - 1

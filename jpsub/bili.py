"""B 站投稿接口(自 biliup 裁剪的 vendored 子集)。

来源:github.com/biliup/biliup  biliup/plugins/bili_webup.py
版本:1.2.9  作者 ForgQi  许可证:MIT
上游文件 sha256:437aa49650c9205dd54dae9a161642bfe5e84f931a77c2550f8a0874feefd15d
裁剪清单与上游同步流程:见 docs/bili.md;拉取上游并生成差异报告:tools/sync_biliup.py

保留(本仓库投稿路径实际用到):
  - Data:稿件元信息容器
  - BiliBili:cookies 登录 → upos 分片上传 → 提交稿件 → 封面上传
移除(本仓库不用,消除其传递依赖):
  - 账号密码登录(唯一依赖 rsa)、短信/扫码登录
  - cos/kodo 上传线路(上游 1.2.9 已注释,实际只走 upos)
  - 合集(season)管理、标签推荐、BiliWeb 命令行封装、myinfo
"""

import asyncio
import base64
import json
import math
import os
import re
import sys
import time
from dataclasses import asdict, dataclass, field, InitVar
from json import JSONDecodeError
from os.path import splitext, basename
from typing import Union, Any, List, Optional

import aiohttp
import requests.utils
from requests.adapters import HTTPAdapter, Retry

import logging


# 日志命名空间用本仓库名,便于与其它模块统一配置(上游为 'biliup')。
logger = logging.getLogger('jpsub')


def _safe_response_status(response):
    """Return only fixed, non-credential status fields for logging."""
    if not isinstance(response, dict):
        return {"type": type(response).__name__}
    return {key: response[key] for key in ("code", "OK") if key in response}


class BiliBili:
    def __init__(self, video: 'Data'):
        self.app_key = None
        self.appsec = None
        if self.app_key is None or self.appsec is None:
            self.app_key = 'ae57252b0c09105d'
            self.appsec = 'c75875c596a69eb55bd119e74b07cfe3'
        self.__session = requests.Session()
        self.video = video
        self.__session.mount('https://', HTTPAdapter(max_retries=Retry(total=5)))
        self.__session.headers.update({
            'user-agent': "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/63.0.3239.108",
            'referer': "https://www.bilibili.com/",
            'connection': 'keep-alive'
        })
        self.cookies = None
        self.access_token = None
        self.refresh_token = None
        self.__bili_jct = None
        self._auto_os = None
        self.persistence_path = 'engine/bili.cookie'

    def login(self, persistence_path, user_cookie):
        """读取 cookies 文件登录(由 biliup-rs 扫码生成)。

        上游此处失败会回退到账号密码登录;本裁剪版只有 cookies 单一路径——
        jpsub 始终先 `biliup-rs login` 扫码,不保存账号密码。登录失败直接抛出,
        由调用方(upload.upload)提示重新扫码。
        """
        self.persistence_path = user_cookie
        if os.path.isfile(self.persistence_path):
            print('使用持久化内容上传')
            self.load()
        if not self.cookies:
            raise RuntimeError(f"cookie 文件不存在或为空:{self.persistence_path}")
        self.login_by_cookies(self.cookies)
        self.store()

    def load(self):
        try:
            with open(self.persistence_path) as f:
                self.cookies = json.load(f)
                self.access_token = self.cookies['token_info']['access_token']
                self.refresh_token = self.cookies['token_info']['refresh_token']
        except (JSONDecodeError, KeyError):
            logger.exception('加载cookie出错')

    def store(self):
        with open(self.persistence_path, "w") as f:
            json.dump({**self.cookies,
                       'access_token': self.access_token,
                       'refresh_token': self.refresh_token
                       }, f)

    def login_by_cookies(self, cookie):
        logger.info(f'{self.__class__.__name__}: login by cookies')
        cookies_dict = {c['name']: c['value'] for c in cookie['cookie_info']['cookies']}
        requests.utils.add_dict_to_cookiejar(self.__session.cookies, cookies_dict)
        if 'bili_jct' in cookies_dict:
            self.__bili_jct = cookies_dict['bili_jct']
        data = self.__session.get("https://api.bilibili.com/x/web-interface/nav", timeout=5).json()
        if data["code"] != 0:
            raise RuntimeError(f"cookie login failed: {_safe_response_status(data)}")
        print('使用cookies上传')

    def probe(self):
        ret = self.__session.get('https://member.bilibili.com/preupload?r=probe', timeout=5).json()
        logger.info(f"线路:{ret['lines']}")
        data, auto_os = None, None
        min_cost = 0
        if ret['probe'].get('get'):
            method = 'get'
        else:
            method = 'post'
            data = bytes(int(1024 * 0.1 * 1024))
        for line in ret['lines']:
            start = time.perf_counter()
            test = self.__session.request(method, f"https:{line['probe_url']}", data=data, timeout=30)
            cost = time.perf_counter() - start
            print(line['query'], cost)
            if test.status_code != 200:
                return
            if not min_cost or min_cost > cost:
                auto_os = line
                min_cost = cost
        auto_os['cost'] = min_cost
        return auto_os

    def upload_file(self, filepath: str, lines='AUTO', tasks=3):
        """上传本地视频文件,返回视频信息dict
        b站目前支持4种上传线路upos, kodo, gcs, bos
        gcs: {"os":"gcs","query":"bucket=bvcupcdngcsus&probe_version=20221109",
        "probe_url":"//storage.googleapis.com/bvcupcdngcsus/OK"},
        bos: {"os":"bos","query":"bucket=bvcupcdnboshb&probe_version=20221109",
        "probe_url":"??"}
        """
        preferred_upos_cdn = None
        if not self._auto_os:
            if lines in {'bda2', 'cs-bda2'}:
                self._auto_os = {"os": "upos", "query": "upcdn=bda2&probe_version=20221109",
                                 "probe_url": "//upos-cs-upcdnbda2.bilivideo.com/OK"}
                preferred_upos_cdn = 'bda2'
            elif lines == 'bldsa':
                self._auto_os = {"os": "upos", "query": "upcdn=bldsa&probe_version=20221109",
                                 "probe_url": "//upos-cs-upcdnbldsa.bilivideo.com/OK"}
                preferred_upos_cdn = 'bldsa'
            elif lines == 'tx':
                self._auto_os = {"os": "upos", "query": "upcdn=tx&probe_version=20221109",
                                 "probe_url": "//upos-cs-upcdntx.bilivideo.com/OK"}
                preferred_upos_cdn = 'tx'
            elif lines == 'txa':
                self._auto_os = {"os": "upos", "query": "upcdn=txa&probe_version=20221109",
                                 "probe_url": "//upos-cs-upcdntxa.bilivideo.com/OK"}
                preferred_upos_cdn = 'txa'
            elif lines == 'alia':
                self._auto_os = {"os": "upos", "query": "upcdn=alia&probe_version=20221109",
                                 "probe_url": "//upos-cs-upcdnalia.bilivideo.com/OK"}
                preferred_upos_cdn = 'alia'
            elif lines == 'estx':
                self._auto_os = {"os": "upos",
                                 "query": "probe_version=20250923&upcdn=estx&zone=cs",
                                 "probe_url": "//e17962d5cstx.esheep.com/OK"}
            elif lines == 'akbd':
                self._auto_os = {"os": "upos",
                                 "query": "probe_version=20250923&upcdn=akbd&zone=cs",
                                 "probe_url": "//bb27c891csbd.aikobo.cn/OK"}
            else:
                self._auto_os = self.probe()
            logger.info(f"线路选择 => {self._auto_os['os']}: {self._auto_os['query']}. "
                        f"time: {self._auto_os.get('cost')}")
        if self._auto_os['os'] == 'upos':
            upload = self.upos
        else:
            logger.error(f"NoSearch:{self._auto_os['os']}")
            raise NotImplementedError(self._auto_os['os'])
        logger.info(f"os: {self._auto_os['os']}")
        total_size = os.path.getsize(filepath)
        with open(filepath, 'rb') as f:
            query = {
                'r': self._auto_os['os'] if self._auto_os['os'] != 'cos-internal' else 'cos',
                'profile': 'ugcupos/bup' if 'upos' == self._auto_os['os'] else "ugcupos/bupfetch",
                'ssl': 0,
                'version': '2.8.12',
                'build': 2081200,
                'name': f.name,
                'size': total_size,
            }
            resp = self.__session.get(
                f"https://member.bilibili.com/preupload?{self._auto_os['query']}", params=query,
                timeout=5)
            ret = resp.json()
            logger.debug(
                "preupload response accepted: storage=%s chunk_size=%s",
                self._auto_os['os'],
                ret.get('chunk_size'),
            )
            if preferred_upos_cdn:
                original_endpoint: str = ret['endpoint']
                if re.match(r'//upos-(sz|cs)-upcdnbda2\.bilivideo\.com', original_endpoint):
                    if re.match(r'bda2', preferred_upos_cdn):
                        logger.debug(f"Preferred UpOS CDN: {preferred_upos_cdn}")
                        new_endpoint = re.sub(r'upcdnbda2', f'upcdn{preferred_upos_cdn}', original_endpoint)
                        logger.debug(f"{original_endpoint} => {new_endpoint}")
                        ret['endpoint'] = new_endpoint
                    else:
                        logger.error(f"Unrecognized preferred_upos_cdn: {preferred_upos_cdn}")
                else:
                    logger.warning(f"Assigned UpOS endpoint {original_endpoint} was never seen before, "
                                   f"something else might have changed, so will not modify it")
            return asyncio.run(upload(f, total_size, ret, tasks=tasks))

    async def upos(self, file, total_size, ret, tasks=3):
        filename = file.name
        chunk_size = ret['chunk_size']
        auth = ret["auth"]
        endpoint = ret["endpoint"]
        biz_id = ret["biz_id"]
        upos_uri = ret["upos_uri"]
        url = f"https:{endpoint}/{upos_uri.replace('upos://', '')}"  # 视频上传路径
        headers = {
            "X-Upos-Auth": auth
        }
        # 向上传地址申请上传，得到上传id等信息
        upload_id = self.__session.post(f'{url}?uploads&output=json', timeout=15,
                                        headers=headers).json()["upload_id"]
        # 开始上传
        parts = []  # 分块信息
        chunks = math.ceil(total_size / chunk_size)  # 获取分块数量

        async def upload_chunk(session, chunks_data, params):
            async with session.put(url, params=params, raise_for_status=True,
                                   data=chunks_data, headers=headers):
                end = time.perf_counter() - start
                parts.append({"partNumber": params['chunk'] + 1, "eTag": "etag"})
                sys.stdout.write(f"\r{params['end'] / 1000 / 1000 / end:.2f}MB/s "
                                 f"=> {params['partNumber'] / chunks:.1%}")

        start = time.perf_counter()
        await self._upload({
            'uploadId': upload_id,
            'chunks': chunks,
            'total': total_size
        }, file, chunk_size, upload_chunk, tasks=tasks)
        cost = time.perf_counter() - start
        p = {
            'name': filename,
            'uploadId': upload_id,
            'biz_id': biz_id,
            'output': 'json',
            'profile': 'ugcupos/bup'
        }
        attempt = 0
        while attempt <= 5:  # 一旦放弃就会丢失前面所有的进度，多试几次吧
            try:
                r = self.__session.post(url, params=p, json={"parts": parts}, headers=headers, timeout=15).json()
                if r.get('OK') == 1:
                    logger.info(f'{filename} uploaded >> {total_size / 1000 / 1000 / cost:.2f}MB/s')
                    return {"title": splitext(os.path.basename(filename))[0],
                            "filename": splitext(basename(upos_uri))[0], "desc": ""}
                raise IOError(f"UPOS merge failed: {_safe_response_status(r)}")
            except IOError:
                attempt += 1
                logger.info(f"请求合并分片时出现问题，尝试重连，次数：" + str(attempt))
                time.sleep(15)

    @staticmethod
    async def _upload(params, file, chunk_size, afunc, tasks=3):
        params['chunk'] = -1

        async def upload_chunk():
            while True:
                chunks_data = file.read(chunk_size)
                if not chunks_data:
                    return
                params['chunk'] += 1
                params['size'] = len(chunks_data)
                params['partNumber'] = params['chunk'] + 1
                params['start'] = params['chunk'] * chunk_size
                params['end'] = params['start'] + params['size']
                clone = params.copy()
                for i in range(10):
                    try:
                        await afunc(session, chunks_data, clone)
                        break
                    except (asyncio.TimeoutError, aiohttp.ClientError) as error:
                        logger.error(
                            "retry chunk%s >> %s (%s)",
                            clone['chunk'],
                            i + 1,
                            type(error).__name__,
                        )

        async with aiohttp.ClientSession() as session:
            await asyncio.gather(*[upload_chunk() for _ in range(tasks)])

    def submit(self, submit_api: Optional[str] = 'web'):
        if not self.video.title:
            self.video.title = self.video.videos[0]["title"]
        self.__session.get('https://member.bilibili.com/x/geetest/pre/add', timeout=5)

        if submit_api != 'web':
            raise Exception(f'不存在的选项：{submit_api}')
        ret = self.submit_web()
        if ret["code"] != 0:
            logger.error("网页端接口提交失败: %s", _safe_response_status(ret))
            raise RuntimeError(f"网页端接口提交失败: {_safe_response_status(ret)}")
        return ret

    def submit_web(self):
        logger.info('使用网页端api提交')
        post_data = asdict(self.video)
        if post_data.get('tid_v2') is None:
            post_data.pop('tid_v2', None)
        return self.__session.post(f'https://member.bilibili.com/x/vu/web/add?csrf={self.__bili_jct}', timeout=5,
                                   json=post_data).json()

    def cover_up(self, img: str):
        """
        :param img: img path or stream
        :return: img URL
        """
        from PIL import Image
        from io import BytesIO

        with Image.open(img) as im:
            # 宽和高,需要16：10
            xsize, ysize = im.size
            if xsize / ysize > 1.6:
                delta = xsize - ysize * 1.6
                region = im.crop((delta / 2, 0, xsize - delta / 2, ysize))
            else:
                delta = ysize - xsize * 10 / 16
                region = im.crop((0, delta / 2, xsize, ysize - delta / 2))
            buffered = BytesIO()
            region.save(buffered, format=im.format)
        r = self.__session.post(
            url='https://member.bilibili.com/x/vu/web/cover/up',
            data={
                'cover': b'data:image/jpeg;base64,' + (base64.b64encode(buffered.getvalue())),
                'csrf': self.__bili_jct
            }, timeout=30
        )
        buffered.close()
        res = r.json()
        if res.get('data') is None:
            raise RuntimeError(f"cover upload failed: {_safe_response_status(res)}")
        return res['data']['url']

    def __enter__(self):
        return self

    def __exit__(self, e_t, e_v, t_b):
        self.close()

    def close(self):
        """Closes all adapters and as such the session"""
        self.__session.close()


@dataclass
class Data:
    """
    cover: 封面图片，可由recovers方法得到视频的帧截图
    """
    copyright: int = 2
    source: str = ''
    tid: int = 21
    tid_v2: Optional[int] = None
    cover: str = ''
    title: str = ''
    desc_format_id: int = 0
    desc: str = ''
    desc_v2: list = field(default_factory=list)
    dynamic: str = ''
    subtitle: dict = field(init=False)
    tag: Union[list, str] = ''
    videos: list = field(default_factory=list)
    dtime: Any = None
    open_subtitle: InitVar[bool] = False

    # interactive: int = 0
    # no_reprint: int 1
    # charging_pay: int 1

    def __post_init__(self, open_subtitle):
        self.subtitle = {"open": int(open_subtitle), "lan": ""}
        if self.dtime and self.dtime - int(time.time()) <= 14400:
            self.dtime = None
        if isinstance(self.tag, list):
            self.tag = ','.join(self.tag)

    def delay_time(self, dtime: int):
        """设置延时发布时间，距离提交大于2小时，格式为10位时间戳"""
        if dtime - int(time.time()) > 7200:
            self.dtime = dtime

    def set_tag(self, tag: list):
        """设置标签，tag为数组"""
        self.tag = ','.join(tag)

    def append(self, video):
        self.videos.append(video)

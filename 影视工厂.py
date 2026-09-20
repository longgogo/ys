# coding=utf-8
# 影视工厂 gdshengjiu.com —— TVBox Python 蜘蛛（py 源）
#
# 站点：MacCMS + stui 标准主题。
# 关键点：/vodtype/ /vodshow/ /vodsearch/ 被 Cloudflare 按小写路径做托管挑战（403），
# 而 CMS 路由不区分大小写 → 统一改用 /VodShow/ /VodSearch/ 首字母大写即全站通行，无需任何 cookie。
# 播放页 player_aaaa 的 encrypt=0，url 就是明文 m3u8（非凡 ffzy CDN，裸请求 200）→ parse=0 直出。
# 分类路由用 /VodShow/{tid}-----------.html（11 破折号 = tid 后接 11 个空字段），
# 页码替换末 3 破折号：{tid}--------{pg}---（实测真分页；/Vodtype/{tid}-{pg}.html 已失效，p1==p2）。

import re
import json
import urllib.parse

try:
    from concurrent.futures import ThreadPoolExecutor
except Exception:
    ThreadPoolExecutor = None

from base.spider import Spider

_UA = "Mozilla/5.0 (Linux; Android 11) AppleWebKit/537.36"


class Spider(Spider):

    # ---- 列表卡片（首页 / 分类页 / 搜索页通用；锚在 thumb 类上，每卡只出 1 块）----
    # ⚠ 捕获组必须包含开标签：href/title/data-original 都写在 <a> 开标签里，只捕获内层会全部取空
    _CARD = re.compile(r'(?s)(<a[^>]*class="[^"]*stui-vodlist__thumb[^"]*"[^>]*>(?:(?!</a>).)*?)</a>')
    _HREF = re.compile(r'/voddetail/(\d+)\.html')
    _TITLE = re.compile(r'title="([^"]*)"')
    _PIC = re.compile(r'data-original="([^"]*)"')
    _REM = re.compile(r'class="pic-text[^"]*">([^<]*)<')

    # ---- 详情页（本站 stui 变体：类型/地区/年份挤在第一个 p.data 里）----
    _H1 = re.compile(r'(?s)<h1 class="title">(.*?)</h1>')
    _TYPE = re.compile(r'类型：</span><a[^>]*>([^<]*)</a>')
    _AREA = re.compile(r'地区：</span><a[^>]*>([^<]*)</a>')
    _YEAR = re.compile(r'年份：</span><a[^>]*>([^<]*)</a>')
    _ACT = re.compile(r'(?s)主演：</span>((?:(?!</p>).)*?)</p>')
    _DIR = re.compile(r'(?s)导演：</span>((?:(?!</p>).)*?)</p>')
    _DESC = re.compile(r'(?s)简介：</span>((?:(?!</p>).)*?)</p>')
    _UPD = re.compile(r'上次更新：</span><font[^>]*>([^<]*)</font>')
    _LINES = re.compile(r'<a data-toggle="tab" href="#down\d+"[^>]*>([^<]*)</a>')
    _GROUPS = re.compile(r'(?s)<ul class="stui-content__playlist[^"]*">(.*?)</ul>')
    _EP = re.compile(r'(?s)<a href="(/vodplay/\d+-\d+-\d+\.html)"([^>]*)>([^<]*)</a>')
    _PAAA = re.compile(r'(?s)player_aaaa\s*=\s*(\{.*?\})\s*[;<]')

    # ---- 筛选条 ----
    _SCREEN = re.compile(r'(?s)<ul class="stui-screen__list[^"]*">(.*?)</ul>')
    _SITEM = re.compile(r'<a href="([^"]+)"[^>]*>([^<]+)</a>')

    # 维度 → vodshow 路径里的字段位（0 起）：地区/排序/剧情/语言/字母，年份在末位(11)不可翻页故不收
    _DIMS = {0: "地区", 2: "剧情", 3: "语言", 4: "字母"}

    def getName(self):
        return "影视工厂"

    def init(self, extend=""):
        host = (extend or "").strip() if isinstance(extend, str) else ""
        if not host:
            host = "https://gdshengjiu.com"
        if not host.startswith("http"):
            host = "https://" + host
        self.host = host.rstrip("/")
        self._fcache = {}

    def isVideoFormat(self, url):
        return False

    def manualVideoCheck(self):
        return False

    def action(self, action):
        pass

    def destroy(self):
        pass

    # ------------------------------------------------------------ 网络层
    def _req(self, url, headers=None, timeout=15, allow_redirects=True):
        h = dict(headers or {})
        for kw in ({"headers": h, "timeout": timeout, "allow_redirects": allow_redirects},
                   {"headers": h, "timeout": timeout},
                   {"headers": h}):
            try:
                return self.fetch(url, **kw)
            except TypeError:
                continue
            except Exception:
                return None
        return None

    def _get(self, url):
        h = {"User-Agent": _UA, "Referer": self.host + "/",
             "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
             "Accept-Language": "zh-CN,zh;q=0.9"}
        r = self._req(url, h)
        if r is None:
            return ""
        try:
            return r.text or ""
        except Exception:
            return ""

    # ------------------------------------------------------------ 解析层
    def _cards(self, html):
        out, seen = [], set()
        if not html:
            return out
        for blk in self._CARD.findall(html):
            mid = self._HREF.search(blk)
            pic = self._PIC.search(blk)
            # 无 data-original 的是首页轮播卡（图片写在 style 里），直接排除
            if not mid or not pic:
                continue
            vid = mid.group(1)
            if vid in seen:
                continue
            seen.add(vid)
            t = self._TITLE.search(blk)
            rem = self._REM.search(blk)
            out.append({"vod_id": vid,
                        "vod_name": (t.group(1) if t else "").strip(),
                        "vod_pic": pic.group(1),
                        "vod_remarks": (rem.group(1) if rem else "").strip()})
        return out

    def _names(self, blk):
        if not blk:
            return ""
        ns = re.findall(r'<a[^>]*>([^<]*)</a>', blk)
        if ns:
            return ",".join(x.strip() for x in ns if x.strip())
        return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', blk)).replace("&nbsp;", " ").strip()

    # ------------------------------------------------------------ 接口
    def homeContent(self, filter):
        classes = [{"type_id": "1", "type_name": "电影"},
                   {"type_id": "2", "type_name": "电视剧"},
                   {"type_id": "3", "type_name": "综艺"},
                   {"type_id": "4", "type_name": "动漫"},
                   {"type_id": "36", "type_name": "短剧"}]
        filters = self._filters() if filter else {}
        return {"class": classes, "filters": filters}

    def homeVideoContent(self):
        return {"list": self._cards(self._get(self.host + "/"))}

    def categoryContent(self, tid, pg, filter, extend):
        try:
            pg = int(pg)
        except Exception:
            pg = 1
        ext = extend or {}
        if not isinstance(ext, dict):
            try:
                ext = json.loads(ext)
            except Exception:
                ext = {}
        # 子分类与属性筛选互斥：站点不支持「子分类 + 剧情/地区/…」组合（实测一律 0 条）
        sub = str(ext.get("分类") or "").strip()
        fields = [""] * 11
        if sub:
            tid_use = sub
        else:
            tid_use = str(tid)
            for k, idx in (("地区", 0), ("剧情", 2), ("语言", 3), ("字母", 4)):
                v = str(ext.get(k) or "").strip()
                if v:
                    fields[idx] = urllib.parse.quote(v, safe="")
        body = "%s-%s" % (tid_use, "-".join(fields))
        if pg > 1:
            body = body[:-3] + str(pg) + "---"      # 页码替换末 3 破折号，总破折号守恒
        html = self._get("%s/VodShow/%s.html" % (self.host, body))
        return {"list": self._cards(html), "page": pg,
                "pagecount": 9999, "limit": 36, "total": 999999}

    def detailContent(self, ids):
        vid = str(ids[0])
        d = {"vod_id": vid, "vod_name": "", "vod_pic": "", "vod_content": "",
             "vod_year": "", "vod_area": "", "type_name": "", "vod_actor": "",
             "vod_director": "", "vod_remarks": ""}
        html = self._get("%s/voddetail/%s.html" % (self.host, vid))
        if not html:
            return {"list": [d]}
        m = self._H1.search(html)
        if m:
            d["vod_name"] = re.sub(r'<[^>]+>', '', m.group(1)).strip()
        m = re.search(r'(?s)stui-content__thumb.*?data-original="([^"]*)"', html)
        if m:
            d["vod_pic"] = m.group(1)
        for k, rx in (("type_name", self._TYPE), ("vod_area", self._AREA), ("vod_year", self._YEAR)):
            m = rx.search(html)
            if m:
                d[k] = m.group(1).strip()
        m = self._ACT.search(html)
        if m:
            d["vod_actor"] = self._names(m.group(1))
        m = self._DIR.search(html)
        if m:
            d["vod_director"] = self._names(m.group(1))
        m = self._UPD.search(html)
        if m:
            d["vod_remarks"] = m.group(1).split(" / ")[0].strip()
        m = self._DESC.search(html)
        if m:
            txt = re.sub(r'<[^>]+>', '', m.group(1)).replace("&nbsp;", " ")
            txt = txt.split("想看更多的")[0]
            d["vod_content"] = re.sub(r'\s+', ' ', txt).strip()
        lines = [x.strip() for x in self._LINES.findall(html) if x.strip()]
        groups = self._GROUPS.findall(html)
        if len(lines) < len(groups):
            lines += ["线路%d" % (i + 1) for i in range(len(lines), len(groups))]
        elif len(lines) > len(groups):
            lines = lines[:len(groups)]
        eps_all = []
        for g in groups:
            eps, seen = [], set()
            for path, attrs, txt in self._EP.findall(g):
                if path in seen:
                    continue
                seen.add(path)
                mt = re.search(r'title="([^"]*)"', attrs)
                name = (mt.group(1) if mt else txt).strip() or "播放"
                eps.append("%s$%s" % (name, path))
            eps_all.append("#".join(eps))
        d["vod_play_from"] = "$$$".join(lines)
        d["vod_play_url"] = "$$$".join(eps_all)
        return {"list": [d]}

    def searchContent(self, key, quick, pg=1):
        try:
            pg = int(pg)
        except Exception:
            pg = 1
        if not str(key or "").strip() or pg > 1:     # 站点搜索不支持翻页（实测第 2 页为空）
            return {"list": [], "page": pg}
        url = "%s/VodSearch/-------------.html?wd=%s" % (
            self.host, urllib.parse.quote(str(key).encode("utf-8")))
        return {"list": self._cards(self._get(url)), "page": 1}

    def playerContent(self, flag, id, vipFlags=None):
        pid = str(id)
        if not pid.startswith("http"):
            pid = self.host + pid
        play_url, parse = pid, 1
        html = self._get(pid)
        m = self._PAAA.search(html) if html else None
        if m:
            try:
                pa = json.loads(m.group(1).replace("\\/", "/"))
            except Exception:
                pa = {}
            u = str(pa.get("url") or "")
            if u.startswith("http") and int(pa.get("encrypt") or 0) == 0:
                play_url, parse = u, 0
        return {"parse": parse, "url": play_url, "header": {"User-Agent": _UA}}

    def localProxy(self, param):
        return [200, "text/plain", ""]

    # ------------------------------------------------------------ 筛选
    def _filters(self):
        if self._fcache:
            return self._fcache
        tids = ["1", "2", "3", "4", "36"]

        def build(tid):
            try:
                return tid, self._parse_filters(tid, self._get(
                    "%s/VodShow/%s-----------.html" % (self.host, tid)))
            except Exception:
                return tid, []

        try:
            if ThreadPoolExecutor:
                with ThreadPoolExecutor(max_workers=5) as ex:
                    for tid, fs in ex.map(build, tids):
                        if fs:
                            self._fcache[tid] = fs
            else:
                raise RuntimeError
        except Exception:
            for tid in tids:
                tid2, fs = build(tid)
                if fs:
                    self._fcache[tid2] = fs
        return self._fcache

    def _parse_filters(self, tid, html):
        dims = {"分类": [], "剧情": [], "地区": [], "语言": [], "字母": []}
        seen = dict((k, set()) for k in dims)
        if not html:
            return []
        for ul in self._SCREEN.findall(html):
            for href, name in self._SITEM.findall(ul):
                name = name.strip()
                m = re.search(r'/vodshow/([^"]*?)\.html', href)
                if not m:
                    continue
                parts = urllib.parse.unquote(m.group(1)).split("-")
                if len(parts) != 12:
                    continue
                if parts[0] != tid:                     # 子分类链接（/vodshow/6-----------.html）
                    if parts[0].isdigit() and not any(parts[1:]) and name not in seen["分类"]:
                        seen["分类"].add(name)
                        dims["分类"].append({"v": parts[0], "n": name})
                    continue
                j = next((i for i in range(1, 12) if parts[i]), None)
                if j is None:                            # 「全部」
                    continue
                fidx = j - 1
                if fidx == 11:                           # 年份位：无法翻页，不收
                    continue
                dim = self._DIMS.get(fidx)
                if not dim or parts[j] in seen[dim]:
                    continue
                seen[dim].add(parts[j])
                dims[dim].append({"v": parts[j], "n": name})
        return [dict(key=k, name=k, value=v) for k, v in dims.items() if v]

# author: szw
"""目的地 → 地理查询参数的解析。

为什么单独一个模块：高德对"目的地"这件事有三套不同的吃法，混在一起写最容易出错。

1. 城市名：``city=青岛`` 或 ``city=青岛市`` 都认；
2. 省名：**必须带"省"字**（``city=山东省``），传 ``city=山东`` 会被静默忽略，
   退回全国匹配——实测"芙蓉街"就会从济南跑到云南红河州，而且接口不报错；
3. adcode：``city=370000`` 等价于 ``city=山东省``，比中文字符串更稳。

所以这里统一把用户给的 destination 拆成 (城市, 省份, 粒度)，
调用方一律用 ``region.query_value`` 去问高德，不要再自己 split 字符串
（之前 `'山东省'.split('省')[1]` 拿到的是空串，城市的约束就此丢失）。
"""
import math
import re
from dataclasses import dataclass
from typing import Optional, Tuple

# (全名, 简称, adcode)。顺序无所谓；匹配时先试全名再试简称。
PROVINCES: Tuple[Tuple[str, str, str], ...] = (
    ("北京市", "北京", "110000"), ("天津市", "天津", "120000"),
    ("河北省", "河北", "130000"), ("山西省", "山西", "140000"),
    ("内蒙古自治区", "内蒙古", "150000"), ("辽宁省", "辽宁", "210000"),
    ("吉林省", "吉林", "220000"), ("黑龙江省", "黑龙江", "230000"),
    ("上海市", "上海", "310000"), ("江苏省", "江苏", "320000"),
    ("浙江省", "浙江", "330000"), ("安徽省", "安徽", "340000"),
    ("福建省", "福建", "350000"), ("江西省", "江西", "360000"),
    ("山东省", "山东", "370000"), ("河南省", "河南", "410000"),
    ("湖北省", "湖北", "420000"), ("湖南省", "湖南", "430000"),
    ("广东省", "广东", "440000"), ("广西壮族自治区", "广西", "450000"),
    ("海南省", "海南", "460000"), ("重庆市", "重庆", "500000"),
    ("四川省", "四川", "510000"), ("贵州省", "贵州", "520000"),
    ("云南省", "云南", "530000"), ("西藏自治区", "西藏", "540000"),
    ("陕西省", "陕西", "610000"), ("甘肃省", "甘肃", "620000"),
    ("青海省", "青海", "630000"), ("宁夏回族自治区", "宁夏", "640000"),
    ("新疆维吾尔自治区", "新疆", "650000"), ("台湾省", "台湾", "710000"),
    ("香港特别行政区", "香港", "810000"), ("澳门特别行政区", "澳门", "820000"),
)

PROVINCE_ADCODE = {full: code for full, _, code in PROVINCES}

# 直辖市既是省级行政区也是一座城市：用户说"北京市"就是要去北京城区，
# 不能当成"只给了省份"去按"省内多城"处理（那样北京市会变成 province 粒度）。
MUNICIPALITIES = {"北京市": "北京", "上海市": "上海", "天津市": "天津", "重庆市": "重庆"}

# 允许的偏移上限（公里）：超过就认为这个坐标不是目的地那个地方。
# 城市 150km 覆盖得下远郊（大连市区到庄河约 150km、北京到延庆约 80km）；
# 省 600km 覆盖得下省内最远的两端（菏泽到威海约 640km，取 600 会卡掉极值，
# 但那种情况说明行程确实跨了大半个省，判为可疑比放行更安全）。
MAX_OFFSET_KM = {"city": 150.0, "province": 600.0, "unknown": 300.0}


@dataclass(frozen=True)
class Region:
    """一次目的地解析的结果。"""

    city: Optional[str] = None
    province: Optional[str] = None
    scope: str = "unknown"      # city / province / unknown

    @property
    def query_value(self) -> Optional[str]:
        """传给高德 city 参数的值。

        有城市用城市名，只有省份用 **adcode**——不用"山东省"是为了避免
        哪天高德改了省名的写法（"山东省" / "山东"）导致又静默退化。
        """
        if self.city:
            return self.city
        if self.province:
            return PROVINCE_ADCODE.get(self.province)
        return None

    @property
    def center_query(self) -> Optional[str]:
        """查目的地中心点时用的地址（地理编码接口要的是名字，不是 adcode）。"""
        return self.city or self.province

    @property
    def max_offset_km(self) -> float:
        return MAX_OFFSET_KM.get(self.scope, MAX_OFFSET_KM["unknown"])

    @property
    def is_province_only(self) -> bool:
        return self.scope == "province"

    @property
    def label(self) -> str:
        return self.city or self.province or ""


def _strip_province(text: str) -> Tuple[Optional[str], str]:
    """从目的地里剥出省份，返回 (省全名, 剩下的部分)。

    先试全名再试简称：'山东省青岛市' 用全名剥得到 '青岛市'，
    只写 '山东' 时用简称剥得到 ''（省份本身就是目的地）。
    """
    text = text or ""
    for full, short, _ in PROVINCES:
        if full and full in text:
            return full, text.replace(full, "", 1)
        if short and text.startswith(short):
            return full, text[len(short):]
    return None, text


def _city_from(rest: str) -> Optional[str]:
    """从剩余片段里取城市名：'青岛市' → '青岛'，'济南市历下区' → '济南'。"""
    rest = (rest or "").strip()
    if not rest:
        return None
    match = re.match(r"(.+?)(?:市|地区|盟)", rest)
    if match:
        return match.group(1).strip() or None
    # '大理白族自治州' 这类带民族名的自治州，去掉后缀保留全称，高德认
    match = re.match(r"(.+?自治州)", rest)
    if match:
        return match.group(1).strip() or None
    return rest


def parse_region(destination: str) -> Region:
    """把用户给的目的地拆成可以拿去查询的区域。

    >>> parse_region("山东省").scope
    'province'
    >>> parse_region("山东省青岛市").city
    '青岛'
    >>> parse_region("大连市").city
    '大连'
    """
    text = (destination or "").strip()
    # "中国山东省"这种写法直接剥掉前缀，否则整串会被当成一个"城市名"
    for prefix in ("中国", "中华人民共和国"):
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
            break
    if not text:
        return Region()

    province, rest = _strip_province(text)
    city = _city_from(rest)

    if city:
        return Region(city=city, province=province, scope="city")
    if province in MUNICIPALITIES:
        return Region(city=MUNICIPALITIES[province], province=province, scope="city")
    if province:
        return Region(province=province, scope="province")
    return Region(scope="unknown")


def haversine_km(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    """两点球面距离（公里）。入参是 (lng, lat)。"""
    lng1, lat1 = float(a[0]), float(a[1])
    lng2, lat2 = float(b[0]), float(b[1])
    radius = 6371.0
    d_lat = math.radians(lat2 - lat1)
    d_lng = math.radians(lng2 - lng1)
    h = (math.sin(d_lat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(d_lng / 2) ** 2)
    return 2 * radius * math.asin(math.sqrt(min(1.0, h)))


def is_offset_ok(coord: Tuple[float, float], center: Tuple[float, float],
                 region: Region) -> Tuple[bool, float]:
    """坐标是否落在目的地合理范围内。

    返回 (是否可信, 实际距离 km)。拿不到中心点时一律放行——
    校验是兜底手段，不能因为它自己失败就把正常的景点全废掉。
    """
    if not center:
        return True, 0.0
    distance = haversine_km(coord, center)
    return distance <= region.max_offset_km, distance

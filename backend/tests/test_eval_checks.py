# author: szw
"""评测判分器的回归测试：地名词典的正例与反例。

判分器是"评测的评测"——它自己判错了，20 条端到端评测的红绿就没有意义。
这里固化了三类真实踩过的坑：

  1. 词典缺词导致的误杀："曾厝垵文创村""十里画廊""龙门石窟"都是正经景点，
     但当时词典没有"村/画廊/石窟"，被判成"没有具体去处"。
  2. 前缀过度排除："酒店早餐后出发前往西山风景区"以"酒店"开头，
     旧逻辑整条否掉，一天的景点数就少了一个。
  3. 加词的反作用：补了"山""路"之后，"午餐：西山脚下农家菜""路上注意安全"
     这类句子会蒙混过关，所以反例必须一起守住。

跑法（不需要启动服务，纯函数）：
    cd backend && python tests/test_eval_checks.py
    cd backend && python -m unittest tests.test_eval_checks -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "evals"))

from checks import _is_named_place  # noqa: E402

# (活动文本, 为什么算具体地点)
POSITIVE = [
    ("游览曾厝垵文创村", "村"),
    ("环岛路骑行（曾厝垵→黄厝海滩）", "路 + 海滩"),
    ("游览十里画廊（乘坐观光车或短途骑", "画廊"),
    ("游览云南民族村，体验25个少数民族", "村"),
    ("游览西山龙门石窟，攀爬龙门栈道", "石窟 / 栈道"),
    ("乘地铁1号线前往集美学村", "学村"),
    ("漫步大同路与思明东路，逛老城文艺", "路"),
    ("游览八市（第八市场），感受老城烟火", "市场"),
    ("酒店早餐后出发前往西山风景区", "三层前缀：酒店+早餐+出发"),
    ("酒店早餐后，游览昆明老街（南屏街", "两层前缀：酒店+早餐"),
    ("游览星海广场", "广场"),
    ("前往金石滩海滨浴场", "交通动词 + 浴场"),
    ("打卡回民街美食街", "美食街是地名，不能因为含'美食'就排除"),
    ("参观陕西历史博物馆", "博物馆"),
    ("登泰山看日出", "山"),
]

# (活动文本, 为什么不算)
NEGATIVE = [
    ("自由活动，享受悠闲时光", "没有去处"),
    ("睡到自然醒，酒店休息", "没有去处"),
    ("午餐：曾厝垵海鲜小吃", "餐饮"),
    ("晚餐：黄厝海鲜大排档", "餐饮"),
    ("早餐：阳朔客栈附近米粉店", "餐饮"),
    ("午餐：西山脚下农家菜", "餐饮——含'山'字，靠结尾词拦住"),
    ("入住曾厝垵附近文艺民宿", "住宿"),
    ("返回酒店休息", "住宿"),
    ("整理行李，退房", "退房"),
    ("路上注意安全，沿途欣赏风景", "'路上'不是地名"),
    ("体验乡村旅游的乐趣", "'乡村'不是地名"),
    ("在酒店休息，调整状态", "没有去处"),
    ("", "空文本"),
    ("游览", "太短，不构成地名"),
]


class NamedPlaceTest(unittest.TestCase):
    def test_positive_are_places(self):
        for text, why in POSITIVE:
            with self.subTest(text=text):
                self.assertTrue(_is_named_place({"activity": text}),
                                f"应判为具体地点（{why}）：{text}")

    def test_negative_are_not_places(self):
        for text, why in NEGATIVE:
            with self.subTest(text=text):
                self.assertFalse(_is_named_place({"activity": text}),
                                 f"不应判为具体地点（{why}）：{text}")

    def test_description_is_not_used(self):
        """判定只看 activity 字段：description 里常有大段形容词，
        混进来会让"午餐：随便吃点"因为描述里提到某个地名而被算成景点。"""
        act = {"activity": "自由活动", "description": "在星海广场附近闲逛"}
        self.assertFalse(_is_named_place(act))


if __name__ == "__main__":
    unittest.main(verbosity=2)

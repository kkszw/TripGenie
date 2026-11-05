# author: szw
"""行程生成的提示词与输出模板。"""

# 给模型看的输出样例。注意：这是普通字符串，不是 f-string，
# 里面的 { } 是 JSON 的花括号本身，不要误加转义。
PLAN_TEMPLATE = """
{
  "title": "大连5日自由行",
  "destination": "辽宁省大连市",
  "totalDays": 5,
  "budget": 6000,
  "overview": "本次行程涵盖城市地标、海洋公园、异域风情和海滨休闲，适合喜欢自由探索的旅行者。",
  "days": [
    {
      "day": 1,
      "theme": "初识浪漫之都 · 城市地标与海滨漫步",
      "date": "9月1日",
      "weather": "晴转多云，25-30℃，海风3-4级，适合户外活动",
      "activities": [
        {
          "time": "09:00-10:30",
          "activity": "抵达大连，入住酒店（推荐青泥洼桥附近经济型酒店）",
          "description": "该活动的具体说明与实用提示",
          "cost": 180
        },
        {
          "time": "11:00-12:30",
          "activity": "游览中山广场，欣赏俄式、日式与欧式混合建筑群",
          "description": "该活动的具体说明与实用提示",
          "cost": 0
        },
        {
          "time": "12:30-13:30",
          "activity": "午餐：本地特色小吃店",
          "description": "推荐尝试焖子、海菜包子、咸鱼饼子等大连地道小吃，人均约30元。",
          "cost": 30
        },
        {
          "time": "14:00-17:00",
          "activity": "星海广场散步，欣赏跨海大桥，海边拍照",
          "description": "该活动的具体说明与实用提示",
          "cost": 0
        },
        {
          "time": "18:00-19:30",
          "activity": "晚餐：星海广场附近海鲜排档",
          "description": "推荐清蒸扇贝、辣炒蚬子、海胆蒸蛋，人均约80元。",
          "cost": 80
        }
      ],
      "notes": "实际总花费远低于6000元预算，预留充足空间用于升级餐饮、住宿或临时消费。所有价格基于普通游客标准，已考虑学生证优惠及经济型消费习惯。",
      "estimatedCost": 320
    }
  ],
  "tips": [
    "大连风大，建议携带薄外套",
    "海边紫外线强，注意防晒",
    "使用学生证可节省景点门票"
  ]
}
"""


def build_plan_prompt(destination: str, days, budget, preferences, weather,
                      start_date: str, guides: str, attractions: str,
                      review_feedback: str = "") -> str:
    """行程生成 prompt。

    review_feedback 只在被质检打回重排时才有值：不带的话第二版大概率只是换个
    措辞重写一遍，那这次反思就白花钱了。
    """
    # 重排时把质检的修改指令带进 prompt；第一版没有 feedback，这段为空。
    review_block = ""
    if review_feedback:
        review_block = (
            "\n    上一版行程被质检打回，这一版必须逐条改掉下面的问题，不要原样再交一遍：\n"
            f"    {review_feedback}\n"
        )

    pref_text = ", ".join(preferences or [])

    return f"""
    你是一个专业的旅行规划师。根据以下信息生成行程：

    目的地：{destination or '未知'}
    天数：{days or 3} 天
    预算：{budget if budget is not None else 5000} 元
    偏好：{pref_text}
    天气：{weather or '未知'}
    开始日期：{start_date}

    参考攻略：
    {guides or '无'}

    可用景点：
    {attractions or '无'}

    规划要求：
    - 每天至少安排 2 个**具体景点/地点**，写全名（例如"星海广场""老虎滩海洋公园"），
      优先从"可用景点"里选。不要写"游览大连广场与公园""自由活动""逛街"这种
      没法在地图上定位的笼统描述——地图是按天标注这些地名的。
    - 每天安排 3~6 个活动条目（含餐饮、返程），**不要超过 6 个**：
      超过会被质检判为当天安排过密而打回重排，白白多等一次。
    - 最后一天如果主要是返程，也要落到具体地点（例如"大连站""周水子国际机场"），
      可以写"返程：大连站 → 周水子国际机场"，但别把一整天都写成"自由活动"。
{review_block}
    请生成详细的每日行程，包含景点、餐饮、住宿建议。
    只输出一个 JSON 对象，不要加 ```json 代码围栏，不要输出任何解释文字。
    返回 JSON 格式，包含每天的具体安排,格式如下所示：{PLAN_TEMPLATE}
    """

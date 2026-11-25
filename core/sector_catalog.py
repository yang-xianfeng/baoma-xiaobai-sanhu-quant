"""Sector catalog for the 30 leaf sectors across 8 major categories.

Maintains continuity with the original 8 major categories and 30 leaf sectors,
while providing clear mappings to A-share industry/concept boards.
"""

from __future__ import annotations
from typing import Dict, List, TypedDict

class SectorDef(TypedDict):
    id: str
    name: str
    category: str
    board_keywords: List[str]
    typical_companies: List[str]
    description: str

CATEGORIES = [
    "科技与数字经济",
    "高端制造与军工",
    "消费",
    "医药医疗",
    "新能源与汽车",
    "金融地产基建",
    "资源化工与公用",
    "农业",
]

SECTOR_DEFINITIONS: List[SectorDef] = [
    # 1. 科技与数字经济
    {
        "id": "cpo_optical",
        "name": "CPO/光模块",
        "category": "科技与数字经济",
        "board_keywords": ["CPO概念", "光通信模块", "光模块", "光通信"],
        "typical_companies": ["中际旭创", "新易盛", "天孚通信"],
        "description": "光模块/CPO共封装光学核心龙头，算力基础设施高速光互联。"
    },
    {
        "id": "pcb",
        "name": "PCB/印制电路板",
        "category": "科技与数字经济",
        "board_keywords": ["PCB", "印制电路板", "覆铜板"],
        "typical_companies": ["沪电股份", "胜宏科技", "生益电子"],
        "description": "AI服务器高速多层板与HDI高阶印制电路板制造。"
    },
    {
        "id": "ai_compute",
        "name": "AI算力/服务器",
        "category": "科技与数字经济",
        "board_keywords": ["算力概念", "AI服务器", "华为算力", "算力租赁"],
        "typical_companies": ["工业富联", "中科曙光", "浪潮信息"],
        "description": "大模型与人工智能计算硬件集群、AI服务器总装与云算力。"
    },
    {
        "id": "ai_application",
        "name": "AI应用/垂直模型",
        "category": "科技与数字经济",
        "board_keywords": ["AI应用", "ChatGPT概念", "Sora概念", "多模态AI"],
        "typical_companies": ["金山办公", "科大讯飞", "万兴科技"],
        "description": "办公、多模态、生成式AI终端与行业垂直模型商业化落地。"
    },
    {
        "id": "chip_design",
        "name": "芯片设计/半导体",
        "category": "科技与数字经济",
        "board_keywords": ["半导体", "集成电路", "汽车芯片", "MCU芯片"],
        "typical_companies": ["寒武纪", "海光信息", "圣邦股份"],
        "description": "GPU/CPU算力芯片、模拟芯片、存储与射频前端设计研发。"
    },
    {
        "id": "semiconductor_equipment",
        "name": "半导体设备与材料",
        "category": "科技与数字经济",
        "board_keywords": ["半导体设备", "光刻机", "芯片材料", "先进封装"],
        "typical_companies": ["北方华创", "中微公司", "拓荆科技"],
        "description": "晶圆制造前道刻蚀、薄膜沉积、清洗设备与国产替代半导体材料。"
    },
    {
        "id": "telecom_equipment",
        "name": "通信设备与运营",
        "category": "科技与数字经济",
        "board_keywords": ["通信设备", "5G概念", "电信运营", "卫星互联网"],
        "typical_companies": ["中兴通讯", "中国移动", "中国电信"],
        "description": "主干网基站通信设备、卫星通信与三大核心电信运营商。"
    },
    {
        "id": "media_gaming",
        "name": "传媒游戏与数字娱乐",
        "category": "科技与数字经济",
        "board_keywords": ["游戏", "网络游戏", "影视传媒", "短剧互动游戏"],
        "typical_companies": ["三七互娱", "昆仑万维", "恺英网络"],
        "description": "移动网络游戏研发出海、数字影视、短剧与泛娱乐内容生态。"
    },

    # 2. 高端制造与军工
    {
        "id": "robotics",
        "name": "机器人与自动化",
        "category": "高端制造与军工",
        "board_keywords": ["机器人概念", "人形机器人", "减速器", "机器视觉"],
        "typical_companies": ["绿的谐波", "三花智控", "鸣志电器"],
        "description": "人形机器人核心零部件（谐波减速器/行星滚柱丝杠/空心杯电机）与工控自动化。"
    },
    {
        "id": "aerospace_defense",
        "name": "航空航天与国防军工",
        "category": "高端制造与军工",
        "board_keywords": ["航空装备", "航天装备", "军工", "低空经济"],
        "typical_companies": ["中航沈飞", "中航光电", "航发动力"],
        "description": "先进战机、航空发动机、航天装备及低空飞行器军民两用供应链。"
    },

    # 3. 消费
    {
        "id": "baijiu",
        "name": "白酒与高端酒类",
        "category": "消费",
        "board_keywords": ["白酒", "白酒概念", "酿酒行业"],
        "typical_companies": ["贵州茅台", "五粮液", "山西汾酒"],
        "description": "核心高端酱香与浓香白酒，消费板块流动性与基本面风向标。"
    },
    {
        "id": "food_beverage",
        "name": "食品饮料与大众消费",
        "category": "消费",
        "board_keywords": ["食品饮料", "调味品", "乳制品", "软饮料"],
        "typical_companies": ["海天味业", "伊利股份", "东鹏饮料"],
        "description": "调味发酵品、常温乳品与功能饮料等大众刚需必选消费品。"
    },
    {
        "id": "home_appliances",
        "name": "家电与智能家居",
        "category": "消费",
        "board_keywords": ["家用电器", "白色家电", "小家电", "智能家居"],
        "typical_companies": ["美的集团", "海尔智家", "格力电器"],
        "description": "大型白电制造、全球家电出海与智慧家居互联场景生态。"
    },

    # 4. 医药医疗
    {
        "id": "innovative_drugs",
        "name": "创新药与生物制品",
        "category": "医药医疗",
        "board_keywords": ["创新药", "生物制品", "CXO", "抗体药物"],
        "typical_companies": ["百济神州", "恒瑞医药", "药明康德"],
        "description": "小分子与抗体偶联药物(ADC)原创新药研发、海外授权与CXO研发外包服务。"
    },
    {
        "id": "medical_devices",
        "name": "医疗器械与体外诊断",
        "category": "医药医疗",
        "board_keywords": ["医疗器械", "体外诊断", "高值耗材", "生命科学"],
        "typical_companies": ["迈瑞医疗", "联影医疗", "新产业"],
        "description": "高端医学影像设备、生命信息监护与化学发光体外诊断仪器。"
    },

    # 5. 新能源与汽车
    {
        "id": "lithium_battery",
        "name": "锂电池与新型储能",
        "category": "新能源与汽车",
        "board_keywords": ["锂电池", "储能概念", "固态电池", "动力电池"],
        "typical_companies": ["宁德时代", "比亚迪", "亿纬锂能"],
        "description": "动力电池与储能电芯制造、固态电池材料体系升级与工商业储能。"
    },
    {
        "id": "photovoltaic",
        "name": "光伏与绿色能源",
        "category": "新能源与汽车",
        "board_keywords": ["光伏设备", "TOPCon电池", "硅片", "逆变器"],
        "typical_companies": ["隆基绿能", "阳光电源", "通威股份"],
        "description": "高效N型电池片组件、大尺寸硅棒与分布式光伏并网逆变器设备。"
    },
    {
        "id": "ev_vehicles",
        "name": "新能源整车制造",
        "category": "新能源与汽车",
        "board_keywords": ["汽车整车", "新能源车", "华为汽车", "智能汽车"],
        "typical_companies": ["比亚迪", "赛力斯", "长城汽车"],
        "description": "纯电与插电混动乘用车整车制造、品牌高端化与全球出海。"
    },
    {
        "id": "auto_parts",
        "name": "汽车零部件与智能驾驶",
        "category": "新能源与汽车",
        "board_keywords": ["汽车零部件", "智能座舱", "无人驾驶", "车路协同"],
        "typical_companies": ["拓普集团", "德赛西威", "伯特利"],
        "description": "轻量化底盘系统、线控底盘、智驾域控制器与智能座舱人机交互。"
    },

    # 6. 金融地产基建
    {
        "id": "banking",
        "name": "银行与高股息资产",
        "category": "金融地产基建",
        "board_keywords": ["银行", "国有大行", "股份制银行", "农商行"],
        "typical_companies": ["工商银行", "招商银行", "农业银行"],
        "description": "大型国有商业银行与优质零售银行，全市场股息率与稳健价值压舱石。"
    },
    {
        "id": "securities_insurance",
        "name": "证券与非银保险",
        "category": "金融地产基建",
        "board_keywords": ["证券", "证券概念", "保险", "互联网金融"],
        "typical_companies": ["中信证券", "东方财富", "中国平安"],
        "description": "券商财富管理、股票市场交易活跃度直接放大器与综合金融保险服务。"
    },
    {
        "id": "real_estate",
        "name": "房地产开发与运营",
        "category": "金融地产基建",
        "board_keywords": ["房地产开发", "物业管理", "城中村改造", "保障房"],
        "typical_companies": ["万科A", "保利发展", "招商蛇口"],
        "description": "核心城市商品住宅开发、央国企高质量土储与城市综合物业资产运营。"
    },
    {
        "id": "infrastructure",
        "name": "基建工程与中央国企",
        "category": "金融地产基建",
        "board_keywords": ["基础建设", "建筑装饰", "铁路公路", "央企改革"],
        "typical_companies": ["中国建筑", "中国中铁", "中国铁建"],
        "description": "交通水利重大工程总承包、城市轨交地下管网与海外一带一路大基建。"
    },

    # 7. 资源化工与公用
    {
        "id": "gold_precious_metals",
        "name": "黄金与贵金属",
        "category": "资源化工与公用",
        "board_keywords": ["贵金属", "黄金概念", "黄金水道"],
        "typical_companies": ["紫金矿业", "山东黄金", "中金黄金"],
        "description": "黄金原生矿山开采、贵金属冶炼加工与全球宏观避险对冲资产。"
    },
    {
        "id": "metals_rare_earth",
        "name": "工业金属与稀土战略资源",
        "category": "资源化工与公用",
        "board_keywords": ["有色金属", "铜", "铝", "小金属", "稀土永磁"],
        "typical_companies": ["洛阳钼业", "中国铝业", "北方稀土"],
        "description": "电解铜、原铝冶炼与轻重稀土永磁战略原材料储备及精细化开采。"
    },
    {
        "id": "basic_chemicals",
        "name": "基础化工与新材料",
        "category": "资源化工与公用",
        "board_keywords": ["化学原料", "基础化工", "氟化工", "磷化工"],
        "typical_companies": ["万华化学", "华鲁恒升", "龙佰集团"],
        "description": "聚氨酯MDI、煤化工精细化学品与电子级精细氟化工特种新材料。"
    },
    {
        "id": "power_utilities",
        "name": "电力运营与绿色电力",
        "category": "资源化工与公用",
        "board_keywords": ["电力行业", "绿色电力", "火电", "水力发电", "核电"],
        "typical_companies": ["长江电力", "中国核电", "华能国际"],
        "description": "三峡巨型水电群稳健现金流资产、沿海基荷核电机组与大容量清洁煤电。"
    },
    {
        "id": "grid_equipment",
        "name": "电网设备与特高压",
        "category": "资源化工与公用",
        "board_keywords": ["电网设备", "特高压", "智能电网", "充电桩"],
        "typical_companies": ["特变电工", "平高电气", "许继电气"],
        "description": "跨区域特高压交直流输变电成套设备、变压器海外出口与配电网智能化。"
    },

    # 8. 农业
    {
        "id": "hog_farming",
        "name": "生猪养殖与现代畜牧",
        "category": "农业",
        "board_keywords": ["养殖业", "猪肉概念", "饲料", "鸡肉概念"],
        "typical_companies": ["牧原股份", "温氏股份", "新希望"],
        "description": "集约化楼房养猪、生物安全防线与自繁自养全产业链现代化养殖龙头。"
    },
    {
        "id": "seed_grain",
        "name": "种业科技与国家粮食安全",
        "category": "农业",
        "board_keywords": ["种植业与林业", "转基因", "粮食概念", "农业种植"],
        "typical_companies": ["北大荒", "隆平高科", "苏垦农发"],
        "description": "优质水稻杂交育种、玉米大豆生物育种产业化与大面积标准化粮田农场。"
    },
]

def get_sector_by_id(sector_id: str) -> SectorDef | None:
    for s in SECTOR_DEFINITIONS:
        if s["id"] == sector_id:
            return s
    return None

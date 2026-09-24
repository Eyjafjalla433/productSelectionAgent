"""Human-facing labels; canonical option values remain unchanged."""

OPTION_LABELS_ZH = {
    'black': '黑色', 'white': '白色', 'blue': '蓝色', 'red': '红色',
    'pink': '粉色', 'green': '绿色', 'brown': '棕色', 'gray': '灰色',
    'purple': '紫色', 'yellow': '黄色', 'orange': '橙色', 'beige': '米色',
    'navy': '藏青色', 'gold': '金色', 'silver': '银色',
    'cotton': '棉质', 'polyester': '聚酯纤维', 'nylon': '尼龙',
    'leather': '皮革', 'wool': '羊毛', 'spandex': '氨纶', 'silk': '丝质',
    'rayon': '人造丝', 'denim': '牛仔布', 'linen': '亚麻', 'suede': '绒面革',
    'fleece': '抓绒', 'canvas': '帆布', 'mesh': '网眼',
    'casual': '休闲', 'formal': '正式', 'athletic': '运动风',
    'vintage': '复古', 'classic': '经典', 'elegant': '优雅',
    'slim fit': '修身', 'loose fit': '宽松', 'oversized': '宽大型',
    'regular fit': '合身', 'relaxed fit': '宽松版型', 'minimalist': '极简',
    'bohemian': '波西米亚风', 'crew neck': '圆领', 'v-neck': 'V 领',
    'short sleeve': '短袖', 'high waisted': '高腰',
    'sleeveless': '无袖', 'long sleeve': '长袖',
    'hiking': '徒步', 'running': '跑步', 'walking': '散步', 'gym': '健身房',
    'winter': '冬季', 'outdoor': '户外', 'work': '上班', 'wedding': '婚礼',
    'party': '聚会', 'beach': '海边', 'travel': '旅行', 'yoga': '瑜伽',
    'workout': '锻炼', 'summer': '夏季',
}


def question_in_chinese(attribute, options):
    labels = [OPTION_LABELS_ZH.get(value, value) for value in options]
    choices = '、'.join(labels[:-1]) + '还是' + labels[-1] if len(labels) > 1 else labels[0]
    lead = {'color': '颜色更偏向', 'material': '材质更偏向',
            'style': '款式更喜欢', 'use_case': '主要是用于'}.get(attribute, '更偏向')
    return f'{lead}{choices}？也可以先看看这批候选。'

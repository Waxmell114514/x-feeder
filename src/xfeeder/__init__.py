"""xfeeder - a Supernova-Era style consensus engine over X/Twitter.

Pipeline:  ingest -> classify -> extract -> cluster -> synthesize -> report
"""

__version__ = "0.1.0"

COHORTS = ("official", "pro_media", "en_kol", "cn_kol", "crowd")

COHORT_LABELS_ZH = {
    "official": "官方信息",
    "pro_media": "专业媒体",
    "en_kol": "英语区 KOL",
    "cn_kol": "中文区 KOL",
    "crowd": "大众用户",
}

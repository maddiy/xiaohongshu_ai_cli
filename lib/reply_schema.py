"""回复决策、审查枚举和结构化映射的共享模式。"""


REPLY_ACTIONS = ("send", "skip", "archive")
LOGIC_VERDICTS = (
    "sound", "partly_sound", "weak", "fallacious",
    "non_argument", "unclear",
)
FACT_VERDICTS = (
    "supported", "mixed", "contradicted", "unverifiable",
    "not_applicable",
)
BOAST_VERDICTS = (
    "none", "possible", "likely", "unverifiable", "not_applicable",
)

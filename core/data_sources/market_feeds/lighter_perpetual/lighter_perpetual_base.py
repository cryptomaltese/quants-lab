from ..funding_rate_feed_base import FundingRateFeedBase


class LighterPerpetualBase(FundingRateFeedBase):
    _base_url = "https://mainnet.zklighter.elliot.ai"

    RATE_LIMITS = {
        "lighter_general": (120, 60),
    }

    def __init__(self, session=None):
        super().__init__(session)
        for limit_id, (max_req, window) in self.RATE_LIMITS.items():
            self.register_rate_limit(limit_id, max_req, window)

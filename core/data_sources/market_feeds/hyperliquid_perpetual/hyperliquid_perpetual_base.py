from ..funding_rate_feed_base import FundingRateFeedBase


class HyperliquidPerpetualBase(FundingRateFeedBase):
    _base_url = "https://api.hyperliquid.xyz"

    RATE_LIMITS = {
        "hyperliquid_general": (1200, 60),  # 1200 weight/min
    }

    def __init__(self, session=None):
        super().__init__(session)
        for limit_id, (max_req, window) in self.RATE_LIMITS.items():
            self.register_rate_limit(limit_id, max_req, window)
